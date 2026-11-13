"""Versioned local circuit model. This is not a Proteus DSN writer."""

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .catalog import catalog, pin_number


class CircuitError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".proteus-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def identifier(value, label="identifier"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value):
        raise CircuitError(f"Invalid {label}: ASCII letters, numbers, underscore only")
    return value


def point(value):
    if not isinstance(value, list) or len(value) != 2 or any(type(v) is not int or abs(v) > 100000 for v in value):
        raise CircuitError("position must be [x, y], integers within +/-100000")
    return value


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, project):
        identifier(project, "project")
        path = (self.root / project).resolve()
        if path.parent != self.root:
            raise CircuitError("Project escapes workspace")
        return path

    def read(self, project):
        path = self.folder(project) / "circuit.json"
        if not path.is_file():
            raise CircuitError(f"Project not found: {project}")
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("schema_version") != 1:
            raise CircuitError("Unsupported circuit schema")
        return result

    @contextmanager
    def lock(self, project):
        path = self.folder(project) / ".lock"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            raise CircuitError("Project is busy; inspect stale lock instead of retrying writes") from None
        try:
            os.close(fd)
            yield
        finally:
            path.unlink()

    def create(self, project):
        folder = self.folder(project)
        folder.mkdir(exist_ok=False)
        value = {"schema_version": 1, "project": project, "revision": 0,
                 "components": {}, "nets": {}, "power_rails": {"VCC": 5.0, "GND": 0.0},
                 "native_status": "not_applied"}
        atomic_write(folder / "circuit.json", json_bytes(value))
        return value

    def mutate(self, project, expected_revision, function):
        with self.lock(project):
            value = self.read(project)
            if type(expected_revision) is not int or expected_revision != value["revision"]:
                raise CircuitError(f"REVISION_CONFLICT: current revision={value['revision']}")
            function(value)
            value["revision"] += 1
            value["native_status"] = "not_applied"
            atomic_write(self.folder(project) / "circuit.json", json_bytes(value))
        return value

    def add(self, project, expected_revision, ref, device, position, properties=None, rotation=0, mirror=0, library_path=None):
        from .geometry import orientation
        orientation(rotation, mirror)
        identifier(ref, "reference")
        definition = catalog(device, library_path)
        point(position)
        props = checked_properties(device, properties or {}, library_path)

        def change(value):
            if ref in value["components"]:
                raise CircuitError(f"Duplicate reference: {ref}")
            value["components"][ref] = {"device": device, "position": position,
                                        "properties": definition["properties"] | props,
                                        "rotation": rotation, "mirror": mirror}
            if 'native_definition' in definition:
                value['components'][ref]['native_definition'] = definition['native_definition']
        return self.mutate(project, expected_revision, change)

    def set_component(self, project, expected_revision, ref, properties, position=None, rotation=None, mirror=None):
        def change(value):
            if ref not in value["components"]:
                raise CircuitError(f"Component not found: {ref}")
            part = value["components"][ref]
            part["properties"].update(checked_properties(part["device"], properties, part.get('native_definition',{}).get('library_path')))
            if position is not None:
                part["position"] = point(position)
            from .geometry import orientation
            orientation(part.get('rotation',0) if rotation is None else rotation,
                        part.get('mirror',0) if mirror is None else mirror)
            if rotation is not None:
                part['rotation'] = rotation
            if mirror is not None:
                part['mirror'] = mirror
        return self.mutate(project, expected_revision, change)

    def connect(self, project, expected_revision, name, pins):
        identifier(name, "net name")
        if not isinstance(pins, list) or len(pins) < 2:
            raise CircuitError("At least two pins are required")

        def change(value):
            normalized = sorted(set(endpoint(value, pin) for pin in pins))
            if len(normalized) < 2:
                raise CircuitError("At least two distinct pins are required")
            for other, members in value["nets"].items():
                if other != name and set(members).intersection(normalized):
                    raise CircuitError(f"Pin already belongs to net {other}; explicit net merge required")
            value["nets"][name] = sorted(set(value["nets"].get(name, []) + normalized))
        return self.mutate(project, expected_revision, change)

    def edit(self, project, expected_revision, operations):
        """Apply an entire edit transaction, or leave the revision untouched."""
        if not isinstance(operations, list) or not 1 <= len(operations) <= 1000:
            raise CircuitError("operations must contain 1..1000 edits")

        def change(value):
            for op in operations:
                if not isinstance(op, dict):
                    raise CircuitError("Each edit must be an object")
                kind = op.get("op")
                fields = {"add": {"op", "ref", "device", "position", "properties", "rotation", "mirror", "library_path"},
                          "set": {"op", "ref", "position", "properties", "rotation", "mirror"},
                          "remove": {"op", "ref"}, "connect": {"op", "name", "pins"},
                          "disconnect": {"op", "name", "pins"}, "merge_nets": {"op", "name", "nets"}}
                if kind not in fields or op.keys()-fields[kind]:
                    raise CircuitError("Unknown edit operation or field")
                if kind == "add":
                    ref = identifier(op["ref"], "reference")
                    if ref in value["components"]:
                        raise CircuitError("Duplicate reference: " + ref)
                    device = op["device"]
                    definition = catalog(device, op.get('library_path'))
                    value["components"][ref] = {"device": device, "position": point(op["position"]),
                        "properties": definition["properties"] | checked_properties(device, op.get("properties", {}), op.get('library_path'))}
                    from .geometry import orientation
                    orientation(op.get('rotation',0),op.get('mirror',0))
                    value['components'][ref].update(rotation=op.get('rotation',0),mirror=op.get('mirror',0))
                    if 'native_definition' in definition:
                        value['components'][ref]['native_definition'] = definition['native_definition']
                elif kind in ("set", "remove"):
                    ref = op["ref"]
                    if ref not in value["components"]:
                        raise CircuitError("Component not found: " + ref)
                    if kind == "set":
                        part = value["components"][ref]
                        from .geometry import orientation
                        orientation(op.get('rotation',part.get('rotation',0)),op.get('mirror',part.get('mirror',0)))
                        for field in ('rotation','mirror'):
                            if field in op:
                                part[field] = op[field]
                        part["properties"].update(checked_properties(part["device"], op.get("properties", {}), part.get('native_definition',{}).get('library_path')))
                        if "position" in op:
                            part["position"] = point(op["position"])
                    else:
                        del value["components"][ref]
                        value["nets"] = {name: [p for p in pins if not p.startswith(ref+".")]
                                         for name, pins in value["nets"].items()}
                        value["nets"] = {name: pins for name, pins in value["nets"].items() if len(pins) >= 2}
                elif kind == "merge_nets":
                    name = identifier(op["name"], "net name")
                    sources = op["nets"]
                    if not isinstance(sources, list) or len(set(sources)) < 2 or any(n not in value["nets"] for n in sources):
                        raise CircuitError("merge_nets requires at least two existing nets")
                    if name in value["nets"] and name not in sources:
                        raise CircuitError("Target net exists; include it in nets")
                    if "VCC" in sources and "GND" in sources:
                        raise CircuitError("Power short: cannot merge VCC and GND")
                    pins = sorted({pin for source in sources for pin in value["nets"][source]})
                    for source in sources:
                        del value["nets"][source]
                    value["nets"][name] = pins
                else:
                    name = identifier(op["name"], "net name")
                    if not isinstance(op["pins"], list) or not op["pins"]:
                        raise CircuitError("pins must be a nonempty array")
                    pins = {endpoint(value, pin) for pin in op["pins"]}
                    if kind == "connect":
                        for other, members in value["nets"].items():
                            if other != name and pins.intersection(members):
                                raise CircuitError("Pin already belongs to net " + other)
                        pins.update(value["nets"].get(name, []))
                        if len(pins) < 2:
                            raise CircuitError("At least two distinct pins are required")
                        value["nets"][name] = sorted(pins)
                    else:
                        if name not in value["nets"] or not pins.issubset(value["nets"][name]):
                            raise CircuitError("Pins are not connected to the named net")
                        remaining = set(value["nets"][name])-pins
                        if len(remaining) < 2:
                            del value["nets"][name]
                        else:
                            value["nets"][name] = sorted(remaining)
        return self.mutate(project, expected_revision, change)


