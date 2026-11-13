import copy
import json
import struct
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock,patch

from proteus_mcp.devices import animation_frames,parse_device,selected_definition
from proteus_mcp.model import CircuitError,Store,atomic_write,json_bytes
from proteus_mcp.server import TOOLS,check_schema
from proteus_mcp.simulation import Simulation,digital_level,input_token,net_names,trace_summary,compact
from proteus_mcp.display import decode_samples,decode_display
from test_extensions import device_blob


class SimulationTests(unittest.TestCase):
    def test_strength_codes_and_unknowns(self):
        self.assertEqual([0,1,0,1,None,None,None],[digital_level(x) for x in (5,7,21,23,0,True,None)])

    def test_invalid_nets(self):
        for value in ([],['X','X'],['bad\nname'],['x']*65,[1],None):
            with self.subTest(value=value),self.assertRaises((CircuitError,TypeError)):
                net_names(value)

    def test_boolean_schema_is_not_integer(self):
        check_schema(True,{'type':'boolean'})
        for value in (1,'true',None):
            with self.assertRaises(ValueError):check_schema(value,{'type':'boolean'})
        with self.assertRaises(ValueError):check_schema(True,{'type':'integer'})

    def test_stimulus_schema_rejects_boolean_and_unknown_fields(self):
        schema=next(t['inputSchema'] for t in TOOLS if t['name']=='simulation_stimulate')
        base={'session':'s','expected_token':'t','nets':['X'],'events':[{'ref':'S1','value':1,'at_ms':0}]}
        check_schema(base,schema)
        for field,value in (('value',True),('at_ms',-1),('at_ms',20001),('ref',None),('unknown',1)):
            bad=copy.deepcopy(base);bad['events'][0][field]=value
            with self.assertRaises(ValueError):check_schema(bad,schema)

    def test_token_excludes_clock_but_includes_epoch_context_and_eid(self):
        inventory={'identity':{'document':'a'},'simulation_time':1,'inputs':[{'ref':'S1','device':'BUTTON','address':'p','eid':2}]}
        token=input_token('s','epoch',inventory)
        changed=copy.deepcopy(inventory);changed['simulation_time']=10
        self.assertEqual(token,input_token('s','epoch',changed))
        self.assertNotEqual(token,input_token('s','new',inventory))
        self.assertNotEqual(token,input_token('other','epoch',inventory))
        for field in ('ref','device','address','eid'):
            changed=copy.deepcopy(inventory);changed['inputs'][0][field]='changed'
            self.assertNotEqual(token,input_token('s','epoch',changed))

    def test_trace_counts_unknown_missing_and_clock_crossings(self):
        samples=[{'simulation_time_before':0,'simulation_time_after':.1,'stable_snapshot':False,
            'native_clock_unchanged':False,'nets':[{'name':'X','found':False,'state':None}]}]
        result=trace_summary(samples)
        self.assertEqual(['X'],result['missing_nets'])
        self.assertEqual(1,result['unknown_state_count'])
        self.assertEqual(1,result['unstable_snapshot_count'])
        self.assertEqual(1,result['clock_crossing_count'])
        self.assertIsNone(result['transitions'][0]['levels'][0])

    def test_epoch_and_receipt_reject_old_run_and_time_rewind(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=MagicMock();runtime.session_path.return_value=Path(folder)/'session.json'
            sim=Simulation(MagicMock(runtime=runtime))
            inventory={'identity':{},'inputs':[],'simulation_time':10}
            with self.assertRaisesRegex(CircuitError,'EPOCH_REQUIRED'):sim.epoch('s')
            atomic_write(Path(folder)/'simulation-epoch.json',json_bytes({'state':'running','epoch':'e'}))
            token=input_token('s','e',inventory)
            atomic_write(sim.receipt('s'),json_bytes({'token':token,'simulation_time':11}))
            with self.assertRaisesRegex(CircuitError,'REWOUND'):sim.validate_token('s',token,inventory)
            inventory['simulation_time']=12
            self.assertEqual(token,sim.validate_token('s',token,inventory))
            atomic_write(Path(folder)/'simulation-epoch.json',json_bytes({'state':'running','epoch':'new'}))
            with self.assertRaisesRegex(CircuitError,'REVISION_CONFLICT'):sim.validate_token('s',token,inventory)

    def test_event_validation_precedes_native_attachment(self):
        bridge=MagicMock();sim=Simulation(bridge)
        for events in ([],[{'ref':'S1','value':True,'at_ms':0}],
                       [{'ref':'S1','value':1,'at_ms':2},{'ref':'S1','value':0,'at_ms':1}],
                       [{'ref':'S1','value':1,'at_ms':0,'extra':True}]):
            with self.assertRaises(CircuitError):sim.stimulate('s','t',events,['X'])
        bridge.attach.assert_not_called()

    def test_trace_bounds_precede_attachment(self):
        bridge=MagicMock();sim=Simulation(bridge)
        for kwargs in ({'wall_ms':0},{'interval_ms':True},{'max_samples':20001}):
            with self.assertRaises(CircuitError):sim.observe('s',['X'],**kwargs)
        bridge.attach.assert_not_called()

    def test_client_transition_summary_is_bounded_without_mutating_evidence(self):
        result={'transitions':[{'i':i} for i in range(600)]}
        summary=compact(result)
        self.assertEqual(512,len(summary['transitions']))
        self.assertEqual(600,len(result['transitions']))
        self.assertTrue(summary['transitions_truncated'])

    def test_failed_trace_retains_partial_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=MagicMock();runtime.session_path.return_value=Path(folder)/'session.json'
            bridge=MagicMock(runtime=runtime);api=MagicMock()
            @contextmanager
            def attach(*args,**kwargs):yield api,'hwnd'
            bridge.attach.side_effect=attach
            api.simtrace.return_value={'samples':[{'simulation_time_before':1,'simulation_time_after':1,
                'stable_snapshot':True,'native_clock_unchanged':True,'nets':[{'name':'X','found':True,'state':7}]}],
                'error':'SIMULATION_CONTEXT_CHANGED'}
            sim=Simulation(bridge)
            inventory={'identity':{},'inputs':[],'simulation_time':1}
            with patch.object(sim,'epoch',return_value='e'),patch.object(sim,'ready',return_value=inventory):
                result=sim.observe('s',['X'])
            self.assertEqual('partial',result['status'])
            self.assertEqual(1,len(json.loads(Path(result['evidence_path']).read_text())['samples']))
            self.assertFalse((Path(folder)/'live.lock').exists())

    def test_delayed_press_cannot_compress_following_release(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=MagicMock();runtime.session_path.return_value=Path(folder)/'session.json'
            bridge=MagicMock(runtime=runtime);api=MagicMock()
            @contextmanager
            def attach(*args,**kwargs):yield api,'hwnd'
            bridge.attach.side_effect=attach
            def sample(t):return {'samples':[{'simulation_time_before':t,'simulation_time_after':t,
                'stable_snapshot':True,'native_clock_unchanged':True,'nets':[{'name':'X','found':True,'state':7}]}],'error':None}
            api.simtrace.side_effect=[sample(1.05),sample(1.05),sample(1.10),sample(1.15)]
            api.simactuate.side_effect=[{'simulation_time_before':t,'simulation_time_after':t,'dispatched':True} for t in (1.05,1.1)]
            sim=Simulation(bridge)
            inventory={'identity':{},'inputs':[{'ref':'SW1'}],'simulation_time':1}
            atomic_write(sim.receipt('s'),json_bytes({}))
            with patch.object(sim,'ready',return_value=inventory),patch.object(sim,'validate_token',return_value='t'),patch('proteus_mcp.simulation.time.sleep'):
                result=sim.stimulate('s','t',[{'ref':'SW1','value':1,'at_ms':0},{'ref':'SW1','value':0,'at_ms':50}],['X'])
            self.assertEqual('dispatched',result['status'])
            self.assertAlmostEqual(1.10,result['dispatched'][1]['effective_due_time'])
            self.assertAlmostEqual(1.05,result['dispatched'][1]['scheduled_simulation_time'])
            self.assertEqual(4,api.simtrace.call_count)

    def test_failed_stimulus_keeps_receipt_and_does_not_hide_applied_events(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=MagicMock();runtime.session_path.return_value=Path(folder)/'session.json'
            bridge=MagicMock(runtime=runtime);api=MagicMock()
            @contextmanager
            def attach(*args,**kwargs):yield api,'hwnd'
            bridge.attach.side_effect=attach
            sample={'simulation_time_before':1,'simulation_time_after':1,'stable_snapshot':True,
                'native_clock_unchanged':True,'nets':[{'name':'X','found':True,'state':7}]}
            api.simtrace.side_effect=[{'samples':[sample],'error':None},{'samples':[sample],'error':'context changed'}]
            api.simactuate.return_value={'simulation_time_before':1,'simulation_time_after':1,'dispatched':True}
            sim=Simulation(bridge);inventory={'identity':{},'inputs':[{'ref':'SW1'}],'simulation_time':1}
            with patch.object(sim,'ready',return_value=inventory),patch.object(sim,'validate_token',return_value='t'):
                result=sim.stimulate('s','t',[{'ref':'SW1','value':1,'at_ms':0},{'ref':'SW1','value':0,'at_ms':100}],['X'])
            self.assertEqual('partial',result['status'])
            self.assertEqual(1,len(result['dispatched']))
            evidence=json.loads(Path(result['evidence_path']).read_text())
            self.assertEqual(2,len(evidence['samples']))
            self.assertTrue(evidence['may_have_applied'])


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.segments=['S'+str(i) for i in range(8)];self.digits=['D'+str(i) for i in range(4)]

    def sample(self,time,position,pattern=0x3f,other=False):
        values=[7 if (pattern>>i)&1 else 5 for i in range(8)]
        values += [13 if i==position or (other and i==(position+1)%4) else 7 for i in range(4)]
        return {'simulation_time_before':time,'simulation_time_after':time,'stable_snapshot':True,
            'native_clock_unchanged':True,'nets':[{'name':n,'found':True,'state':v} for n,v in zip(self.segments+self.digits,values)]}

    def test_zero_9999_and_repeated_frames(self):
        for pattern,want in ((0x3f,'0000'),(0x6f,'9999')):
            samples=[self.sample(i*.01,i%4,pattern) for i in range(8)]
            result=decode_samples(samples,self.segments,self.digits)
            self.assertEqual(want,result['frames'][0]['text'])
            self.assertEqual(2,result['frames'][0]['confirmations'])

    def test_missing_digit_does_not_invent_full_frame(self):
        result=decode_samples([self.sample(i*.01,i%3) for i in range(12)],self.segments,self.digits)
        self.assertEqual([],result['frames'])

    def test_unknown_segment_ambiguous_select_and_stale_frames(self):
        bad=self.sample(.01,0);bad['nets'][0]['state']=0
        samples=[bad,self.sample(.02,1,other=True),self.sample(.03,2),self.sample(1,3)]
        result=decode_samples(samples,self.segments,self.digits,max_frame_ms=100)
        self.assertEqual(1,result['invalid_samples'])
        self.assertEqual(1,result['ambiguous_select_samples'])
        self.assertEqual(1,result['expired_partial_frames'])
        self.assertEqual([],result['frames'])

    def test_does_not_decode_inconsistent_vector_or_clock_crossing(self):
        bad=self.sample(0,0);bad['native_clock_unchanged']=False
        result=decode_samples([bad],self.segments,self.digits)
        self.assertEqual(1,result['unstable_or_clock_crossing_samples'])

    def test_polarities_overlap_and_rewind_rejected(self):
        for args in ((self.segments,self.digits,True),(self.segments,self.segments[:4],1)):
            with self.assertRaises(CircuitError):decode_samples([],args[0],args[1],args[2])
        with self.assertRaisesRegex(CircuitError,'REWOUND'):
            decode_samples([self.sample(1,0),self.sample(0,1)],self.segments,self.digits)

    def test_source_hash_and_workspace_boundary(self):
        from proteus_mcp.model import digest
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'trace.json'
            atomic_write(path,json_bytes({'samples':[self.sample(0,0)],'native_nets':self.segments+self.digits}))
            sha=digest(path.read_bytes())
            with self.assertRaisesRegex(CircuitError,'REVISION_CONFLICT'):
                decode_display(folder,str(path),'old',self.segments,self.digits)
            with self.assertRaisesRegex(CircuitError,'workspace'):
                decode_display(Path(folder)/'other',str(path),sha,self.segments,self.digits)
            result=decode_display(folder,str(path),sha,self.segments,self.digits)
            self.assertTrue(Path(result['evidence_path']).is_file())


class AnimationTests(unittest.TestCase):
    def test_frame_bounds_count_and_duplicates(self):
        frame=b'F0\0'+struct.pack('<III',20,1,1)+struct.pack('<II',8,123)
        self.assertEqual('F0',animation_frames(frame,0,len(frame))[0]['name'])
        for bad in (frame[:-1],frame+frame,b'F0\0'+struct.pack('<III',20,2,1)+struct.pack('<II',8,123),
                    b'F0\0'+struct.pack('<III',20,1,0)+struct.pack('<II',8,123)):
            with self.assertRaises(CircuitError):animation_frames(bad,0,len(bad))

    def test_unpinned_animated_definition_rejected(self):
        blob=bytearray(device_blob());struct.pack_into('<H',blob,10,2)
        with self.assertRaisesRegex(CircuitError,'ANIMATED_DEVICE_NOT_CALIBRATED'):parse_device(bytes(blob),'UNKNOWN')

    def test_selected_definition_checks_both_hashes(self):
        spec={'library_sha256':'l','definition_sha256':'d'}
        with patch('proteus_mcp.devices.definition',return_value=spec) as definition:
            part={'device':'BUZZER','native_definition':{'library_path':'explicit.LIB',**spec}}
            self.assertEqual(spec,selected_definition(part));definition.assert_called_with('BUZZER','explicit.LIB')
            part['native_definition']['definition_sha256']='changed'
            with self.assertRaisesRegex(CircuitError,'LIBRARY_CHANGED'):selected_definition(part)

    def test_store_carries_library_choice_through_add_set_and_connect(self):
        from proteus_mcp.catalog import catalog
        spec={'pins':[{'number':'1','name':'A'},{'number':'2','name':'B'}],
            'defaults':{'VALUE':'BUZZER'},'library':'ACTIVE','library_path':'explicit.LIB',
            'library_sha256':'l','definition_sha256':'d','source':'fixture'}
        with tempfile.TemporaryDirectory() as folder,patch('proteus_mcp.devices.definition',return_value=spec) as get:
            store=Store(folder);store.create('Test')
            store.add('Test',0,'B1','BUZZER',[0,0],library_path='explicit.LIB')
            store.set_component('Test',1,'B1',{'value':'test'})
            store.add('Test',2,'R1','RES',[100,0])
            value=store.connect('Test',3,'SIGNAL',['B1.A','R1.1'])
            self.assertEqual(['B1.1','R1.1'],value['nets']['SIGNAL'])
            self.assertEqual('explicit.LIB',value['components']['B1']['native_definition']['library_path'])
            self.assertTrue(all(c.args==('BUZZER','explicit.LIB') for c in get.call_args_list))


if __name__=='__main__':unittest.main()
