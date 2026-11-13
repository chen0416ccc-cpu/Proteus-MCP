"""Portable previews and deterministic semantic execution plans."""

import html
import json
import zipfile
from pathlib import Path

from .catalog import catalog
from .model import CircuitError, atomic_write, digest, json_bytes, validate


def export_plan(store, project):
    value = store.read(project)
    check = validate(value)
    operations = [{"op": "place_component", "ref": ref, **part}
                  for ref, part in value["components"].items()]
    operations += [{"op": "connect_net", "name": name, "pins": pins}
                   for name, pins in value["nets"].items()]
    plan = {"schema_version": 1, "project": project, "revision": value["revision"],
            "circuit_sha256": digest(json_bytes(value)), "status": "planned",
            "native_applied": False, "validation": check, "operations": operations}
    path = store.folder(project) / "plan.json"
    atomic_write(path, json_bytes(plan))
    return {"path": str(path), **plan}


def export_netlist(store, project):
    value = store.read(project)
    result = {"format": "local-proteus-semantic-netlist-v1", "not_native_proteus": True,
              "components": value["components"], "nets": value["nets"],
              "power_rails": value["power_rails"]}
    path = store.folder(project) / "netlist.json"
    atomic_write(path, json_bytes(result))
    return {"path": str(path), "components": len(value["components"]), "nets": len(value["nets"])}


def export_svg(store, project):
    """Pin-accurate connectivity preview, not a screenshot or native Proteus design."""
    value = store.read(project)
    esc = lambda text: html.escape(str(text), quote=True)
    parts = value["components"]
    max_x = max((p["position"][0] for p in parts.values()), default=700) + 300
    max_y = max((p["position"][1] for p in parts.values()), default=700) + 160
    points, shapes = {}, []
    for ref, part in parts.items():
        x, y = part["position"]
        pins = catalog(part["device"], part.get('native_definition',{}).get('library_path'))["pins"]
        is_mcu = len(pins) > 2
        if is_mcu:
            ordered = sorted(pins, key=lambda n: int(n))
            height = 20 * ((len(ordered)+1)//2) + 30
            shapes.append(f'<rect x="{x}" y="{y}" width="180" height="{height}" rx="6"/>')
            shapes.append(f'<text x="{x+90}" y="{y+20}" text-anchor="middle">{esc(ref)} {esc(part["device"])}</text>')
            for index, number in enumerate(ordered):
                right = index >= len(ordered)//2
                px = x+200 if right else x-20
                py = y+45+(index % (len(ordered)//2))*20
                points[f"{ref}.{number}"] = (px, py)
                bx = x+180 if right else x
                anchor, tx = ("end", x+174) if right else ("start", x+6)
                shapes.append(f'<path d="M{px},{py} H{bx}"/><text x="{tx}" y="{py+4}" text-anchor="{anchor}">{esc(number)} {esc(pins[number])}</text>')
        else:
            points[f"{ref}.1"] = (x-45, y)
            points[f"{ref}.2"] = (x+45, y)
            device = part["device"]
            shapes.append(f'<path d="M{x-45},{y} H{x-20} M{x+20},{y} H{x+45}"/>')
            if device == "RES":
                shapes.append(f'<rect x="{x-20}" y="{y-8}" width="40" height="16"/>')
            elif device == "LED-RED":
                shapes.append(f'<path d="M{x-18},{y-12} L{x+10},{y} L{x-18},{y+12} Z M{x+10},{y-14} V{y+14}"/><text x="{x-43}" y="{y+19}">A</text><text x="{x+28}" y="{y+19}">K</text>')
            else:
                shapes.append(f'<rect x="{x-20}" y="{y-10}" width="40" height="20"/>')
            shapes.append(f'<text x="{x}" y="{y-21}" text-anchor="middle">{esc(ref)} {esc(part["properties"]["value"])}</text>')
    wires = []
    for index, (name, pins) in enumerate(value["nets"].items()):
        locations = [points[p] for p in pins if p in points]
        # Named terminals avoid ambiguous crossings in a compact connectivity preview.
        color = "#a22c25" if name == "VCC" else "#235b51"
        for x, y in locations:
            wires.append(f'<g stroke="{color}"><circle cx="{x}" cy="{y}" r="2" fill="{color}"/><text x="{x}" y="{y-5}" stroke="none" fill="{color}" font-size="9">{esc(name)}</text></g>')
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="-40 -20 {max_x+40} {max_y+80}">
<rect x="-40" y="-20" width="100%" height="100%" fill="#f8f5eb"/>
<text x="20" y="10" font-family="monospace" font-size="17">{esc(project)} | Connectivity Preview, NOT Proteus</text>
<g font-family="monospace" font-size="10" stroke="#263e3a" stroke-width="1.3" fill="none">{''.join(shapes)}</g>
<style>text{{fill:#182c2a;stroke:none}}</style>{''.join(wires)}
<text x="20" y="{max_y+30}" font-family="monospace" font-size="12">12 MHz / classic 12T 8051 | P1.0 to P1.7 | Active LOW | 4 x 50 ms</text>
</svg>'''
    path = store.folder(project) / "preview.svg"
    atomic_write(path, svg.encode())
    return {"path": str(path), "native_proteus": False, "kind": "named-net connectivity preview"}


def inspect_native(path):
    path = Path(path).resolve()
    if path.suffix.lower() != ".pdsprj":
        raise CircuitError("Expected .pdsprj")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > 1024 or sum(info.file_size for info in infos) > 64*1024*1024:
            raise CircuitError("Archive exceeds inspection limits")
        if "ROOT.DSN" not in archive.namelist():
            raise CircuitError("No ROOT.DSN in project")
        dsn = archive.read("ROOT.DSN")
        if not dsn.startswith(b"ISIS SCHEMATIC FILE"):
            raise CircuitError("Unknown DSN signature")
        entries = [{"name": info.filename, "bytes": info.file_size,
                    "sha256": digest(archive.read(info))} for info in infos]
    return {"path": str(path), "sha256": digest(path.read_bytes()), "entries": entries,
            "inspection": "container and signatures only", "connectivity_verified": False,
            "simulation_verified": False}
