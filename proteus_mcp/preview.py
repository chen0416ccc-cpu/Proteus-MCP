"""Vector preview reconstructed from native geometry, never a screen capture."""

import html
from pathlib import Path

from .model import CircuitError, atomic_write, digest
from .native9 import GRID, PIN_ORDER, pin_xy, sheet_dimensions
from .devices import native_order, definition
from .geometry import part_transform
import struct
from .native_backend import read_project, native_read
from .label_layout import text_style


def native_preview(path, output=None):
    path = Path(path).resolve(strict=True)
    target = Path(output).resolve() if output else path.with_suffix(".svg")
    if target.suffix.lower() != ".svg" or target.exists():
        raise CircuitError("Preview needs a new .svg output path")
    data, parsed = read_project(path)
    from .devices import cache_entries, parse_device
    entries, _ = cache_entries(data)
    for part in parsed['components']:
        if part['device'] not in PIN_ORDER:
            part['_embedded_definition'] = parse_device(entries[part['device']]['blob'], part['device'])
    points = [obj["origin"] for obj in parsed["objects"] if "origin" in obj]
    points += [point for obj in parsed["wires"] for point in obj["points"]]
    points += [p for part in parsed['components'] for number in
               ([pin['number'] for pin in part['_embedded_definition']['pins']] if '_embedded_definition' in part else native_order(part['device']))
               if (p := pin_xy(part, number)) is not None]
    width, height = sheet_dimensions(data)
    points += [[-width*GRID/2, -height*GRID/2], [width*GRID/2, height*GRID/2]]
    if not points:
        points = [[0, 0], [GRID*30, GRID*20]]
    scale = 12/GRID
    xy = lambda p: (round(p[0]*scale, 2), round(-p[1]*scale, 2))
    converted = [xy(p) for p in points]
    min_x, max_x = min(p[0] for p in converted)-80, max(p[0] for p in converted)+210
    min_y, max_y = min(p[1] for p in converted)-70, max(p[1] for p in converted)+80
    esc = lambda v: html.escape(str(v), quote=True)
    elements = []
    def draw_label(label, css):
        style = text_style(label)
        if not style['visible']:
            return
        x, y = xy(label['xy'])
        size = style['height'] * scale
        length = max(1, len(label['text'])) * style['width'] * scale
        rotate = f' transform="rotate({-style["rotation"]} {x} {y})"' if style['rotation'] else ''
        elements.append(f'<text x="{x}" y="{y}" class="{css}" style="font-size:{size}px"'
                        f' textLength="{length}" lengthAdjust="spacingAndGlyphs"{rotate}>{esc(label["text"])}</text>')
    fx, fy = xy([-width*GRID/2, height*GRID/2])
    elements.append(f'<rect x="{fx}" y="{fy}" width="{width*12}" height="{height*12}" class="frame"/>')
    for wire in parsed["wires"]:
        coordinates = " ".join(f"{x},{y}" for x, y in map(xy, wire["points"]))
        elements.append(f'<polyline points="{coordinates}" class="wire"/>')
        for label in wire["labels"]:
            draw_label(label, 'net')
    for node in parsed["junctions"]:
        x, y = xy(node["origin"])
        elements.append(f'<circle cx="{x}" cy="{y}" r="2.2" fill="#267647"/>')
    for terminal in parsed["terminals"]:
        x, y = xy(terminal["origin"])
        if terminal["device"] == "$TERPOWER":
            elements.append(f'<path d="M{x-5},{y+5} L{x},{y} L{x+5},{y+5}" class="wire"/>')
        else:
            elements.append(f'<path d="M{x-6},{y} H{x+6} M{x-4},{y+3} H{x+4} M{x-2},{y+6} H{x+2}" class="wire"/>')
        draw_label(terminal['label'], 'net')
    for part in parsed["components"]:
        world_x, world_y = xy(part['origin'])
        angle, mirror = struct.unpack('<hH', bytes.fromhex(part['transform']))
        local = {**part, 'origin':[0,0], 'transform':'00000000'}
        elements.append(f'<g transform="translate({world_x},{world_y}) rotate({-angle/10}) scale({-1 if mirror & 1 else 1},{-1 if mirror & 2 else 1})">')
        x, y = 0, 0
        device = part["device"]
        if device == "AT89C51":
            elements.append(f'<rect x="{x}" y="{y}" width="168" height="324" fill="#fff9db" class="body"/>')
            from .catalog import catalog
            names = catalog(device)["pins"]
            for number in PIN_ORDER[device]:
                pin = pin_xy(local, number)
                if pin is None:
                    continue
                px, py = xy(pin)
                left = px < x
                bx = x if left else x+168
                elements.append(f'<path d="M{px},{py} H{bx}" class="body"/>')
                tx, anchor = (x+5, "start") if left else (x+163, "end")
                elements.append(f'<text x="{tx}" y="{py+3}" text-anchor="{anchor}" class="pin">{esc(names[number])} ({number})</text>')
        elif device == "LED-RED":
            elements.append(f'<path d="M{x},{y-24} V{y-10} M{x-8},{y-10} L{x+8},{y-10} L{x},{y+9} Z M{x-9},{y+9} H{x+9} M{x},{y+9} V{y+24}" class="body"/>')
            elements.append(f'<path d="M{x+5},{y-8} l7,-7 m-2,0 h2 v2 M{x+8},{y-3} l7,-7 m-2,0 h2 v2" class="body"/>')
        elif device in ('RES','CAP','CAP-ELEC','CRYSTAL'):
            end = 36 if device == "RES" else 24
            elements.append(f'<path d="M{x-24},{y} H{x-12} M{x+12},{y} H{x+end}" class="body"/>')
            if device == "RES":
                elements.append(f'<rect x="{x-12}" y="{y-5}" width="24" height="10" class="body" fill="#fff9db"/>')
            elif device == "CRYSTAL":
                elements.append(f'<path d="M{x-11},{y-9} V{y+9} M{x+11},{y-9} V{y+9}" class="body"/><rect x="{x-6}" y="{y-9}" width="12" height="18" class="body" fill="#fff9db"/>')
            else:
                elements.append(f'<path d="M{x-4},{y-10} V{y+10} M{x+4},{y-10} V{y+10}" class="body"/>')
                if device == "CAP-ELEC":
                    elements.append(f'<text x="{x-18}" y="{y-10}">+</text>')
        elif device.startswith('7SEG-MPX4-'):
            elements.append('<rect x="-12" y="0" width="360" height="132" fill="#3c2425" stroke="#783b3d"/>')
            for offset in (24, 108, 192, 276):
                elements.append(f'<path d="M{offset},20 h48 m0,4 v36 m0,8 v36 m-48,4 h48 m-48,-4 v-36 m0,-8 v-36 m0,40 h48" fill="none" stroke="#ab494a" stroke-width="3"/>')
                elements.append(f'<circle cx="{offset+56}" cy="108" r="2" fill="#ab494a"/>')
            for pin in part['_embedded_definition']['pins']:
                if pin['hidden']:
                    continue
                px, py = xy(pin['xy']); ax, ay = xy(pin['anchor'])
                elements.append(f'<path d="M{px},{py} L{ax},{ay}" class="body"/>')
                elements.append(f'<text x="{ax}" y="{ay-3}" text-anchor="middle" class="display-pin">{esc(pin["number"])}</text>')
        elif device == 'BUTTON':
            elements.append('<path d="M-30,0 h12 M18,0 h12 M-18,-3 l30,-8 M0,-11 v-8" class="body"/>')
            elements.append('<circle cx="-18" cy="0" r="2" class="body"/><circle cx="18" cy="0" r="2" class="body"/>')
        elif device == 'BC547':
            elements.append('<path d="M-24,0 h12 M-12,-13 v26 M-12,-9 l24,-15 v-12 M-12,9 l24,15 v12 M5,23 l7,1 l-1,-7" class="body"/>')
        elif device == 'BUZZER':
            elements.append('<circle cx="12" cy="0" r="23" fill="#fff9db" class="body"/><path d="M-24,-12 h16 M-24,12 h16 M-3,-10 v20 M3,-13 v26 M29,-13 q12,13 0,26" class="body"/>')
        else:
            spec = part['_embedded_definition']
            visible_pins = [pin for pin in spec['pins'] if not pin['hidden']]
            anchors = [xy(pin['anchor']) for pin in visible_pins] or [(0,0)]
            left, right = min(p[0] for p in anchors), max(p[0] for p in anchors)
            top, bottom = min(p[1] for p in anchors), max(p[1] for p in anchors)
            if right-left < 24:
                left -= 12; right += 12
            if bottom-top < 24:
                top -= 12; bottom += 12
            elements.append(f'<rect x="{left}" y="{top}" width="{right-left}" height="{bottom-top}" class="body" style="stroke-dasharray:3 2"/>')
            for pin in visible_pins:
                px, py = xy(pin['xy']); ax, ay = xy(pin['anchor'])
                elements.append(f'<path d="M{px},{py} L{ax},{ay}" class="body"/><circle cx="{px}" cy="{py}" r="1.6" fill="#243653"/>')
                elements.append(f'<text x="{ax+3}" y="{ay-3}" class="pin">{esc(pin["number"])}:{esc(pin["name"])}</text>')
            elements.append(f'<text x="{left}" y="{top-17}">{esc(part["ref"])} {esc(device)}</text><text x="{left}" y="{top-5}" class="pin">pin geometry / generic body</text>')
        elements.append('</g>')
        for label in part['labels'][:2]:
            draw_label(label, 'component-label')
    readback = native_read(path)
    content = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x} {min_y-40} {max_x-min_x} {max_y-min_y+40}">
