"""Direct, experimental Proteus 9 file operations. No desktop automation."""

import copy
import json
import os
import re
import time
import uuid
import zipfile
import struct
from pathlib import Path

from .firmware import inspect_hex
from .model import CircuitError, atomic_write, checked_properties, digest, json_bytes, validate
from .native9 import Builder, GRID, PIN_ORDER, inspect_components, rewrite_project
from .devices import native_order


CALIBRATION = Path(os.getenv('PROTEUS_MCP_CALIBRATION') or
                   Path(__file__).resolve().parents[1] / "calibration" / "10-terminal-wire.pdsprj")


def read_project(path):
    path = Path(path).resolve()
    if path.suffix.lower() != ".pdsprj":
        raise CircuitError("Expected .pdsprj")
    with zipfile.ZipFile(path) as archive:
        if sum(info.file_size for info in archive.infolist()) > 64*1024*1024:
            raise CircuitError("Native project exceeds 64 MiB")
        data = archive.read("ROOT.DSN")
    return data, inspect_components(data)


def connectivity(parsed, pin_orders=None, hidden_pins=None):
    """Decode stored pin references, without claiming a simulator or full ERC."""
    nets = {wire["start"]: {"pins": [], "terminals": [label["text"] for label in wire["labels"] if label["text"]]}
            for wire in parsed["wires"]}
    parents = {key: key for key in nets}

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for junction in parsed["junctions"]:
        keys = [key for key in junction["pointers"] if key]
        for key in keys[1:]:
            parents[root(key)] = root(keys[0])
    for part in parsed["components"]:
        order = pin_orders[part['ref']] if pin_orders is not None else native_order(part["device"], part.get('native_definition'))
        if order is None or len(order) != part["pin_count"]:
            raise CircuitError(f"No calibrated pin order for {part['device']}")
        for pin, pointer in zip(order, part["pointers"]):
            if pointer:
                nets[pointer]["pins"].append(part["ref"]+"."+pin)
    for terminal in parsed["terminals"]:
        if terminal["pointer"]:
            nets[terminal["pointer"]]["terminals"].append(terminal["label"]["text"])
    groups = {}
    for offset, net in nets.items():
        group = groups.setdefault(root(offset), {"pins": [], "terminals": []})
        group["pins"].extend(net["pins"])
        group["terminals"].extend(net["terminals"])
    merged = {}
    for offset, net in groups.items():
        names = set(net["terminals"])
        if len(names) > 1:
            raise CircuitError("A native wire joins distinct net labels or power-terminal names")
        name = next(iter(names)) if names else f"wire_{offset}"
        merged.setdefault(name, set()).update(net["pins"])
    # These two MCU pins are implicit supplies in the calibrated library symbol.
    for part in parsed["components"]:
        if part["device"] == "AT89C51":
            merged.setdefault("VCC", set()).add(part["ref"]+".40")
            merged.setdefault("GND", set()).add(part["ref"]+".20")
        elif hidden_pins is not None:
            for pin in hidden_pins.get(part['ref'],[]):
                merged.setdefault(pin['name'],set()).add(part['ref']+'.'+pin['number'])
        elif part['device'] not in PIN_ORDER:
            from .devices import selected_definition
            for pin in selected_definition(part)['pins']:
                if pin['hidden']:
                    merged.setdefault(pin['name'],set()).add(part['ref']+'.'+pin['number'])
    return {name: sorted(pins) for name, pins in merged.items()}


