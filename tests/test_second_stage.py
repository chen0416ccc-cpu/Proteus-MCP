import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path

from proteus_mcp.checks import command_inventory,drc_report,erc_report,fresh_reports
from proteus_mcp.devices import external_pinouts,pinout_tables,unit_view
from proteus_mcp.live import Live,geometry_issues,property_projection,reference,semantic_state
from proteus_mcp.model import CircuitError
from proteus_mcp.native_backend import property_map
from proteus_mcp.runtime import normal_close_targets,project_windows


PINOUT = '*PINOUT DIL14\nELEMENTS=4\nIP A=1,4,10,13\nIP B=2,5,9,12\nOP Y=3,6,8,11\nPP (VCC)=14\nPP (GND)=7\n'


class MultiUnitTests(unittest.TestCase):
    def spec(self):
        return {'pins':[{'name':name,'number':str(i),'hidden':False} for i,name in enumerate(('A','B','Y'),1)],
                'defaults':{'PACKAGE':'DIL14'},'packagings':pinout_tables(PINOUT)}

    def test_explicit_unit_mapping_and_implicit_supply_source(self):
        info = unit_view(self.spec(),'B')
        self.assertEqual({'A':'4','B':'5','Y':'6','VCC':'14','GND':'7'}, {p['name']:p['number'] for p in info['pins']})
        self.assertEqual(4,info['unit_count'])
        self.assertEqual('native_packaging_hidden_supply',info['pins'][-1]['source'])

    def test_missing_or_invalid_unit_rejected(self):
        for unit in (None,'E','AB',True):
            with self.assertRaises(CircuitError):
                unit_view(self.spec(),unit)

    def test_native_external_record_length_and_name_guard(self):
        data = PINOUT.encode()
        blob = struct.pack('<I',len(data)+5)+data+b'\0'
        self.assertIn('DIL14',external_pinouts(blob,'DIL14'))
        self.assertFalse(external_pinouts(blob,'OTHER'))
        self.assertFalse(external_pinouts(b'BAD!'+blob[4:],'DIL14'))

    def test_unit_reference_is_bounded(self):
        self.assertEqual('U12:B',reference('U12:B'))
        for ref in ('U1:AA','U1:b','U1:A:B','U1;Delete',None):
            with self.assertRaises(CircuitError):
                reference(ref)


class HistoryTests(unittest.TestCase):
    def state(self):
        return {'components':[{'address':'p','ref':'R1','device':'RES','value':'1k','properties':{'VALUE':'1k'},
                               'pins':[{'number':'1','wire':'w','xy':[0,0]}]}],
                'objects':[], 'wires':[{'address':'w','points':[[0,0],[10,0]],'labels':[],
                                       'endpoints':[{'owner':'p','xy':[0,0]},{'owner':'0x0','xy':[10,0]}]}]}

    def test_semantics_ignore_reallocated_addresses_and_wire_direction(self):
        before = self.state()
        after = copy.deepcopy(before)
        after['components'][0]['address'] = 'new'
        after['components'][0]['pins'][0]['wire'] = 'neww'
        after['wires'][0].update(address='neww',points=[[10,0],[0,0]])
        after['wires'][0]['endpoints'][0]['owner'] = 'new'
        self.assertEqual(semantic_state(before),semantic_state(after))
        after['components'][0]['pins'][0]['wire'] = '0x0'
        self.assertNotEqual(semantic_state(before),semantic_state(after))

    def test_moved_connected_pin_requires_current_polyline_and_owner(self):
        state=self.state()
        state['components'][0]['pins'][0]['xy']=[2,3]
        state['wires'][0]['endpoints'][0]['xy']=[2,3]
        self.assertEqual('LIVE_WIRE_POLYLINE_ENDPOINT_MISMATCH',geometry_issues(state)[0]['error'])
        state['wires'][0]['points'][0]=[2,3]
        self.assertFalse(geometry_issues(state))
        state['wires'][0]['endpoints'][0]['owner']='wrong'
        self.assertEqual('LIVE_PIN_WIRE_LINK_MISMATCH',geometry_issues(state)[0]['error'])

    def test_property_expansion_cannot_hide_structural_revision(self):
        before = self.state(); before['components'][0]['properties'] = {'CLOCK':None}
        expanded = copy.deepcopy(before)
        expanded['components'][0]['properties']['PROGRAM'] = 'firmware.hex'
        self.assertEqual(before,property_projection(expanded,['CLOCK']))
        expanded['components'][0]['value'] = 'bad'
        self.assertNotEqual(before,property_projection(expanded,['CLOCK']))

    def test_history_branch_trim_divergence_and_noop(self):
        with tempfile.TemporaryDirectory() as folder:
            live = Live.__new__(Live)
            live.receipt_path = lambda session:Path(folder)/'live-state.json'
            a = self.state();b = copy.deepcopy(a);c = copy.deepcopy(a)
            b['components'][0]['value'] = '2k'
            c['components'][0]['value'] = '3k'
            live.record_history('s',a,b,[])
            path = live.history_path('s')
            data = json.loads(path.read_text());data['cursor'] = 0
            path.write_text(json.dumps(data))
            live.record_history('s',a,c,[])
            data = json.loads(path.read_text())
            self.assertEqual(1,len(data['frames']))
            self.assertEqual(c,data['frames'][0]['after'])
            live.record_history('s',a,b,[])
            self.assertEqual(1,len(json.loads(path.read_text())['frames']))
            live.record_history('s',b,b,[])
            self.assertFalse(path.exists())