<title>{esc(path.name)} - Native geometry preview</title>
<desc>Reconstructed from ROOT.DSN components, pin locations, wires, junctions and labels. This is a vector preview, not a Proteus screenshot.</desc>
<style>text{{font:10px Consolas,monospace;fill:#222}}.pin{{font-size:8px}}.display-pin{{font-size:8px;fill:#f5cccc}}.component-label{{font-size:10px}}.net{{font-size:9px;fill:#175d39}}.wire{{fill:none;stroke:#267647;stroke-width:1.4}}.body{{stroke:#243653;stroke-width:1.3;fill:none}}.frame{{fill:none;stroke:#3167b5;stroke-width:1.5}}</style>
<rect x="{min_x}" y="{min_y-40}" width="{max_x-min_x}" height="{max_y-min_y+40}" fill="#fffdf3"/>
<text x="{min_x+15}" y="{min_y-20}" style="font-size:15px">{esc(path.name)} | native geometry preview</text>
{''.join(elements)}
<text x="{min_x+15}" y="{max_y-15}">{len(parsed['components'])} components / {len(readback['stored_connectivity'])} nets. Uncalibrated glyphs use a dashed generic body with real pin geometry.</text>
</svg>'''
    atomic_write(target, content.encode("utf-8"))
    return {"path": str(target), "source_sha256": digest(path.read_bytes()),
            "kind": "native_geometry_vector_preview", "screen_capture": False}
