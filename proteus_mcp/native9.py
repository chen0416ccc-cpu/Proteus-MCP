"""Proteus 9 calibration reader. Unknown object types are deliberately rejected.

This module is experimental until generated files pass a native reopen check.
Only local, explicitly captured assets may be used; no assumed COM interface.
"""

import struct
import copy
import zipfile
from pathlib import Path

from .model import CircuitError, atomic_write


SIGNATURE = b"ISIS CIRCUIT FILE\x1a"
OBJECTS = b"OBJECT DATA\x00"
EMPTY_CDB = bytes.fromhex(
    "090000000100000001000000000000000400524f4f540000"
    "000000010000000100000002000000010000000300000001"
    "00000000000a000000000000000200000002000000000000"
    "000c004d61737465722053686565740a0000000000000000"
    "000000010000000100000000000001000000000000000000"
    "0000")


class Reader:
    def __init__(self, data, offset=0):
        self.data, self.offset = data, offset

    def read(self, length):
        if length < 0 or self.offset + length > len(self.data):
            raise CircuitError("Truncated native calibration data")
        start = self.offset
        self.offset += length
        return self.data[start:self.offset]

    def integer(self, size=4):
        return int.from_bytes(self.read(size), "little")

    def text(self):
        start = self.offset
        if self.read(1) != b"\xff":
            raise CircuitError(f"Expected native UTF-8 label at {start}")
        length = self.integer()
        if length > 128000:
            raise CircuitError("Native label exceeds limit")
        text = self.read(length).decode("utf-8")
        xy_offset = self.offset
        xy = struct.unpack("<ii", self.read(8))
        transform = self.read(6)
        style_length = self.integer()
        if style_length < 4:
            raise CircuitError("Invalid native text style length")
        style = self.read(style_length - 4)
        return {"offset": start, "end": self.offset, "text": text, "xy": list(xy),
                "xy_offset": xy_offset, "transform": transform.hex(), "style": style.hex()}

    def component(self):
        start = self.offset
        kind = self.integer(1)
        if kind not in (0, 8):
            raise CircuitError(f"Uncalibrated native component kind {kind}")
        labels = [self.text() for _ in range(4)]
        pins = self.integer(2)
        device = self.read(self.integer(2)).decode("utf-8")
        origin_offset = self.offset
        origin = struct.unpack("<ii", self.read(8))
        transform = self.read(4)
        identity_offset = self.offset
        identity = self.integer()
        tail_offset = self.offset
        pin_header = self.read(9)
        pointers_offset = self.offset
        pointers = [self.integer() for _ in range(pins)]
        return {"type": "component", "kind": kind, "start": start, "end": self.offset, "ref": labels[0]["text"],
                "device": device, "pin_count": pins, "origin": list(origin),
                "origin_offset": origin_offset, "identity_offset": identity_offset,
                "identity": identity, "transform": transform.hex(), "labels": labels,
                "tail_offset": tail_offset, "pin_header": pin_header.hex(),
                "pointers_offset": pointers_offset, "pointers": pointers}

    def wire(self):
        start = self.offset
        flags = self.integer(1)
        if flags not in (0, 1):
            raise CircuitError("Expected native wire")
        style_length = self.integer()
        if not 4 <= style_length <= 4096:
            raise CircuitError("Invalid wire style length")
        style = self.read(style_length - 4)
        label_count = self.integer(2)
        if label_count > 64:
            raise CircuitError("Too many native wire labels")
        labels = [self.text() for _ in range(label_count)]
        count = self.integer(2)
        if not 2 <= count <= 4096:
            raise CircuitError("Invalid wire point count")
        points = [list(struct.unpack("<ii", self.read(8))) for _ in range(count)]
        return {"type": "wire", "start": start, "end": self.offset,
                "flags": flags, "style": style.hex(), "labels": labels, "points": points}

    def junction(self):
        start = self.offset
        if self.integer(1) != 1:
            raise CircuitError("Expected native junction")
        origin = list(struct.unpack("<ii", self.read(8)))
        pointers = [self.integer() for _ in range(4)]
        return {"type": "junction", "start": start, "end": self.offset,
                "origin": origin, "pointers": pointers}

    def terminal(self):
        start = self.offset
        if self.integer(1) != 16:
            raise CircuitError("Expected native terminal")
        origin_offset = self.offset
        origin = list(struct.unpack("<ii", self.read(8)))
        transform = self.read(4)
        device = self.read(self.integer(2)).decode("utf-8")
        if device not in ("$TERPOWER", "$TERGROUND"):
            raise CircuitError(f"Uncalibrated terminal {device}")
        attributes = self.read(6)
        label = self.text()
        pointer_offset = self.offset
        pointer = self.integer()
        return {"type": "terminal", "start": start, "end": self.offset,
                "origin_offset": origin_offset, "origin": origin,
                "transform": transform.hex(), "device": device,
                "attributes": attributes.hex(), "label": label,
                "pointer_offset": pointer_offset, "pointer": pointer}


