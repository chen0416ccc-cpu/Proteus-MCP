import tempfile
import unittest
from pathlib import Path

from proteus_mcp.firmware import inspect_hex
from proteus_mcp.model import CircuitError


def record(address, kind, payload=b""):
    data = bytes([len(payload)]) + address.to_bytes(2, "big") + bytes([kind]) + payload
    return ":" + (data + bytes([-sum(data) & 255])).hex().upper() + "\n"


class HexTests(unittest.TestCase):
    def inspect(self, text):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "firmware.hex"
            path.write_text(text, encoding="ascii")
            return inspect_hex(path)

    def test_valid(self):
        report = self.inspect(record(0, 0, bytes([2, 0, 3])) + record(0, 1))
        self.assertTrue(report["valid"])
        self.assertEqual(3, report["code_bytes"])

    def test_checksum(self):
        with self.assertRaises(CircuitError):
            self.inspect(":0100000000FE\n:00000001FF\n")

    def test_missing_eof(self):
        with self.assertRaises(CircuitError):
            self.inspect(record(0, 0, b"\0"))

    def test_missing_reset(self):
        with self.assertRaises(CircuitError):
            self.inspect(record(1, 0, b"\0") + record(0, 1))

    def test_overlap(self):
        with self.assertRaises(CircuitError):
            self.inspect(record(0, 0, b"\0") * 2 + record(0, 1))

    def test_after_eof(self):
        with self.assertRaises(CircuitError):
            self.inspect(record(0, 0, b"\0") + record(0, 1) + record(0, 1))

    def test_8051_address_limit(self):
        with self.assertRaises(CircuitError):
            self.inspect(record(0, 4, b"\0\1") + record(0, 0, b"\0") + record(0, 1))

    def test_malformed_record_type(self):
        with self.assertRaises(CircuitError):
            self.inspect(record(0, 0, b"\0") + record(0, 2, b"\0") + record(0, 1))


if __name__ == "__main__":
    unittest.main()
