import copy
import math
import tempfile
import unittest
from pathlib import Path

from proteus_mcp.label_layout import (apply_label, apply_component_labels, text_style, layout_snapshot,
                                     wire_label_mismatches, terminal_label_mismatches)
from proteus_mcp.model import CircuitError, Store
from proteus_mcp.native9 import GRID, Reader, pack_text
from proteus_mcp.native_backend import read_project, native_export, native_read
from tests.local_fixtures import CALIBRATION
from proteus_mcp.preview import native_preview


class LabelLayoutTests(unittest.TestCase):
    def setUp(self):
        self.label = copy.deepcopy(read_project(CALIBRATION)[1]['components'][0]['labels'][0])

    def test_visibility_and_sizes_override_inheritance_without_renaming(self):
        original = copy.deepcopy(self.label)
        apply_label(self.label, {'at': [2.5, -3], 'height': 1.2, 'width': .65,
                                 'rotation': 90, 'visible': False})
        style = text_style(self.label)
        self.assertEqual(original['text'], self.label['text'])
        self.assertEqual([5 * GRID // 2, -3 * GRID], self.label['xy'])
        self.assertEqual(round(1.2 * GRID), style['height'])
        self.assertEqual(round(.65 * GRID), style['width'])
        self.assertEqual(90, style['rotation'])
        self.assertFalse(style['visible'])
        self.assertFalse(style['flags'] & ((1 << 17) | (1 << 18) | (1 << 24)))
        mask = ((1 << 17) | (1 << 18) | (1 << 24) | 1)
        self.assertEqual(text_style(original)['flags'] & ~mask, style['flags'] & ~mask)
        self.assertEqual(original['style'][32:], self.label['style'][32:])
        reread = Reader(pack_text(self.label)).text()
        self.assertEqual(style, text_style(reread))

    def test_position_only_preserves_legacy_style_and_transform(self):
        original = copy.deepcopy(self.label)
        apply_label(self.label, [-2, 3])
        self.assertEqual(original['style'], self.label['style'])
        self.assertEqual(original['transform'], self.label['transform'])
        self.assertEqual([-2 * GRID, 3 * GRID], self.label['xy'])

    def test_invalid_layout_fails_without_partial_label_changes(self):
        for spec in ({'text': 'renamed'}, {'visible': 0}, {'height': 0}, {'width': 5},
                     {'height': math.nan}, {'rotation': True}, {'rotation': 45},
                     {'at': [.25, 0]}, {'height': 1, 'rotation': 45}):
            with self.subTest(spec=spec):
                original = copy.deepcopy(self.label)
                with self.assertRaises(CircuitError):
                    apply_label(self.label, spec)
                self.assertEqual(original, self.label)

    def test_component_fields_cannot_edit_properties_or_unknown_references(self):
        parts = {'R1': {'labels': [self.label, copy.deepcopy(self.label)]}}
        for spec in ({'R2': {'ref': {}}}, {'R1': {'properties': {}}}, []):
            with self.assertRaises(CircuitError):
                apply_component_labels(parts, spec)

    def test_native_label_comparison_checks_visibility_size_rotation_and_position(self):
        apply_label(self.label, {'at': [2, 3], 'height': 1, 'width': .6, 'visible': False, 'rotation': 270})
        objects = [{'type': 'wire', 'labels': [self.label]}]
        style = text_style(self.label)
        geometry = {'position_nm': self.label['xy'], 'height': style['height'], 'width': style['width'],
                    'angle_tenths': 2700, 'mirror': style['mirror'],
                    'hjust': style['justification'] & 3, 'vjust': style['justification'] & 12, 'hidden': True}
        state = {'wires': [{'labels': [{'text': self.label['text'], 'geometry': geometry}]}]}
        self.assertFalse(wire_label_mismatches(state, objects))
        for key, value in (('hidden', False), ('height', GRID * 2), ('width', GRID),
                           ('angle_tenths', 0), ('position_nm', [0, 0])):
            bad = copy.deepcopy(state)
            bad['wires'][0]['labels'][0]['geometry'][key] = value
            self.assertTrue(wire_label_mismatches(bad, objects))
        self.assertTrue(wire_label_mismatches({'wires': []}, objects))

    def test_example_preserves_readable_refs_and_semantic_names_for_hidden_intermediates(self):
        from proteus_mcp.interrupts import external_interrupt
        from proteus_mcp.orderly import route_plan
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            example = external_interrupt(store, 'interrupt_labels')
            model = store.read('interrupt_labels')
            objects, _ = route_plan(read_project(CALIBRATION)[1]['objects'], model, None,
                                    example['placements'], 'model', example['sheet_size'], example['wire_plan'])
            layout = layout_snapshot(objects)
            for i, letter in enumerate('ABCDEFGH'):
                self.assertFalse(next(l for l in layout['wires'] if l['text'] == letter + '_OUT')['visible'])
                self.assertFalse(next(l for l in layout['wires'] if l['text'] == 'SEG_' + letter)['visible'])
                for ref in ('RS' + str(i), 'PU' + str(i)):
                    self.assertTrue(all(l['visible'] for l in layout['components'][ref]))
                    self.assertTrue(all(l['rotation'] == 90 for l in layout['components'][ref]))
                    self.assertEqual(round(.65 * GRID), layout['components'][ref][0]['width'])
                x = 12 + 3 * i
                terminal = next(t for t in layout['terminals'] if t['origin_nm'] == [(x + 1) * GRID, 16 * GRID])
                self.assertEqual([round((x + 1.5) * GRID), 14 * GRID], terminal['label']['position_nm'])
                self.assertEqual(90, terminal['label']['rotation'])
            for i in range(4):
                self.assertFalse(next(l for l in layout['wires'] if l['text'] == 'DIG_' + str(i + 1))['visible'])
                x = -12 + 6 * i
                self.assertEqual([round((x + 1.5) * GRID), -35 * GRID],
                                 layout['components']['RD' + str(i)][0]['position_nm'])
                terminal = next(t for t in layout['terminals'] if t['origin_nm'] == [x * GRID, -40 * GRID])
                self.assertEqual([(x - 1) * GRID, -42 * GRID], terminal['label']['position_nm'])
                self.assertEqual('VCC', terminal['net'])
            for ref in ('SW1', 'SW2'):
                self.assertTrue(layout['components'][ref][0]['visible'])
                self.assertFalse(layout['components'][ref][1]['visible'])
            plain_plan = copy.deepcopy(example['wire_plan'])
            plain_plan.pop('labels')
            plain_plan.pop('component_labels')
            for terminal in plain_plan['terminals'].values():
                terminal.pop('label', None)
            original, _ = route_plan(read_project(CALIBRATION)[1]['objects'], model, None,
                                     example['placements'], 'model', example['sheet_size'], plain_plan)
            def without_text(items):
                result = copy.deepcopy(items)
                for obj in result:
                    obj.pop('label', None)
                    obj.pop('labels', None)
                return result
            self.assertEqual(without_text(original), without_text(objects))
            self.assertEqual(set(model['nets']) - {'VCC', 'GND'}, {l['text'] for l in layout['wires']})
            self.assertEqual(model, store.read('interrupt_labels'))

    def test_terminal_comparison_matches_origin_and_rejects_missing_duplicate_or_swapped_labels(self):
        labels = [copy.deepcopy(self.label), copy.deepcopy(self.label)]
        for i, label in enumerate(labels):
            label['text'] = 'VCC'
            apply_label(label, {'at': [i * 6, -42], 'height': 1, 'width': .6, 'visible': not i})
        objects = [{'type': 'terminal', 'origin': [i * 6 * GRID, -40 * GRID], 'label': label}
                   for i, label in enumerate(labels)]
        terminals = []
        for obj in objects:
            label = obj['label']; style = text_style(label)
            terminals.append({'type': '.?AVTERMINAL@@', 'origin': obj['origin'], 'net': 'VCC',
                              'label': {'text': 'VCC', 'geometry': {
                                  'position_nm': label['xy'], 'height': style['height'], 'width': style['width'],
                                  'angle_tenths': round(style['rotation'] * 10), 'mirror': style['mirror'],
                                  'hjust': style['justification'] & 3, 'vjust': style['justification'] & 12,
                                  'hidden': not style['visible']}}})
        state = {'objects': terminals[::-1]}
        self.assertFalse(terminal_label_mismatches(state, objects))
        self.assertTrue(terminal_label_mismatches({'objects': terminals[:1]}, objects))
        self.assertTrue(terminal_label_mismatches({'objects': [terminals[0], terminals[0]]}, objects))
        bad = copy.deepcopy(state)
        bad['objects'][0]['label'], bad['objects'][1]['label'] = bad['objects'][1]['label'], bad['objects'][0]['label']
        self.assertTrue(terminal_label_mismatches(bad, objects))
        bad = copy.deepcopy(state)
        bad['objects'][0]['net'] = 'GND'
        self.assertTrue(terminal_label_mismatches(bad, objects))
        changed = copy.deepcopy(objects)
        changed[0]['label']['xy'][0] += GRID
        self.assertNotEqual(layout_snapshot(objects), layout_snapshot(changed))

    def test_terminal_layout_cannot_rename_supplies_or_accept_unknown_fields(self):
        from proteus_mcp.interrupts import external_interrupt
        from proteus_mcp.orderly import route_plan
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            example = external_interrupt(store, 'invalid_terminal_layout')
            model = store.read('invalid_terminal_layout')
            for spec in ({'net': 'VCC', 'at': [0, 0], 'label': {'text': 'GND'}},
                         {'net': 'VCC', 'at': [0, 0], 'visible': False},
                         {'net': 'VCC'}, {'net': 'RENAMED', 'at': [0, 0]}, []):
                with self.subTest(spec=spec):
                    plan = copy.deepcopy(example['wire_plan'])
                    plan['terminals']['@RL1_2'] = spec
                    with self.assertRaises(CircuitError):
                        route_plan(read_project(CALIBRATION)[1]['objects'], model, None,
                                   example['placements'], 'model', example['sheet_size'], plan)

    def test_export_and_preview_keep_hidden_net_semantics_and_new_text_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            store.create('labels')
            store.edit('labels', 0, [
                {'op': 'add', 'ref': 'R1', 'device': 'RES', 'position': [0, 0]},
                {'op': 'add', 'ref': 'R2', 'device': 'RES', 'position': [150, 0]},
                {'op': 'connect', 'name': 'HIDDEN_NET', 'pins': ['R1.2', 'R2.1']},
                {'op': 'connect', 'name': 'VCC', 'pins': ['R1.1', 'R2.2']}])
            plan = {'terminals': {'@a': {'net': 'VCC', 'at': [-4, 0],
                                        'label': {'at': [-7, 5], 'height': 1, 'width': .6, 'rotation': 270}},
                                  '@b': {'net': 'VCC', 'at': [20, 0], 'label': {'visible': False}}},
                    'wires': [
                        {'net': 'HIDDEN_NET', 'ends': ['R1.2', 'R2.1'], 'points': [[3, 0], [13, 0]]},
                        {'net': 'VCC', 'ends': ['R1.1', '@a'], 'points': [[-2, 0], [-4, 0]]},
                        {'net': 'VCC', 'ends': ['R2.2', '@b'], 'points': [[18, 0], [20, 0]]}],
                    'labels': {'HIDDEN_NET': {'visible': False}},
                    'component_labels': {'R1': {'ref': {'at': [0, 3], 'rotation': 90, 'height': 1},
                                                'value': {'visible': False}}}}
            result = native_export(store, 'labels', layout='model', routing='schematic', wire_plan=plan)
            self.assertEqual(['R1.2', 'R2.1'], native_read(result['path'])['stored_connectivity']['HIDDEN_NET'])
            self.assertEqual(['R1.1', 'R2.2'], native_read(result['path'])['stored_connectivity']['VCC'])
            parsed = read_project(result['path'])[1]
            label = next(l for w in parsed['wires'] for l in w['labels'] if l['text'] == 'HIDDEN_NET')
            self.assertFalse(text_style(label)['visible'])
            preview = native_preview(result['path'])
            svg = Path(preview['path']).read_text(encoding='utf-8')
            self.assertNotIn('>HIDDEN_NET</text>', svg)
            self.assertEqual(1, svg.count('>VCC</text>'))
            self.assertIn('x="-84.0" y="-60.0" class="net"', svg)
            self.assertIn('rotate(-270.0 -84.0 -60.0)', svg)
            self.assertIn('rotate(-90.0 ', svg)
            self.assertIn('font-size:12.0px', svg)
            bad = copy.deepcopy(plan)
            bad['terminals']['@a']['label']['at'] = [-6, 4]
            with self.assertRaisesRegex(CircuitError, 'LABEL_SPACE_EXHAUSTED'):
                native_export(store, 'labels', layout='model', routing='schematic', wire_plan=bad)


if __name__ == '__main__':
    unittest.main()
