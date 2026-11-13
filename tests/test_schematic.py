import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from proteus_mcp.model import CircuitError, Store
from proteus_mcp.native9 import GRID, inspect_components, resize_sheet, sheet_dimensions
from proteus_mcp.native_backend import native_export, native_read, read_project
from tests.local_fixtures import CALIBRATION
from proteus_mcp.schematic import HORIZONTAL, VERTICAL, audit_crossings, crossing_allowed, search, STEP


class SchematicTests(unittest.TestCase):
    def test_complete_wire_contacts_are_audited_independently(self):
        horizontal = [(-1, 0), (0, 0), (1, 0)]
        self.assertEqual(1, audit_crossings({'A':[horizontal], 'B':[[(0, -1), (0, 0), (0, 1)]]}))
        for path in ([(-1, 0), (0, 0)], [(0, -1), (0, 0)],
                     [(0, -1), (0, 0), (1, 0)]):
            with self.assertRaisesRegex(CircuitError, 'ILLEGAL_CONTACT'):
                audit_crossings({'A':[horizontal], 'B':[path]})

    def test_crossings_are_only_perpendicular_straight_interiors(self):
        for mask in range(1, 16):
            for direction in range(4):
                self.assertEqual(crossing_allowed(mask, direction),
                    (mask == HORIZONTAL and direction % 2 == 1) or (mask == VERTICAL and direction % 2 == 0))

    def test_crossing_must_continue_straight(self):
        occupied = {(x, 0): HORIZONTAL for x in range(-3, 4)}
        occupied[-3, 0], occupied[3, 0] = 1, 4
        route = search((0, -2), {(2, 2)}, set(), occupied, (-5, -5, 5, 5))
        crossing = next(i for i, p in enumerate(route) if p in occupied)
        self.assertEqual(route[crossing-1][0], route[crossing][0])
        self.assertEqual(route[crossing][0], route[crossing+1][0])
        self.assertNotIn((-3, 0), route)
        self.assertNotIn((3, 0), route)

    def test_sheet_dimensions_are_checked_and_preserved(self):
        with ZipFile(CALIBRATION) as archive:
            data = archive.read('ROOT.DSN')
        self.assertEqual([100, 70], sheet_dimensions(data))
        changed = resize_sheet(data, [160, 110])
        self.assertEqual([160, 110], sheet_dimensions(changed))
        self.assertEqual(inspect_components(data)['objects'], inspect_components(changed)['objects'])
        for value in ([101, 70], [10, 10], [100.0, 70], [100, 70, 80]):
            with self.assertRaises(CircuitError):
                resize_sheet(data, value)
        invalid = bytearray(data)
        invalid[inspect_components(data)['circuit_start']+18] = 1
        with self.assertRaisesRegex(CircuitError, 'Uncalibrated'):
            resize_sheet(bytes(invalid), [100, 70])

    def test_button_half_grid_is_not_rounded_and_tree_is_continuous(self):
        with TemporaryDirectory() as temp:
            store = Store(temp)
            store.create('exact')
            model = store.edit('exact', 0, [
                {'op':'add', 'ref':'SW1', 'device':'BUTTON', 'position':[0, 0]},
                {'op':'add', 'ref':'R1', 'device':'RES', 'position':[-150, 0]},
                {'op':'connect', 'name':'SIGNAL', 'pins':['SW1.1','R1.2']},
                {'op':'connect', 'name':'GND', 'pins':['SW1.2','R1.1']}])
            result = native_export(store, 'exact', layout='model', routing='schematic')
            native = native_read(result['path'])
            _, parsed = read_project(result['path'])
            self.assertTrue(any([int(-2.5*GRID), 0] in wire['points'] for wire in parsed['wires']))
            self.assertTrue(all(v % STEP == 0 for w in parsed['wires'] for p in w['points'] for v in p))
            self.assertEqual({frozenset(v) for v in model['nets'].values()},
                             {frozenset(v) for v in native['stored_connectivity'].values()})
            self.assertEqual('continuous_wires_and_explicit_junctions', result['routing']['signal_connections'])

    def test_sheet_overflow_fails_instead_of_exporting_scattered_parts(self):
        with TemporaryDirectory() as temp:
            store = Store(temp)
            store.create('outside')
            store.edit('outside', 0, [{'op':'add','ref':'R1','device':'RES','position':[600,0]},
                {'op':'connect','name':'VCC','pins':['R1.1','R1.2']}])
            with self.assertRaisesRegex(CircuitError, 'OUTSIDE_SHEET'):
                native_export(store, 'outside', layout='model', routing='schematic')


if __name__ == '__main__':
    unittest.main()
