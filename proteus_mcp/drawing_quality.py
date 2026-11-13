"""Shared, bounded schematic checks; never certify native font rasterization."""

import math
from collections import defaultdict

from .label_layout import text_style
from .model import CircuitError
from .native9 import GRID


def intersects(a, b, clearance=0):
    return (max(a[0], b[0]) < min(a[2], b[2])+clearance and
            max(a[1], b[1]) < min(a[3], b[3])+clearance)


def label_box(label):
    """Reserve the full string, not just its anchor, with a glyph safety margin."""
    if not label.get('text'):
        return None
    style = text_style(label)
    if not style['visible']:
        return None
    lines = label['text'].splitlines() or ['']
    units = max(sum(1 if ord(char) < 128 else 2 for char in line) for line in lines)
    width = math.ceil(units*(style['width']+max(0, style['spacing']))*1.2+GRID*.4)
    height = math.ceil(style['height']*max(1, len(lines))*1.2+GRID*.25)
    x, y = label['xy']
    hjust = style['justification'] & 3
    left = -width if hjust == 2 else -width//2 if hjust == 1 else 0
    # Vertical font baselines differ between inherited native styles. Cover
    # both sides rather than claiming that an anchor is a complete text bound.
    corners = [(left, -height), (left+width, -height),
               (left, height), (left+width, height)]
    from .geometry import transform_xy
    if style['rotation'] not in (0, 90, 180, 270):
        raise CircuitError('TEXT_GEOMETRY_NOT_CALIBRATED: non-quadrant text')
    corners = [transform_xy(point, round(style['rotation']*10), style['mirror']) for point in corners]
    return (x+min(p[0] for p in corners), y+min(p[1] for p in corners),
            x+max(p[0] for p in corners), y+max(p[1] for p in corners))


def labels(objects):
    for obj in objects:
        if obj['type'] == 'component':
            for index, label in enumerate(obj['labels'][:2]):
                yield (obj['ref'], index), label, obj
        elif obj['type'] == 'terminal':
            yield ('terminal', tuple(obj['origin'])), obj['label'], obj
        elif obj['type'] == 'wire':
            for index, label in enumerate(obj['labels']):
                yield ('wire', obj.get('key', obj.get('start')), index), label, obj


def segment_hits_box(a, b, box, boundary=False):
    inside = (lambda lo, value, hi: lo <= value <= hi) if boundary else (lambda lo, value, hi: lo < value < hi)
    overlap = (lambda lo, hi: lo <= hi) if boundary else (lambda lo, hi: lo < hi)
    if a[0] == b[0]:
        return inside(box[0], a[0], box[2]) and overlap(max(min(a[1], b[1]), box[1]), min(max(a[1], b[1]), box[3]))
    if a[1] == b[1]:
        return inside(box[1], a[1], box[3]) and overlap(max(min(a[0], b[0]), box[0]), min(max(a[0], b[0]), box[2]))
    return intersects((min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])), box)


def symbol_boxes(objects):
    from .geometry import part_transform
    from .schematic import body_box
    for index, obj in enumerate(objects):
        if obj['type'] == 'component':
            yield obj['ref'], body_box(obj)
        elif obj['type'] == 'terminal':
            corners = [part_transform(obj, [x, y]) for x in (-GRID, GRID) for y in (-GRID, GRID)]
            yield 'terminal:'+str(index), (min(p[0] for p in corners), min(p[1] for p in corners),
                                          max(p[0] for p in corners), max(p[1] for p in corners))


