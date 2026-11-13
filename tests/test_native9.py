import unittest
import zipfile
import copy
from pathlib import Path

from proteus_mcp.native9 import Reader, inspect_components, relocate_dsn
from proteus_mcp.model import CircuitError


CALIBRATION = Path(__file__).resolve().parents[1] / "calibration"


class NativeCalibrationTests(unittest.TestCase):
    def read(self, name):
        with zipfile.ZipFile(CALIBRATION / name) as archive:
            return inspect_components(archive.read("ROOT.DSN"))

    def test_saved_native_samples(self):
        names = ["01-mcu", "02-res", "03-wire", "04-led", "05-power",
                 "06-ground", "07-elec", "08-cap", "09-crystal"]
        for index, name in enumerate(names):
            with self.subTest(name=name):
                result = self.read(name + ".pdsprj")
                self.assertEqual([1, 2, 2, 3, 3, 3, 4, 5, 6][index], len(result["components"]))
                self.assertEqual(int(index >= 2), len(result["wires"]))
                self.assertEqual(min(2, max(0, index - 3)), len(result["terminals"]))

    def test_pin_pointer_order(self):
        result = self.read("09-crystal.pdsprj")
        parts = {part["ref"]: part for part in result["components"]}
        pointer = result["wires"][0]["start"]
        self.assertEqual(pointer, parts["U1"]["pointers"][14])
        self.assertEqual(pointer, parts["R1"]["pointers"][1])
        self.assertEqual(8, parts["D1"]["kind"])

    def test_truncated_data(self):
        with self.assertRaises(CircuitError):
            inspect_components(b"ISIS CIRCUIT FILE\x1aOBJECT DATA\x00\x00")

    def test_saved_projects_roundtrip_without_changing_one_byte(self):
        for path in sorted(CALIBRATION.glob("[01][0-9]-*.pdsprj")):
            with self.subTest(path=path.name), zipfile.ZipFile(path) as archive:
                original = archive.read("ROOT.DSN")
                objects = copy.deepcopy(inspect_components(original)["objects"])
                for obj in objects:
                    if obj["type"] == "component":
                        obj["wire_keys"] = obj["pointers"]
                    elif obj["type"] == "terminal":
                        obj["wire_key"] = obj["pointer"]
                    else:
                        obj["key"] = obj["start"]
                self.assertEqual(original, relocate_dsn(original, objects))

    def test_missing_wire_is_an_error(self):
        with zipfile.ZipFile(CALIBRATION / "10-terminal-wire.pdsprj") as archive:
            data = archive.read("ROOT.DSN")
        objects = inspect_components(data)["objects"]
        for obj in objects:
            if obj["type"] == "component":
                obj["wire_keys"] = obj["pointers"]
            elif obj["type"] == "terminal":
                obj["wire_key"] = obj["pointer"]
            else:
                obj["key"] = obj["start"]
        with self.assertRaisesRegex(CircuitError, "Unresolved native wire"):
            relocate_dsn(data, [obj for obj in objects if obj["type"] != "wire"])

    def test_reference_junction_record(self):
        # Junction record from the installed AD1674 & 80C51 example. The
        # first two pointer values identify the two incident wire records.
        data = bytes.fromhex("0150211b0000033e0091530100025801000000000000000000")
        result = Reader(data).junction()
        self.assertEqual([1778000, 4064000], result["origin"])
        self.assertEqual([86929, 88066, 0, 0], result["pointers"])

    def test_third_endpoint_requires_explicit_junction(self):
        with zipfile.ZipFile(CALIBRATION / "10-terminal-wire.pdsprj") as archive:
            data = archive.read("ROOT.DSN")
        objects = inspect_components(data)["objects"]
        for obj in objects:
            if obj["type"] in ("component", "junction"):
                obj["wire_keys"] = obj["pointers"]
            elif obj["type"] == "terminal":
                obj["wire_key"] = obj["pointer"]
            else:
                obj["key"] = obj["start"]
        first = next(obj for obj in objects if obj.get("ref") == "U1")
        third = next(obj for obj in objects if obj.get("ref") == "C1")
        third["wire_keys"][0] = first["wire_keys"][14]
        with self.assertRaisesRegex(CircuitError, "exactly two endpoints"):
            relocate_dsn(data, objects)


if __name__ == "__main__":
    unittest.main()
