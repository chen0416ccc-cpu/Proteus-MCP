import copy
import json
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock,patch

from proteus_mcp.audio import AudioCapture,GUID,summarize_audio
from proteus_mcp.diagnostics import diagnostic_names,join_diagnostic,references,topology
from proteus_mcp.model import CircuitError
from proteus_mcp.observations import Observations,correlate_audio
from proteus_mcp.server import TOOLS,check_schema
from proteus_mcp.simulation import digital_level


def state():
    return {'path':'example.pdsprj','components':[
        {'ref':'D1','device':'LED-RED','address':'p1','properties':{'STATE':'0'},
         'pins':[{'number':'1','name':'A','wire':'w0'},{'number':'2','name':'K','wire':'w1'}]},
        {'ref':'U1','device':'AT89C51','address':'p2','properties':{},
         'pins':[{'number':'1','name':'P1.0','wire':'w2'},{'number':'17','name':'P3.7','wire':'0x0'}]}],
        'wires':[{'address':w,'endpoints':[{'owner':a},{'owner':b}],
                  'labels':[{'text':label}] if label else []} for w,a,b,label in
                 [('w0','p1','supply',''),('w1','p1','dot','LED1_K'),('w2','dot','p2','')]],
        'objects':[{'address':'dot','type':'.?AVDOT@@'},
                   {'address':'supply','type':'.?AVTERMINAL@@','net':'VCC'}]}


def sample(t=0.05):
    return {'simulation_time_before':t,'simulation_time_after':t,
            'wall_time_ms':1000+int(t*1000),'stable_snapshot':True,'native_clock_unchanged':True,
            'nets':[{'name':'LED1_K','found':True,'state':7}]}


def snapshot(value):
    return {'modal':None,'actions':[
        {'text':'Run','enabled':value!='running'},
        {'text':'Pause','checked':value=='paused'},
        {'text':'Stop','enabled':value!='stopped'}]}


class AudioTests(unittest.TestCase):
    def test_guid_has_com_layout(self):
        value=GUID.from_text('A95664D2-9614-4F35-A746-DE8DB63617E6')
        self.assertEqual(0xA95664D2,value.data1)
        self.assertEqual(0x9614,value.data2)
        self.assertEqual(16,len(bytes(value)))

    def test_audio_states_are_not_equated_with_human_audibility(self):
        row={'process_id':7,'endpoint_id':'endpoint','peak':.5,'muted':False,'volume':1}
        cases=[([], 'no_session'),([dict(row,peak=0)],'silent'),
               ([dict(row,muted=True)],'muted_or_zero_volume'),([row],'output_detected')]
        for rows,want in cases:
            result=summarize_audio([{'sessions':rows}])
            self.assertEqual(want,result['status'])
            self.assertFalse(result['audibility_verified'])
            self.assertFalse(result['recording'])
            self.assertFalse(result['volume_changed'])
        self.assertEqual('partial',summarize_audio([{'sessions':[row]}],[{'error':'failed'}])['status'])

    def test_capture_closes_provider_on_its_own_thread(self):
        class Provider:
            closed=False
            def __enter__(self):return self
            def read(self,pid):return [],[]
            def __exit__(self,*unused):Provider.closed=True
        with AudioCapture(7,20,Provider) as capture:
            time.sleep(.025)
        self.assertTrue(Provider.closed)
        self.assertEqual('no_session',capture.summary()['status'])

    def test_capture_failure_is_unavailable_not_silence(self):
        class Provider:
            def __enter__(self):raise OSError('endpoint failure')
            def __exit__(self,*unused):pass
        with AudioCapture(7,20,Provider) as capture:pass
        self.assertEqual('unavailable',capture.summary()['status'])

    def test_wall_clock_correlation_does_not_extend_beyond_trace(self):
        frames=[{'wall_time_ms':x,'sessions':[]} for x in (900,1050,1060,2000)]
        rows=correlate_audio(frames,[sample(.05),sample(.1)])
        self.assertEqual([1050,1060],[r['wall_time_ms'] for r in rows])
        self.assertEqual(1,rows[0]['levels']['LED1_K'])
        self.assertEqual(10,rows[1]['digital_age_ms'])


