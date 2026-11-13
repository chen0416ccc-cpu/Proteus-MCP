import tempfile
import unittest
from pathlib import Path

from proteus_mcp.examples import led_chaser
from proteus_mcp.model import CircuitError, Store
from proteus_mcp.native_backend import CALIBRATION, native_export, native_read
from proteus_mcp.native9 import inspect_components, relocate_dsn
from proteus_mcp.routing import route_model


class NativeRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.create("test")

    def resistor_tree(self):
        return self.store.edit("test", 0, [
            {"op": "add", "ref": "R11", "device": "RES", "position": [0, 0]},
            {"op": "add", "ref": "R22", "device": "RES", "position": [200, -100]},
            {"op": "add", "ref": "R33", "device": "RES", "position": [200, 100]},
            {"op": "connect", "name": "VCC", "pins": ["R11.1", "R22.2", "R33.2"]},
            {"op": "connect", "name": "SIGNAL", "pins": ["R11.2", "R22.1", "R33.1"]}])

    def test_arbitrary_topology_orthogonal_tree_with_real_junction(self):
        model = self.resistor_tree()
        result = native_export(self.store, "test", layout="model", routing="wires")
        native = native_read(result["path"])
        self.assertEqual("orthogonal_Astar_tree", result["routing"]["algorithm"])
        self.assertEqual(1, native["junctions"])
        self.assertEqual({frozenset(p) for p in model["nets"].values()},
                         {frozenset(p) for p in native["stored_connectivity"].values()})
        self.assertTrue(native["database_consistent"])
        self.assertEqual({"R11", "R22", "R33"}, {p["ref"] for p in native["components"]})

    def test_named_net_export_retains_exact_topology(self):
        model = self.resistor_tree()
        result = native_export(self.store, "test", layout="model", routing="labels")
        native = native_read(result["path"])
        self.assertEqual({name: sorted(p) for name, p in model["nets"].items()}, native["stored_connectivity"])

    def test_overlapping_parts_and_unmapped_devices_fail(self):
        self.resistor_tree()
        with self.assertRaisesRegex(CircuitError, "COLLISION"):
            native_export(self.store, "test", placements={"R11": [0, 0], "R22": [0, 0]})
        with self.assertRaisesRegex(CircuitError, "unknown references"):
            native_export(self.store, "test", placements={"NO": [0, 0]})

    def test_default_full_model_has_continuous_wires_inside_sheet(self):
        import zipfile
        model = led_chaser(self.store, "full")
        with zipfile.ZipFile(CALIBRATION) as archive:
            data = archive.read("ROOT.DSN")
        objects, routing = route_model(inspect_components(data)["objects"], model)
        generated = inspect_components(relocate_dsn(data, objects))
        from proteus_mcp.native_backend import connectivity
        self.assertEqual("half_grid_orthogonal_schematic_tree", routing["algorithm"])
        self.assertEqual('topology_port_footprint_candidates', routing['auto_layout']['algorithm'])
        self.assertTrue(routing['quality']['passed'])
        self.assertNotIn("fallback_reason", routing)
        self.assertTrue(routing['sheet']['all_geometry_inside_frame'])
        self.assertEqual('continuous_wires_and_explicit_junctions', routing['signal_connections'])
        self.assertEqual(0, sum(1 for w in generated['wires'] for a,b in zip(w['points'],w['points'][1:])
                                if a[0]!=b[0] and a[1]!=b[1]))
        self.assertEqual({frozenset(p) for p in model["nets"].values()},
                         {frozenset(p) for p in connectivity(generated).values()})

    def test_default_label_hiding_survives_native_export(self):
        from proteus_mcp.model import atomic_write, digest, json_bytes
        from proteus_mcp.firmware import inspect_hex
        from proteus_mcp.native_backend import read_project
        from proteus_mcp.label_layout import text_style
        led_chaser(self.store, 'led')
        folder = self.store.folder('led')
        firmware = folder/'fixture.hex'
        atomic_write(firmware, b':03000000020000FB\n:00000001FF\n')
        atomic_write(folder/'latest-build.json', json_bytes({'status':'compiled', 'hex_path':str(firmware),
            'hex':inspect_hex(firmware), 'source_sha256':digest((folder/'main.c').read_bytes())}))
        result = native_export(self.store, 'led')
        parsed = read_project(result['path'])[1]
        self.assertEqual(result['routing']['sheet']['sheet_size_grid'], native_read(result['path'])['sheet_size_grid'])
        self.assertTrue(result['routing']['file_readback_quality']['passed'])
        self.assertTrue(result['routing']['sheet']['all_geometry_inside_frame'])
        self.assertFalse(any(text_style(label)['visible'] for wire in parsed['wires'] for label in wire['labels']))

    def test_explicit_label_mode_also_rejects_sheet_overflow(self):
        self.resistor_tree()
        with self.assertRaisesRegex(CircuitError, 'OUTSIDE_SHEET'):
            native_export(self.store, 'test', routing='labels', placements={'R11':[80,0]}, sheet_size=[100,70])