class NativeReportTests(unittest.TestCase):
    def test_native_property_comparison_includes_unbraced_user_fields(self):
        self.assertEqual({'PACKAGE':'RES40','TOLERANCE':'5%'},property_map('{PACKAGE=RES40}\n\nTOLERANCE=5%\n'))
    def text(self,ending='No ERC errors found.'):
        return '#I:Native log\nELECTRICAL RULES CHECK\n======================\nDesign:   test.pdsprj\n'+ending+'\n'

    def test_complete_clean_error_and_undriven_reports(self):
        self.assertTrue(erc_report(self.text(),'test.pdsprj')['passed'])
        report = erc_report(self.text('ERROR:    U1:Y connected to U2:Y\nUNDRIVEN: U1:A\nERC errors found.'),'test.pdsprj')
        self.assertEqual(1,report['error_count'])
        self.assertEqual(1,report['undriven_count'])
        self.assertFalse(report['passed'])

    def test_unrecognized_incomplete_and_wrong_project_rejected(self):
        for text,design in ((self.text(),'other.pdsprj'),(self.text('Running...'),'test.pdsprj'),
                            ('No ERC errors found.','test.pdsprj'),(self.text('ERC errors found.'),'test.pdsprj')):
            self.assertIsNone(erc_report(text,design))

    def test_undriven_only_respects_native_failed_verdict(self):
        report = erc_report(self.text('UNDRIVEN: U1:A\nERC errors found.'),'test.pdsprj')
        self.assertTrue(report['completed'])
        self.assertFalse(report['passed'])
        self.assertEqual(0,report['error_count'])
        self.assertEqual(1,report['undriven_count'])

    def test_contradictory_clean_and_count_mismatches_are_rejected(self):
        for ending in ('ERROR: output short\nNo ERC errors found.',
                       'WARNING: unknown supply\nNo ERC errors found.',
                       'UNDRIVEN: U1:A\n0 ERC errors found.',
                       '2 ERC errors found.',
                       'No ERC errors found.\nERC errors found.'):
            self.assertIsNone(erc_report(self.text(ending),'test.pdsprj'))

    def test_same_text_requires_new_native_change_event(self):
        before = [{'document':'d','text':self.text(),'change_count':0}]
        self.assertFalse(fresh_reports(before,before))
        self.assertEqual(1,len(fresh_reports([{**before[0],'change_count':1}],before)))

    def test_command_deduplication_and_discovery_scope(self):
        action = {'class':'Appln::Action','owner_module':'ISIS','command_id':334858,'text':'ERC','enabled':True,'checked':False}
        items = command_inventory([action,action,{**action,'owner_module':'ARES'}])
        self.assertEqual(2,len(items))
        item = command_inventory([action,{**action,'enabled':False}],module='ISIS')[0]
        self.assertFalse(item['state_consistent'])
        self.assertFalse(item['arbitrary_execution_exposed'])
        self.assertEqual('discovered',item['status'])

    def test_project_anchor_survives_report_window_title(self):
        process = {'ProcessId':1,'windows':[{'hwnd':55,'visible':True,'title':'ERC operation...'},
                                           {'hwnd':99,'visible':True,'title':'Electrical Rules Check'}]}
        data = {'project_hwnd':55}
        self.assertEqual([55],[w['hwnd'] for w in project_windows(process,data)])
        self.assertFalse(project_windows(process,{}))


