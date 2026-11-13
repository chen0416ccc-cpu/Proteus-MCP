"""Native CTS transforms, using tenths of degrees internally."""
import math
import struct


def transform_xy(point, angle=0, mirror=0):
    x, y = point
    if mirror & 1:
        x = -x
    if mirror & 2:
        y = -y
    angle %= 3600
    if angle == 0:
        return [x, y]
    if angle == 900:
        return [-y, x]
    if angle == 1800:
        return [-x, -y]
    if angle == 2700:
        return [y, -x]
    radians = math.radians(angle / 10)
    return [round(x*math.cos(radians)-y*math.sin(radians)),
            round(x*math.sin(radians)+y*math.cos(radians))]


def part_transform(part, point):
    angle, mirror = struct.unpack('<hH', bytes.fromhex(part['transform']))
    xy = transform_xy(point, angle, mirror)
    return [part['origin'][i]+xy[i] for i in (0, 1)]


def orientation(rotation=0, mirror=0):
    from .model import CircuitError
    if type(rotation) is not int or rotation not in (0, 90, 180, 270):
        raise CircuitError('rotation must be 0, 90, 180 or 270 degrees')
    if type(mirror) is not int or mirror not in range(4):
        raise CircuitError('mirror must be 0..3 (x=1, y=2)')
    return struct.pack('<hH', rotation*10, mirror).hex()