def fit_labels(objects, sheet_size, locked=()):
    """Move visible captions locally; do not shrink, truncate or hide values."""
    frame = [-sheet_size[0]*GRID//2+GRID, -sheet_size[1]*GRID//2+GRID,
             sheet_size[0]*GRID//2-GRID, sheet_size[1]*GRID//2-GRID]
    bodies = [box for _, box in symbol_boxes(objects)]
    wires = [obj for obj in objects if obj['type'] == 'wire']
    entries = [(key, label, obj) for key, label, obj in labels(objects) if label_box(label)]
    entries.sort(key=lambda item: (item[0] not in locked, -len(item[1]['text']), str(item[0])))
    placed, moved = [], 0
    for key, label, obj in entries:
        original = list(label['xy'])
        candidates = [original]
        for radius in range(1, 1 if key in locked else 13):
            step = GRID//2
            offsets = [(0, radius), (0, -radius), (-radius, 0), (radius, 0)]
            offsets += [(x, y) for x in (-radius, radius) for y in range(-radius, radius+1, 2)]
            offsets += [(x, y) for y in (-radius, radius) for x in range(-radius+2, radius, 2)]
            candidates.extend([original[0]+dx*step, original[1]+dy*step] for dx, dy in offsets)
        selected = None
        for position in candidates:
            label['xy'] = position
            box = label_box(label)
            if box[0] < frame[0] or box[1] < frame[1] or box[2] > frame[2] or box[3] > frame[3]:
                continue
            if any(intersects(box, other, GRID//4) for other in (*bodies, *placed)):
                continue
            if any(segment_hits_box(a, b, box) for wire in wires if wire is not obj
                   for a, b in zip(wire['points'], wire['points'][1:])):
                continue
            selected = box
            break
        if selected is None:
            label['xy'] = original
            raise CircuitError('LABEL_SPACE_EXHAUSTED: '+str(key)+'; move components or enlarge the sheet')
        moved += label['xy'] != original
        placed.append(selected)
    return {'visible_labels': len(placed), 'moved_labels': moved,
            'text_bounds': 'conservative_full_string_estimates_not_native_font_raster'}


def physical_nets(objects, model, continuous=True):
    """Check physical wire trees independently of electrically aliased labels."""
    from .devices import native_order
    from .native9 import pin_xy
    from .geometry import part_transform
    refs = [obj['ref'] for obj in objects if obj['type'] == 'component']
    if len(set(refs)) != len(refs) or set(refs) != model['components'].keys():
        raise CircuitError('QUALITY_COMPONENT_SET_MISMATCH')
    wires = {}
    for obj in objects:
        if obj['type'] != 'wire':
            continue
        key = obj.get('key', obj.get('start'))
        if key is None or key == 0 or key in wires:
            raise CircuitError('QUALITY_WIRE_ID_MISSING_OR_DUPLICATED')
        if len(obj['points']) < 2:
            raise CircuitError('QUALITY_WIRE_POINTS_MISSING')
        wires[key] = obj
    parents = {key: key for key in wires}
    ends = defaultdict(list)

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for obj in objects:
        if obj['type'] != 'junction':
            continue
        keys = [key for key in obj.get('wire_keys', obj.get('pointers', [])) if key is not None and key != 0]
        if not keys:
            raise CircuitError('QUALITY_UNUSED_JUNCTION')
        if any(key not in wires for key in keys):
            raise CircuitError('QUALITY_DANGLING_JUNCTION')
        for key in keys[1:]:
            parents[root(key)] = root(keys[0])
    pins, visible, names = {}, set(), defaultdict(set)
    for part in objects:
        if part['type'] in ('terminal', 'junction'):
            if part['type'] == 'terminal':
                keys = [part.get('wire_key', part.get('pointer'))]
                point = part_transform(part, [0, GRID if part['device'] == '$TERGROUND' else -GRID])
            else:
                keys = part.get('wire_keys', part.get('pointers', []))
                point = part['origin']
            for key in keys:
                if key is None or key == 0:
                    continue
                if key not in wires:
                    raise CircuitError('QUALITY_DANGLING_NODE')
                ends[key].append(list(point))
                if part['type'] == 'terminal':
                    names[root(key)].add(part['label']['text'])
            continue
        if part['type'] != 'component':
            continue
        order = native_order(part['device'], part.get('native_definition'))
        links = part.get('wire_keys', part.get('pointers', []))
        if len(order) != len(links):
            raise CircuitError('QUALITY_PIN_COUNT_MISMATCH: '+part['ref'])
        for number, key in zip(order, links):
            endpoint = part['ref']+'.'+number
            point = pin_xy(part, number)
            if point is not None:
                visible.add(endpoint)
            if key is None or key == 0:
                continue
            if key not in wires or point is None:
                raise CircuitError('QUALITY_DANGLING_PIN: '+endpoint)
            ends[key].append(point)
            pins[endpoint] = root(key)
    expected = {pin for endpoints in model['nets'].values() for pin in endpoints}
    missing = (expected & visible)-pins.keys()
    if missing:
        raise CircuitError('QUALITY_MISSING_PIN_WIRE: '+', '.join(sorted(missing)))
    if pins.keys()-expected:
        raise CircuitError('QUALITY_UNMODELED_PIN_WIRE: '+', '.join(sorted(pins.keys()-expected)))
    for key, wire in wires.items():
        if len(ends[key]) != 2 or sorted(ends[key]) != sorted([wire['points'][0], wire['points'][-1]]):
            raise CircuitError('QUALITY_WIRE_ENDPOINT_MISMATCH: '+str(key))
        for label in wire.get('labels', []):
            if label['text']:
                names[root(key)].add(label['text'])
    for name, endpoints in model['nets'].items():
        connected = [pin for pin in endpoints if pin in pins]
        if continuous and name not in ('VCC', 'GND') and len({pins[pin] for pin in connected}) > 1:
            raise CircuitError('QUALITY_DISCONNECTED_SIGNAL: '+name)
        for pin in connected:
            names[pins[pin]].add(name)
    if any(len(values) > 1 for values in names.values()):
        raise CircuitError('QUALITY_PHYSICAL_SHORT: '+str(dict(names)))
    if any(not names[root(key)] for key in wires):
        raise CircuitError('QUALITY_UNMODELED_WIRE')
    return {key: next(iter(names[root(key)])) for key in wires}


def drawing_report(objects, model, sheet_size, continuous=True):
    from .schematic import STEP, audit_crossings, body_box, bounds_report
    from .orderly import dense
    issues = []
    bodies = list(symbol_boxes(objects))
    component_bodies = [(obj['ref'], body_box(obj)) for obj in objects if obj['type'] == 'component']
    wires = [obj for obj in objects if obj['type'] == 'wire']
    for index, (ref, box) in enumerate(bodies):
        for other, other_box in bodies[index+1:]:
            if intersects(box, other_box):
                issues.append({'code': 'BODY_OVERLAP', 'refs': [ref, other]})
    text = [(key, label_box(label), obj) for key, label, obj in labels(objects) if label_box(label)]
    for index, (key, box, owner) in enumerate(text):
        for other, other_box, _ in text[index+1:]:
            if intersects(box, other_box):
                issues.append({'code': 'TEXT_OVERLAP', 'labels': [key, other]})
        for ref, body in bodies:
            if intersects(box, body):
                issues.append({'code': 'TEXT_BODY_OVERLAP', 'label': key, 'ref': ref})
        if any(segment_hits_box(a, b, box) for wire in wires if wire is not owner
               for a, b in zip(wire['points'], wire['points'][1:])):
            issues.append({'code': 'TEXT_WIRE_OVERLAP', 'label': key})
    paths, bends, length, detours = defaultdict(list), 0, 0, []
    try:
        names = physical_nets(objects, model, continuous)
        for wire in wires:
            points = wire['points']
            if any(v % STEP for point in points for v in point):
                raise CircuitError('QUALITY_OFF_HALF_GRID')
            paths[names[wire.get('key', wire.get('start'))]].append(dense(points))
            bends += max(0, len(points)-2)
            path_length = sum(abs(a[0]-b[0])+abs(a[1]-b[1]) for a, b in zip(points, points[1:]))
            direct = abs(points[0][0]-points[-1][0])+abs(points[0][1]-points[-1][1])
            length += path_length
            if direct >= 8*GRID:
                detours.append(path_length/direct)
            for ref, box in component_bodies:
                if any(segment_hits_box(a, b, box, boundary=True) for a, b in zip(points, points[1:])):
                    issues.append({'code': 'WIRE_BODY_CONTACT', 'ref': ref})
        crossings = audit_crossings(paths)
    except CircuitError as error:
        crossings = None
        issues.append({'code': 'WIRE_INTEGRITY', 'reason': str(error)})
    bounds = bounds_report(objects, sheet_size)
    if not bounds['all_geometry_inside_frame']:
        issues.append({'code': 'OUTSIDE_SHEET', 'details': bounds})
    signal_count = sum(name not in ('VCC', 'GND') for name in model['nets'])
    if crossings is not None and crossings > max(4, signal_count*2):
        issues.append({'code': 'EXCESSIVE_CROSSINGS', 'count': crossings})
    if detours and max(detours) > 8:
        issues.append({'code': 'EXCESSIVE_DETOUR', 'ratio': round(max(detours), 2)})
    return {'passed': not issues, 'issues': issues, 'sheet': bounds,
            'metrics': {'wires': len(wires), 'visible_labels': len(text), 'crossings': crossings,
                        'bends': bends, 'wire_length_grid': length/GRID,
                        'max_detour_ratio': round(max(detours, default=1), 2)},
            'scope': 'physical signal continuity, calibrated bodies, orthogonal contacts, estimated full text bounds and bounded route budgets',
            'native_visual_verified': False}


def require_drawing_quality(objects, model, sheet_size, continuous=True, reflow=True, locked=()):
    from .schematic import bounds_report
    if not bounds_report(objects, sheet_size, include_text=False)['all_geometry_inside_frame']:
        raise CircuitError('SCHEMATIC_OUTSIDE_SHEET: fixed bodies, pins or wires exceed the frame')
    moved = fit_labels(objects, sheet_size, locked) if reflow else None
    report = drawing_report(objects, model, sheet_size, continuous)
    if moved:
        report['label_layout'] = moved
    if not report['passed']:
        import json
        raise CircuitError('SCHEMATIC_QUALITY_FAILED: '+json.dumps(report, ensure_ascii=True))
    return report
