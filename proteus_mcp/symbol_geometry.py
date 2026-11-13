"""Strict envelopes for the supported static library drawing primitives."""

import struct

from .geometry import transform_xy
from .model import CircuitError
from .native9 import GRID


def graphic_bounds(blob):
    """Read declared geometry; pin anchors are not a symbol's bounding box."""
    if len(blob) < 28:
        raise CircuitError('SYMBOL_GRAPHICS_TRUNCATED')
    size, count, version = struct.unpack_from('<III', blob, 16)
    end = 16+size
    if size < 12 or end > len(blob) or version != 1 or not 1 <= count <= 10000:
        raise CircuitError('SYMBOL_GRAPHICS_HEADER_NOT_CALIBRATED')
    pos, boxes = 28, []
    for _ in range(count):
        if pos+10 > end:
            raise CircuitError('SYMBOL_GRAPHICS_TRUNCATED')
        length, native_size, kind = struct.unpack_from('<IIH', blob, pos)
        stop = pos+length
        if length < 10 or stop > end:
            raise CircuitError('SYMBOL_GRAPHICS_RECORD_LENGTH')
        raw = blob[pos:stop]
        pos = stop
        if kind == 0x306 and native_size == 48 and b'$MKRORIGIN\0' in raw:
            continue
        if kind not in (0x300, 0x301, 0x302, 0x307):
            raise CircuitError('SYMBOL_PRIMITIVE_NOT_CALIBRATED: '+hex(kind))
        if len(raw) < 12:
            raise CircuitError('SYMBOL_GRAPHICS_STYLE')
        style_size = struct.unpack_from('<H', raw, 10)[0]
        at = 10+style_size
        if style_size < 2 or at > len(raw):
            raise CircuitError('SYMBOL_GRAPHICS_STYLE')
        payload = raw[at:]
        if kind in (0x300, 0x302) and native_size == 52 and len(payload) == 16:
            x1, y1, x2, y2 = struct.unpack('<iiii', payload)
            box = min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
        elif kind == 0x301 and native_size == 56 and len(payload) == 20:
            x, y, angle, mirror, width, height = struct.unpack('<iihHii', payload)
            if angle % 900 or mirror > 3:
                raise CircuitError('SYMBOL_RECTANGLE_TRANSFORM_NOT_CALIBRATED')
            corners = [transform_xy([u, v], angle, mirror) for u in (0, width) for v in (0, height)]
            box = (x+min(p[0] for p in corners), y+min(p[1] for p in corners),
                   x+max(p[0] for p in corners), y+max(p[1] for p in corners))
        elif kind == 0x307 and native_size == 120 and len(payload) >= 22:
            points, flags = struct.unpack_from('<HH', payload)
            if flags != 0 or not 2 <= points <= 4096 or len(payload) != 22+10*points:
                raise CircuitError('SYMBOL_POLYGON_HEADER_NOT_CALIBRATED')
            box = struct.unpack_from('<iiii', payload, 4)
            if box[0] > box[2] or box[1] > box[3]:
                raise CircuitError('SYMBOL_POLYGON_BOUNDS')
        else:
            raise CircuitError('SYMBOL_PRIMITIVE_PAYLOAD_NOT_CALIBRATED: '+hex(kind))
        if any(abs(value) > GRID*500 for value in box):
            raise CircuitError('SYMBOL_GRAPHICS_BOUNDS_LIMIT')
        boxes.append(box)
    if pos != end or not boxes:
        raise CircuitError('SYMBOL_GRAPHICS_COUNT_OR_EMPTY')
    # This envelope includes a stroke margin, not native font or pin captions.
    return (min(box[0] for box in boxes)-GRID//4, min(box[1] for box in boxes)-GRID//4,
            max(box[2] for box in boxes)+GRID//4, max(box[3] for box in boxes)+GRID//4)
