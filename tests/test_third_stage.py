import copy
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proteus_mcp.manufacturing import bom_rows, export_bom, safe_csv_cell
from proteus_mcp.live import deletion_projection, disk_matches, prepare_operations, semantic_state, state_token
from proteus_mcp.model import CircuitError, digest
from proteus_mcp.pcb import board_token, route_spec
from proteus_mcp.pcb_chain import plan_chain
from proteus_mcp.pcb_push import wire_id


class BranchDeletionTests(unittest.TestCase):
    def setUp(self):
        self.state = {'components': [
            {'address': '0x100', 'ref': 'R1', 'device': 'RES', 'value': '1k', 'position_nm': [0,0],
             'rotation': 0, 'mirror': 0, 'properties': {}, 'pins': [{'number':'1','wire':'0x400','xy':[0,0]}]},
            {'address': '0x200', 'ref': 'R2', 'device': 'RES', 'value': '1k', 'position_nm': [20,0],
             'rotation': 0, 'mirror': 0, 'properties': {}, 'pins': [{'number':'1','wire':'0x500','xy':[20,0]}]}],
            'objects': [{'address':'0x300','type':'.?AVDOT@@','origin':[10,0]},
                        {'address':'0x700','type':'.?AVTERMINAL@@','origin':[10,10],'net':'GND','label':{'text':'GND'}}],
            'wires': [{'address':'0x400','points':[[0,0],[10,0]],'endpoints':[{'owner':'0x100','xy':[0,0]},
                       {'owner':'0x300','xy':[10,0]}],'labels':[{'text':'BRANCH','geometry':{'font':'Default Font'}}]},
                      {'address':'0x500','points':[[20,0],[10,0]],'endpoints':[{'owner':'0x200','xy':[20,0]},
                       {'owner':'0x300','xy':[10,0]}],'labels':[]},
                      {'address':'0x600','points':[[10,0],[10,10]],'endpoints':[{'owner':'0x300','xy':[10,0]},
                       {'owner':'0x700','xy':[10,10]}],'labels':[]}]}

    def test_one_branch_does_not_flood_fill_or_remove_dot(self):
        before = copy.deepcopy(self.state)
        ops = prepare_operations(self.state, [{'op':'delete_wire','wire_address':'0x400'}], None)
        result = deletion_projection(self.state, ops)
        self.assertEqual(['0x500','0x600'], [w['address'] for w in result['wires']])
        self.assertEqual(before['objects'], result['objects'])
        self.assertEqual('0x0', result['components'][0]['pins'][0]['wire'])
        self.assertEqual('0x500', result['components'][1]['pins'][0]['wire'])
        self.assertEqual(before, self.state)

    def test_dot_removal_requires_exact_incident_wire_list(self):
        for supplied in (None, [], ['0x400'], ['0x400','0x500','0x600','0x800'], ['0x400']*3):
            with self.assertRaises(CircuitError):
                prepare_operations(self.state, [{'op':'delete_junction','junction_address':'0x300',
                                                 'connected_wire_addresses':supplied}], None)
        ops = prepare_operations(self.state, [{'op':'delete_junction','junction_address':'0x300',
                                               'connected_wire_addresses':['0x600','0x400','0x500']}], None)
        result = deletion_projection(self.state, ops)
        self.assertFalse(result['wires'])
        self.assertEqual([self.state['objects'][1]], result['objects'])
        self.assertTrue(all(p['pins'][0]['wire']=='0x0' for p in result['components']))

    def test_wrong_type_stale_address_repetition_and_mixed_batches_rejected(self):
        for ops in ([{'op':'delete_wire','wire_address':'0x300'}],
                    [{'op':'delete_junction','junction_address':'0x700','connected_wire_addresses':[]}],
                    [{'op':'delete_wire','wire_address':'0x888'}],
                    [{'op':'delete_wire','wire_address':'0x400'}]*2,
                    [{'op':'delete_wire','wire_address':'0x400'},{'op':'set','ref':'R1','properties':{'VALUE':'2k'}}]):
            with self.assertRaises(CircuitError): prepare_operations(self.state, ops, None)

    def test_unrecognized_owner_is_not_silently_removed(self):
        self.state['wires'][0]['endpoints'][1]['owner'] = '0x999'
        with self.assertRaisesRegex(CircuitError, 'UNKNOWN_ENDPOINT'):
            prepare_operations(self.state, [{'op':'delete_wire','wire_address':'0x400'}], None)

    def test_addresses_are_not_numeric_expressions_or_null(self):
        for address in (None, 1024, '0x0', '0x0400', '0x400+1', '0xfffffffffffffffff'):
            with self.assertRaises(CircuitError):
                prepare_operations(self.state, [{'op':'delete_wire','wire_address':address}], None)

    def test_label_layout_and_effective_terminal_net_are_revision_checked(self):
        before = state_token(self.state)
        self.state['wires'][0]['labels'][0]['geometry']['font'] = 'Changed'
        self.assertNotEqual(before, state_token(self.state))
        before = semantic_state(self.state)
        self.state['objects'][1]['net'] = 'VCC'
        self.assertNotEqual(before, semantic_state(self.state))

    def test_terminal_aliases_join_only_same_named_nets_in_disk_projection(self):
        self.state['wires'] = [self.state['wires'][0], self.state['wires'][1]]
        self.state['objects'] = [{'address':'0x300','type':'.?AVTERMINAL@@','net':'GND'},
                                 {'address':'0x700','type':'.?AVTERMINAL@@','net':'GND'}]
        self.state['wires'][1]['endpoints'][1]['owner'] = '0x700'
        self.state['wires'][0]['labels'] = []
        disk = {'components':[{k:v for k,v in p.items() if k in ('ref','device','value','rotation','mirror')} |
                              {'origin_grid':[0,0] if p['ref']=='R1' else [20/254000,0], 'native_properties':''}
                              for p in self.state['components']],
                'stored_connectivity':{'GND':['R1.1','R2.1']}}
        self.assertTrue(disk_matches(self.state, disk))
        self.state['objects'][1]['net'] = 'VCC'
        self.assertFalse(disk_matches(self.state, disk))