def inspect_components(data):
    circuit = data.find(SIGNATURE)
    if circuit < 0:
        raise CircuitError("No native circuit section")
    begin = data.find(OBJECTS, circuit)
    if begin < 0:
        raise CircuitError("No native object section")
    reader = Reader(data, begin + len(OBJECTS))
    objects, seen, pending = [], set(), []
    while reader.offset < len(data) and data[reader.offset] != 255:
        if pending:
            expected = pending.pop(0)
            if reader.offset != expected:
                raise CircuitError(f"Native wire order mismatch at {reader.offset}; expected {expected}")
            objects.append(reader.wire())
            continue
        kind = data[reader.offset]
        if kind in (0, 8) and data[reader.offset+1:reader.offset+2] == b"\xff":
            objects.append(reader.component())
        elif kind == 1:
            objects.append(reader.junction())
        elif kind == 16:
            objects.append(reader.terminal())
        else:
            raise CircuitError(f"Uncalibrated native object at {reader.offset}: {kind}")
        obj = objects[-1]
        for pointer in obj.get("pointers", [obj.get("pointer", 0)]):
            if not pointer:
                continue
            if pointer in seen:
                pending.append(pointer)
            else:
                seen.add(pointer)
        if len(objects) > 10000:
            raise CircuitError("Too many native objects")
    if reader.offset >= len(data):
        raise CircuitError("Missing native object terminator")
    if pending:
        raise CircuitError("Missing queued native wire records")
    wires = {obj["start"] for obj in objects if obj["type"] == "wire"}
    for obj in objects:
        pointers = obj.get("pointers", [obj.get("pointer", 0)])
        if any(pointer and pointer not in wires for pointer in pointers):
            raise CircuitError(f"Invalid wire reference in object at {obj['start']}")
    return {"objects": objects,
            "components": [obj for obj in objects if obj["type"] == "component"],
            "wires": [obj for obj in objects if obj["type"] == "wire"],
            "terminals": [obj for obj in objects if obj["type"] == "terminal"],
            "junctions": [obj for obj in objects if obj["type"] == "junction"],
            "objects_start": begin+len(OBJECTS),
            "objects_end": reader.offset, "circuit_start": circuit}


def pack_text(label):
    encoded = label["text"].encode("utf-8")
    style = bytes.fromhex(label["style"])
    return (b"\xff" + struct.pack("<I", len(encoded)) + encoded
            + struct.pack("<ii", *label["xy"])
            + bytes.fromhex(label["transform"])
            + struct.pack("<I", len(style) + 4) + style)


def wire_pointer(key, wire_offsets):
    if key is None or key == 0 or wire_offsets is None:
        return 0
    if key not in wire_offsets:
        raise CircuitError(f"Unresolved native wire key: {key}")
    return wire_offsets[key]


