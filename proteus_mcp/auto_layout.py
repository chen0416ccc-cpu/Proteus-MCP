"""Bounded topology/port-aware placement, independent of example identities."""

import math
import struct
from collections import defaultdict
from itertools import combinations

from .drawing_quality import label_box
from .geometry import orientation
from .model import CircuitError
from .native9 import Builder, GRID, pin_xy


def _prototypes(model, templates):
    from .native_backend import set_properties
    from .schematic import arrange_labels, body_box
    from .devices import native_order
    from .routing import escape, natural
    builder = Builder(templates) if templates else None
    parts, footprints, ports = {}, {}, {}
    for ref in sorted(model['components'], key=natural):
        spec = model['components'][ref]
        if builder:
            part = builder.component(ref, spec['device'], [0, 0], rotation=spec.get('rotation', 0),
                                     mirror=spec.get('mirror', 0), native_definition=spec.get('native_definition'))
            set_properties(part, spec['properties'])
        else:
            # Public planning also works without a proprietary project template.
            # Export uses the actual template text styles instead of these bounds.
            style = struct.pack('<iiiIH', GRID*3//2, GRID*4//5, 0, 1, 0).hex()
            part = {'ref': ref, 'device': spec['device'], 'origin': [0, 0],
                    'transform': orientation(spec.get('rotation', 0), spec.get('mirror', 0)),
                    'native_definition': spec.get('native_definition'),
                    'labels': [{'text': value, 'xy': [0, 0], 'style': style, 'transform': '000000000000'}
                               for value in (ref, spec['properties'].get('value', spec['device']))]}
        arrange_labels(part)
        boxes = [body_box(part)] + [box for label in part['labels'][:2] if (box := label_box(label))]
        ports[ref] = {}
        for number in native_order(part['device'], part.get('native_definition')):
            point = pin_xy(part, number)
            if point is None:
                continue
            direction = escape(part, number)
            ports[ref][number] = (tuple(value/GRID for value in point), direction)
            # Reserve leads and the outward two-grid wire escape corridor.
            end = [point[axis]+direction[axis]*2*GRID for axis in (0, 1)]
            boxes.extend([(point[0], point[1], point[0], point[1]), (end[0], end[1], end[0], end[1])])
        footprints[ref] = (math.floor(min(box[0] for box in boxes)/GRID)-1,
                           math.floor(min(box[1] for box in boxes)/GRID)-2,
                           math.ceil(max(box[2] for box in boxes)/GRID)+2,
                           math.ceil(max(box[3] for box in boxes)/GRID)+2)
        parts[ref] = part
    return parts, footprints, ports


def _graph(model, ports):
    """Use hypernet weights, not supply-rail all-to-all attraction."""
    graph = {ref: defaultdict(list) for ref in model['components']}
    for name, endpoints in sorted(model['nets'].items()):
        if name in ('VCC', 'GND'):
            continue
        grouped = defaultdict(list)
        for endpoint in endpoints:
            ref, number = endpoint.split('.')
            if number in ports[ref]:
                grouped[ref].append(ports[ref][number])
        representatives = {}
        for ref, pins in grouped.items():
            point = tuple(sum(pin[0][axis] for pin in pins)/len(pins) for axis in (0, 1))
            direction = max(sorted({pin[1] for pin in pins}), key=lambda d: sum(pin[1] == d for pin in pins))
            representatives[ref] = point, direction
        weight = 1/max(1, len(grouped)-1)
        for a, b in combinations(sorted(grouped), 2):
            pa, da = representatives[a]
            pb, db = representatives[b]
            graph[a][b].append((pa, da, pb, db, weight))
            graph[b][a].append((pb, db, pa, da, weight))
    return graph


def _box(footprint, position):
    return tuple(footprint[index]+position[index % 2] for index in range(4))


def _overlap(a, b, gap):
    return a[0] < b[2]+gap and b[0] < a[2]+gap and a[1] < b[3]+gap and b[1] < a[3]+gap


def _extent(footprints, positions):
    boxes = [_box(footprints[ref], position) for ref, position in positions.items()]
    return (min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)) if boxes else (0, 0, 0, 0)


