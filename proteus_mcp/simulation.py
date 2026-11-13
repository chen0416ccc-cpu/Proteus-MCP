"""Bounded native input stimulation and simulator-clock-bracketed net traces."""
import json
import re
import time
import uuid

from .bridge import simulation_state
from .live import Live
from .model import CircuitError, atomic_write, digest, json_bytes


def digital_level(raw):
    # Only individually observed calibrated codes; unknown strengths stay unknown.
    return {5:0,7:1,13:0,21:0,23:1}.get(raw) if type(raw) is int else None


def net_names(nets):
    if not isinstance(nets,list) or not 1<=len(nets)<=64 or any(
        not isinstance(n,str) or not re.fullmatch(r'[A-Za-z0-9_#+.:-]{1,64}',n) for n in nets) or len(set(nets))!=len(nets):
        raise CircuitError('Provide 1..64 distinct bounded native net names')
    return nets


def input_token(session, epoch, inventory):
    return digest(json_bytes({'session':session,'epoch':epoch,'identity':inventory['identity'],
        'inputs':[{k:p[k] for k in ('ref','device','address','eid')} for p in inventory['inputs']]}))


def trace_summary(samples):
    transitions=[]
    for i,sample in enumerate(samples):
        for net in sample['nets']:
            net['level']=digital_level(net['state'])
        levels=[net['level'] for net in sample['nets']]
        if not transitions or transitions[-1]['levels']!=levels:
            transitions.append({'sample_index':i,'simulation_time_before':sample['simulation_time_before'],
                'simulation_time_after':sample['simulation_time_after'],'levels':levels,
                'raw_states':[net['state'] for net in sample['nets']]})
    return {'sample_count':len(samples),'transitions':transitions,
        'missing_nets':sorted({n['name'] for s in samples for n in s['nets'] if not n['found']}),
        'unknown_state_count':sum(n['level'] is None for s in samples for n in s['nets']),
        'unstable_snapshot_count':sum(not s['stable_snapshot'] for s in samples),
        'clock_crossing_count':sum(not s['native_clock_unchanged'] for s in samples),
        'simulation_time_start':samples[0]['simulation_time_before'] if samples else None,
        'simulation_time_end':samples[-1]['simulation_time_after'] if samples else None}


def compact(result):
    transitions=result.get('transitions',[])
    return result | {'transitions':transitions[:512],'transition_count':len(transitions),
                     'transitions_truncated':len(transitions)>512}


