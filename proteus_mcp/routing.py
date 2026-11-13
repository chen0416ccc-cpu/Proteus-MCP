"""Deterministic orthogonal routing with component and pin keepouts."""

import heapq
import re
from collections import defaultdict

from .model import CircuitError
from .native9 import Builder, GRID, PIN_ORDER
from .devices import native_order
from .geometry import part_transform, transform_xy


def natural(value):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", value)]


def simplify(path):
    result = []
    for point in path:
        if result and result[-1] == point:
            continue
        if len(result) > 1:
            a, b = result[-2:]
            if (a[0] == b[0] == point[0]) or (a[1] == b[1] == point[1]):
                result.pop()
        result.append(point)
    return result


def cells(path):
    result = set()
    for a, b in zip(path, path[1:]):
        if a[0] != b[0] and a[1] != b[1]:
            raise CircuitError("Nonorthogonal path")
        dx = (b[0] > a[0])-(b[0] < a[0])
        dy = (b[1] > a[1])-(b[1] < a[1])
        result.update((a[0]+dx*i, a[1]+dy*i) for i in range(abs(a[0]-b[0])+abs(a[1]-b[1])+1))
    return result


def astar(start, goals, blocked, bounds, max_expansions=180000):
    """Search position + incoming direction so bends have a real cost."""
    goals = set(goals)
    if not goals:
        raise CircuitError("No route targets")
    if start in blocked or not goals-blocked:
        raise CircuitError("Route endpoint is obstructed")
    goal_list = list(goals)

    def estimate(p):
        return min(abs(p[0]-g[0])+abs(p[1]-g[1]) for g in goal_list)*10

    initial = (start[0], start[1], -1)
    queue = [(estimate(start), 0, initial)]
    costs, previous, expanded = {initial: 0}, {}, 0
    directions = ((1, 0), (0, 1), (-1, 0), (0, -1))
    while queue:
        _, cost, state = heapq.heappop(queue)
        if costs.get(state) != cost:
            continue
        x, y, incoming = state
        if (x, y) in goals:
            path = [(x, y)]
            while state in previous:
                state = previous[state]
                path.append(state[:2])
            return list(reversed(path))
        expanded += 1
        if expanded > max_expansions:
            raise CircuitError("ROUTING_LIMIT: reduce density or supply wider placements")
        for direction, (dx, dy) in enumerate(directions):
            p = x+dx, y+dy
            if p in blocked or not (bounds[0] <= p[0] <= bounds[2] and bounds[1] <= p[1] <= bounds[3]):
                continue
            child = (*p, direction)
            candidate = cost+10+(7 if incoming not in (-1, direction) else 0)
            if candidate < costs.get(child, 10**30):
                costs[child], previous[child] = candidate, state
                heapq.heappush(queue, (candidate+estimate(p), candidate, child))
    raise CircuitError("NO_ORTHOGONAL_ROUTE: move components or widen spacing")