class JointTopologyTests(unittest.TestCase):
    def test_live_branches_and_named_terminals_map_physical_pins(self):
        mapping=topology(state())
        self.assertEqual(['D1.2','U1.1'],mapping['D1.2']['pins'])
        self.assertEqual(['LED1_K'],mapping['U1.1']['names'])
        self.assertEqual(['VCC'],mapping['D1.1']['names'])
        self.assertFalse(mapping['U1.17']['connected'])
        self.assertEqual(['LED1_K','VCC'],diagnostic_names(state(),['D1']))

    def test_unknown_owners_and_missing_refs_are_not_guessed(self):
        bad=state();bad['objects']=[]
        with self.assertRaisesRegex(CircuitError,'UNKNOWN_WIRE_OWNER'):topology(bad)
        with self.assertRaisesRegex(CircuitError,'NOT_FOUND'):diagnostic_names(state(),['BAD'])
        for refs in ([],['D1','D1'],['bad\nref']):
            with self.assertRaises(CircuitError):references(refs)

    def test_analogue_and_unnamed_signals_stay_unknown(self):
        capture={'state':state(),'vector':sample(),'simulation_time_before':0,'simulation_time_after':.1}
        result=join_diagnostic(capture,['D1'],digital_level)
        pins=result['components'][0]['pins']
        self.assertIsNone(pins[0]['level'])
        self.assertEqual(1,pins[1]['level'])
        self.assertFalse(result['same_native_clock'])
        self.assertFalse(result['optical_verified'])
        self.assertEqual('0',result['components'][0]['native_state'])

    def test_separate_same_named_wires_join_without_component_body_short(self):
        data=state();data['wires'][2]['endpoints'][0]['owner']='0x0'
        data['wires'][2]['labels']=[{'text':'LED1_K'}]
        mapping=topology(data)
        self.assertEqual(['D1.2','U1.1'],mapping['D1.2']['pins'])
        self.assertNotEqual(mapping['D1.1']['pins'],mapping['D1.2']['pins'])


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.project=root/'fixture.pdsprj';self.project.write_bytes(b'fixture')
        runtime=MagicMock();runtime.session_path.return_value=root/'session.json'
        runtime.status.return_value={'processes':[{'ProcessId':7,'created':'now','ExecutablePath':'PDS.exe'}]}
        runtime.load.return_value={'path':str(self.project)}
        self.api=MagicMock();self.bridge=MagicMock(runtime=runtime)
        @contextmanager
        def attach(*args,**kwargs):yield self.api,'hwnd'
        self.bridge.attach.side_effect=attach
        self.obs=Observations(self.bridge)
        self.api.snapshot.return_value=snapshot('stopped')
        self.data=state();self.data['path']=str(self.project)
        self.data['components'].append({'ref':'SW1','device':'BUTTON','address':'p3','properties':{},'pins':[]})
        self.api.liveread.return_value=self.data
        self.events=[{'ref':'SW1','value':1,'at_ms':100},{'ref':'SW1','value':0,'at_ms':200}]
        self.api.simcold.return_value={'samples':[sample()], 'error':None,'identity':{},
            'dispatched':[dict(e,dispatched=True) for e in self.events]}

    def test_invalid_cold_plan_never_attaches(self):
        for kwargs in ({'events':[]},{'audio':1},{'max_start_ms':True},
                       {'events':[{'ref':'SW1','value':1,'at_ms':True}]}):
            args={'session':'s','events':self.events,'nets':['LED1_K'],**kwargs}
            with self.assertRaises(CircuitError):self.obs.cold_start(**args)
        self.bridge.attach.assert_not_called()

    def test_running_and_paused_sessions_are_not_restarted(self):
        for value in ('running','paused'):
            self.api.snapshot.return_value=snapshot(value)
            with self.assertRaisesRegex(CircuitError,'REQUIRES_STOPPED'):
                self.obs.cold_start('s',self.events,['LED1_K'])
        self.api.simcold.assert_not_called()
        self.api.command.assert_not_called()

    def test_success_has_startup_evidence_and_closed_epoch(self):
        result=self.obs.cold_start('s',self.events,['LED1_K'],refs=['D1'])
        self.assertEqual('sampled',result['status'])
        self.assertTrue(result['startup_window_observed'])
        self.assertTrue(result['project_file_unchanged'])
        self.assertTrue(result['cleanup']['stopped'])
        saved=json.loads(Path(result['evidence_path']).read_text())
        self.assertEqual(1,len(saved['samples']))
        self.assertFalse(saved['simulation_verified'])
        epoch=json.loads((Path(self.temp.name)/'simulation-epoch.json').read_text())
        self.assertEqual('stopped',epoch['state'])

    def test_missed_start_window_retains_partial_samples(self):
        self.api.simcold.return_value={'samples':[sample(2)],'dispatched':[],
                                       'error':'COLD_START_WINDOW_MISSED'}
        result=self.obs.cold_start('s',self.events,['LED1_K'])
        self.assertEqual('partial',result['status'])
        self.assertFalse(result['startup_window_observed'])
        self.assertIn('WINDOW_MISSED',result['error'])

    def test_failed_native_call_still_stops_owned_simulation(self):
        self.api.simcold.side_effect=RuntimeError('failed')
        self.api.snapshot.side_effect=[snapshot('stopped'),snapshot('running'),snapshot('stopped')]
        result=self.obs.cold_start('s',self.events,['LED1_K'])
        self.api.command.assert_called_once()
        self.assertTrue(result['cleanup']['stopped'])
        self.assertEqual('unconfirmed',result['status'])

    def test_unknown_button_is_rejected_before_run(self):
        with self.assertRaisesRegex(CircuitError,'INPUT_NOT_CALIBRATED'):
            self.obs.cold_start('s',[{'ref':'D1','value':1,'at_ms':0}],['LED1_K'])
        self.api.simcold.assert_not_called()

    def test_post_run_file_failure_preserves_samples_and_cleanup(self):
        from proteus_mcp.diagnostics import file_provenance
        before=file_provenance(self.data)
        with patch('proteus_mcp.observations.file_provenance',side_effect=[before,OSError('disk failure')]):
            result=self.obs.cold_start('s',self.events,['LED1_K'])
        self.assertEqual('partial',result['status'])
        self.assertIn('disk failure',result['identity_error'])
        saved=json.loads(Path(result['evidence_path']).read_text())
        self.assertEqual(1,len(saved['samples']))
        self.assertTrue(saved['cleanup']['stopped'])

    def test_changed_document_is_not_stopped_or_reported_as_success(self):
        changed=copy.deepcopy(self.data);changed['path']='other.pdsprj'
        self.api.liveread.side_effect=[self.data,changed,changed]
        self.api.snapshot.side_effect=[snapshot('stopped'),snapshot('running')]
        result=self.obs.cold_start('s',self.events,['LED1_K'])
        self.assertEqual('partial',result['status'])
        self.assertFalse(result['cleanup']['stopped'])
        self.api.command.assert_not_called()

    def test_audio_rpc_failure_retains_meter_frames(self):
        meter=MagicMock();meter.__enter__.return_value=meter
        meter.frames=[{'wall_time_ms':1000,'sessions':[]}]
        meter.summary.return_value={'status':'no_session'}
        self.api.simtrace.side_effect=RuntimeError('trace disconnected')
        self.obs.sim.ready=MagicMock(return_value={'identity':{'path':str(self.project)}})
        self.obs.sim.epoch=MagicMock(return_value='epoch')
        with patch('proteus_mcp.observations.AudioCapture',return_value=meter):
            result=self.obs.audio('s',['LED1_K'],wall_ms=100)
        self.assertEqual('partial',result['status'])
        self.assertIn('trace disconnected',result['capture_error'])
        saved=json.loads(Path(result['evidence_path']).read_text())
        self.assertEqual(meter.frames,saved['frames'])
        meter.__exit__.assert_called_once()

    def test_diagnostic_rejects_connections_changed_during_preflight(self):
        changed=copy.deepcopy(self.data)
        changed['wires'][1]['labels']=[{'text':'CHANGED'}]
        self.obs.sim.ready=MagicMock(return_value={'identity':{'path':str(self.project)}})
        self.obs.sim.epoch=MagicMock(return_value='epoch')
        self.api.simdiagnose.return_value={'identity':{'path':str(self.project)},'state':changed}
        with self.assertRaisesRegex(CircuitError,'TOPOLOGY_CHANGED'):
            self.obs.diagnose('s',['D1'])

    def test_public_schemas_cover_new_tools_and_reject_bool_offsets(self):
        tools={t['name']:t for t in TOOLS}
        for name in ('simulation_audio','simulation_diagnose','simulation_cold_start'):
            self.assertIn(name,tools)
        schema=tools['simulation_cold_start']['inputSchema']
        check_schema({'session':'s','events':self.events,'nets':['LED1_K']},schema)
        bad=copy.deepcopy(self.events);bad[0]['at_ms']=True
        with self.assertRaises(ValueError):check_schema({'session':'s','events':bad,'nets':['LED1_K']},schema)
