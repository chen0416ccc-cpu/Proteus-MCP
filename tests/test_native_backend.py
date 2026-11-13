import tempfile
import unittest
from pathlib import Path

from proteus_mcp.examples import led_chaser
from proteus_mcp.model import CircuitError, Store, atomic_write, digest, json_bytes
from proteus_mcp.native_backend import native_create, native_export_led_chaser, native_read, native_set_component
from tests.local_fixtures import CALIBRATION
from proteus_mcp.firmware import inspect_hex


class NativeFileBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        led_chaser(self.store, "test")

    def compiled_artifact_fixture(self):
        folder = self.store.folder("test")
        path = folder / "fixture.hex"
        path.write_text(":03000000020000FB\n:00000001FF\n", encoding="ascii")
        report = {"status": "compiled", "hex_path": str(path), "hex": inspect_hex(path),
                  "source_sha256": digest((folder/"main.c").read_bytes())}
        atomic_write(folder / "latest-build.json", json_bytes(report))
        return folder

    def test_full_native_export_records_match_design_and_preserve_identities(self):
        self.compiled_artifact_fixture()
        result = native_export_led_chaser(self.store, "test")
        self.assertEqual("generated_unverified", result["status"])
        self.assertFalse(result["native_reopen_verified"])
        self.assertFalse(result["simulation_verified"])
        self.assertEqual(23, result["components"])
        self.assertEqual(21, result["stored_nets"])
        self.assertEqual(3, result["junctions"])
        self.assertEqual(40, result["wires"])
        native = native_read(result["path"])
        parts = {part["ref"]: part for part in native["components"]}
        self.assertEqual(23, len({p['identity'] for p in parts.values()}))
        self.assertTrue(result['routing']['sheet']['all_geometry_inside_frame'])
        self.assertEqual(0, native['nonorthogonal_wire_segments'])
        self.assertIn("{CLOCK=12000000}", parts["U1"]["native_properties"])
        self.assertIn(result["firmware_path"], parts["U1"]["native_properties"])
        self.assertIn("{FREQ=12MHz}", parts["X1"]["native_properties"])
        for i in range(1, 9):
            self.assertIn(sorted([f"U1.{i}", f"D{i}.2"]), native["stored_connectivity"].values())

    def test_model_changes_are_not_silently_ignored(self):
        folder = self.compiled_artifact_fixture()
        model = self.store.read("test")
        model["nets"]["LED1_K"] = ["U1.2", "D1.2"]
        atomic_write(folder / "circuit.json", json_bytes(model))
        with self.assertRaisesRegex(CircuitError, "topology"):
            native_export_led_chaser(self.store, "test")

    def test_stale_source_is_rejected_before_export(self):
        folder = self.compiled_artifact_fixture()
        with (folder / "main.c").open("a") as stream:
            stream.write("\n/* Changed after compilation. */\n")
        with self.assertRaisesRegex(CircuitError, "Build the current"):
            native_export_led_chaser(self.store, "test")

    def test_empty_native_project_has_no_parts_or_wires(self):
        result = native_create(self.store, "test")
        native = native_read(result["path"])
        self.assertEqual([], native["components"])
        self.assertEqual(0, native["wires"])

    def test_native_property_edit_preserves_source_and_connectivity(self):
        source_hash = digest(CALIBRATION.read_bytes())
        output = Path(self.temp.name) / "changed.pdsprj"
        before = native_read(CALIBRATION)
        native_set_component(CALIBRATION, output, "U1", {"clock_hz": 11059200}, source_hash)
        after = native_read(output)
        self.assertEqual(source_hash, digest(CALIBRATION.read_bytes()))
        self.assertEqual(sorted(before["stored_connectivity"].values()), sorted(after["stored_connectivity"].values()))
        self.assertIn("{CLOCK=11059200}", after["components"][0]["native_properties"])
        with self.assertRaisesRegex(CircuitError, "overwrite"):
            native_set_component(CALIBRATION, output, "U1", {"clock_hz": 12000000}, source_hash)
        with self.assertRaisesRegex(CircuitError, "CONFLICT"):
            native_set_component(CALIBRATION, Path(self.temp.name)/"new.pdsprj", "U1", {}, "0"*64)


if __name__ == "__main__":
    unittest.main()