def pack_object(obj, wire_offsets=None):
    if obj["type"] == "component":
        device = obj["device"].encode("utf-8")
        if len(obj["wire_keys"]) != obj["pin_count"]:
            raise CircuitError("Native component pin count mismatch")
        pointers = [wire_pointer(key, wire_offsets) for key in obj["wire_keys"]]
        return (bytes([obj["kind"]]) + b"".join(pack_text(label) for label in obj["labels"])
                + struct.pack("<HH", obj["pin_count"], len(device)) + device
                + struct.pack("<ii", *obj["origin"]) + bytes.fromhex(obj["transform"])
                + struct.pack("<I", obj["identity"]) + bytes.fromhex(obj["pin_header"])
                + b"".join(struct.pack("<I", value) for value in pointers))
    if obj["type"] == "wire":
        style = bytes.fromhex(obj["style"])
        labels = obj.get("labels", [])
        return (bytes([obj.get("flags", 0)]) + struct.pack("<I", len(style)+4) + style
                + struct.pack("<H", len(labels)) + b"".join(pack_text(label) for label in labels)
                + struct.pack("<H", len(obj["points"]))
                + b"".join(struct.pack("<ii", *point) for point in obj["points"]))
    if obj["type"] == "junction":
        return (b"\x01" + struct.pack("<ii", *obj["origin"])
                + b"".join(struct.pack("<I", wire_pointer(key, wire_offsets)) for key in obj["wire_keys"]))
    if obj["type"] == "terminal":
        device = obj["device"].encode("utf-8")
        return (b"\x10" + struct.pack("<ii", *obj["origin"]) + bytes.fromhex(obj["transform"])
                + struct.pack("<H", len(device)) + device + bytes.fromhex(obj["attributes"])
                + pack_text(obj["label"]) + struct.pack("<I", wire_pointer(obj["wire_key"], wire_offsets)))
    raise CircuitError("Cannot serialize unknown native object")


def movable_copy(obj, origin, ref=None, identity=None):
    result = copy.deepcopy(obj)
    dx, dy = origin[0] - obj["origin"][0], origin[1] - obj["origin"][1]
    result["origin"] = list(origin)
    labels = result.get("labels", [result.get("label")])
    for label in labels:
        label["xy"] = [label["xy"][0] + dx, label["xy"][1] + dy]
    if ref is not None:
        result["ref"] = result["labels"][0]["text"] = ref
    if identity is not None:
        result["identity"] = identity
    if result["type"] == "component":
        result["wire_keys"] = [None] * result["pin_count"]
    else:
        result["wire_key"] = None
    return result


def relocate_dsn(data, objects):
    """Replace the single calibrated root sheet and relocate its directory."""
    original = inspect_components(data)
    begin, end = original["objects_start"], original["objects_end"]
    second = end + 1
    if not data[second:].startswith(SIGNATURE):
        raise CircuitError("Expected calibrated empty default circuit")
    directory = data.find(OBJECTS, second) + len(OBJECTS) + 1
    if data[directory-1:directory] != b"\xff":
        raise CircuitError("Default circuit must be empty")
    if struct.unpack_from("<I", data, original["circuit_start"]-4)[0] != directory:
        raise CircuitError("Unexpected root directory pointer")
    objects = schedule_wires(objects)
    components = [obj for obj in objects if obj["type"] == "component"]
    for field in ("ref", "identity"):
        if len({part[field] for part in components}) != len(components):
            raise CircuitError(f"Duplicate native component {field}")
    cursor, offsets = begin, {}
    for obj in objects:
        if obj["type"] == "wire":
            if obj["key"] in offsets:
                raise CircuitError("Duplicate native wire key")
            offsets[obj["key"]] = cursor
        cursor += len(pack_object(obj))
    payload = b"".join(pack_object(obj, offsets) for obj in objects)
    delta = len(payload) - (end - begin)
    prefix = bytearray(data[:begin])
    struct.pack_into("<I", prefix, original["circuit_start"]-4, directory+delta)
    suffix = bytearray(data[end:])
    reader = Reader(data, directory)
    count = reader.integer(2)
    if count != 2:
        raise CircuitError("Only a root and empty default circuit are calibrated")
    for _ in range(count):
        reader.integer()
        reader.read(reader.integer(2))
        reader.integer(2)
        pointer_offset = reader.offset
        pointer = reader.integer()
        if pointer not in (original["circuit_start"], second):
            raise CircuitError("Unexpected native sheet directory reference")
        new_pointer = pointer + delta if pointer == second else pointer
        struct.pack_into("<I", suffix, pointer_offset-end, new_pointer)
    result = bytes(prefix) + payload + bytes(suffix)
    inspected = inspect_components(result)
    if len(inspected["objects"]) != len(objects):
        raise CircuitError("Generated native object count mismatch")
    return result


