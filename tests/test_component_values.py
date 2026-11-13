import unittest

from proteus_mcp.native9 import Builder
from proteus_mcp.native_backend import property_map, set_properties
from proteus_mcp.model import CircuitError
from tests.drawing_fixtures import templates


class ComponentValueTests(unittest.TestCase):
    def test_builder_updates_library_default_value_without_mutating_the_template(self):
        source = templates()
        resistor = next(p for p in source if p.get('device') == 'RES')
        resistor['labels'][3]['text'] = '{VALUE=10k}\n{PACKAGE=RES40}\n'
        builder = Builder(source)
        for ref, value in (('RA', '100k'), ('RB', '1k')):
            part = builder.component(ref, 'RES', [0, 0], value=value)
            self.assertEqual(value, part['labels'][1]['text'])
            self.assertEqual(value, property_map(part['labels'][3]['text'])['VALUE'])
        self.assertIn('{VALUE=10k}', resistor['labels'][3]['text'])

    def test_set_properties_updates_braced_or_unbraced_value_and_keeps_other_fields(self):
        for stored in ('{VALUE=1nF}', 'VALUE=1nF'):
            part = Builder(templates()).component('C1', 'CAP', [0, 0])
            part['labels'][3]['text'] = stored+'\n{PACKAGE=CAP10}\n'
            set_properties(part, {'value': '10uF'})
            self.assertEqual('10uF', part['labels'][1]['text'])
            self.assertEqual({'VALUE': '10uF', 'PACKAGE': 'CAP10'}, property_map(part['labels'][3]['text']))

    def test_no_value_assignment_is_invented_for_devices_without_one(self):
        part = Builder(templates()).component('D1', 'LED-RED', [0, 0])
        part['labels'][3]['text'] = '{PACKAGE=LED}\n'
        set_properties(part, {'value': 'LED-RED'})
        self.assertEqual({'PACKAGE': 'LED'}, property_map(part['labels'][3]['text']))

    def test_crystal_frequency_still_sets_the_simulation_property(self):
        part = Builder(templates()).component('X1', 'CRYSTAL', [0, 0])
        set_properties(part, {'value': '11.0592MHz'})
        self.assertEqual('11.0592MHz', property_map(part['labels'][3]['text'])['FREQ'])

    def test_value_cannot_inject_an_extra_native_assignment(self):
        for value in ('1k}\n{PROGRAM=other}', '1k\nPROGRAM=other'):
            part = Builder(templates()).component('R1', 'RES', [0, 0])
            part['labels'][3]['text'] = '{VALUE=10k}\n'
            with self.assertRaises(CircuitError):
                set_properties(part, {'value': value})
