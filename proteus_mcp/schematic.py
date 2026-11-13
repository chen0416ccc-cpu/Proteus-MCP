"""Exact half-grid schematic trees with explicit, unconnected wire crossings."""

import heapq
from collections import defaultdict
from itertools import combinations

from .devices import native_order, selected_definition
from .geometry import part_transform
from .model import CircuitError
from .native9 import GRID, PIN_ORDER, movable_copy
from .routing import cells, escape, natural, place_components, simplify


STEP = GRID // 2
DIRECTIONS = ((1, 0), (0, 1), (-1, 0), (0, -1))
HORIZONTAL, VERTICAL = 5, 10


def body_box(part):
    """Calibrated bodies, not a degenerate rectangle around display pins."""
    known = {
        'AT89C51': (0, -27, 14, 0), 'RES': (-1, -.5, 2, .5),
        'LED-RED': (-1, -1, 1, 1), 'CAP': (-.5, -1, .5, 1),
        'CAP-ELEC': (-.5, -1, .5, 1), 'CRYSTAL': (-1, -1, 1, 1),
        'BUTTON': (-1.5, -.5, 1.5, 1), '7SEG-MPX4-CC': (-1, -11, 29, 0),
        '7SEG-MPX4-CA': (-1, -11, 29, 0), 'BC547': (-1, -2, 2, 2),
        'BUZZER': (-1, -2, 3, 2),
    }
    if part['device'] in known:
        box = known[part['device']]
    else:
        from .symbol_geometry import graphic_bounds
        try:
            bounds = graphic_bounds(selected_definition(part)['blob'])
        except CircuitError as error:
            raise CircuitError('SCHEMATIC_BODY_NOT_CALIBRATED: '+part['device']+'; '+str(error)) from error
        box = tuple(value/GRID for value in bounds)
    corners = [part_transform(part, [round(x*GRID), round(y*GRID)])
               for x in (box[0], box[2]) for y in (box[1], box[3])]
    return (min(p[0] for p in corners), min(p[1] for p in corners),
            max(p[0] for p in corners), max(p[1] for p in corners))


