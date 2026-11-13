import copy
import unittest

from proteus_mcp.model import CircuitError
from proteus_mcp.pcb import board_token, parse_board, plan_route, route_spec
from proteus_mcp.pcb_push import displace, plan_push, simple_path, wire_id
from test_pcb import FIXTURE


class BoundedPushTests(unittest.TestCase):
    def setUp(self):
        self.board = parse_board(FIXTURE.replace('(place C 10 10 front 90)','(place C 10 3 front 90)'))
        self.board['nets']['OTHER']['pins'] = ['C-0','D-0']
        self.board['pads'].append({**self.board['pads'][2], 'id':'D-0','ref':'D','position_nm':[16000000,3000000]})
        self.old = {'net':'OTHER','layer':'TOP','width_nm':300000,
                    'points_nm':[[10000000,3000000],[10000000,9000000],[16000000,9000000],[16000000,3000000]]}
        self.board['wires'] = [self.old]
        self.spec = route_spec(self.board,'N','TOP','A-0','B-0')
        self.target = [[2000000,10000000],[18000000,10000000]]

    def plan(self, **kwargs):
        args = {'points_nm':self.target,'movable_wire_ids':[wire_id(self.old)],
                'managed_wire_ids':[wire_id(self.old)],'grid_nm':100000,'max_displacement_nm':2000000}
        args.update(kwargs)
        return plan_push(self.board,self.spec,**args)

    def test_push_is_a_local_displacement_not_replacing_the_target_path(self):
        self.old['points_nm'][1][1] = self.old['points_nm'][2][1] = 9600000
        before = copy.deepcopy(self.board)
        with self.assertRaises(CircuitError):
            plan_route(self.board,self.spec,self.target)
        plan = self.plan()
        self.assertEqual(before,self.board)
        self.assertEqual(self.target,plan['wires'][1]['points_nm'])
        changed = plan['wires'][0]
        self.assertEqual(self.old['points_nm'][0],changed['points_nm'][0])
        self.assertEqual(self.old['points_nm'][-1],changed['points_nm'][-1])
        self.assertEqual(4,len(changed['points_nm']))
        self.assertEqual(300000,plan['maximum_actual_displacement_nm'])
        self.assertEqual(-300000,plan['displacements'][0]['offset_nm'])
        self.assertEqual([self.old],plan['remove_wires'])
        self.assertFalse(plan['vendor_interactive_push_engine'])

    def test_unknown_unmanaged_duplicate_and_same_net_traces_rejected(self):
        for change in ({'movable_wire_ids':['unknown']},{'managed_wire_ids':[]},
                       {'movable_wire_ids':[wire_id(self.old)]*2}):
            with self.assertRaises(CircuitError):
                self.plan(**change)
        self.board['wires'].append(copy.deepcopy(self.old))
        with self.assertRaisesRegex(CircuitError,'duplicate'):
            self.plan()

    def test_branches_and_same_net_vias_are_not_detached(self):
        self.old['points_nm'][1][1] = self.old['points_nm'][2][1] = 9600000
        for mode in ('pad','wire','via'):
            original = copy.deepcopy(self.board)
            if mode == 'pad':
                self.board['pads'].append({**self.board['pads'][2], 'id':'E-0','position_nm':[13000000,9600000]})
            if mode == 'wire':
                self.board['wires'].append({**self.old,'points_nm':[[13000000,9600000],[13000000,8000000]]})
            if mode == 'via':
                self.board['vias']=[{'net':'OTHER','position_nm':[13000000,9600000],
                                     'style':{'radius_nm':{'TOP':500000}}}]
            unchanged = copy.deepcopy(self.board)
            with self.assertRaises(CircuitError):
                self.plan()
            self.assertEqual(unchanged,self.board)
            self.board = original
            self.old = self.board['wires'][0]

    def test_fixed_obstacle_or_exhausted_bound_does_not_change_board(self):
        self.old['points_nm'][1][1] = self.old['points_nm'][2][1] = 9600000
        token = board_token(self.board)
        with self.assertRaisesRegex(CircuitError,'NO_BOUNDED_PUSH'):
            self.plan(max_displacement_nm=100000)
        self.assertEqual(token,board_token(self.board))
        self.board['pads'].append({**self.board['pads'][2],'id':'E-0','net':'BLOCK',
                                   'position_nm':[13000000,10000000]})
        with self.assertRaises(CircuitError):
            self.plan()

    def test_input_bounds_noop_and_trace_complexity(self):
        for change in ({'grid_nm':True},{'grid_nm':10001},{'max_displacement_nm':6000000},
                       {'max_displacement_nm':10},{'movable_wire_ids':[]}):
            with self.assertRaises(CircuitError):
                self.plan(**change)
        with self.assertRaisesRegex(CircuitError,'SINGLE_BLOCKER'):
            self.plan()

    def test_self_intersection_and_stub_reversal(self):
        self.assertFalse(simple_path([[0,0],[10,0],[10,10],[5,10],[5,-1]]))
        self.assertFalse(simple_path([[0,0],[10,0],[5,0]]))
        self.assertIsNone(displace(self.old,1,-6000000))
        self.assertIsNone(displace({**self.old,'points_nm':[[0,0],[1,1],[2,2],[3,0]]},1,10))

    def test_protected_or_unknown_wire_attributes_block_all_route_writes(self):
        for attribute in ('(type protect)','(type fixed)','(shield true)'):
            text=FIXTURE.replace('(wiring))',f'(wiring (wire (path TOP 0.3 2 10 18 10) (net N) {attribute})))')
            board=parse_board(text)
            with self.assertRaisesRegex(CircuitError,'UNCALIBRATED'):
                route_spec(board,'N','TOP','A-0','B-0')


if __name__ == '__main__':
    unittest.main()