def schedule_wires(objects):
    """ISIS drains a FIFO of completed two-endpoint wires after each node."""
    wires = {}
    for obj in objects:
        if obj["type"] == "wire":
            if obj["key"] in wires:
                raise CircuitError("Duplicate native wire key")
            wires[obj["key"]] = obj
    counts, ordered = {}, []
    for obj in objects:
        if obj["type"] == "wire":
            continue
        ordered.append(obj)
        for key in obj.get("wire_keys", [obj.get("wire_key")]):
            if key is None or key == 0:
                continue
            if key not in wires:
                raise CircuitError(f"Unresolved native wire key: {key}")
            counts[key] = counts.get(key, 0)+1
            if counts[key] > 2:
                raise CircuitError("Native wires require exactly two endpoints; use a junction")
            if counts[key] == 2:
                # ISIS binds the first serialized endpoint to points[0]. It
                # replaces ends, not the interior vertices, on native load.
                wire = wires[key]
                generated_ends = wire.get('_component_ends')
                if generated_ends is not None:
                    if obj['type'] == 'component':
                        from .devices import native_order
                        pin = native_order(obj['device'], obj.get('native_definition'))[
                            obj['wire_keys'].index(key)]
                        end = generated_ends.get(obj['ref']+'.'+pin)
                    elif obj['type'] == 'junction':
                        end = obj['origin']
                    else:
                        # Terminals and components use the same calibrated
                        # clockwise transform. Their connection is local y=+1
                        # (ground) or -1 (power) from the symbol origin.
                        from .geometry import part_transform
                        end = part_transform(obj, [0, GRID if obj['device']=='$TERGROUND' else -GRID])
                    if end == wire['points'][0]:
                        wire['points'] = wire['points'][::-1]
                    elif end != wire['points'][-1]:
                        raise CircuitError('NATIVE_WIRE_ENDPOINT_ORDER_MISMATCH: '+str(key))
                ordered.append(wires[key])
    if any(counts.get(key) != 2 for key in wires):
        raise CircuitError("Native wires require exactly two endpoints")
    return ordered


def checked_sheet_size(size):
    if (not isinstance(size, (list, tuple)) or len(size) != 2 or
            any(type(v) is not int or not 20 <= v <= 1000 or v % 2 for v in size)):
        raise CircuitError('sheet_size must contain two even integer grid dimensions in 20..1000')
    return list(size)


def sheet_dimensions(data):
    parsed = inspect_components(data)
    start = parsed['circuit_start']+len(SIGNATURE)
    if (data[start:start+7] != bytes.fromhex('00010001000100') or
            data[start+15:start+17] != b'\0\0' or data[start+17:start+29] != OBJECTS):
        raise CircuitError('Uncalibrated native sheet header')
    width, height = struct.unpack_from('<ii', data, start+7)
    if width <= 0 or height <= 0:
        raise CircuitError('Invalid native sheet dimensions')
    return [width/GRID, height/GRID]


def resize_sheet(data, size):
    size = checked_sheet_size(size)
    sheet_dimensions(data)
    start = inspect_components(data)['circuit_start']+len(SIGNATURE)
    result = bytearray(data)
    struct.pack_into('<ii', result, start+7, size[0]*GRID, size[1]*GRID)
    return bytes(result)


def rewrite_project(template, output, transform, reset_database=False, database_mode=None, sheet_size=None):
    """Experimental local serializer; native reopen remains a separate check."""
    template, output = Path(template), Path(output)
    if output.exists():
        raise CircuitError("Refusing to overwrite native output")
    with zipfile.ZipFile(template) as archive:
        data = archive.read("ROOT.DSN")
        original = inspect_components(data)
        objects = copy.deepcopy(original["objects"])
        for obj in objects:
            if obj["type"] == "wire":
                obj["key"] = obj["start"]
            elif obj["type"] in ("component", "junction"):
                obj["wire_keys"] = obj["pointers"]
            else:
                obj["wire_key"] = obj["pointer"]
        objects = transform(objects)
        from .devices import ensure_cache
        data = ensure_cache(data, [obj for obj in objects if obj['type'] == 'component'])
        generated = relocate_dsn(data, objects)
        if sheet_size is not None:
            generated = resize_sheet(generated, sheet_size)
        database = archive.read("ROOT.CDB")
        if database_mode:
            if reset_database or database_mode not in ("mirror", "generate"):
                raise CircuitError("Invalid native database mode")
            from .database import mirror_database
            database = mirror_database(database, objects, generate=database_mode == "generate")
        import io
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
            for item in archive.infolist():
                if item.filename == "ROOT.CDB":
                    target.writestr(item.filename, EMPTY_CDB if reset_database else database)
                    continue
                target.writestr(item.filename, generated if item.filename == "ROOT.DSN" else archive.read(item))
        atomic_write(output, buffer.getvalue())
    return {"path": str(output.resolve()), "status": "generated_unverified",
            "native_reopen_verified": False, "simulation_verified": False}