class DrcReportTests(unittest.TestCase):
    def native(self,ignored=None):
        rows = [] if ignored is None else [{'rule':'POWER','violation_type':'RESIST-TRACE',
                'layers':'Top Copper','specified_clearance':'1.00th','actual_clearance':'-1.00th','ignored':ignored}]
        return {'rows':rows,'total':len(rows),'active':sum(not r['ignored'] for r in rows),
                'ignored':sum(r['ignored'] for r in rows),'table_truncated':False,
                'native_checker_limit':5000,'native_checker_limit_reached':False,
                'refreshed':True,'global_disabled':False,'real_time_enabled':True}

    def test_clean_requires_fresh_complete_native_result(self):
        result = drc_report(self.native())
        self.assertTrue(result['completed'])
        self.assertTrue(result['passed'])
        self.assertFalse(result['full_pcb_drc'])
        self.assertIn('connectivity_CRC',result['unverified_coverage'])

    def test_real_violation_is_failed_design_not_failed_invocation(self):
        result = drc_report(self.native(False))
        self.assertTrue(result['completed'])
        self.assertFalse(result['passed'])
        self.assertEqual(1,result['active_error_count'])

    def test_ignored_violation_is_never_clean(self):
        result = drc_report(self.native(True))
        self.assertTrue(result['native_passed_excluding_ignored'])
        self.assertFalse(result['passed'])
        self.assertEqual(1,result['ignored_error_count'])

    def test_disabled_or_cached_empty_table_cannot_pass(self):
        for change in ({'global_disabled':True},{'refreshed':False}):
            result = drc_report(self.native() | change)
            self.assertFalse(result['completed'])
            self.assertFalse(result['fresh'])
            self.assertIsNone(result['passed'])

    def test_native_and_readback_limits_cannot_pass(self):
        for change in ({'table_truncated':True},{'native_checker_limit_reached':True}):
            result = drc_report(self.native() | change)
            self.assertFalse(result['completed'])
            self.assertIsNone(result['passed'])

    def test_native_count_disagreement_is_unconfirmed(self):
        for change in ({'total':1},{'active':1},{'ignored':1}):
            result = drc_report(self.native() | change)
            self.assertFalse(result['result_table_complete'])
            self.assertIsNone(result['native_passed_excluding_ignored'])

    def test_explicit_refresh_does_not_require_changing_realtime_settings(self):
        result = drc_report(self.native() | {'real_time_enabled':False})
        self.assertTrue(result['completed'])
        self.assertFalse(result['real_time_enabled'])


class NativeCloseTests(unittest.TestCase):
    def state(self,enabled=True):
        return {'processes':[{'ProcessId':1,'windows':[
            {'hwnd':10,'visible':True,'enabled':enabled,'title':'owned.pdsprj - Proteus'},
            {'hwnd':20,'visible':True,'enabled':True,'title':'Save Changes'},
            {'hwnd':30,'visible':True,'enabled':True,'title':'PCB Layout'}]}]}

    def test_only_anchored_project_not_qt_dialogs_can_be_closed(self):
        self.assertEqual([10],[w['hwnd'] for w in normal_close_targets(self.state(),{'project_hwnd':10})])

    def test_modal_disabled_project_blocks_all_close_messages(self):
        self.assertFalse(normal_close_targets(self.state(False),{'project_hwnd':10}))

    def test_missing_enabled_state_or_ambiguous_main_is_rejected(self):
        state=self.state();state['processes'][0]['windows'][0].pop('enabled')
        self.assertFalse(normal_close_targets(state,{'project_hwnd':10}))
        state=self.state();state['processes'][0]['windows'].append(
            {'hwnd':40,'title':'other.pdsprj','visible':True,'enabled':True})
        self.assertFalse(normal_close_targets(state,{}))


if __name__ == '__main__':
    unittest.main()