def label_nets(objects, model):
    """Give each signal tree its semantic name in a native wire LABEL record."""
    wires = {obj["key"]: obj for obj in objects if obj["type"] == "wire"}
    parents = {key: key for key in wires}

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for obj in objects:
        if obj["type"] == "junction":
            keys = [key for key in obj["wire_keys"] if key]
            for key in keys[1:]:
                parents[root(key)] = root(keys[0])
    pins = {obj["ref"]+"."+pin: key for obj in objects if obj["type"] == "component"
            for pin, key in zip(native_order(obj["device"], obj.get('native_definition')), obj["wire_keys"]) if key}
    # Terminal labels already use the calibrated native LABEL layout and font.
    style = next((obj["label"] for obj in objects if obj["type"] == "terminal"), None)
    if style is None:
        style = next(obj["labels"][0] for obj in objects if obj["type"] == "component")
    for name, endpoints in model["nets"].items():
        if name in ("VCC", "GND"):
            continue
        trees = {root(pins[pin]) for pin in endpoints if pin in pins}
        for key in trees:
            candidates = [(abs(a[0]-b[0]), str(k), index, k, a, b)
                          for k, w in wires.items() if root(k) == key
                          for index, (a, b) in enumerate(zip(w['points'], w['points'][1:])) if a[1] == b[1]]
            if candidates:
                _, _, _, selected, a, b = max(candidates)
                wire = wires[selected]
            else:
                wire = wires[key]
                a, b = wire['points'][:2]
            label = copy.deepcopy(style)
            label.update(text=name, xy=[(a[0]+b[0])//2-len(name)*GRID//4, (a[1]+b[1])//2-GRID])
            wire["labels"] = [label]
    return objects


def native_read(path):
    path = Path(path).resolve()
    data, parsed = read_project(path)
    parts = [{"ref": part["ref"], "device": part["device"],
              "identity": part["identity"], "value": part["labels"][1]["text"],
              "origin_grid": [n/GRID for n in part["origin"]],
              "rotation": (struct.unpack('<hH',bytes.fromhex(part['transform']))[0]/10)%360,
              "mirror": struct.unpack('<hH',bytes.fromhex(part['transform']))[1],
              "native_properties": part["labels"][3]["text"]}
             for part in parsed["components"]]
    from .database import read_database
    with zipfile.ZipFile(path) as archive:
        database = read_database(archive.read("ROOT.CDB"))
    cache = {part["ref"]: part for part in database["parts"]}
    instances = {part['ref']:part for part in database['instances']}
    if len(instances) != len(database['instances']) or len(cache) != len(database['parts']):
        raise CircuitError('Duplicate CDB reference')
    orders, hidden, package_devices, order_sources = {}, {}, {}, {}
    from .devices import cache_entries, parse_device, unit_view
    entries, _ = cache_entries(data)
    for part in parsed['components']:
        package_devices[part['ref']] = part['device']
        instance = instances.get(part['ref'])
        if instance is None or int.from_bytes(bytes.fromhex(instance['header'])[:4],'little') != part['identity']:
            raise CircuitError('DSN/CDB instance identity or pin count mismatch')
        mapping = instance['pins']
        order_sources[part['ref']] = 'native_CDB_instance'
        if not mapping and part['device'] not in PIN_ORDER:
            spec = parse_device(entries[part['device']]['blob'],part['device'],allow_unresolved_pins=True)
            if spec['animation_frames'] and not cache.get(part['ref'],{}).get('package'):
                mapping = [(p['name'],p['number']) for p in spec['pins']]
                order_sources[part['ref']] = 'hash_calibrated_animated_definition_no_package_CDB'
        if len(mapping) != part['pin_count']:
            raise CircuitError('DSN/CDB instance identity or pin count mismatch')
        orders[part['ref']] = [number for _,number in mapping]
        if part['device'] in PIN_ORDER:
            expected = native_order(part['device'])
            if len(expected) != len(orders[part['ref']]) or any(actual and actual != want for actual,want in zip(orders[part['ref']],expected)):
                raise CircuitError('Calibrated DSN/CDB pin numbering mismatch')
            orders[part['ref']] = expected
        if len(set(orders[part['ref']])) != part['pin_count']:
            raise CircuitError('Ambiguous CDB physical pin numbering')
        if part['device'] not in PIN_ORDER:
            # Disk connectivity uses CDB physical pin identities, not glyph
            # lengths. Do not invent geometry for native custom pin symbols.
            spec = parse_device(entries[part['device']]['blob'],part['device'],allow_unresolved_pins=True)
            if spec['pinout_name']:
                package_devices[part['ref']] = spec['pinout_name']
            byname = {p['name']:p for p in spec['pins']}
            hidden[part['ref']] = [{'name':name,'number':number} for name,number in mapping
                                   if name not in byname or byname[name]['hidden']]
    physical_refs = {part['ref'].split(':')[0] for part in parts}
    consistent = cache.keys() == physical_refs and len(instances) == len(parts) and all(
        part["ref"].split(':')[0] in cache and
        cache[part['ref'].split(':')[0]]['device'] in (part['device'],package_devices[part['ref']]) and
        part['value'] == cache[part['ref'].split(':')[0]]['value'] and
        property_map(part["native_properties"]) == property_map(cache[part["ref"].split(':')[0]]["properties"])
        for part in parts)
    from .native9 import sheet_dimensions
    wire_geometry = sorted(min(w['points'],w['points'][::-1]) for w in parsed['wires'])
    return {"path": str(path), "sha256": digest(path.read_bytes()), "components": parts,
            "wire_geometry":wire_geometry,
            "nonorthogonal_wire_segments":sum(a[0]!=b[0] and a[1]!=b[1]
                for w in parsed['wires'] for a,b in zip(w['points'],w['points'][1:])),
            "sheet_size_grid": sheet_dimensions(data),
            "database_consistent": consistent,
            "wires": len(parsed["wires"]), "terminals": len(parsed["terminals"]),
            "junctions": len(parsed["junctions"]),
            "stored_connectivity": connectivity(parsed,orders,hidden), "native_reopen_verified": False,
            "physical_packages":len(cache),'instance_pin_orders':orders,'instance_pin_order_sources':order_sources,
            'package_device_names':{ref:part['device'] for ref,part in cache.items()},
            'symbol_packaging_links':{part['ref']:{'symbol':part['device'],'pinout':package_devices[part['ref']]}
                                      for part in parts if part['device'] != package_devices[part['ref']]},
            "simulation_verified": False, "inspection": "calibrated native records and pin references"}


def property_map(text):
    # Native property assignment can save user fields as unbraced lines.
    # Compare those too; ignoring them would hide a real save mismatch.
    return dict(re.findall(r'(?m)^\{?([A-Za-z0-9_]+)=([^{}\r\n]*)\}?$',text))


def circuit_signature(readback):
    return {"sheet_size_grid": readback.get('sheet_size_grid'),
            "wire_geometry":readback.get('wire_geometry'),
            "components": {part["ref"]: {"device": part["device"], "value": part["value"],
                                           "properties": property_map(part["native_properties"]),
                                           "origin_grid": part["origin_grid"],
                                           "rotation": part.get('rotation',0), "mirror": part.get('mirror',0)}
                           for part in readback["components"]},
            "connections": sorted(sorted(pins) for pins in readback["stored_connectivity"].values())}


def set_properties(part, properties):
    embedded=part.get('_embedded_definition')
    if embedded:
        from .catalog import properties_from_definition
        props=checked_properties(part['device'],properties,allowed=properties_from_definition(embedded))
    else:
        props = checked_properties(part["device"], properties, part.get('native_definition',{}).get('library_path'))
    for key, value in props.items():
        if key == "value":
            part["labels"][1]["text"] = value
            if part["device"] != "CRYSTAL":
                continue
            if any(c in value for c in "{}\r\n"):
                raise CircuitError("Native crystal frequency contains a reserved character")
            native_key = "FREQ"
        else:
            native_key = {"clock_hz": "CLOCK", "program": "PROGRAM"}.get(key, key.upper())
        if any(c in str(value) for c in "{}\r\n"):
            raise CircuitError("Native property contains a reserved character")
        setting = "{"+native_key+"="+str(value)+"}"
        pattern = r"\{"+native_key+r"=[^}]*\}"
        text = part["labels"][3]["text"]
        part["labels"][3]["text"] = re.sub(pattern, lambda _: setting, text) if re.search(pattern, text) else text+setting+"\n"


def native_set_component(path, output, ref, properties, expected_sha256):
    path, output = Path(path).resolve(), Path(output).resolve()
    if digest(path.read_bytes()) != expected_sha256:
        raise CircuitError("NATIVE_SOURCE_CONFLICT: source SHA-256 changed")
    if path == output:
        raise CircuitError("Native edits require a new output path")
    data,parsed=read_project(path)
    from .devices import cache_entries,parse_device
    entries,_=cache_entries(data)

    def transform(objects):
        matches = [obj for obj in objects if obj.get("ref") == ref]
        if len(matches) != 1:
            raise CircuitError(f"Expected exactly one component {ref}")
        if matches[0]['device'] not in PIN_ORDER:
            matches[0]['_embedded_definition']=parse_device(entries[matches[0]['device']]['blob'],matches[0]['device'],allow_unresolved_pins=True)
        set_properties(matches[0], properties)
        return objects

    result = rewrite_project(path, output, transform, database_mode="mirror")
    return {**result, "sha256": digest(output.read_bytes()), "source_sha256": expected_sha256}


def new_export_folder(store, project):
    folder = store.folder(project) / "native" / (time.strftime("%Y%m%d-%H%M%S-")+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def native_create(store, project):
    store.read(project)
    with store.lock(project):
        output = new_export_folder(store, project) / "empty.pdsprj"
        result = rewrite_project(CALIBRATION, output, lambda objects: [], reset_database=True)
    return {**result, "components": 0, "sha256": digest(output.read_bytes()),
            "backend": "experimental_native_file_writer"}


def expected_led_topology():
    devices = {"U1": "AT89C51", "X1": "CRYSTAL", "C1": "CAP", "C2": "CAP",
               "C3": "CAP-ELEC", "C4": "CAP", "R9": "RES"}
    nets = {"XTAL1": ["U1.19", "X1.1", "C1.1"],
            "XTAL2": ["U1.18", "X1.2", "C2.1"], "RESET": ["U1.9", "C3.2", "R9.1"],
            "VCC": ["U1.40", "U1.31", "C3.1", "C4.1"],
            "GND": ["U1.20", "R9.2", "C1.2", "C2.2", "C4.2"]}
    for i in range(1, 9):
        devices[f"R{i}"] = "RES"
        devices[f"D{i}"] = "LED-RED"
        nets[f"LED{i}_K"] = [f"U1.{i}", f"D{i}.2"]
        nets[f"LED{i}_A"] = [f"R{i}.1", f"D{i}.1"]
        nets["VCC"].append(f"R{i}.2")
    return devices, nets


def draw_led_chaser(objects, model, firmware):
    b = Builder(objects)
    # A fresh database allocates references in object order for each prefix.
    initial = [("U1", [15, 30], 1), ("R1", [-13, 2], 2), ("D1", [-3, 0], 3),
               ("C1", [-10, 29], 4), ("C2", [2, 29], 5),
               ("C3", [-4, 22], 6), ("X1", [-4, 33], 7)]

    def add(ref, position, identity=None):
        source = model["components"][ref]
        part = b.component(ref, source["device"], position, identity=identity)
        props = dict(source["properties"])
        if ref == "U1":
            props["program"] = str(firmware)
        set_properties(part, props)

    for ref, position, identity in initial:
        add(ref, position, identity)
    for i in range(2, 9):
        add(f"R{i}", [-13, 2-6*(i-1)])
        add(f"D{i}", [-3, -6*(i-1)])
    for ref, position in (("R9", [6, 22]), ("C4", [33, 31])):
        add(ref, position)

    def route(key, endpoints, grids):
        b.wire(key, endpoints, [[x*GRID, y*GRID] for x, y in grids])

    for i in range(1, 9):
        row = i-1
        # R.2 is the supply end in the semantic model. Route R.1 around
        # the resistor so its calibrated default orientation can be retained.
        b.rail(f"vcc_{i}", f"R{i}.2", "VCC", [-8, 4-6*row])
        route(f"anode_{i}", [f"R{i}.1", f"D{i}.1"],
              [[-15, 2-6*row], [-17, 2-6*row], [-17, 0-6*row],
               [-5, 0-6*row], [-5, 2-6*row], [-3, 2-6*row]])
        route(f"cathode_{i}", [f"U1.{i}", f"D{i}.2"],
              [[13, 11-row], [5+row, 11-row], [5+row, -2-6*row], [-3, -2-6*row]])
    b.rail("ea", "U1.31", "VCC", [8, 15])
    b.rail("reset_vcc", "C3.1", "VCC", [-10, 22])
    b.join("reset", ["C3.2", "R9.1", "U1.9"], [2, 22],
           [[[-2, 22], [2, 22]], [[4, 22], [2, 22]],
            [[13, 22], [11, 22], [11, 24], [2, 24], [2, 22]]])
    b.rail("reset_ground", "R9.2", "GND", [9, 19])
    b.join("xtal1", ["C1.1", "X1.1", "U1.19"], [-12, 33],
           [[[-12, 29], [-12, 33]], [[-6, 33], [-12, 33]],
            [[13, 29], [11, 29], [11, 35], [-12, 35], [-12, 33]]])
    b.join("xtal2", ["X1.2", "C2.1", "U1.18"], [0, 27],
           [[[-2, 33], [-1, 33], [-1, 27], [0, 27]], [[0, 29], [0, 27]],
            [[13, 26], [0, 26], [0, 27]]])
    b.rail("c1_ground", "C1.2", "GND", [-8, 27])
    b.rail("c2_ground", "C2.2", "GND", [4, 27])
    b.rail("bypass_vcc", "C4.1", "VCC", [31, 34])
    b.rail("bypass_ground", "C4.2", "GND", [35, 28])
    return b.objects


def native_export_led_chaser(store, project):
    return native_export(store, project, routing='schematic', _led_only=True)


def native_export(store, project, layout="auto", routing="auto", placements=None, sheet_size=None, wire_plan=None, _led_only=False):
    """Export the calibrated device subset and arbitrary validated topology."""
    from .routing import route_model
    if sheet_size is not None:
        from .native9 import checked_sheet_size
        sheet_size = checked_sheet_size(sheet_size)
    with store.lock(project):
        model = store.read(project)
        if _led_only:
            devices, nets = expected_led_topology()
            if {ref: part['device'] for ref, part in model['components'].items()} != devices:
                raise CircuitError('This experimental native layout requires the example_led_chaser device set')
            if {name: sorted(pins) for name,pins in model['nets'].items()} != {name: sorted(pins) for name,pins in nets.items()}:
                raise CircuitError('This experimental native layout requires the example_led_chaser topology')
        if sheet_size is None and placements is None and layout == 'auto' and routing in ('auto', 'schematic') and wire_plan is None:
            from .led_layout import matches, SHEET_SIZE
            if matches(model):
                sheet_size = list(SHEET_SIZE)
        for part in model['components'].values():
            if part['device'] not in PIN_ORDER:
                provenance = part.get('native_definition')
                if not provenance or digest(Path(provenance['library_path']).read_bytes()) != provenance['library_sha256']:
                    raise CircuitError('NATIVE_LIBRARY_CHANGED: recreate the component from the current library')
        check = validate(model)
        if not check["ok"]:
            raise CircuitError("Circuit validation failed: " + "; ".join(check["errors"]))
        folder = store.folder(project)
        firmware, report = None, None
        if any(p["device"] == "AT89C51" for p in model["components"].values()):
            report = json.loads((folder/"latest-build.json").read_text(encoding="utf-8"))
            if report.get("status") != "compiled" or digest((folder/"main.c").read_bytes()) != report.get("source_sha256"):
                raise CircuitError("Build the current C51 source before native export")
            source_hex = Path(report["hex_path"])
            if inspect_hex(source_hex)["sha256"] != report["hex"]["sha256"]:
                raise CircuitError("Compiled HEX changed after build")
        target = new_export_folder(store, project)
        if report:
            firmware = target/"firmware.hex"
            atomic_write(firmware, source_hex.read_bytes())
            atomic_write(target/"main.c", (folder/"main.c").read_bytes())
        routing_report = {}

        def transform(objects):
            if wire_plan is not None:
                from .orderly import route_plan
                routed,details=route_plan(objects,model,firmware,placements,layout,sheet_size,wire_plan)
            else:
                routed, details = route_model(objects, model, firmware, placements, layout, routing, sheet_size)
            routing_report.update(details)
            if wire_plan is None and details.get('algorithm') != 'explicit_ordered_channel_plan':
                routed = label_nets(routed, model)
            from .schematic import bounds_report
            audit = bounds_report(routed, sheet_size or [100, 70])
            if not audit['all_geometry_inside_frame']:
                raise CircuitError('SCHEMATIC_LABEL_OUTSIDE_SHEET: '+str(audit))
            routing_report['sheet'] = audit
            return routed

        output = target/(project+".pdsprj")
        result = rewrite_project(CALIBRATION, output, transform, database_mode="generate", sheet_size=sheet_size)
        readback = native_read(output)
        if {frozenset(pins) for pins in readback["stored_connectivity"].values()} != {frozenset(pins) for pins in model["nets"].values()}:
            raise CircuitError("Native stored connectivity differs from model")
        if not readback["database_consistent"]:
            raise CircuitError("Generated CDB does not match DSN")
        manifest = {**result, "backend": "calibrated_native_file_writer", "revision": model["revision"],
                    "circuit_sha256": digest(json_bytes(model)), "sha256": readback["sha256"],
                    "components": len(readback["components"]), "stored_nets": len(readback["stored_connectivity"]),
                    "wires": readback["wires"], "junctions": readback["junctions"],
                    "stored_pin_references_match_model": True, "database_consistent": True,
                    "routing": routing_report, "desktop_input": False,
                    "firmware_path": str(firmware) if firmware else None,
                    "applied_to": "new_native_file", "live_session_updated": False}
        atomic_write(target/"manifest.json", json_bytes(manifest))
        return manifest
