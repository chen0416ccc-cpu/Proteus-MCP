import copy
import tempfile
import unittest
from pathlib import Path

from proteus_mcp.bridge import upgrade_confirmation_allowed
from proteus_mcp.model import CircuitError
from proteus_mcp.pcb import (board_token, expressions, nm, parse_board, plan_route,
                             route_spec, segment_distance, session_text, wire_key)

FIXTURE = '''(PCB test (unit mm) (resolution mm 100000)
 (structure (boundary (path signal 0.2 0 0 20 0 20 20 0 20 0 0))
  (layer TOP (type signal) (direction horizontal))
  (layer BOT (type signal) (direction vertical))
  (layer I1 (type signal) (direction off))
  (rule (width 0.3) (clearance 0.4)))
 (placement (component P_A (place A 2 10 front 0))
  (component P_B (place B 18 10 front 0))
  (component P_C (place C 10 10 front 90)))
 (library (image P_A (pin PS (rotate 0) 0 0 0))
  (image P_B (pin PS (rotate 0) 0 0 0))
  (image P_C (pin PS (rotate 0) 0 0 0))
  (padstack PS (shape (circle TOP 2 0 0)) (shape (circle BOT 2 0 0))))
 (network (net "N" (circuit (use_layer TOP BOT)) (pins A-0 B-0))
  (net "OTHER" (circuit (use_layer TOP BOT)) (pins C-0)))
 (wiring))'''


class PCBTests(unittest.TestCase):
    def setUp(self):
        self.board = parse_board(FIXTURE)
        self.spec = route_spec(self.board,'N','TOP','A-0','B-0')

    def test_native_units_and_pad_indices(self):
        self.assertEqual(304800,nm('0.30480'))
        self.assertEqual(3,len(self.board['pads']))
        self.assertEqual([2000000,10000000],self.spec['start'])
        self.assertEqual([0,0,20000000,20000000],self.board['boundary_nm'])

    def test_exact_nm_precision_and_finite_values(self):
        for value in ('nan','Infinity','0.00000001','1e20'):
            with self.assertRaises(CircuitError):
                nm(value)

    def test_parser_bounds_and_quotes(self):
        self.assertEqual(['a','x y'],expressions('(a "x y")'))
        for value in ('(a','(a))','"bad','('*81+'x'+')'*81):
            with self.assertRaises(CircuitError):
                expressions(value)

    def test_automatic_route_avoids_foreign_pad(self):
        route = plan_route(self.board,self.spec,grid_nm=200000)
        self.assertGreater(route['searched_nodes'],0)
        self.assertGreater(len(route['points_nm']),2)
        self.assertEqual(self.spec['start'],route['points_nm'][0])
        self.assertEqual(self.spec['end'],route['points_nm'][-1])

    def test_explicit_collision_and_boundary_rejected(self):
        paths = [[self.spec['start'],self.spec['end']],
                 [self.spec['start'],[0,10000000],[0,18000000],[18000000,18000000],self.spec['end']]]
        for points in paths:
            with self.assertRaises(CircuitError):
                plan_route(self.board,self.spec,points)

    def test_wrong_net_layer_and_physical_pin_guess_rejected(self):
        for net,layer,start in (('N','I1','A-0'),('OTHER','TOP','A-0'),('N','TOP','A-1')):
            with self.assertRaises(CircuitError):
                route_spec(self.board,net,layer,start,'B-0')

    def test_rules_and_10nm_alignment(self):
        for width in (True,300001,10000):
            with self.assertRaises(CircuitError):
                route_spec(self.board,'N','TOP','A-0','B-0',width_nm=width)
        with self.assertRaises(CircuitError):
            route_spec(self.board,'N','TOP','A-0','B-0',clearance_nm=10000)

    def test_session_coordinates_are_10nm_not_nm(self):
        route = {'net':'N','layer':'TOP','width_nm':304800,'points_nm':[[1000000,2000000],[3000000,4000000]]}
        text = session_text(route)
        self.assertIn('TOP 30480 100000 200000 300000 400000',text)
        self.assertIn('(resolution mm 100000)',text)
        route['net'] = 'bad" (inject)'
        with self.assertRaises(CircuitError):
            session_text(route)

    def test_session_preserves_existing_nets_on_other_layers(self):
        old = {'net':'OLD','layer':'TOP','width_nm':304800,'points_nm':[[1000000,2000000],[3000000,4000000]]}
        new = {**old,'net':'NEW','layer':'BOT'}
        text = session_text(new,[old])
        self.assertIn('(net "OLD" (wire (path TOP',text)
        self.assertIn('(net "NEW" (wire (path BOT',text)
        self.assertEqual(2,text.count('(wire '))

    def test_existing_foreign_copper_is_an_obstacle(self):
        self.board['wires'] = [{'net':'OTHER','layer':'TOP','width_nm':300000,'points_nm':[[10000000,0],[10000000,20000000]]}]
        with self.assertRaisesRegex(CircuitError,'NO_CONSERVATIVE_ROUTE'):
            plan_route(self.board,self.spec,grid_nm=1000000)

    def test_unsupported_copper_fails_closed(self):
        board = parse_board(FIXTURE.replace('(wiring))','(wiring (via PS 1 1 (net N))))'))
        with self.assertRaisesRegex(CircuitError,'UNCALIBRATED'):
            route_spec(board,'N','TOP','A-0','B-0')

    def test_tokens_and_wire_canonicalization(self):
        before = board_token(self.board)
        self.board['nets']['N']['clearance_nm'] += 10
        self.assertNotEqual(before,board_token(self.board))
        a = {'net':'N','layer':'TOP','width_nm':300000,'points_nm':[[0,0],[1,0],[2,0]]}
        b = {**a,'points_nm':[[2,0],[0,0]]}
        self.assertEqual(wire_key(a),wire_key(b))
        self.board['wires'] = [a]
        before = board_token(self.board)
        self.board['wires'] = [b]
        self.assertEqual(before,board_token(self.board))

    def test_crossing_and_collinear_segment_distances(self):
        self.assertEqual(0,segment_distance([0,0],[10,10],[0,10],[10,0]))
        self.assertEqual(5,segment_distance([0,0],[10,0],[15,0],[20,0]))


class UpgradeDialogTests(unittest.TestCase):
    def test_only_exact_known_upgrade_in_workspace_is_confirmed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'pcb-test.pdsprj'
            modal = {'class':'Appln::MessageBox','title':'Proteus 9 Professional','standard_buttons':0x400400,
                     'informative_text':'','text':"文件将以 9.0\0 版本格式保存。\n此格式无法被旧版 Proteus 加载。\n\n先前版本将另存为 'pcb-test [Proteus 8.13].pdsprj'。"}
            self.assertTrue(upgrade_confirmation_allowed(modal,path,folder))
            self.assertFalse(upgrade_confirmation_allowed(modal,Path(folder).parent/'pcb-test.pdsprj',folder))
            for key,value in (('text',modal['text']+'额外问题'),('standard_buttons',0x400),('class','QMessageBox'),('informative_text','Unknown question')):
                changed = copy.deepcopy(modal); changed[key] = value
                self.assertFalse(upgrade_confirmation_allowed(changed,path,folder))


if __name__ == '__main__':
    unittest.main()