class BomTests(unittest.TestCase):
    def state(self):
        part = lambda ref, device, value, package: {'ref': ref, 'device': device, 'value': value,
                                                  'native_properties': '{PACKAGE='+package+'}\n' if package else ''}
        return {'database_consistent': True, 'components': [part('U1:A', '74HC00', '74HC00', 'DIL14'),
            part('U1:B', '74HC00', '74HC00', 'DIL14'), part('R10', 'RES', '1k', 'RES40'),
            part('R2', 'RES', '1k', 'RES40'), part('VM1', 'VOLTMETER', '', '')],
            'package_device_names': {'U1': '74HC00'}}

    def test_units_count_once_and_references_are_naturally_sorted(self):
        state = self.state()
        before = copy.deepcopy(state)
        result = bom_rows(state)
        self.assertEqual(3, result['physical_part_count'])
        self.assertEqual(5, result['instance_count'])
        self.assertEqual(4, result['package_count'])
        self.assertEqual(['R2', 'R10'], next(r for r in result['rows'] if r['device']=='RES')['references'])
        self.assertEqual(['U1:A', 'U1:B'], result['physical_parts'][-1]['units'])
        self.assertEqual('VM1', result['excluded_unassigned'][0]['ref'])
        self.assertEqual(before, state)

    def test_inconsistent_database_and_unit_properties_rejected(self):
        for change in ('database', 'value', 'package'):
            state = self.state()
            if change == 'database': state['database_consistent'] = False
            elif change == 'value': state['components'][1]['value'] = 'different'
            else: state['components'][1]['native_properties'] = '{PACKAGE=SO14}\n'
            with self.assertRaises(CircuitError): bom_rows(state)

    def test_formula_injection_is_escaped_but_original_json_remains_data(self):
        for value in ('=1+1', '+cmd', '-2+3', '@SUM(A1)', '  =HYPERLINK(1)', '\ttext'):
            self.assertEqual("'"+value, safe_csv_cell(value))
        self.assertEqual('1k', safe_csv_cell('1k'))

    def test_export_hash_and_new_output_guard(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'source.pdsprj'; path.write_bytes(b'fixture')
            state = self.state() | {'sha256': digest(b'fixture')}
            with patch('proteus_mcp.native_backend.native_read', return_value=state):
                first = export_bom(root, path, state['sha256'])
                second = export_bom(root, path, state['sha256'])
                self.assertNotEqual(first['csv_path'], second['csv_path'])
                data = Path(first['csv_path']).read_bytes()
                self.assertEqual(first['csv_sha256'], digest(data))
                rows = list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))
                self.assertEqual('Quantity', rows[0][0])
                self.assertEqual(first['rows'], json.loads(Path(first['evidence_path']).read_text())['rows'])
                with self.assertRaisesRegex(CircuitError, 'REVISION_CONFLICT'):
                    export_bom(root, path, '0'*64)
                state['sha256'] = '0'*64
                with self.assertRaisesRegex(CircuitError, 'SOURCE_CHANGED'):
                    export_bom(root, path, digest(b'fixture'))


