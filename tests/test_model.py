import json
import tempfile
import unittest
from pathlib import Path

from proteus_mcp.catalog import pin_number
from proteus_mcp.examples import led_chaser
from proteus_mcp.exports import export_netlist, export_plan, export_svg
from proteus_mcp.model import CircuitError, Store, digest, validate
from proteus_mcp.service import Service


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.create("test")

    def test_no_overwrite(self):
        with self.assertRaises(FileExistsError):
            self.store.create("test")

    def test_path_escape(self):
        for name in ("../evil", "a/b", "a\\b", "C:\\evil", "..", "", "hello world"):
            with self.subTest(name=name), self.assertRaises(CircuitError):
                self.store.folder(name)

    def test_bool_revision_rejected(self):
        with self.assertRaises(CircuitError):
            self.store.add("test", False, "U1", "AT89C51", [0, 0])

    def test_revision_conflict_preserves_file(self):
        before = self.store.read("test")
        with self.assertRaises(CircuitError):
            self.store.add("test", 99, "U1", "AT89C51", [0, 0])
        self.assertEqual(before, self.store.read("test"))

    def test_duplicate_component(self):
        self.store.add("test", 0, "U1", "AT89C51", [0, 0])
        with self.assertRaises(CircuitError):
            self.store.add("test", 1, "U1", "AT89C51", [20, 20])
        self.assertEqual(1, self.store.read("test")["revision"])

    def test_invalid_positions(self):
        for position in ([True, 0], [0.0, 1], [0], [0, 100001]):
            with self.subTest(position=position), self.assertRaises(CircuitError):
                self.store.add("test", 0, "R1", "RES", position)

    def test_unknown_device_property_pin(self):
        with self.assertRaises(ValueError):
            self.store.add("test", 0, "U1", "made-up-device", [0, 0])
        with self.assertRaises(ValueError):
            self.store.add("test", 0, "U1", "AT89C51", [0, 0], {"typo": 2})
        with self.assertRaises(ValueError):
            pin_number("AT89C51", "P1.8")

    def test_canonical_pin_alias(self):
        self.assertEqual("1", pin_number("AT89C51", "P1.0"))
        self.assertEqual("40", pin_number("AT89C51", "VCC"))

    def test_implicit_short_rejected(self):
        for index in range(3):
            self.store.add("test", index, f"R{index+1}", "RES", [index*100, 0])
        self.store.connect("test", 3, "ONE", ["R1.1", "R2.1"])
        with self.assertRaises(CircuitError):
            self.store.connect("test", 4, "TWO", ["R1.1", "R3.1"])
        self.assertEqual(4, self.store.read("test")["revision"])

    def test_duplicate_alias_not_two_pins(self):
        self.store.add("test", 0, "U1", "AT89C51", [0, 0])
        with self.assertRaises(CircuitError):
            self.store.connect("test", 1, "BAD", ["U1.1", "U1.P1.0"])

    def test_lock_prevents_writes(self):
        with self.store.lock("test"):
            with self.assertRaises(CircuitError):
                self.store.add("test", 0, "R1", "RES", [0, 0])

    def test_floating_pins_fail_validation(self):
        result = self.store.add("test", 0, "U1", "AT89C51", [0, 0])
        self.assertFalse(validate(result)["ok"])
        self.assertTrue(any("RST" in error for error in validate(result)["errors"]))

    def test_example_and_exports(self):
        value = led_chaser(self.store, "example")
        self.assertEqual(23, len(value["components"]))
        self.assertEqual(21, len(value["nets"]))
        self.assertTrue(validate(value)["ok"])
        self.assertEqual("not_applied", value["native_status"])
        self.assertEqual("planned", export_plan(self.store, "example")["status"])
        svg = Path(export_svg(self.store, "example")["path"]).read_text()
        self.assertIn("NOT Proteus", svg)
        result = json.loads(Path(export_netlist(self.store, "example")["path"]).read_text())
        self.assertTrue(result["not_native_proteus"])

    def test_source_conflict_and_backup(self):
        service = Service(self.temp.name)
        path = Path(service.source_write("test", "void main(void) {}\n")["path"])
        old = path.read_bytes()
        with self.assertRaises(CircuitError):
            service.source_write("test", "new source")
        self.assertEqual(old, path.read_bytes())
        service.source_write("test", "new source", digest(old))
        self.assertEqual(old, (path.parent / "source-backups" / f"{digest(old)}.c").read_bytes())

    def test_native_capabilities_distinguish_file_edits_from_live_control(self):
        service = Service(self.temp.name)
        capability = service.doctor()["capabilities"]["native_schematic_write"]
        self.assertTrue(capability["available"])
        self.assertFalse(capability["live_edit"])
        value = service.call("simulation_control", {"project": "test", "action": "run"})
        self.assertEqual("unsupported", value["status"])
        self.assertFalse(value["simulation_verified"])

    def test_edit_batch_is_atomic_on_invalid_later_operation(self):
        before = self.store.read("test")
        with self.assertRaises(CircuitError):
            self.store.edit("test", 0, [{"op": "add", "ref": "R1", "device": "RES", "position": [0, 0]},
                                        {"op": "connect", "name": "BAD", "pins": ["R1.1", "NO.1"]}])
        self.assertEqual(before, self.store.read("test"))

    def test_edit_move_disconnect_remove_and_explicit_merge(self):
        value = self.store.edit("test", 0, [
            {"op": "add", "ref": f"R{i}", "device": "RES", "position": [i*10, 0]} for i in range(1, 4)])
        value = self.store.edit("test", value["revision"], [
            {"op": "connect", "name": "ONE", "pins": ["R1.1", "R2.1"]},
            {"op": "connect", "name": "TWO", "pins": ["R1.2", "R3.1"]},
            {"op": "merge_nets", "name": "MERGED", "nets": ["ONE", "TWO"]},
            {"op": "set", "ref": "R1", "position": [9, 8], "properties": {"value": "1k"}},
            {"op": "disconnect", "name": "MERGED", "pins": ["R1.2"]},
            {"op": "remove", "ref": "R3"}])
        self.assertEqual(2, value["revision"])
        self.assertEqual({"MERGED": ["R1.1", "R2.1"]}, value["nets"])
        self.assertEqual([9, 8], value["components"]["R1"]["position"])
        self.assertEqual("1k", value["components"]["R1"]["properties"]["value"])


if __name__ == "__main__":
    unittest.main()