def checked_properties(device, properties, library_path=None, allowed=None):
    if not isinstance(properties, dict):
        raise CircuitError("properties must be an object")
    allowed = catalog(device, library_path)["properties"] if allowed is None else allowed
    if properties.keys() - allowed.keys():
        raise CircuitError(f"Unknown properties: {sorted(properties.keys() - allowed.keys())}")
    for key, value in properties.items():
        if key == "clock_hz":
            if type(value) is not int or not 1 <= value <= 100000000:
                raise CircuitError("clock_hz must be an integer within 1..100000000")
        elif not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value):
            raise CircuitError(f"Invalid property {key}")
    return properties.copy()


def endpoint(value, text):
    if not isinstance(text, str) or "." not in text:
        raise CircuitError("Pin must be REF.PIN, e.g. U1.P1.0 or D1.A")
    ref, pin = text.split(".", 1)
    if ref not in value["components"]:
        raise CircuitError(f"Component not found: {ref}")
    part = value['components'][ref]
    return f"{ref}.{pin_number(part['device'], pin, part.get('native_definition',{}).get('library_path'))}"


def validate(value):
    errors, warnings = [], []
    occupied = {}
    for name, pins in value["nets"].items():
        for pin in pins:
            try:
                canonical = endpoint(value, pin)
                if canonical in occupied and occupied[canonical] != name:
                    errors.append(f"Pin {pin} belongs to multiple nets")
                occupied[canonical] = name
            except ValueError as error:
                errors.append(str(error))
    if "VCC" in value["nets"] and "GND" in value["nets"]:
        if set(value["nets"]["VCC"]) & set(value["nets"]["GND"]):
            errors.append("Power short: VCC/GND")
    for ref, part in value["components"].items():
        device = part["device"]
        if device in ("AT89C51", "80C51"):
            for number, required_net in (("40", "VCC"), ("20", "GND"), ("31", "VCC")):
                if occupied.get(f"{ref}.{number}") != required_net:
                    errors.append(f"{ref}.{number} must be connected to {required_net}")
            if f"{ref}.9" not in occupied:
                errors.append(f"{ref}.RST is floating")
        elif device in ("RES", "LED-RED", "CAP", "CAP-ELEC", "CRYSTAL"):
            for number in catalog(device)["pins"]:
                if f"{ref}.{number}" not in occupied:
                    errors.append(f"Unconnected pin: {ref}.{number}")
    warnings.append("Static catalog checks only; not full ERC, Proteus simulation, or hardware verification")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "components": len(value["components"]), "nets": len(value["nets"]),
            "native_status": value["native_status"]}