def clone_experiment(template, output):
    def transform(objects):
        resistor = next(obj for obj in objects if obj.get("ref") == "R1")
        objects.append(movable_copy(resistor, [-5080000, -5080000], "R2", 7))
        return objects
    return rewrite_project(template, output, transform)


GRID = 254000
MCU_ORDER = ["18", "19", "30", "31", "29", "9", *map(str, range(39, 31, -1)),
             *map(str, range(1, 9)), "10", "11", "12", "13", "14", "17", "16", "15",
             "40", "20", "28", *map(str, range(21, 28))]
PIN_ORDER = {"AT89C51": MCU_ORDER, "RES": ["1", "2"], "LED-RED": ["1", "2"],
             "CAP": ["2", "1"], "CAP-ELEC": ["1", "2"], "CRYSTAL": ["1", "2"]}


def pin_xy(part, number):
    from .geometry import part_transform
    if part['device'] not in PIN_ORDER:
        from .devices import selected_definition
        spec = part.get('_embedded_definition') or selected_definition(part)
        pin = next(p for p in spec['pins'] if p['number'] == number)
        return None if pin['xy'] is None else part_transform(part, pin['xy'])
    device = part["device"]
    if device == "AT89C51":
        if number in ("40", "20"):
            return None
        left = {"19": -1, "18": -4, "29": -13, "30": -14, "31": -15, "9": -8}
        left.update({str(i+1): -19-i for i in range(8)})
        right = {str(39-i): -1-i for i in range(8)}
        right.update({str(21+i): -10-i for i in range(8)})
        right.update({str(10+i): -19-i for i in range(8)})
        dx, dy = (-2, left[number]) if number in left else (16, right[number])
    elif device == "LED-RED":
        dx, dy = (0, 2 if number == "1" else -2)
    else:
        dx, dy = (-2 if number == "1" else 3 if device == "RES" else 2), 0
    return part_transform(part, [dx*GRID, dy*GRID])


def component_value(part, value):
    import re
    part['labels'][1]['text'] = value
    # Some library definitions store VALUE as well as the visible value label.
    # Proteus prefers that assignment when loading, so both must agree.
    text = part['labels'][3]['text']
    pattern = r'(?m)^\{?VALUE=[^{}\r\n]*\}?$'
    if re.search(pattern, text) and any(c in str(value) for c in '{}\r\n'):
        raise CircuitError('Native value property contains a reserved character')
    part['labels'][3]['text'] = re.sub(pattern, lambda _: '{VALUE='+str(value)+'}', text)


