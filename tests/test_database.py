import tempfile
import unittest
import zipfile
from pathlib import Path

from proteus_mcp.database import read_database, pack_database
from proteus_mcp.model import CircuitError, digest
from proteus_mcp.native_backend import CALIBRATION, native_set_component, native_read


class NativeDatabaseTests(unittest.TestCase):
    def test_real_saved_databases_roundtrip_identically(self):
        for path in sorted(CALIBRATION.parent.glob("[01][0-9]-*.pdsprj")):
            with self.subTest(path=path.name), zipfile.ZipFile(path) as archive:
                data = archive.read("ROOT.CDB")
                self.assertEqual(data, pack_database(read_database(data)))

    def test_property_edit_updates_database_and_schematic_together(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/"changed.pdsprj"
            native_set_component(CALIBRATION, output, "R1", {"value": "470"}, digest(CALIBRATION.read_bytes()))
            self.assertTrue(native_read(output)["database_consistent"])
            with zipfile.ZipFile(output) as archive:
                parts = read_database(archive.read("ROOT.CDB"))["parts"]
            self.assertEqual("470", next(p for p in parts if p["ref"] == "R1")["value"])

    def test_truncated_or_unknown_database_is_rejected(self):
        with zipfile.ZipFile(CALIBRATION) as archive:
            valid = archive.read("ROOT.CDB")
        for data in (b"", valid[:100], b"X"+valid[1:], valid+b"X"):
            with self.subTest(length=len(data)), self.assertRaises(CircuitError):
                read_database(data)