class ChainTests(unittest.TestCase):
    def setUp(self):
        mm = lambda x: round(x*1000000)
        pad = lambda name, net, x, y: {'id': name, 'ref': name, 'net': net,
             'position_nm': [mm(x), mm(y)], 'radius_nm': {'TOP': 200000, 'BOT': 200000}}
        wire = lambda net, ps: {'net': net, 'layer': 'TOP', 'width_nm': 300000,
                                'points_nm': [[mm(x), mm(y)] for x, y in ps]}
        self.board = {'layers': ['TOP', 'BOT'], 'boundary_nm': [0, 0, 20000000, 20000000],
            'routing_blockers': [], 'placements': [], 'vias': [], 'via_styles': {},
            'nets': {n: {'layers': ['TOP', 'BOT'], 'minimum_width_nm': 300000, 'clearance_nm': 400000}
                     for n in ('NEW', 'INNER', 'OUTER')},
            'pads': [pad('A', 'NEW', 1, 9), pad('B', 'NEW', 19, 9),
                     pad('I1', 'INNER', 6, 3), pad('I2', 'INNER', 12, 3),
                     pad('O1', 'OUTER', 4, 2), pad('O2', 'OUTER', 14, 2)],
            'wires': [wire('INNER', [(6, 3), (6, 9.5), (12, 9.5), (12, 3)]),
                      wire('OUTER', [(4, 2), (4, 10.3), (14, 10.3), (14, 2)])]}
        self.spec = route_spec(self.board, 'NEW', 'TOP', 'A', 'B')
        # Above the two U stubs: only their horizontal spans are obstructive.
        self.target = [[1000000, 9000000], [1000000, 9800000], [19000000, 9800000], [19000000, 9000000]]

    def plan(self, **kwargs):
        ids = [wire_id(w) for w in self.board['wires']]
        args = {'movable_wire_ids': ids, 'managed_wire_ids': ids, 'grid_nm': 100000,
                'max_displacement_nm': 1500000}
        args.update(kwargs)
        return plan_chain(self.board, self.spec, self.target, **args)

    def test_two_trace_chain_preserves_input_anchors_and_allows_exact_replacements(self):
        before = copy.deepcopy(self.board)
        # Target above INNER and just below OUTER: both need displacement.
        result = self.plan()
        self.assertEqual(before, self.board)
        self.assertEqual(2, result['moved_trace_count'])
        self.assertEqual(2, len(result['remove_wires']))
        self.assertEqual(3, len(result['wires']))
        for item in result['displacements']:
            self.assertEqual(item['before']['points_nm'][0], item['after']['points_nm'][0])
            self.assertEqual(item['before']['points_nm'][-1], item['after']['points_nm'][-1])
        self.assertFalse(result['vendor_interactive_push_engine'])

    def test_displacement_propagates_to_non_target_blocker(self):
        # Only INNER initially obstructs the target. Its required displacement
        # encounters a narrower nested trace, which must also be displaced.
        self.board['pads'][-2]['position_nm'] = [7000000,2000000]
        self.board['pads'][-1]['position_nm'] = [11000000,2000000]
        self.board['wires'][1]['points_nm'] = [[7000000,2000000],[7000000,8700000],
                                             [11000000,8700000],[11000000,2000000]]
        result = self.plan()
        self.assertEqual(2, result['moved_trace_count'])
        self.assertTrue(all(item['offset_nm']<0 for item in result['displacements']))
        self.assertEqual({'INNER','OUTER'}, {item['after']['net'] for item in result['displacements']})

    def test_unknown_unmanaged_duplicate_and_bounds_fail_without_mutation(self):
        token = board_token(self.board)
        for values in ({'movable_wire_ids': ['unknown']}, {'managed_wire_ids': []},
                       {'movable_wire_ids': [wire_id(self.board['wires'][0])]*2},
                       {'grid_nm': True}, {'max_displacement_nm': 10}):
            with self.assertRaises(CircuitError): self.plan(**values)
            self.assertEqual(token, board_token(self.board))

    def test_unmovable_blocker_and_exhausted_search_do_not_change_board(self):
        before = copy.deepcopy(self.board)
        with self.assertRaises(CircuitError):
            self.plan(movable_wire_ids=[wire_id(self.board['wires'][0])])
        with self.assertRaises(CircuitError): self.plan(max_displacement_nm=100000)
        self.assertEqual(before, self.board)

    def test_attached_via_or_branch_is_not_detached(self):
        self.board['vias'] = [{'net': 'INNER', 'position_nm': [6000000, 7000000],
                              'style': {'radius_nm': {'TOP': 500000}}}]
        with self.assertRaisesRegex(CircuitError, 'BRANCH'):
            self.plan()


if __name__ == '__main__':
    unittest.main()
