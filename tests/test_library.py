import struct
import tempfile
import unittest
from pathlib import Path

from proteus_mcp.library import INDEX_START, RECORD_SIZE, LibraryIndex, read_index
from proteus_mcp.model import CircuitError


def library_fixture(names):
    content = INDEX_START + len(names)*RECORD_SIZE
    data = bytearray(content+len(names))
    data[:16] = b"DEVICE LIBRARY\x1a\x00"
    struct.pack_into("<II", data, 16, 610, len(names))
    struct.pack_into("<H", data, 26, len(names))
    for i, name in enumerate(names):
        at = INDEX_START + RECORD_SIZE*i
        encoded = name.encode("utf-8")
        data[at:at+len(encoded)] = encoded
        struct.pack_into("<I", data, at+128, content+i)
    return bytes(data)


class NativeLibraryTests(unittest.TestCase):
    def test_index_names_include_utf8(self):
        names = ["AT89C51", "CAP", "1\u00b0 ORD : LP"]
        self.assertEqual(names, [row["device"] for row in read_index(library_fixture(names))])

    def test_invalid_pointer_and_truncation_rejected(self):
        data = bytearray(library_fixture(["RES"]))
        struct.pack_into("<I", data, INDEX_START+128, 1)
        with self.assertRaises(CircuitError):
            read_index(data)
        with self.assertRaises(CircuitError):
            read_index(library_fixture(["RES"])[:-2])

    def test_search_actual_file_and_cache_invalidation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.LIB"
            path.write_bytes(library_fixture(["AT89C51.BUS", "AT89C51", "RES"]))
            index = LibraryIndex(temp)
            result = index.search("at89c51", 1)
            self.assertEqual(2, result["total_matches"])
            self.assertEqual("AT89C51", result["results"][0]["device"])
            path.write_bytes(library_fixture(["RES"]))
            self.assertEqual(0, index.search("at89c51")["total_matches"])


if __name__ == "__main__":
    unittest.main()
