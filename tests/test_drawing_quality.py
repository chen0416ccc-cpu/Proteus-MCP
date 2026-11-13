import copy
import unittest

from proteus_mcp.drawing_quality import drawing_report, label_box, physical_nets
from proteus_mcp.model import CircuitError
from proteus_mcp.native9 import Builder, GRID
from proteus_mcp.routing import route_model
from proteus_mcp.schematic import bounds_report
from tests.drawing_fixtures import chain, label, templates


class DrawingQualityTests(unittest.TestCase):
    def test_same_labels_do_not_hide_disconnected_signal_stubs(self):
        circuit = chain(2)
        objects, details = route_model(templates(), circuit, routing='labels')
        self.assertTrue(details['quality']['passed'])
        with self.assertRaisesRegex(CircuitError, 'DISCONNECTED_SIGNAL'):
            physical_nets(objects, circuit)
        self.assertTrue(physical_nets(objects, circuit, continuous=False))

    def test_missing_visible_pin_and_duplicate_wire_ids_fail(self):
        circuit = chain(2)
        objects, _ = route_model(templates(), circuit)
        broken = copy.deepcopy(objects)
        part = next(obj for obj in broken if obj.get('ref') == 'R0')
        part['wire_keys'][1] = None
        with self.assertRaisesRegex(CircuitError, 'MISSING_PIN_WIRE'):
            physical_nets(broken, circuit)
        wire = next(obj for obj in objects if obj['type'] == 'wire')
        with self.assertRaisesRegex(CircuitError, 'DUPLICATED'):
            physical_nets(objects+[copy.deepcopy(wire)], circuit)

    def test_endpoint_geometry_not_only_pin_pointers_is_checked(self):
        circuit = chain(2)
        objects, details = route_model(templates(), circuit)
        wire = next(obj for obj in objects if obj['type'] == 'wire')
        wire['points'][0][0] += GRID
        report = drawing_report(objects, circuit, details['sheet']['sheet_size_grid'])
        self.assertFalse(report['passed'])
        self.assertTrue(any('ENDPOINT_MISMATCH' in issue.get('reason', '') for issue in report['issues']))

    def test_unexpected_component_and_conflicting_net_identity_fail(self):
        circuit = chain(3)
        objects, _ = route_model(templates(), circuit)
        with self.assertRaisesRegex(CircuitError, 'COMPONENT_SET_MISMATCH'):
            physical_nets([obj for obj in objects if obj.get('ref') != 'R2'], circuit)
        wire = next(obj for obj in objects if obj['type'] == 'wire' and obj['labels'])
        wire['labels'][0]['text'] = 'WRONG_NET'
        with self.assertRaisesRegex(CircuitError, 'PHYSICAL_SHORT'):
            physical_nets(objects, circuit)

    def test_illegal_contact_and_wire_through_symbol_are_not_accepted(self):
        circuit = chain(3)
        objects, details = route_model(templates(), circuit)
        wire = next(obj for obj in objects if obj['type'] == 'wire')
        part = next(obj for obj in objects if obj.get('ref') == 'R2')
        x, y = part['origin']
        start, end = wire['points'][0], wire['points'][-1]
        points = [start, [start[0], y], [x-5*GRID, y], [x+5*GRID, y], [end[0], y], end]
        wire['points'] = [point for index, point in enumerate(points) if index == 0 or point != points[index-1]]
        report = drawing_report(objects, circuit, details['sheet']['sheet_size_grid'])
        self.assertFalse(report['passed'])
        self.assertTrue(any(issue['code'] == 'WIRE_BODY_CONTACT' for issue in report['issues']))

    def test_full_text_not_only_anchor_is_inside_frame(self):
        builder = Builder(templates())
        part = builder.component('R1', 'RES', [0, 0])
        part['labels'][0] = label('A_VERY_LONG_COMPONENT_REFERENCE', [45*GRID, 0])
        part['labels'][1]['text'] = ''
        self.assertFalse(bounds_report(builder.objects, [100, 70])['all_geometry_inside_frame'])
        self.assertTrue(bounds_report(builder.objects, [100, 70], include_text=False)['all_geometry_inside_frame'])
        box = label_box(part['labels'][0])
        self.assertGreater(box[2], 50*GRID)

    def test_text_rotation_mirror_and_non_ascii_are_bounded(self):
        text = label('FULL_VALUE')
        a = label_box(text)
        text['transform'] = '840300000000'
        b = label_box(text)
        self.assertEqual(a[2]-a[0], b[3]-b[1])
        text['text'] = '\u7535\u963b'*10
        self.assertGreater(label_box(text)[3]-label_box(text)[1], b[3]-b[1])


if __name__ == '__main__':
    unittest.main()