def _place_group(refs, graph, footprints, ports, variant, fixed=None, sheet_size=None):
    from .routing import natural
    gap = (3, 5, 7)[variant % 3]
    positions = {ref: list(point) for ref, point in (fixed or {}).items() if ref in refs}
    for ref in positions:
        if not isinstance(positions[ref], list) or len(positions[ref]) != 2 or any(
                type(v) is not int or abs(v) > 500 for v in positions[ref]):
            raise CircuitError('Native placements use integer grid coordinates within +/-500')
        box = _box(footprints[ref], positions[ref])
        if sheet_size and (box[0] < -sheet_size[0]/2+3 or box[2] > sheet_size[0]/2-3 or
                           box[1] < -sheet_size[1]/2+3 or box[3] > sheet_size[1]/2-3):
            raise CircuitError('AUTO_LAYOUT_OUTSIDE_SHEET: fixed placement '+ref)
    strength = {ref: sum(sum(edge[4] for edge in edges) for edges in graph[ref].values()) for ref in refs}

    def priority(ref):
        return (-sum(sum(edge[4] for edge in edges) for other, edges in graph[ref].items() if other in positions),
                -strength[ref], -len(ports[ref]), natural(ref))

    if not positions:
        positions[min(refs, key=priority)] = [0, 0]
    while refs-positions.keys():
        ref = min(refs-positions.keys(), key=priority)
        footprint = footprints[ref]
        candidates = set()
        extent = _extent(footprints, positions)
        neighbors = [other for other in graph[ref] if other in positions]
        for other in neighbors or sorted(positions, key=natural):
            obox = _box(footprints[other], positions[other])
            edges = graph[ref].get(other) or [((0, 0), (1, 0), (0, 0), (-1, 0), 1)]
            for pa, _, pb, _, _ in edges:
                align = [positions[other][axis]+pb[axis]-pa[axis] for axis in (0, 1)]
                for delta in (0, -4, 4, -8, 8, -16, 16, -24, 24):
                    candidates.update({(round(align[0]+delta), obox[3]+gap-footprint[1]),
                                       (round(align[0]+delta), obox[1]-gap-footprint[3]),
                                       (obox[2]+gap-footprint[0], round(align[1]+delta)),
                                       (obox[0]-gap-footprint[2], round(align[1]+delta))})
        # The perimeter is always a bounded fallback, never a fixed ref grid.
        for other in positions:
            x, y = positions[other]
            candidates.update({(extent[2]+gap-footprint[0], y), (extent[0]-gap-footprint[2], y),
                               (x, extent[3]+gap-footprint[1]), (x, extent[1]-gap-footprint[3])})
        boxes = [_box(footprints[other], point) for other, point in positions.items()]

        def score(point):
            cost = 0
            for other in neighbors:
                for pa, da, pb, db, weight in graph[ref][other]:
                    delta = [positions[other][axis]+pb[axis]-point[axis]-pa[axis] for axis in (0, 1)]
                    cost += weight*(abs(delta[0])+abs(delta[1]) +
                                    2*max(0, -sum(delta[i]*da[i] for i in (0, 1))) +
                                    2*max(0, sum(delta[i]*db[i] for i in (0, 1))))
            box = _box(footprint, point)
            width, height = max(box[2], extent[2])-min(box[0], extent[0]), max(box[3], extent[3])-min(box[1], extent[1])
            return cost+.15*(width+height)+.002*width*height

        valid = []
        for point in candidates:
            box = _box(footprint, point)
            if any(abs(v) > 490 for v in point) or any(_overlap(box, other, gap) for other in boxes):
                continue
            if fixed and sheet_size and (box[0] < -sheet_size[0]/2+3 or box[2] > sheet_size[0]/2-3 or
                                         box[1] < -sheet_size[1]/2+3 or box[3] > sheet_size[1]/2-3):
                continue
            valid.append(point)
        if not valid:
            raise CircuitError('AUTO_LAYOUT_CAPACITY: no collision-free candidate for '+ref)
        positions[ref] = list(min(valid, key=lambda point: (score(point), point)))
    return positions


def layout_candidates(model, templates=None, sheet_size=None, fixed=None):
    """Three deterministic candidates; pins, values and explicit anchors survive."""
    from .routing import natural
    from .native9 import checked_sheet_size
    if len(model['components']) > 100:
        raise CircuitError('Native router is limited to 100 calibrated components')
    if fixed is not None and (not isinstance(fixed, dict) or fixed.keys()-model['components'].keys()):
        raise CircuitError('placements contains unknown references')
    _, footprints, ports = _prototypes(model, templates)
    if not footprints:
        raise CircuitError('AUTO_LAYOUT_EMPTY: add components before export')
    graph = _graph(model, ports)
    unseen, groups = set(graph), []
    while unseen:
        pending, group = [min(unseen, key=natural)], set()
        while pending:
            ref = pending.pop()
            if ref in group:
                continue
            group.add(ref)
            pending.extend(set(graph[ref])-group)
        unseen -= group
        groups.append(group)
    groups.sort(key=lambda group: (-len(group), natural(min(group, key=natural))))
    results = []
    for variant in range(3):
        try:
            if fixed:
                positions = _place_group(set(graph), graph, footprints, ports, variant, fixed, sheet_size)
            else:
                local = [_place_group(group, graph, footprints, ports, variant) for group in groups]
                areas = [(_extent(footprints, positions), positions) for positions in local]
                area = sum((b[2]-b[0]+8)*(b[3]-b[1]+8) for b, _ in areas)
                ratio = sheet_size[0]/sheet_size[1] if sheet_size else (1.4, 1.8, 1.1)[variant]
                row_width = max(max(b[2]-b[0] for b, _ in areas), math.ceil(math.sqrt(area*ratio)))
                positions, x, y, row_height = {}, 0, 0, 0
                for box, group in areas:
                    width, height = box[2]-box[0], box[3]-box[1]
                    if x and x+width > row_width:
                        x, y, row_height = 0, y+row_height+10, 0
                    for ref, point in group.items():
                        positions[ref] = [point[0]+x-box[0], point[1]+y-box[1]]
                    x += width+10
                    row_height = max(row_height, height)
                box = _extent(footprints, positions)
                center = [round((box[i]+box[i+2])/2) for i in (0, 1)]
                positions = {ref: [p[i]-center[i] for i in (0, 1)] for ref, p in positions.items()}
            box = _extent(footprints, positions)
            required = [2*math.ceil(max(abs(box[i]), abs(box[i+2]))+6) for i in (0, 1)]
            size = checked_sheet_size(sheet_size or [max(100, required[0]), max(70, required[1])])
            if any(required[i] > size[i] for i in (0, 1)):
                raise CircuitError('AUTO_LAYOUT_OUTSIDE_SHEET: required '+str(required)+', supplied '+str(size))
            results.append({'placements': positions, 'sheet_size': size, 'variant': variant,
                            'algorithm': 'topology_port_footprint_candidates', 'connected_groups': len(groups)})
        except CircuitError as error:
            results.append({'variant': variant, 'error': str(error)})
    return results


def auto_positions(model, templates=None, fixed=None):
    candidates = layout_candidates(model, templates, fixed=fixed)
    for candidate in candidates:
        if 'placements' in candidate:
            return candidate['placements']
    raise CircuitError('AUTO_LAYOUT_FAILED: '+str(candidates))