def integer_point(point):
    if any(v % STEP for v in point):
        raise CircuitError('SCHEMATIC_OFF_HALF_GRID: pin position is not rounded')
    return tuple(v//STEP for v in point)


def path_usage(path):
    usage = defaultdict(int)
    for a, b in zip(path, path[1:]):
        direction = DIRECTIONS.index((b[0]-a[0], b[1]-a[1]))
        usage[a] |= 1 << direction
        usage[b] |= 1 << ((direction+2) % 4)
    return dict(usage)


def crossing_allowed(mask, direction):
    return (mask == HORIZONTAL and direction % 2 == 1) or (mask == VERTICAL and direction % 2 == 0)


def audit_crossings(net_paths):
    owners = defaultdict(dict)
    for name, paths in net_paths.items():
        for path in paths:
            for point, mask in path_usage(path).items():
                owners[point][name] = owners[point].get(name, 0) | mask
    crossings = []
    for point, nets in owners.items():
        if len(nets) < 2:
            continue
        if len(nets) != 2 or set(nets.values()) != {HORIZONTAL, VERTICAL}:
            raise CircuitError('SCHEMATIC_ILLEGAL_CONTACT: '+str(point)+' '+str(nets))
        crossings.append(point)
    return len(crossings)


def search(start, goals, blocked, occupied, bounds, max_expansions=320000):
    """Cross only straight interior segments, never endpoints, bends or dots."""
    goals = set(goals)-set(occupied)-blocked
    if start in blocked or start in occupied or not goals:
        raise CircuitError('SCHEMATIC_ENDPOINT_OBSTRUCTED')
    goal_list = tuple(goals)

    def estimate(p):
        return 10*min(abs(p[0]-g[0])+abs(p[1]-g[1]) for g in goal_list)

    initial = (*start, -1)
    costs, previous = {initial: 0}, {}
    queue = [(estimate(start), 0, initial)]
    expanded = 0
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
            return path[::-1]
        expanded += 1
        if expanded > max_expansions:
            raise CircuitError('SCHEMATIC_ROUTING_LIMIT')
        for direction, (dx, dy) in enumerate(DIRECTIONS):
            p = x+dx, y+dy
            if p in blocked or not (bounds[0] <= p[0] <= bounds[2] and bounds[1] <= p[1] <= bounds[3]):
                continue
            # The move after entering a crossing must continue straight.
            if (x, y) in occupied and direction != incoming:
                continue
            if p in occupied and not crossing_allowed(occupied[p], direction):
                continue
            nearby = sum(n in occupied for n in ((p[0]+dy, p[1]+dx), (p[0]-dy, p[1]-dx)))
            candidate = cost+10+(12 if incoming not in (-1, direction) else 0)+(35 if p in occupied else 0)+nearby*3
            child = (*p, direction)
            if candidate < costs.get(child, 10**30):
                costs[child], previous[child] = candidate, state
                heapq.heappush(queue, (candidate+estimate(p), candidate, child))
    raise CircuitError('NO_SCHEMATIC_ROUTE: adjust placements or sheet size')


def arrange_labels(part):
    """Keep native text upright when the symbol itself is rotated."""
    x1, y1, x2, y2 = body_box(part)
    if part['device'] == 'AT89C51':
        positions = [(x1, y2+GRID), (x1, y1-GRID)]
    elif part['device'].startswith('7SEG-'):
        positions = [(x1, y2+2*GRID), (x1+7*GRID, y2+2*GRID)]
    elif part['device'] == 'LED-RED':
        positions = [(x1, y2+GRID), (x2+GRID, y2+GRID)]
    elif len(native_order(part['device'], part.get('native_definition'))) == 2 and (
            part_transform(part, [GRID, 0])[0] == part['origin'][0]):
        positions = [(x2+GRID//2, (y1+y2)//2+GRID//2), (x2+GRID//2, (y1+y2)//2-GRID)]
    elif x2-x1 < y2-y1:
        positions = [(x2+GRID//2, (y1+y2)//2+GRID//2), (x2+GRID//2, (y1+y2)//2-GRID)]
    else:
        positions = [(x1, y2+GRID), (x1, y1-GRID)]
    for label, position in zip(part['labels'], positions):
        label['xy'] = list(position)
        label['transform'] = '000000000400'


def bounds_report(objects, sheet_size, include_text=True):
    from .drawing_quality import label_box, labels
    from .native9 import pin_xy
    points = []
    for obj in objects:
        if obj['type'] == 'component':
            x1, y1, x2, y2 = body_box(obj)
            points.extend([(x1, y1), (x2, y2)])
            points.extend(point for number in native_order(obj['device'], obj.get('native_definition'))
                          if (point := pin_xy(obj, number)) is not None)
        elif obj['type'] == 'wire':
            points.extend(obj['points'])
        elif 'origin' in obj:
            x, y = obj['origin']
            points.extend([(x-GRID, y-GRID), (x+GRID, y+GRID)])
    for _, label, _ in labels(objects) if include_text else ():
        box = label_box(label)
        if box:
            points.extend([(box[0], box[1]), (box[2], box[3])])
    frame = [-sheet_size[0]*GRID//2, -sheet_size[1]*GRID//2,
              sheet_size[0]*GRID//2, sheet_size[1]*GRID//2]
    outside = [p for p in points if not (frame[0] <= p[0] <= frame[2] and frame[1] <= p[1] <= frame[3])]
    extent = ([min(p[0] for p in points), min(p[1] for p in points),
               max(p[0] for p in points), max(p[1] for p in points)] if points else [0]*4)
    return {'sheet_size_grid': list(sheet_size), 'frame_grid': [v/GRID for v in frame],
            'content_bounds_grid': [v/GRID for v in extent], 'outside_geometry_or_label_anchors': len(outside),
            'outside_geometry_or_text_bounds': len(outside),
            'all_geometry_inside_frame': not outside,
            'scope': 'native bodies, pins, wires, terminal extents and estimated full text bounds; not native font raster extents'}


def route_schematic(templates, model, firmware=None, placements=None, layout='auto', sheet_size=None):
    from .native9 import checked_sheet_size
    sheet_size = checked_sheet_size(sheet_size or [100, 70])
    b, positions = place_components(templates, model, firmware, placements, layout)
    bodies, pins, exits, leads = set(), {}, {}, set()
    for ref, part in b.parts.items():
        arrange_labels(part)
        x1, y1, x2, y2 = body_box(part)
        body = {(x, y) for x in range(x1//STEP, x2//STEP+1) for y in range(y1//STEP, y2//STEP+1)}
        if body & bodies:
            raise CircuitError('COMPONENT_COLLISION: '+ref)
        bodies |= body
        for number in native_order(part['device'], part.get('native_definition')):
            endpoint = ref+'.'+number
            exact = b.pin(endpoint)
            if exact is None:
                continue
            p = integer_point(exact)
            dx, dy = escape(part, number)
            pins[endpoint] = p
            exits[endpoint] = p[0]+dx*2, p[1]+dy*2
            for distance in range(1, 65):
                inner = p[0]-dx*distance, p[1]-dy*distance
                leads.add(inner)
                if inner in body:
                    break
            else:
                raise CircuitError('SCHEMATIC_PIN_DOES_NOT_ENTER_BODY: '+endpoint)
    if len(pins.values()) != len(set(pins.values())) or set(pins.values()) & bodies:
        raise CircuitError('PIN_COLLISION: components are too close')
    all_pins = set(pins.values())
    escape_cells = {endpoint: cells([point, exits[endpoint]]) for endpoint, point in pins.items()}
    reserved = set().union(*escape_cells.values()) if pins else set()
    bounds = (-sheet_size[0]+6, -sheet_size[1]+6, sheet_size[0]-6, sheet_size[1]-6)
    occupied, reports, crossing_points, net_paths = {}, [], set(), defaultdict(list)

    def occupy(path):
        usage = path_usage(path)
        crossing_points.update(usage.keys() & occupied.keys())
        for p, mask in usage.items():
            occupied[p] = occupied.get(p, 0) | mask

    # Supplies use short, real terminal wires, not signal-name stubs.
    power_cells = set()
    for name in ('VCC', 'GND'):
        for endpoint in model['nets'].get(name, []):
            if endpoint not in pins:
                continue
            p, end = pins[endpoint], exits[endpoint]
            ref, number = endpoint.split('.')
            dx, dy = escape(b.parts[ref], number)
            # Keep terminal symbols at least two full grid units from the pin.
            end = end[0]+dx*2, end[1]+dy*2
            route = [p, end]
            if (cells(route)-{p}) & (bodies | all_pins | leads | power_cells):
                raise CircuitError('POWER_STUB_COLLISION: '+endpoint)
            key = 'supply_'+endpoint
            connection = [v*STEP for v in end]
            origin = [connection[0], connection[1]+(-GRID if name == 'GND' else GRID)]
            terminal = movable_copy(b.templates['$TERGROUND' if name == 'GND' else '$TERPOWER'], origin)
            terminal['wire_key'] = key
            terminal['label']['text'] = name
            b.objects.append(terminal)
            b.wire(key, [endpoint], [b.pin(endpoint), connection])
            power_cells |= cells(route)
            # Endpoint masks are deliberately not crossable.
            dense = [(p[0]+dx*i, p[1]+dy*i) for i in range(5)]
            occupy(dense)
            net_paths[name].append(dense)

    signals = [(name, [p for p in endpoints if p in pins]) for name, endpoints in model['nets'].items()
               if name not in ('VCC', 'GND')]
    for name, endpoints in signals:
        if len(endpoints) < 2:
            raise CircuitError('Signal net needs at least two visible pins: '+name)
    signals.sort(key=lambda item: (max(abs(pins[a][0]-pins[c][0])+abs(pins[a][1]-pins[c][1])
                                     for a, c in combinations(item[1], 2)), natural(item[0])))
    for net_index, (name, endpoints) in enumerate(signals):
        if len(endpoints) < 2:
            raise CircuitError('Signal net needs at least two visible pins: '+name)
        graph, tree = defaultdict(set), set()
        endpoint_at = {pins[p]: p for p in endpoints}
        allowed = set().union(*(escape_cells[p] for p in endpoints))
        blocked = bodies | all_pins | leads | (reserved-allowed) | power_cells
        blocked -= allowed-all_pins-bodies-leads-power_cells
        blocked |= allowed-{exits[p] for p in endpoints}

        def extend(path):
            for a, c in zip(path, path[1:]):
                graph[a].add(c)
                graph[c].add(a)
            tree.update(path)

        def lead(endpoint):
            point = pins[endpoint]
            ref, number = endpoint.split('.')
            dx, dy = escape(b.parts[ref], number)
            return [point, (point[0]+dx, point[1]+dy), exits[endpoint]]

        first, second = min(combinations(endpoints, 2), key=lambda pair:
                            (abs(pins[pair[0]][0]-pins[pair[1]][0])+abs(pins[pair[0]][1]-pins[pair[1]][1]), pair))
        try:
            middle = search(exits[first], {exits[second]}, blocked, occupied, bounds)
            extend([*lead(first)[:-1], *middle, *lead(second)[-2::-1]])
            remaining = set(endpoints)-{first, second}
            while remaining:
                endpoint = min(remaining, key=lambda p: min(abs(pins[p][0]-q[0])+abs(pins[p][1]-q[1]) for q in tree))
                goals = {p for p in tree-all_pins if len(graph[p]) < 4 and p not in occupied}
                middle = search(exits[endpoint], goals, blocked, occupied, bounds)
                extend([*lead(endpoint)[:-1], *middle])
                remaining.remove(endpoint)
        except CircuitError as error:
            raise CircuitError('Net '+name+': '+str(error)) from error

        nodes = {p for p, neighbors in graph.items() if len(neighbors) != 2 or p in endpoint_at}
        junctions = {}
        for p in sorted(nodes-endpoint_at.keys()):
            if not 3 <= len(graph[p]) <= 4 or p in occupied:
                raise CircuitError('SCHEMATIC_INVALID_JUNCTION: '+name)
            obj = {'type': 'junction', 'origin': [v*STEP for v in p], 'wire_keys': [None]*4}
            b.objects.append(obj)
            junctions[p] = obj
        walked, branches, dense_paths = set(), 0, []
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
                key = f'schematic_{net_index}_{branches}'
                branches += 1
                for node in (start, current):
                    if node in junctions:
                        slots = junctions[node]['wire_keys']
                        slots[slots.index(None)] = key
                b.wire(key, [endpoint_at[p] for p in (start, current) if p in endpoint_at],
                       [[v*STEP for v in p] for p in simplify(path)])
                dense_paths.append(path)
        # Preserve full degree at junctions when merging branch occupancy.
        usage = {}
        for path in dense_paths:
            for p, mask in path_usage(path).items():
                usage[p] = usage.get(p, 0) | mask
        crossing_points.update(usage.keys() & occupied.keys())
        for p, mask in usage.items():
            occupied[p] = occupied.get(p, 0) | mask
        net_paths[name].extend(dense_paths)
        reports.append({'net': name, 'pins': endpoints, 'wire_branches': branches, 'junctions': len(junctions),
                        'grid_length': sum(map(len, graph.values()))/4})
    audit = bounds_report(b.objects, sheet_size)
    checked_crossings = audit_crossings(net_paths)
    if checked_crossings != len(crossing_points):
        raise CircuitError('SCHEMATIC_CROSSING_AUDIT_MISMATCH')
    return b.objects, {'algorithm': 'half_grid_orthogonal_schematic_tree', 'placements': positions, 'nets': reports,
                       'grid_pitch': .5, 'crossings': 'perpendicular_interior_without_junction',
                       'crossing_count': checked_crossings, 'illegal_wire_contacts': 0,
                       'body_collisions': 0, 'pin_collisions': 0,
                       'signal_connections': 'continuous_wires_and_explicit_junctions', 'sheet': audit}
