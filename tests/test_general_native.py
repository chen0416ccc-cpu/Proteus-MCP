import copy
import tempfile
import unittest
from pathlib import Path

from proteus_mcp.library import LibraryIndex
from proteus_mcp.model import Store
from proteus_mcp.native_backend import native_export, native_read, read_project
from proteus_mcp.native9 import PIN_ORDER
from tests.drawing_fixtures import chain, model


class GeneralNativeTests(unittest.TestCase):
    def export(self, circuit):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(temp.name)
        store.create('general')
        operations = []
        for ref, spec in circuit['components'].items():
            op = {'op': 'add', 'ref': ref, 'device': spec['device'], 'position': [0, 0],
                  'properties': spec['properties'], 'rotation': spec['rotation'], 'mirror': spec['mirror']}
            if spec['device'] not in PIN_ORDER:
                matches = [r for r in LibraryIndex().search(spec['device'], 200)['results'] if r['device'] == spec['device']]
                matches.sort(key=lambda item: (Path(item['library_path']).name != 'DEVICE.LIB', item['library_path']))
                op['library_path'] = matches[0]['library_path']
            operations.append(op)
        operations.extend({'op': 'connect', 'name': name, 'pins': pins} for name, pins in circuit['nets'].items())
        stored = store.edit('general', 0, operations)
        before = copy.deepcopy(stored)
        result = native_export(store, 'general')
        readback = native_read(result['path'])
        self.assertEqual(stored['nets'], readback['stored_connectivity'])
        self.assertEqual(result['routing']['sheet']['sheet_size_grid'], readback['sheet_size_grid'])
        self.assertTrue(result['routing']['quality']['passed'])
        self.assertTrue(result['routing']['file_readback_quality']['passed'])
        self.assertTrue(readback['database_consistent'])
        self.assertEqual(0, readback['nonorthogonal_wire_segments'])
        self.assertFalse(result['native_reopen_verified'])
        self.assertFalse(result['simulation_verified'])
        self.assertEqual(before, store.read('general'))
        return result, read_project(result['path'])[1]

    def test_ne555_with_feedback_timing_branch_and_load(self):
        circuit = model({'TIMER': 'NE555', 'RA': 'RES', 'RB': 'RES', 'RL': 'RES', 'CT': 'CAP', 'CV': 'CAP'},
                        {'VCC': ['TIMER.8', 'TIMER.4', 'RA.1'],
                         'GND': ['TIMER.1', 'CT.2', 'CV.2', 'RL.2'],
                         'CHARGE': ['TIMER.7', 'RA.2', 'RB.1'],
                         'TIMING': ['TIMER.2', 'TIMER.6', 'RB.2', 'CT.1'],
                         'CONTROL': ['TIMER.5', 'CV.1'], 'OUT': ['TIMER.3', 'RL.1']})
        self.export(circuit)

    def test_opamp_feedback_is_not_an_led_layout(self):
        circuit = model({'AMP': 'OPAMP', 'RIN': 'RES', 'RF': 'RES', 'RG': 'RES', 'RL': 'RES', 'CI': 'CAP'},
                        {'IN': ['RIN.1', 'CI.1'], 'POSITIVE': ['RIN.2', 'AMP.POS_IP'],
                         'FEEDBACK': ['AMP.NEG_IP', 'RF.1', 'RG.1'],
                         'OUT': ['AMP.OP', 'RF.2', 'RL.1'], 'GND': ['CI.2', 'RG.2', 'RL.2']})
        self.export(circuit)

    def test_transistor_button_driver_uses_real_library_pin_directions(self):
        circuit = model({'QDRV': 'BC547', 'SWITCH': 'BUTTON', 'RBASE': 'RES', 'RLOAD': 'RES', 'CB': 'CAP'},
                        {'VCC': ['SWITCH.1', 'RLOAD.1'], 'DRIVE': ['SWITCH.2', 'RBASE.1'],
                         'BASE': ['RBASE.2', 'QDRV.2'], 'OUT': ['RLOAD.2', 'QDRV.1', 'CB.1'],
                         'GND': ['QDRV.3', 'CB.2']})
        circuit['components']['RBASE'].update(rotation=270, mirror=1)
        self.export(circuit)

    def test_auto_sheet_growth_is_written_and_read_back(self):
        circuit = chain(12)
        circuit['nets']['RETURN'] = ['R0.1', 'R11.2']
        result, _ = self.export(circuit)
        self.assertTrue(any(a > b for a, b in zip(result['routing']['sheet']['sheet_size_grid'], [100, 70])))


if __name__ == '__main__':
    unittest.main()
