import copy
import random
import unittest

from proteus_mcp.auto_layout import layout_candidates
from proteus_mcp.drawing_quality import drawing_report
from proteus_mcp.model import CircuitError
from proteus_mcp.routing import auto_positions, route_model
from tests.drawing_fixtures import chain, model, templates


class GeneralLayoutTests(unittest.TestCase):
    def test_seeded_non_example_networks_across_rotations_and_mirrors(self):
        for seed in range(40):
            with self.subTest(seed=seed):
                randomizer = random.Random(seed)
                devices = {f'PART{i}': 'RES' for i in range(4+seed % 5)}
                pins = [f'{ref}.{number}' for ref in devices for number in ('1', '2')]
                randomizer.shuffle(pins)
                circuit = model(devices, {f'NET{i//2}': pins[i:i+2] for i in range(0, len(pins), 2)})
                for part in circuit['components'].values():
                    part.update(rotation=randomizer.choice([0, 90, 180, 270]), mirror=randomizer.randrange(4))
                _, details = route_model(templates(), circuit)
                self.assertTrue(details['quality']['passed'])
                self.assertEqual('topology_port_footprint_candidates', details['auto_layout']['algorithm'])

    def test_topology_not_ref_grid_controls_layout(self):
        original = chain(6)
        changed = copy.deepcopy(original)
        changed['nets'] = {'STAR': [f'R{i}.1' for i in range(6)]}
        self.assertNotEqual(auto_positions(original), auto_positions(changed))
        reordered = copy.deepcopy(original)
        reordered['components'] = dict(reversed(list(original['components'].items())))
        reordered['nets'] = dict(reversed(list(original['nets'].items())))
        self.assertEqual(auto_positions(original), auto_positions(reordered))

    def test_non_example_topologies_have_continuous_checked_wires(self):
        circuits = {
            'chain': chain(),
            'branch': model({f'R{i}': 'RES' for i in range(4)},
                            {'BUS': ['R0.2', 'R1.1', 'R2.1', 'R3.1']}),
            'parallel': model({f'R{i}': 'RES' for i in range(4)},
                              {'A': [f'R{i}.1' for i in range(4)], 'B': [f'R{i}.2' for i in range(4)]}),
            'bridge': model({f'R{i}': 'RES' for i in range(5)},
                            {'A': ['R0.1', 'R1.1'], 'B': ['R0.2', 'R2.1', 'R4.1'],
                             'C': ['R1.2', 'R3.1', 'R4.2'], 'D': ['R2.2', 'R3.2']}),
            'independent': model({f'R{i}': 'RES' for i in range(4)},
                                 {'A': ['R0.2', 'R1.1'], 'B': ['R2.2', 'R3.1']}),
            'RC': model({'FILTER': 'RES', 'C1': 'CAP', 'C2': 'CAP'},
                        {'IN': ['FILTER.1', 'C1.1'], 'OUT': ['FILTER.2', 'C2.1'],
                         'GND': ['C1.2', 'C2.2']}),
        }
        for name, circuit in circuits.items():
            with self.subTest(name=name):
                before = copy.deepcopy(circuit)
                objects, details = route_model(templates(), circuit)
                self.assertEqual('half_grid_orthogonal_schematic_tree', details['algorithm'])
                self.assertTrue(details['quality']['passed'])
                self.assertTrue(drawing_report(objects, circuit, details['sheet']['sheet_size_grid'])['passed'])
                self.assertFalse(details['quality']['native_visual_verified'])
                self.assertEqual(before, circuit)

    def test_rotations_mirrors_and_arbitrary_references_survive(self):
        circuit = model({'FEEDBACK': 'RES', 'LOAD': 'RES', 'OUTPUT': 'LED-RED'},
                        {'A': ['FEEDBACK.2', 'LOAD.1'], 'B': ['LOAD.2', 'OUTPUT.1']})
        circuit['components']['LOAD'].update(rotation=90, mirror=1)
        objects, details = route_model(templates(), circuit)
        from proteus_mcp.geometry import orientation
        part = next(obj for obj in objects if obj.get('ref') == 'LOAD')
        self.assertEqual(orientation(90, 1), part['transform'])
        self.assertTrue(details['quality']['passed'])

    def test_fixed_anchors_and_explicit_model_positions_are_never_moved(self):
        circuit = chain(3)
        candidates = layout_candidates(circuit, templates(), fixed={'R1': [5, -4]})
        for candidate in candidates:
            if 'placements' in candidate:
                self.assertEqual([5, -4], candidate['placements']['R1'])
        for index, part in enumerate(circuit['components'].values()):
            part['position'] = [index*200-200, 0]
        _, details = route_model(templates(), circuit, layout='model')
        self.assertEqual({'R0': [-20, 0], 'R1': [0, 0], 'R2': [20, 0]}, details['placements'])

    def test_small_explicit_sheet_and_capacity_fail_without_label_fallback(self):
        with self.assertRaisesRegex(CircuitError, 'OUTSIDE_SHEET|CAPACITY'):
            route_model(templates(), chain(12), sheet_size=[20, 20])
        with self.assertRaisesRegex(CircuitError, '100 calibrated'):
            layout_candidates(chain(101), templates())

    def test_long_values_are_reserved_not_hidden_or_shortened(self):
        circuit = chain(3)
        value = 'precision_1000000000_ohm'
        circuit['components']['R1']['properties']['value'] = value
        objects, details = route_model(templates(), circuit)
        part = next(obj for obj in objects if obj.get('ref') == 'R1')
        from proteus_mcp.label_layout import text_style
        self.assertEqual(value, part['labels'][1]['text'])
        self.assertTrue(text_style(part['labels'][1])['visible'])
        self.assertTrue(details['quality']['passed'])


if __name__ == '__main__':
    unittest.main()
