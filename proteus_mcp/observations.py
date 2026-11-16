"""Evidence-scoped audio, joint diagnosis and bounded cold-start runs."""
import time
import uuid

from .audio import AudioCapture
from .bridge import LABELS, simulation_state
from .diagnostics import diagnostic_names, diagnostic_topology, file_provenance, join_diagnostic, references
from .model import CircuitError, atomic_write, json_bytes
from .simulation import Simulation, compact, digital_level, net_names, trace_summary


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise CircuitError(label+' must be an integer in '+str(low)+'..'+str(high))


def event_plan(events):
    import re
    if not isinstance(events,list) or not 1 <= len(events) <= 32:
        raise CircuitError('Provide 1..32 cold-start input events')
    previous = -1
    for event in events:
        if (not isinstance(event,dict) or set(event) != {'ref','value','at_ms'} or
                not isinstance(event['ref'],str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',event['ref'])):
            raise CircuitError('Cold-start events require ref, value and at_ms')
        integer(event['value'],0,1,'value')
        integer(event['at_ms'],0,20000,'at_ms')
        if event['at_ms'] < previous:
            raise CircuitError('Cold-start event offsets must be nondecreasing')
        previous = event['at_ms']


def correlate_audio(frames, samples):
    """Nearest preceding digital vector; never infer analogue current or causation."""
    import bisect
    times = [s['wall_time_ms'] for s in samples]
    matched = []
    if not times:
        return matched
    for frame in frames:
        index = bisect.bisect_right(times,frame['wall_time_ms'])-1
        if index < 0 or frame['wall_time_ms'] > times[-1]:
            continue
        sample = samples[index]
        matched.append({'wall_time_ms':frame['wall_time_ms'],
                        'digital_age_ms':frame['wall_time_ms']-times[index],
                        'simulation_time_before':sample['simulation_time_before'],
                        'simulation_time_after':sample['simulation_time_after'],
                        'levels':{n['name']:digital_level(n['state']) for n in sample['nets']},
                        'sessions':frame['sessions']})
    return matched


class Observations:
    def __init__(self, bridge):
        self.bridge, self.runtime = bridge, bridge.runtime
        self.sim = Simulation(bridge)

    def owned_process(self, session):
        status = self.runtime.status(session)
        if len(status['processes']) != 1:
            raise CircuitError('OBSERVATION_REQUIRES_ONE_OWNED_PROCESS')
        return status['processes'][0]

    def target(self, session, prefix):
        return self.runtime.session_path(session).with_name(prefix+'-'+uuid.uuid4().hex[:12]+'.json')

    def check_process(self, session, expected):
        current = self.owned_process(session)
        if any(current[k] != expected[k] for k in ('ProcessId','created','ExecutablePath')):
            raise CircuitError('SIMULATION_PROCESS_CHANGED')

    def diagnose(self, session, refs):
        references(refs)
        with self.sim.live.lock(session), self.bridge.attach(session,simulation=True) as (api,hwnd):
            epoch = self.sim.epoch(session)
            inventory = self.sim.ready(session,api,hwnd)
            state = api.liveread(hwnd,['PROGRAM','CLOCK'])
            self.sim.live.check_document(session,state)
            names = diagnostic_names(state,refs)
            if names:
                net_names(names)
            capture = api.simdiagnose(hwnd,names)
            if inventory['identity'] != capture['identity']:
                raise CircuitError('SIMULATION_CONTEXT_CHANGED')
            if capture['state']['path'] != state['path']:
                raise CircuitError('SIMULATION_DOCUMENT_CHANGED')
            self.sim.live.check_document(session,capture['state'])
            if diagnostic_topology(state) != diagnostic_topology(capture['state']):
                raise CircuitError('DIAGNOSTIC_TOPOLOGY_CHANGED: retry with current connections')
            result = {'status':'sampled','session':session,'epoch':epoch,
                      **join_diagnostic(capture,refs,digital_level),
                      'provenance':file_provenance(capture['state']),
                      'desktop_input':False,'simulation_verified':False}
            path = self.target(session,'diagnostic')
            atomic_write(path,json_bytes(result | {'capture':capture}))
            return result | {'evidence_path':str(path)}

    def audio(self, session, nets=None, wall_ms=2000, interval_ms=50):
        integer(wall_ms,100,25000,'wall_ms')
        integer(interval_ms,20,1000,'interval_ms')
        names = [] if nets is None else net_names(nets)
        with self.sim.live.lock(session), self.bridge.attach(session,simulation=True) as (api,hwnd):
            epoch = self.sim.epoch(session)
            inventory = self.sim.ready(session,api,hwnd)
            process = self.owned_process(session)
            path = self.target(session,'audio')
            capture = {'samples':[],'error':None}
            audio = AudioCapture(process['ProcessId'],interval_ms)
            try:
                with audio:
                    if names:
                        capture = api.simtrace(hwnd,inventory['identity'],names,wall_ms,interval_ms,20000)
                    else:
                        time.sleep(wall_ms/1000)
            except Exception as exc:
                capture['error'] = capture.get('error') or str(exc)
            identity_error = None
            try:
                after = self.sim.ready(session,api,hwnd)
                self.check_process(session,process)
                if after['identity'] != inventory['identity']:
                    raise CircuitError('SIMULATION_CONTEXT_CHANGED')
                if self.sim.epoch(session) != epoch:
                    raise CircuitError('SIMULATION_EPOCH_CHANGED')
            except Exception as exc:
                identity_error = str(exc)
            result = {'session':session,'epoch':epoch,'process_id':process['ProcessId'],
                      **audio.summary(),'digital':trace_summary(capture['samples']),
                      'identity_error':identity_error,'capture_error':capture.get('error'),
                      'desktop_input':False,'simulation_verified':False}
            if identity_error or capture.get('error'):
                result['status'] = 'partial'
            atomic_write(path,json_bytes(result | {'frames':audio.frames,'samples':capture['samples'],
                'correlated':correlate_audio(audio.frames,capture['samples'])}))
            return result | {'evidence_path':str(path)}

    def cold_start(self, session, events, nets, refs=None, audio=False, timeout_ms=15000,
                   interval_ms=10, max_start_ms=500, tail_ms=800):
        event_plan(events); net_names(nets)
        refs = [] if refs is None else references(refs)
        integer(timeout_ms,100,30000,'timeout_ms')
        integer(interval_ms,1,1000,'interval_ms')
        integer(max_start_ms,1,5000,'max_start_ms')
        integer(tail_ms,100,5000,'tail_ms')
        if type(audio) is not bool:
            raise CircuitError('audio must be boolean')
        with self.sim.live.lock(session), self.bridge.attach(session,simulation=True) as (api,hwnd):
            before = api.snapshot(hwnd)
            if before.get('modal') or simulation_state(before) != 'stopped':
                raise CircuitError('COLD_START_REQUIRES_STOPPED_SESSION')
            state = api.liveread(hwnd,['PROGRAM','CLOCK'])
            self.sim.live.check_document(session,state)
            parts = {p['ref']:p for p in state['components']}
            if set(refs)-parts.keys():
                raise CircuitError('DIAGNOSTIC_COMPONENT_NOT_FOUND')
            for event in events:
                if parts.get(event['ref'],{}).get('device') not in ('BUTTON','LOGICSTATE'):
                    raise CircuitError('SIMULATION_INPUT_NOT_CALIBRATED')
            provenance = file_provenance(state)
            process = self.owned_process(session)
            epoch = uuid.uuid4().hex
            epoch_path = self.sim.receipt(session).with_name('simulation-epoch.json')
            atomic_write(epoch_path,json_bytes({'epoch':epoch,'state':'unconfirmed','action':'cold_start','session':session}))
            path = self.target(session,'cold-start')
            result = {'session':session,'epoch':epoch,'status':'unconfirmed','events':events,
                      'provenance':provenance,'desktop_input':False,'persistent_state_changed':False,
                      'simulation_verified':False,'network_effect_verified':False,'optical_verified':False,
                      'sampling':'Bounded digital polling and native STATE; not complete waveform or optical verification'}
            atomic_write(path,json_bytes(result | {'may_have_applied':True}))
            meter = None
            capture = {'samples':[],'dispatched':[],'error':None}
            try:
                if audio:
                    meter = AudioCapture(process['ProcessId'],max(20,interval_ms))
                    meter.__enter__()
                capture = api.simcold(hwnd,nets,refs,events,timeout_ms,interval_ms,max_start_ms,tail_ms)
            except Exception as exc:
                capture['error'] = str(exc)
            finally:
                if meter is not None:
                    try:
                        meter.__exit__(None,None,None)
                    except Exception as exc:
                        capture['error'] = capture.get('error') or str(exc)
                try:
                    self.check_process(session,process)
                    self.sim.live.check_document(session,api.liveread(hwnd,['PROGRAM','CLOCK']))
                    current = api.snapshot(hwnd)
                    if current.get('modal'):
                        raise CircuitError('SIMULATION_MODAL_BLOCKED')
                    if simulation_state(current) != 'stopped':
                        if capture.get('identity') and api.siminputs(hwnd)['identity'] != capture['identity']:
                            raise CircuitError('SIMULATION_CONTEXT_CHANGED')
                        api.command(hwnd,LABELS['stop'],'')
                        deadline = time.monotonic()+6
                        while simulation_state(api.snapshot(hwnd)) != 'stopped':
                            if time.monotonic() >= deadline:
                                raise CircuitError('COLD_START_STOP_UNCONFIRMED')
                            time.sleep(.05)
                    result['cleanup'] = {'stopped':True}
                except Exception as exc:
                    result['cleanup'] = {'stopped':False,'error':str(exc)}
                atomic_write(epoch_path,json_bytes({'epoch':epoch,'state':'stopped' if result['cleanup']['stopped'] else 'unconfirmed',
                                                   'action':'cold_start','session':session}))
            result.update({k:v for k,v in capture.items() if k != 'samples'})
            result.update(trace_summary(capture['samples']))
            result['startup_window_observed'] = bool(capture['samples']) and capture['samples'][0]['simulation_time_before']*1000 <= max_start_ms
            try:
                self.check_process(session,process)
                after = api.liveread(hwnd,['PROGRAM','CLOCK'])
                self.sim.live.check_document(session,after)
                if diagnostic_topology(state) != diagnostic_topology(after):
                    raise CircuitError('DIAGNOSTIC_TOPOLOGY_CHANGED')
                result['provenance_after'] = file_provenance(after)
                result['project_file_unchanged'] = result['provenance_after']['project_sha256'] == provenance['project_sha256']
                result['firmware_files_unchanged'] = result['provenance_after']['firmware'] == provenance['firmware']
                result['identity_error'] = None
            except Exception as exc:
                result['identity_error'] = str(exc)
            success = (not capture.get('error') and not result['identity_error'] and result['startup_window_observed'] and
                       result.get('project_file_unchanged') and result.get('firmware_files_unchanged') and
                       len(capture['dispatched'])==len(events) and result['cleanup']['stopped'])
            result['status'] = 'sampled' if success else 'partial' if capture['samples'] or capture['dispatched'] else 'unconfirmed'
            if meter is not None:
                result['audio'] = meter.summary()
            evidence = result | {'samples':capture['samples']}
            if meter is not None:
                evidence.update(audio_frames=meter.frames,correlated_audio=correlate_audio(meter.frames,capture['samples']))
            atomic_write(path,json_bytes(evidence))
            return compact(result) | {'evidence_path':str(path)}
