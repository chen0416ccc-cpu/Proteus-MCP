import struct
import unittest

from proteus_mcp.model import CircuitError
from proteus_mcp.native9 import GRID
from proteus_mcp.symbol_geometry import graphic_bounds


def graphic(records):
    content = struct.pack('<II', len(records), 1)+b''.join(records)
    return bytes(16)+struct.pack('<I', len(content)+4)+content


def primitive(kind, size, payload):
    return struct.pack('<IIHH', len(payload)+12, size, kind, 2)+payload


class SymbolGeometryTests(unittest.TestCase):
    def test_line_and_transformed_rectangle_are_not_inferred_from_pins(self):
        line = primitive(0x302, 52, struct.pack('<iiii', -GRID, GRID, GRID, -GRID))
        rect = primitive(0x301, 56, struct.pack('<iihHii', 0, 0, 900, 0, 4*GRID, 2*GRID))
        self.assertEqual((-2*GRID-GRID//4, -GRID-GRID//4, GRID+GRID//4, 4*GRID+GRID//4),
                         graphic_bounds(graphic([line, rect])))
        plain = primitive(0x300, 52, struct.pack('<iiii', -GRID, GRID, GRID, -GRID))
        self.assertEqual(graphic_bounds(graphic([line])), graphic_bounds(graphic([plain])))

    def test_unknown_or_corrupt_primitives_fail_not_anchor_fallback(self):
        for blob in (graphic([primitive(0x399, 52, bytes(16))]),
                     graphic([primitive(0x302, 0, bytes(16))]),
                     graphic([primitive(0x302, 52, bytes(15))]),
                     graphic([]), bytes(20)):
            with self.subTest(length=len(blob)):
                with self.assertRaises(CircuitError):
                    graphic_bounds(blob)

    def test_count_length_and_coordinate_guards(self):
        line = primitive(0x302, 52, struct.pack('<iiii', 0, 0, GRID*501, 0))
        with self.assertRaisesRegex(CircuitError, 'BOUNDS_LIMIT'):
            graphic_bounds(graphic([line]))
        blob = bytearray(graphic([primitive(0x302, 52, bytes(16))]))
        struct.pack_into('<I', blob, 20, 2)
        with self.assertRaises(CircuitError):
            graphic_bounds(bytes(blob))

    def test_declared_polygon_envelope_and_payload_limits(self):
        payload = struct.pack('<HHiiii', 2, 0, -GRID, -3*GRID, GRID, 3*GRID)+bytes(22)
        polygon = primitive(0x307, 120, payload)
        self.assertEqual((-GRID-GRID//4, -3*GRID-GRID//4, GRID+GRID//4, 3*GRID+GRID//4),
                         graphic_bounds(graphic([polygon])))
        with self.assertRaisesRegex(CircuitError, 'POLYGON_HEADER'):
            graphic_bounds(graphic([primitive(0x307, 120, payload[:-1])]))


if __name__ == '__main__':
    unittest.main()