class Simulation:
    def __init__(self, bridge):
        self.bridge,self.runtime=bridge,bridge.runtime
        self.live=Live(bridge,None)

    def receipt(self,session):
        return self.runtime.session_path(session).parent/'simulation-inputs.json'

    def epoch(self,session):
        path=self.receipt(session).with_name('simulation-epoch.json')
        data=json.loads(path.read_text()) if path.is_file() else {}
        if data.get('state')!='running' or not data.get('epoch'):
            raise CircuitError('SIMULATION_EPOCH_REQUIRED: start the simulation with simulation_control')
        return data['epoch']

    def ready(self,session,api,hwnd):
        status=api.snapshot(hwnd)
        if status.get('modal') or simulation_state(status)!='running':
            raise CircuitError('SIMULATION_REQUIRES_RUNNING_UNBLOCKED_SESSION')
        inventory=api.siminputs(hwnd)
        self.live.check_document(session,inventory['identity'])
        return inventory

    def inputs(self,session):
        with self.live.lock(session),self.bridge.attach(session,simulation=True) as (api,hwnd):
            epoch=self.epoch(session)
            inventory=self.ready(session,api,hwnd)
            token=input_token(session,epoch,inventory)
            result={'status':'read','session':session,'token':token,'epoch':epoch,**inventory,
                'desktop_input':False,'persistence':'runtime_events_only; configured STATE and disk not changed'}
            atomic_write(self.receipt(session),json_bytes(result))
            return result

    def validate_token(self,session,expected_token,inventory):
        epoch=self.epoch(session)
        path=self.receipt(session)
        previous=json.loads(path.read_text()) if path.is_file() else {}
        actual=input_token(session,epoch,inventory)
        if expected_token!=actual or previous.get('token')!=actual:
            raise CircuitError('SIMULATION_INPUT_REVISION_CONFLICT: refresh simulation_inputs')
        if inventory['simulation_time']<previous.get('simulation_time',0):
            raise CircuitError('SIMULATION_TIME_REWOUND: refresh simulation_inputs after restart')
        return actual

    def observe(self,session,nets,wall_ms=1000,interval_ms=5,max_samples=10000):
        net_names(nets)
        if type(wall_ms) is not int or not 1<=wall_ms<=25000 or type(interval_ms) is not int or not 1<=interval_ms<=1000 or type(max_samples) is not int or not 1<=max_samples<=20000:
            raise CircuitError('Trace bounds: wall_ms=1..25000, interval_ms=1..1000, max_samples=1..20000')
        with self.live.lock(session),self.bridge.attach(session,simulation=True) as (api,hwnd):
            epoch=self.epoch(session)
            inventory=self.ready(session,api,hwnd)
            capture=api.simtrace(hwnd,inventory['identity'],nets,wall_ms,interval_ms,max_samples)
            samples=capture['samples'];error=capture.get('error')
            try:
                after=self.ready(session,api,hwnd)
                if input_token(session,epoch,inventory)!=input_token(session,self.epoch(session),after):
                    raise CircuitError('SIMULATION_CONTEXT_CHANGED_DURING_TRACE')
            except Exception as exc:error=error or str(exc)
            result={'status':('partial' if samples else 'unconfirmed') if error else 'sampled',
                'error':error,'session':session,'epoch':epoch,'native_nets':nets,**trace_summary(samples),
                'sample_limit_reached':capture.get('sample_limit_reached',False),
                'wall_ms_requested':wall_ms,'wall_ms_elapsed':capture.get('wall_ms_elapsed'),
                'desktop_input':False,'simulation_verified':False,'sampling':'bounded wall-clock polling; no complete waveform guarantee',
                'time_source':'VSMDEBUG native simulator clock, bracketed per vector; animation clock may be coarser than MCU events'}
            target=self.receipt(session).with_name('trace-'+uuid.uuid4().hex[:12]+'.json')
            atomic_write(target,json_bytes(result | {'samples':samples,'identity':inventory['identity']}))
            return compact(result) | {'evidence_path':str(target)}

    def stimulate(self,session,expected_token,events,nets,timeout_ms=10000,interval_ms=5):
        net_names(nets)
        if not isinstance(events,list) or not 1<=len(events)<=32:
            raise CircuitError('Provide 1..32 input events')
        previous=-1
        for event in events:
            if not isinstance(event,dict) or set(event)!={'ref','value','at_ms'} or not isinstance(event['ref'],str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',event['ref']):
                raise CircuitError('Input event requires exactly ref, value and at_ms')
            if type(event['value']) is not int or event['value'] not in (0,1) or type(event['at_ms']) is not int or not 0<=event['at_ms']<=20000 or event['at_ms']<previous:
                raise CircuitError('Input events require binary values and nondecreasing at_ms=0..20000')
            previous=event['at_ms']
        if type(timeout_ms) is not int or not 100<=timeout_ms<=30000 or type(interval_ms) is not int or not 1<=interval_ms<=1000:
            raise CircuitError('Input bounds: timeout_ms=100..30000, interval_ms=1..1000')
        with self.live.lock(session),self.bridge.attach(session,simulation=True) as (api,hwnd):
            inventory=self.ready(session,api,hwnd)
            self.validate_token(session,expected_token,inventory)
            targets={p['ref']:p for p in inventory['inputs']}
            if any(e['ref'] not in targets for e in events):
                raise CircuitError('SIMULATION_INPUT_NOT_CALIBRATED: only inventory BUTTON/LOGICSTATE inputs')
            target=self.receipt(session).with_name('stimulus-'+uuid.uuid4().hex[:12]+'.json')
            result={'status':'unconfirmed','session':session,'token':expected_token,'events':events,
                'dispatched':[],'native_nets':nets,'desktop_input':False,'atomic':False,'persistent_state_changed':False,
                'simulation_verified':False,'network_effect_verified':False,
                'time_origin':inventory['simulation_time'],
                'schedule':'wait for actual native time; preserve requested inter-event spacing after delivery delays; never future-time injection'}
            atomic_write(target,json_bytes(result | {'may_have_applied':True}))
            deadline=time.monotonic()+timeout_ms/1000
            samples=[]
            try:
                for index,event in enumerate(events):
                    nominal=inventory['simulation_time']+event['at_ms']/1000
                    due=nominal
                    if result['dispatched']:
                        spacing=(event['at_ms']-events[index-1]['at_ms'])/1000
                        due=max(due,result['dispatched'][-1]['simulation_time_after']+spacing)
                    while True:
                        capture=api.simtrace(hwnd,inventory['identity'],nets,1,interval_ms,1)
                        batch=capture['samples']
                        samples.extend(batch)
                        if capture.get('error'):raise CircuitError(capture['error'])
                        if len(samples)>=19800:raise CircuitError('SIMULATION_TRACE_SAMPLE_LIMIT')
                        if batch[-1]['simulation_time_after']>=due:
                            break
                        if time.monotonic()>=deadline:
                            raise CircuitError('SIMULATION_INPUT_TIMEOUT: no more events delivered')
                        time.sleep(interval_ms/1000)
                    if time.monotonic()>=deadline:
                        raise CircuitError('SIMULATION_INPUT_TIMEOUT: no more events delivered')
                    response=api.simactuate(hwnd,inventory['identity'],targets[event['ref']],event['value'])
                    response.update(index=index,scheduled_simulation_time=nominal,effective_due_time=due,
                        lateness_ms=1000*(response['simulation_time_before']-nominal))
                    result['dispatched'].append(response)
                    atomic_write(target,json_bytes(result | {'may_have_applied':True}))
                capture=api.simtrace(hwnd,inventory['identity'],nets,100,interval_ms,200)
                samples.extend(capture['samples'])
                if capture.get('error'):raise CircuitError(capture['error'])
                after=self.ready(session,api,hwnd)
                self.validate_token(session,expected_token,after)
                atomic_write(self.receipt(session),json_bytes(json.loads(self.receipt(session).read_text()) | {'simulation_time':after['simulation_time']}))
                result['status']='dispatched'
            except Exception as error:
                result.update(status='partial' if result['dispatched'] else 'unconfirmed',error=str(error),may_have_applied=True)
            result.update(trace_summary(samples))
            atomic_write(target,json_bytes(result | {'samples':samples,'identity':inventory['identity']}))
            return compact(result) | {'evidence_path':str(target)}
