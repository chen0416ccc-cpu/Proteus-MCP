"""Calibrated Proteus 9 component database, mirrored with ROOT.DSN."""

import copy
import struct

from .model import CircuitError
from .native9 import EMPTY_CDB, PIN_ORDER, Reader


def read_string(reader, size=2):
    at = reader.offset
    length = reader.integer(size)
    if size == 4:
        length -= 4
    if length < 0:
        raise CircuitError("Invalid CDB property length")
    if length > 128000:
        raise CircuitError("CDB string exceeds limit")
    if length > len(reader.data)-reader.offset:
        raise CircuitError(f"CDB string at {at:#x} has invalid length {length}")
    return reader.read(length).decode("utf-8")


def pack_string(text, size=2):
    encoded = text.encode("utf-8")
    return (len(encoded) + (4 if size == 4 else 0)).to_bytes(size, "little") + encoded


def read_database(data):
    if data[:95] != EMPTY_CDB[:95]:
        raise CircuitError("Uncalibrated native CDB root/sheet structure")
    r = Reader(data, 95)
    count = r.integer()
    if count > 10000:
        raise CircuitError("Too many CDB instances")
    instances = []
    for _ in range(count):
        header = r.read(16)
        ref = read_string(r)
        pins = r.integer()
        if pins > 1024:
            raise CircuitError("Too many CDB pins")
        mapping = [(read_string(r), read_string(r)) for _ in range(pins)]
        tail = r.read(12)
        instances.append({"header": header.hex(), "ref": ref, "pins": mapping, "tail": tail.hex()})
    header = r.read(15)
    if header != EMPTY_CDB[99:114]:
        raise CircuitError("Uncalibrated CDB property-table header")
    count = r.integer()
    if count > 10000:
        raise CircuitError("Too many CDB parts")
    parts = []
    for _ in range(count):
        record = {"header": r.read(20).hex()}
        for field in ("ref", "value", "device", "package"):
            record[field] = read_string(r)
        properties = read_string(r, 4)
        if not properties.endswith("\0"):
            raise CircuitError("CDB properties must be NUL terminated")
        record["properties"] = properties[:-1]
        parts.append(record)
    tail = r.read(len(data)-r.offset)
    if tail != b"\0\0\0\0":
        raise CircuitError("Uncalibrated CDB suffix")
    return {"instances": instances, "parts": parts}


def pack_database(database):
    result = bytearray(EMPTY_CDB[:95])
    result += struct.pack("<I", len(database["instances"]))
    for item in database["instances"]:
        result += bytes.fromhex(item["header"]) + pack_string(item["ref"]) + struct.pack("<I", len(item["pins"]))
        for name, number in item["pins"]:
            result += pack_string(name) + pack_string(number)
        result += bytes.fromhex(item["tail"])
    result += EMPTY_CDB[99:114] + struct.pack("<I", len(database["parts"]))
    for item in database["parts"]:
        result += bytes.fromhex(item["header"])
        for field in ("ref", "value", "device", "package"):
            result += pack_string(item[field])
        result += pack_string(item["properties"] + "\0", 4)
    result += b"\0\0\0\0"
    return bytes(result)


def mirror_database(data, objects, generate=False):
    source = read_database(data)
    if generate:
        templates = {item["device"]: item for item in source["parts"]}
        instances = {item["ref"]: item for item in source["instances"]}
        result = {"instances": [], "parts": []}
    else:
        result = copy.deepcopy(source)
    for obj in objects:
        if obj["type"] != "component":
            continue
        if generate:
            if obj["device"] not in templates:
                from .devices import selected_definition
                spec = selected_definition(obj)
                templates[obj['device']] = {'ref':obj['ref'],'device':obj['device'],
                    'package':spec['defaults'].get('PACKAGE',''), 'header':'00'*20,
                    'value':spec['defaults'].get('VALUE',obj['device']), 'properties':spec['properties']}
                instances[obj['ref']] = {'header':'00'*16,'ref':obj['ref'],
                    'pins':[(p['name'],p.get('native_number',p['number']) if obj['device'] in PIN_ORDER
                             else p['number']) for p in spec['pins']], 'tail':'00'*12}
            part = copy.deepcopy(templates[obj["device"]])
            instance = copy.deepcopy(instances[part["ref"]])
            identity = obj["identity"]
            instance["header"] = struct.pack("<IIII", identity, 1, 0, identity).hex()
            instance["tail"] = struct.pack("<III", 0, identity, 0xffffffff).hex()
            instance["ref"] = obj["ref"]
            part["header"] = struct.pack("<IIIII", identity, 1, 0, 0, 0).hex()
            result["instances"].append(instance)
            result["parts"].append(part)
        else:
            matches = [p for p in result["parts"] if int.from_bytes(bytes.fromhex(p["header"])[:4], "little") == obj["identity"]]
            if len(matches) != 1:
                raise CircuitError("Native CDB identity does not match DSN")
            part = matches[0]
            if part["device"] != obj["device"] or part["ref"] != obj["ref"]:
                raise CircuitError("Native CDB reference/device does not match DSN")
        part.update(ref=obj["ref"], value=obj["labels"][1]["text"], properties=obj["labels"][3]["text"])
    packed = pack_database(result)
    read_database(packed)
    return packed
