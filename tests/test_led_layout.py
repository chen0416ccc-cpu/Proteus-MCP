import tempfile
import unittest
from unittest.mock import patch

from proteus_mcp.examples import led_chaser
from proteus_mcp.led_layout import PLACEMENTS, ROTATIONS, SHEET_SIZE, matches, plan
from proteus_mcp.model import CircuitError, Store
from proteus_mcp.orderly import dense, grid_point
from proteus_mcp.routing import auto_positions, route_model
from proteus_mcp.schematic import audit_crossings


class LedLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(temporary.name)
        self.model = led_chaser(self.store, 'demo')

    def test_semantic_and_native_layout_share_compact_positions(self):
        self.assertTrue(matches(self.model))
        self.assertEqual(PLACEMENTS, auto_positions(self.model))
        for ref, component in self.model['components'].items():
            x, y = PLACEMENTS[ref]
            self.assertEqual([10*x, -10*y], component['position'])
            self.assertEqual(ROTATIONS.get(ref, 0), component['rotation'])

    def test_each_led_channel_is_full_length_and_channels_do_not_cross(self):
        spec = plan(self.model)
        paths = {}
        for wire in spec['wires']:
            if wire['net'].startswith('LED'):
                paths.setdefault(wire['net'], []).append(dense([grid_point(p) for p in wire['points']]))
                self.assertFalse(any(endpoint.startswith('@') for endpoint in wire['ends']))
        self.assertEqual(16, len(paths))
        self.assertEqual(0, audit_crossings(paths))
        self.assertEqual(40, len(spec['wires']))
        self.assertTrue(all(-SHEET_SIZE[0]/2 < p[0] < SHEET_SIZE[0]/2 and
                            -SHEET_SIZE[1]/2 < p[1] < SHEET_SIZE[1]/2
                            for wire in spec['wires'] for p in wire['points']))

    def test_default_does_not_fall_back_to_named_net_stubs(self):
        other = {'components': {}, 'nets': {}}
        with patch('proteus_mcp.schematic.route_schematic', side_effect=CircuitError('NO_SCHEMATIC_ROUTE')):
            with patch('proteus_mcp.routing.route_labels') as labels:
                with self.assertRaisesRegex(CircuitError, 'NO_SCHEMATIC_ROUTE'):
                    route_model([], other)
                labels.assert_not_called()

    def test_explicit_orientation_change_is_not_overridden_by_profile(self):
        self.model['components']['D1']['rotation'] = 180
        self.assertFalse(matches(self.model))


if __name__ == '__main__':
    unittest.main()
