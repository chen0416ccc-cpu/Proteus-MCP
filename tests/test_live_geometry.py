import copy
import unittest

from proteus_mcp.live import prepare_operations
from proteus_mcp.model import CircuitError


class LiveGeometryTests(unittest.TestCase):
    def state(self):
        return {'components': [{'ref': ref, 'device': 'RES', 'address': address, 'position_nm': [x, 0],
                                'rotation': 0, 'mirror': 0,
                                'pins': [{'number': '1', 'name': '1', 'xy': [x, 0], 'wire': 'w'}]}
                               for ref, address, x in (('R1', 'a', 0), ('R2', 'b', 100))],
                'wires': [{'address': 'w', 'points': [[0, 0], [100, 0]],
                           'endpoints': [{'owner': 'a', 'xy': [0, 0]}, {'owner': 'b', 'xy': [100, 0]}]}]}

    def test_connected_move_that_would_create_diagonal_is_rejected_before_write(self):
        state = self.state()
        before = copy.deepcopy(state)
        with self.assertRaisesRegex(CircuitError, 'REQUIRES_REROUTE'):
            prepare_operations(state, [{'op': 'transform', 'ref': 'R1', 'position_nm': [0, 20]}], None)
        self.assertEqual(before, state)

    def test_both_ends_can_translate_together_in_one_preflight(self):
        result = prepare_operations(self.state(), [{'op': 'transform', 'ref': 'R1', 'position_nm': [0, 20]},
                                                   {'op': 'transform', 'ref': 'R2', 'position_nm': [100, 20]}], None)
        self.assertEqual(2, len(result))

    def test_existing_bends_are_not_silently_rewritten(self):
        state = self.state()
        state['wires'][0]['points'] = [[0, 0], [0, 50], [100, 50], [100, 0]]
        with self.assertRaisesRegex(CircuitError, 'REQUIRES_REROUTE'):
            prepare_operations(state, [{'op': 'transform', 'ref': 'R1', 'position_nm': [20, 0]}], None)
        self.assertEqual([0, 50], state['wires'][0]['points'][1])


if __name__ == '__main__':
    unittest.main()
