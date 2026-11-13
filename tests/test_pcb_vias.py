import copy
import unittest

from proteus_mcp.model import CircuitError
from proteus_mcp.pcb import board_token,parse_board,route_spec,segment_clear
from proteus_mcp.pcb_vias import layered_spec,plan_layered,session_text,style_key,via_clear,via_key
from test_pcb import FIXTURE


META = {'native_style_name':'DEFAULT','native_type':'CVIA','span_code':'800000000000ffff',
        'calibrated':True,'width_nm':1000000,'height_nm':1000000,'drill_nm':400000,
        'drill_mark_nm':200000,'guard_nm':200000,'secondary_dimension_nm':0,'flags':0,'aux_flags':0}
TEXT = FIXTURE.replace('(rule (width','(via "$VIA#0_SIGNAL") (rule (width',1).replace(
    '(padstack PS ', '(padstack "$VIA#0_SIGNAL" (shape (circle TOP 1 0 0)) (shape (circle BOT 1 0 0))) (padstack PS ',1)


class ThroughViaTests(unittest.TestCase):
    def setUp(self):
        self.board = parse_board(TEXT,{'$VIA#0_SIGNAL':META})
        self.spec = layered_spec(self.board,'N','A-0','B-0')

    def test_style_requires_matching_copper_drill_and_known_span(self):
        style = self.board['via_styles']['$VIA#0_SIGNAL']
        self.assertEqual(400000,style['drill_nm'])
        self.assertEqual(500000,style['radius_nm']['BOT'])
        for field,value in (('drill_nm',0),('drill_nm',2000000),('width_nm',1200000),
                            ('span_code','80000000000000ff'),('flags',1),('native_type','STACK')):
            altered = {**META,field:value}
            board = parse_board(TEXT,{'$VIA#0_SIGNAL':altered})
            with self.assertRaisesRegex(CircuitError,'VIA_STYLE'):
                layered_spec(board,'N','A-0','B-0')

    def test_foreign_via_blocks_a_wire_on_both_layers(self):
        self.board['vias'] = [{'net':'OTHER','position_nm':[5000000,10000000],
                              'style':self.spec['via_style']}]
        for layer in ('TOP','BOT'):
            spec = route_spec(self.board,'N',layer,'A-0','B-0')
            self.assertFalse(segment_clear(self.board,spec,[4000000,10000000],[6000000,10000000]))

    def test_via_checks_full_radius_on_all_layers_and_no_via_in_pad(self):
        self.assertFalse(via_clear(self.board,self.spec,[10600000,10000000]))
        self.assertFalse(via_clear(self.board,self.spec,[2000000,10000000]))
        self.assertFalse(via_clear(self.board,self.spec,[200000,200000]))
        self.assertTrue(via_clear(self.board,self.spec,[6000000,6000000]))
        self.board['wires'] = [{'net':'OTHER','layer':'BOT','width_nm':300000,
                               'points_nm':[[4000000,6000000],[8000000,6000000]]}]
        self.assertFalse(via_clear(self.board,self.spec,[6000000,6000000]))

    def test_automatic_cross_layer_route_has_a_verified_transition(self):
        plan = plan_layered(self.board,self.spec,grid_nm=500000,max_vias=1)
        self.assertEqual(1,plan['transition_count'])
        self.assertEqual(1,len(plan['vias']))
        self.assertEqual({'TOP','BOT'},{w['layer'] for w in plan['wires']})
        self.assertGreater(plan['searched_nodes'],0)
        self.assertTrue(via_clear(self.board,self.spec,plan['vias'][0]['position_nm']))
        self.assertFalse(plan['push_shove'])
        self.assertFalse(plan['full_drc'])

    def test_layer_state_avoids_top_barrier_using_two_vias(self):
        self.board['wires']=[{'net':'OTHER','layer':'TOP','width_nm':300000,
                              'points_nm':[[10000000,0],[10000000,20000000]]}]
        spec = layered_spec(self.board,'N','A-0','B-0',end_layer='TOP')
        plan = plan_layered(self.board,spec,grid_nm=1000000,max_vias=2)
        self.assertEqual(2,plan['transition_count'])
        self.assertEqual(2,len(plan['vias']))

    def test_explicit_layer_jumps_and_endpoint_vias_fail_closed(self):
        paths = [
            [{'layer':'TOP','position_nm':self.spec['start']},{'layer':'BOT','position_nm':self.spec['end']}],
            [{'layer':'TOP','position_nm':self.spec['start']},{'layer':'BOT','position_nm':self.spec['start']},
             {'layer':'BOT','position_nm':self.spec['end']}],
        ]
        for path in paths:
            with self.assertRaises(CircuitError):
                plan_layered(self.board,self.spec,path)
        with self.assertRaisesRegex(CircuitError,'NO_CONSERVATIVE_LAYER_ROUTE'):
            plan_layered(self.board,self.spec,grid_nm=1000000,max_vias=0)

    def test_unknown_existing_via_stays_a_routing_blocker(self):
        board = parse_board(TEXT.replace('(wiring))','(wiring (via "$VIA#0_SIGNAL" 6 6 (net N))))'))
        with self.assertRaisesRegex(CircuitError,'UNCALIBRATED'):
            route_spec(board,'N','TOP','A-0','B-0')

    def test_existing_via_parse_preservation_and_exchange_aliases(self):
        text = TEXT.replace('(wiring))','(wiring (via "$VIA#0_SIGNAL" 6 6 (net N))))')
        board = parse_board(text,{'$VIA#0_SIGNAL':META})
        self.assertEqual(1,len(board['vias']))
        via = board['vias'][0]
        self.assertEqual([6000000,6000000],via['position_nm'])
        altered = copy.deepcopy(via);altered['style']['exchange_name']='PS19'
        self.assertEqual(via_key(via),via_key(altered))
        self.assertIn('(via "$VIA#0_SIGNAL" 600000 600000',session_text([],board['vias']))
        self.assertEqual(style_key(via['style']),style_key(altered['style']))
        token = board_token(board)
        board['vias']=[altered]
        self.assertEqual(token,board_token(board))
        board['vias'].append(altered)
        self.assertNotEqual(token,board_token(board))

    def test_token_captures_hole_or_style_change(self):
        token = board_token(self.board)
        self.board['via_styles']['$VIA#0_SIGNAL']['native']['drill_nm']+=10
        self.assertNotEqual(token,board_token(self.board))

    def test_session_preserves_every_existing_via_and_name_safety(self):
        via = {'net':'N','position_nm':[6000000,6000000],'style':self.spec['via_style']}
        text = session_text([], [via,via])
        self.assertEqual(2,text.count('(via '))
        via['style']={**via['style'],'exchange_name':'bad" (inject)'}
        with self.assertRaises(CircuitError):
            session_text([], [via])


if __name__ == '__main__':
    unittest.main()