class Builder:
    """Small calibrated device subset. Every pin owns at most one wire object."""
    def __init__(self, templates):
        self.templates = {obj["device"]: obj for obj in templates if obj["type"] in ("component", "terminal")}
        self.wire_template = next(obj for obj in templates if obj["type"] == "wire")
        self.parts, self.objects = {}, []

    def component(self, ref, device, origin_grid, value=None, properties=None, identity=None,
                  rotation=0, mirror=0, native_definition=None):
        from .devices import selected_definition, native_order, instance_profile
        from .geometry import orientation
        if device not in self.templates:
            spec = selected_definition({'device':device,'native_definition':native_definition or {}})
            if spec['elements'] != 1 or spec['units'] != 1:
                raise CircuitError('Multi-unit devices need an explicit unit mapping: '+device)
            template = copy.deepcopy(self.templates['RES'])
            kind, pin_header = instance_profile(spec)
            template.update(device=device,kind=kind,pin_count=len(spec['pins']),
                            pointers=[0]*len(spec['pins']),
                            pin_header=pin_header)
            template['labels'][1]['text'] = spec['defaults'].get('VALUE',device)
            template['labels'][2]['text'] = device
            template['labels'][3]['text'] = spec['properties']
            self.templates[device] = template
        if ref in self.parts:
            raise CircuitError(f"Duplicate native reference: {ref}")
        if identity is None:
            identity = max((p["identity"] for p in self.parts.values()), default=0) + 1
        if any(p["identity"] == identity for p in self.parts.values()):
            raise CircuitError("Duplicate native identity")
        part = movable_copy(self.templates[device], [n*GRID for n in origin_grid], ref, identity)
        if native_definition:
            part['native_definition'] = copy.deepcopy(native_definition)
        part['transform'] = orientation(rotation, mirror)
        if value is not None:
            component_value(part, value)
        for key, value in (properties or {}).items():
            import re
            text = part["labels"][3]["text"]
            setting = "{" + key + "=" + str(value) + "}"
            pattern = r"\{" + re.escape(key) + r"=[^}]*\}"
            part["labels"][3]["text"] = re.sub(pattern, lambda _: setting, text) if re.search(pattern, text) else text+setting+"\n"
        self.parts[ref] = part
        self.objects.append(part)
        return part

    def pin(self, endpoint):
        ref, number = endpoint.split(".")
        return pin_xy(self.parts[ref], number)

    def wire(self, key, endpoints, points):
        if len(points) < 2 or any(a == b for a, b in zip(points, points[1:])):
            raise CircuitError("Native wire needs distinct points")
        if any(a[0] != b[0] and a[1] != b[1] for a, b in zip(points, points[1:])):
            raise CircuitError("Native builder requires orthogonal wire segments")
        wire = copy.deepcopy(self.wire_template)
        wire.update(key=key, name="", points=points)
        wire['_component_ends'] = {endpoint:self.pin(endpoint) for endpoint in endpoints}
        for endpoint in endpoints:
            ref, number = endpoint.split(".")
            part = self.parts[ref]
            from .devices import native_order
            index = native_order(part["device"], part.get('native_definition')).index(number)
            if part["wire_keys"][index] is not None:
                raise CircuitError(f"Native pin {endpoint} already connected")
            if self.pin(endpoint) not in points:
                raise CircuitError(f"Native pin {endpoint} missing from path vertices")
            part["wire_keys"][index] = key
        self.objects.append(wire)

    def rail(self, key, endpoint, name, target_grid):
        ground = name == "GND"
        connection = [n*GRID for n in target_grid]
        origin = [connection[0], connection[1] + (-GRID if ground else GRID)]
        terminal = movable_copy(self.templates["$TERGROUND" if ground else "$TERPOWER"], origin)
        terminal["label"]["text"] = name
        terminal["wire_key"] = key
        self.objects.append(terminal)
        point = self.pin(endpoint)
        path = [point]
        if point[0] != connection[0] and point[1] != connection[1]:
            path.append([connection[0], point[1]])
        path.append(connection)
        self.wire(key, [endpoint], path)

    def join(self, key, endpoints, junction_grid, routes):
        if not 2 <= len(endpoints) <= 4 or len(routes) != len(endpoints):
            raise CircuitError("Junction requires two to four branches")
        origin = [n*GRID for n in junction_grid]
        junction = {"type": "junction", "origin": origin, "wire_keys": [None]*4}
        self.objects.append(junction)
        for index, (endpoint, route) in enumerate(zip(endpoints, routes)):
            branch = f"{key}_{index}"
            points = [[x*GRID, y*GRID] for x, y in route]
            if points[-1] != origin:
                raise CircuitError("Junction branch must end at junction")
            junction["wire_keys"][index] = branch
            self.wire(branch, [endpoint], points)


def led_test_experiment(template, output, reset_database=False):
    def transform(objects):
        b = Builder(objects)
        b.component("R1", "RES", [-20, 0], "330")
        b.component("D1", "LED-RED", [-10, 0])
        b.rail("supply", "R1.1", "VCC", [-26, 0])
        b.wire("series", ["R1.2", "D1.1"], [b.pin("R1.2"), [-13*GRID, 0], [-13*GRID, 2*GRID], b.pin("D1.1")])
        b.rail("ground", "D1.2", "GND", [-10, -5])
        return b.objects
    return rewrite_project(template, output, transform, reset_database=reset_database)