def body_cells(part):
    x, y = (int(n//GRID) for n in part["origin"])
    if part["device"] == "AT89C51":
        box = x, y-27, x+14, y
    elif part["device"] == "LED-RED":
        box = x-1, y-1, x+1, y+1
    elif part["device"] == "RES":
        box = x-1, y-1, x+2, y+1
    else:
        box = x-1, y-1, x+1, y+1
    if part['device'] not in PIN_ORDER:
        from .devices import selected_definition
        anchors = [p['anchor'] for p in selected_definition(part)['pins'] if not p['hidden']]
        box = (min(p[0]//GRID for p in anchors)+x, min(p[1]//GRID for p in anchors)+y,
               max(p[0]//GRID for p in anchors)+x, max(p[1]//GRID for p in anchors)+y)
    points = [part_transform(part,[(u-x)*GRID,(v-y)*GRID])
              for u,v in ((box[0],box[1]),(box[2],box[3]),(box[0],box[3]),(box[2],box[1]))]
    return {(u,v) for u in range(min(p[0]//GRID for p in points),max(p[0]//GRID for p in points)+1)
            for v in range(min(p[1]//GRID for p in points),max(p[1]//GRID for p in points)+1)}


def escape(part, pin):
    import struct
    angle, mirror = struct.unpack('<hH',bytes.fromhex(part['transform']))
    if part['device'] not in PIN_ORDER:
        from .devices import selected_definition
        direction = next(p['direction'] for p in selected_definition(part)['pins'] if p['number']==pin)
        return tuple(transform_xy(direction,angle,mirror))
    if part["device"] == "LED-RED":
        direction = (0, 1 if pin == "1" else -1)
        return tuple(transform_xy(direction,angle,mirror))
    if part["device"] == "AT89C51":
        direction = (-1, 0) if pin in {"18", "19", "29", "30", "31", "9", *map(str, range(1, 9))} else (1, 0)
    else:
        direction = (-1, 0) if pin == "1" else (1, 0)
    return tuple(transform_xy(direction,angle,mirror))


def auto_positions(model, templates=None, fixed=None):
    from .auto_layout import auto_positions as topology_positions
    return topology_positions(model, templates, fixed)


def place_components(templates, model, firmware=None, placements=None, layout="auto"):
    from .native_backend import set_properties
    if layout not in ("auto", "model"):
        raise CircuitError("layout must be auto or model")
    if len(model["components"]) > 100:
        raise CircuitError("Native router is limited to 100 calibrated components")
    if placements and isinstance(placements, dict) and placements.keys() == model['components'].keys():
        positions = dict(placements)
    else:
        positions = auto_positions(model, templates, placements) if layout == "auto" else {
        ref: [round(item["position"][0]/10), -round(item["position"][1]/10)] for ref, item in model["components"].items()}
    if placements:
        if not isinstance(placements, dict) or placements.keys()-positions.keys():
            raise CircuitError("placements contains unknown references")
        positions.update(placements)
    b = Builder(templates)
    for ref in sorted(model["components"], key=natural):
        spec = model["components"][ref]
        p = positions[ref]
        if not isinstance(p, list) or len(p) != 2 or any(type(v) is not int or abs(v)>500 for v in p):
            raise CircuitError("Native placements use integer grid coordinates within +/-500")
        part = b.component(ref, spec["device"], p, rotation=spec.get('rotation',0), mirror=spec.get('mirror',0),
                           native_definition=spec.get('native_definition'))
        properties = dict(spec["properties"])
        if spec["device"] == "AT89C51" and firmware:
            properties["program"] = str(firmware)
        set_properties(part, properties)
    return b, positions


def prepare(templates, model, firmware=None, placements=None, layout="auto"):
    b, positions = place_components(templates, model, firmware, placements, layout)
    bodies, pins, exits = set(), {}, {}
    for ref, part in b.parts.items():
        body = body_cells(part)
        if bodies & body:
            raise CircuitError("COMPONENT_COLLISION: " + ref)
        bodies |= body
        for number in native_order(part["device"], part.get('native_definition')):
            xy = b.pin(ref+"."+number)
            if xy is None:
                continue
            p = tuple(n//GRID for n in xy)
            pins[ref+"."+number] = p
            dx, dy = escape(part, number)
            exits[ref+"."+number] = p[0]+dx, p[1]+dy
    if len(set(pins.values())) != len(pins) or any(p in bodies for p in pins.values()):
        raise CircuitError("PIN_COLLISION: components are too close")
    return b, positions, bodies, pins, exits


def route_wires(templates, model, firmware=None, placements=None, layout="auto"):
    b, positions, bodies, pins, exits = prepare(templates, model, firmware, placements, layout)
    if any(any(v % GRID for v in b.pin(endpoint)) for endpoint in pins):
        raise CircuitError('OFF_GRID_PIN_REQUIRES_LABEL_ROUTING: exact endpoints cannot be rounded')
    all_pins, all_exits, used, report = set(pins.values()), set(exits.values()), set(), []
    all_points = bodies | all_pins
    if not all_points:
        return b.objects, {"nets": [], "algorithm": "orthogonal_Astar_tree", "placements": positions}
    xs, ys = zip(*all_points)
    bounds = min(xs)-40, min(ys)-40, max(xs)+40, max(ys)+40
    for name in ("VCC", "GND"):
        for endpoint in model["nets"].get(name, []):
            if endpoint not in pins:
                continue
            ref, number = endpoint.split(".")
            dx, dy = escape(b.parts[ref], number)
            p = pins[endpoint]
            target = [p[0]+dx*4, p[1]+dy*4]
            path = [p, tuple(target)]
            occupied = cells(path)
            if (occupied-{p}) & (bodies | all_pins | used):
                raise CircuitError("POWER_STUB_COLLISION: " + endpoint)
            b.rail("power_"+endpoint, endpoint, name, target)
            used |= occupied
    signals = [(name, [pin for pin in endpoints if pin in pins]) for name, endpoints in model["nets"].items() if name not in ("VCC", "GND")]
    signals.sort(key=lambda item: (len(item[1]), sum(abs(pins[p][0])+abs(pins[p][1]) for p in item[1]), item[0]))
    for net_index, (name, endpoints) in enumerate(signals):
        if len(endpoints) < 2:
            raise CircuitError("Signal net needs at least two visible pins: " + name)
        endpoint_at = {pins[pin]: pin for pin in endpoints}
        tree, graph = set(), defaultdict(set)

        def extend(path):
            for a, c in zip(path, path[1:]):
                graph[a].add(c)
                graph[c].add(a)
            tree.update(path)

        first, second, *remaining = sorted(endpoints, key=lambda p: (pins[p], p))
        blocked = bodies | used | all_pins | (all_exits-{exits[p] for p in endpoints})
        try:
            middle = astar(exits[first], {exits[second]}, blocked, bounds)
        except CircuitError as error:
            raise CircuitError(f"Net {name}: {error}") from error
        extend([pins[first], *middle, pins[second]])
        for endpoint in remaining:
            goals = tree-all_pins
            try:
                middle = astar(exits[endpoint], goals, blocked, bounds)
            except CircuitError as error:
                raise CircuitError(f"Net {name}: {error}") from error
            extend([pins[endpoint], *middle])
        nodes = {point for point, adjacent in graph.items() if len(adjacent) != 2 or point in endpoint_at}
        junctions = {}
        for point in sorted(nodes-endpoint_at.keys()):
            if not 3 <= len(graph[point]) <= 4:
                raise CircuitError("Unexpected routing-tree degree")
            obj = {"type": "junction", "origin": [n*GRID for n in point], "wire_keys": [None]*4}
            b.objects.append(obj)
            junctions[point] = obj
        walked, branches = set(), 0
        for start in sorted(nodes):
            for neighbor in sorted(graph[start]):
                edge = frozenset((start, neighbor))
                if edge in walked:
                    continue
                path, previous, current = [start, neighbor], start, neighbor
                walked.add(edge)
                while current not in nodes:
                    nxt = next(p for p in graph[current] if p != previous)
                    walked.add(frozenset((current, nxt)))
                    path.append(nxt)
                    previous, current = current, nxt
                key = f"net_{net_index}_{branches}"
                branches += 1
                component_pins = [endpoint_at[p] for p in (start, current) if p in endpoint_at]
                for node in (start, current):
                    if node in junctions:
                        slots = junctions[node]["wire_keys"]
                        slots[slots.index(None)] = key
                b.wire(key, component_pins, [[n*GRID for n in p] for p in simplify(path)])
        used |= tree
        report.append({"net": name, "pins": endpoints, "wire_branches": branches, "junctions": len(junctions), "grid_length": sum(len(v) for v in graph.values())//2})
    return b.objects, {"nets": report, "algorithm": "orthogonal_Astar_tree", "placements": positions,
                       "crossings": "forbidden", "body_collisions": 0, "pin_collisions": 0,
                       "scope": "calibrated symbols, no rotations, no PCB copper routing"}


def route_labels(templates, model, firmware=None, placements=None, layout="auto"):
    """Short physical pin stubs joined by native named-net labels."""
    from .native_backend import label_nets
    b, positions, bodies, pins, exits = prepare(templates, model, firmware, placements, layout)
    used, all_pins, report = set(), set(pins.values()), []
    for index, (name, endpoints) in enumerate(model["nets"].items()):
        for endpoint in endpoints:
            if endpoint not in pins:
                continue
            p = pins[endpoint]
            ref, number = endpoint.split(".")
            dx, dy = escape(b.parts[ref], number)
            end = p[0]+dx*3, p[1]+dy*3
            occupied = cells([p, end])
            if (occupied-{p}) & (bodies | all_pins | used):
                raise CircuitError("LABEL_STUB_COLLISION: " + endpoint)
            key = f"stub_{index}_{endpoint}"
            if name in ("VCC", "GND"):
                b.rail(key, endpoint, name, list(end))
            else:
                # Native wire serialization requires a second endpoint record.
                exact = b.pin(endpoint)
                exact_end = [exact[0]+dx*3*GRID, exact[1]+dy*3*GRID]
                b.objects.append({"type": "junction", "origin": exact_end,
                                  "wire_keys": [key, None, None, None]})
                b.wire(key, [endpoint], [exact, exact_end])
            used |= occupied
        report.append({"net": name, "pins": list(endpoints), "connection": "power_terminal" if name in ("VCC", "GND") else "native_wire_labels"})
    label_nets(b.objects, model)
    return b.objects, {"nets": report, "algorithm": "pin_stubs_and_named_nets", "placements": positions,
                       "crossings": "none", "body_collisions": 0, "pin_collisions": 0,
                       "scope": "calibrated symbols, no rotations, native named-net connections"}


def route_model(templates, model, firmware=None, placements=None, layout="auto", routing="auto", sheet_size=None):
    from .native9 import checked_sheet_size
    from .native_backend import label_nets
    from .drawing_quality import require_drawing_quality
    if routing not in ("auto", "wires", "labels", "schematic"):
        raise CircuitError("routing must be auto, wires, labels or schematic")
    if layout not in ('auto', 'model'):
        raise CircuitError('layout must be auto or model')
    if sheet_size is not None:
        sheet_size = checked_sheet_size(sheet_size)

    def finish(objects, details, size):
        if details['algorithm'] != 'explicit_ordered_channel_plan':
            label_nets(objects, model, visible=routing == 'labels')
        quality = require_drawing_quality(objects, model, size, continuous=routing != 'labels')
        details.update(quality=quality, sheet=quality['sheet'])
        return objects, details

    if layout == 'auto':
        from .auto_layout import layout_candidates
        candidates = layout_candidates(model, templates, sheet_size, placements)
    else:
        candidates = [{'placements': placements, 'sheet_size': sheet_size or [100, 70], 'variant': 'explicit'}]
    attempts, successful = [], []
    for candidate in candidates:
        if 'error' in candidate:
            attempts.append(candidate)
            continue
        size, proposed = candidate['sheet_size'], candidate['placements']
        try:
            if routing in ('auto', 'schematic'):
                from .schematic import route_schematic
                objects, details = route_schematic(templates, model, firmware, proposed, layout, size)
            elif routing == 'labels':
                objects, details = route_labels(templates, model, firmware, proposed, layout)
            else:
                objects, details = route_wires(templates, model, firmware, proposed, layout)
            objects, details = finish(objects, details, size)
            metrics = details['quality']['metrics']
            score = metrics['wire_length_grid']+2*metrics['bends']+12*metrics['crossings']+.001*size[0]*size[1]
            attempts.append({'variant': candidate['variant'], 'passed': True, 'score': round(score, 3)})
            details['auto_layout'] = {key: value for key, value in candidate.items() if key != 'placements'}
            successful.append((score, objects, details))
        except CircuitError as error:
            if layout != 'auto':
                raise
            attempts.append({'variant': candidate['variant'], 'passed': False, 'reason': str(error)})
    if not successful:
        import json
        raise CircuitError('AUTO_SCHEMATIC_FAILED: '+json.dumps(attempts, ensure_ascii=True))
    _, objects, details = min(successful, key=lambda item: item[0])
    details['layout_attempts'] = attempts
    return objects, details
