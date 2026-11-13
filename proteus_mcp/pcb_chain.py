"""Bounded multi-trace constraint search; not the vendor interactive router."""
import collections

from .model import CircuitError
from .pcb import plan_route, route_spec, segment_distance, simplify
from .pcb_push import displace, isolated_endpoints, segments, simple_path, wire_id


def conflict(a, b, gap):
    return a['layer'] == b['layer'] and a['net'] != b['net'] and any(
        segment_distance(p, q, r, s) < (a['width_nm']+b['width_nm'])/2+gap
        for p, q in segments(a) for r, s in segments(b))


def plan_chain(board, spec, points_nm, movable_wire_ids, managed_wire_ids,
               grid_nm=127000, max_displacement_nm=1270000):
    if type(grid_nm) is not int or grid_nm % 10 or not 10000 <= grid_nm <= 1000000:
        raise CircuitError('PCB_CHAIN_GRID: requires 10 nm aligned integer in 10000..1000000')
    if type(max_displacement_nm) is not int or max_displacement_nm % 10 or not grid_nm <= max_displacement_nm <= 5000000:
        raise CircuitError('PCB_CHAIN_BOUND: displacement must be grid..5000000 nm and 10 nm aligned')
    if not isinstance(movable_wire_ids, list) or not 1 <= len(movable_wire_ids) <= 8 or any(
            not isinstance(key, str) for key in movable_wire_ids) or len(set(movable_wire_ids)) != len(movable_wire_ids):
        raise CircuitError('PCB_CHAIN_ALLOWLIST: requires 1..8 unique wire ids')
    inventory = collections.defaultdict(list)
    for wire in board['wires']:
        inventory[wire_id(wire)].append(wire)
    if any(len(inventory[key]) != 1 for key in movable_wire_ids):
        raise CircuitError('PCB_CHAIN_ALLOWLIST: unknown or duplicate native wire')
    if not set(movable_wire_ids) <= set(managed_wire_ids):
        raise CircuitError('PCB_CHAIN_UNMANAGED_TRACE')
    movable = [inventory[key][0] for key in movable_wire_ids]
    if any(w['net'] == spec['net'] or w['layer'] != spec['layer'] for w in movable):
        raise CircuitError('PCB_CHAIN_ALLOWLIST: foreign-net traces on the requested layer only')
    gap = max([spec['clearance_nm']] + [n['clearance_nm'] for n in board['nets'].values()])
    spec = {**spec, 'clearance_nm': gap}
    fixed = {**board, 'wires': [w for w in board['wires'] if wire_id(w) not in movable_wire_ids]}
    route = plan_route(fixed, spec, points_nm, grid_nm)
    if not simple_path(route['points_nm']):
        raise CircuitError('PCB_CHAIN_TARGET_SELF_INTERSECTION')
    if not any(conflict(w, route, gap) for w in movable):
        raise CircuitError('PCB_CHAIN_NO_BLOCKER')
    movable.sort(key=lambda w: (not conflict(w, route, gap), wire_id(w)))
    specs, anchors = {}, {}
    for wire in movable:
        key = wire_id(wire)
        anchors[key] = isolated_endpoints(board, wire)
        specs[key] = route_spec(board, wire['net'], wire['layer'], *anchors[key],
                                width_nm=wire['width_nm'], clearance_nm=gap)
        plan_route(board, specs[key], wire['points_nm'])
    searched = 0
    chosen = []

    def candidates(wire):
        yield wire, None, 0
        for distance in range(grid_nm, max_displacement_nm+1, grid_nm):
            for index in range(1, len(simplify(wire['points_nm']))-2):
                for offset in (-distance, distance):
                    moved = displace(wire, index, offset)
                    if moved is not None:
                        yield moved, index, offset

    def search(index):
        nonlocal searched
        if index == len(movable):
            return True
        original = movable[index]
        baseline = {**fixed, 'wires': [*fixed['wires'], route, *[item[0] for item in chosen]]}
        for moved, segment, offset in candidates(original):
            searched += 1
            if searched > 12000:
                raise CircuitError('PCB_CHAIN_SEARCH_BOUND: no mutation')
            try:
                plan_route(baseline, specs[wire_id(original)], moved['points_nm'])
            except CircuitError:
                continue
            chosen.append((moved, segment, offset))
            if search(index+1):
                return True
            chosen.pop()
        return False

    if not search(0):
        raise CircuitError('PCB_NO_BOUNDED_CHAIN: no mutation')
    replacements = [(old, *item) for old, item in zip(movable, chosen) if item[2]]
    final = {**board, 'wires': [w for w in board['wires'] if wire_id(w) not in movable_wire_ids]+[item[0] for item in chosen]+[route]}
    # Revalidate the complete final copper, including unchanged allowlisted lines.
    plan_route(final, spec, route['points_nm'])
    for old, (moved, _, _) in zip(movable, chosen):
        if moved['points_nm'][0] != old['points_nm'][0] or moved['points_nm'][-1] != old['points_nm'][-1]:
            raise CircuitError('PCB_CHAIN_FIXED_ANCHOR_MISMATCH')
        plan_route(final, specs[wire_id(old)], moved['points_nm'])
        isolated_endpoints(final, moved)
    return {'wires': [*[m for _, m, _, _ in replacements], route], 'vias': [],
            'remove_wires': [o for o, _, _, _ in replacements],
            'displacements': [{'wire_id': wire_id(o), 'before': o, 'after': m, 'segment_index': i,
                               'offset_nm': off, 'fixed_pad_ids': anchors[wire_id(o)]} for o, m, i, off in replacements],
            'planner': 'bounded_multi_trace_constraint_search', 'push_shove': True,
            'vendor_interactive_push_engine': False, 'moved_trace_count': len(replacements),
            'searched_candidates': searched, 'full_drc': False,
            'maximum_actual_displacement_nm': max(abs(off) for _, _, _, off in replacements),
            'scope': 'Up to eight isolated MCP traces; fixed pad anchors; no branch or via movement'}
