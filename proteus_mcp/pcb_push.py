"""Bounded local segment displacement, not the vendor interactive push engine."""
import collections
import math

from .model import CircuitError, digest, json_bytes
from .pcb import plan_route, point_distance, route_spec, segment_distance, simplify, wire_key


def wire_id(wire):
    return digest(json_bytes(wire_key(wire)))


def segments(wire):
    return list(zip(wire['points_nm'], wire['points_nm'][1:]))


def simple_path(points):
    for a, b, c in zip(points, points[1:], points[2:]):
        u, v = (b[0]-a[0], b[1]-a[1]), (c[0]-b[0], c[1]-b[1])
        if u[0]*v[1] == u[1]*v[0] and u[0]*v[0]+u[1]*v[1] <= 0:
            return False
    return not any(segment_distance(a,b,c,d) == 0
                   for i,(a,b) in enumerate(zip(points,points[1:]))
                   for c,d in list(zip(points,points[1:]))[i+2:])


def isolated_endpoints(board, wire):
    """Only an isolated pad-to-pad trace can move without a topology engine."""
    points = wire['points_nm']
    if len(points) > 64 or not simple_path(points):
        raise CircuitError('PCB_PUSH_TRACE_COMPLEXITY: requires a simple trace of at most 64 vertices')
    ends = []
    for position in (points[0], points[-1]):
        candidates = [p for p in board['pads'] if p['net'] == wire['net'] and
                      wire['layer'] in p['radius_nm'] and p['position_nm'] == position]
        if len(candidates) != 1:
            raise CircuitError('PCB_PUSH_ANCHORS: each fixed endpoint must be exactly one real pad centre')
        ends.append(candidates[0]['id'])
    if ends[0] == ends[1]:
        raise CircuitError('PCB_PUSH_ANCHORS: loop traces are not calibrated')
    for pad in board['pads']:
        if pad['net'] == wire['net'] and pad['id'] not in ends and wire['layer'] in pad['radius_nm']:
            if any(point_distance(pad['position_nm'],a,b) <= pad['radius_nm'][wire['layer']]+wire['width_nm']/2
                   for a,b in segments(wire)):
                raise CircuitError('PCB_PUSH_BRANCH: trace touches another same-net pad')
    for other in board['wires']:
        if other is not wire and other['net'] == wire['net'] and other['layer'] == wire['layer']:
            if any(segment_distance(a,b,c,d) <= (wire['width_nm']+other['width_nm'])/2
                   for a,b in segments(wire) for c,d in segments(other)):
                raise CircuitError('PCB_PUSH_BRANCH: trace touches another same-net wire')
    for via in board.get('vias', []):
        radius = via['style']['radius_nm'].get(wire['layer'])
        if via['net'] == wire['net'] and radius is not None:
            if any(point_distance(via['position_nm'],a,b) <= radius+wire['width_nm']/2 for a,b in segments(wire)):
                raise CircuitError('PCB_PUSH_BRANCH: trace touches a same-net via')
    return ends


def displace(wire, index, offset):
    points = [list(p) for p in simplify(wire['points_nm'])]
    if not 1 <= index < len(points)-2:
        return None
    a,p,q,b = points[index-1:index+3]
    axis = 0 if p[1] == q[1] and p[0] != q[0] else 1 if p[0] == q[0] and p[1] != q[1] else None
    if axis is None:
        return None
    other = 1-axis
    if a[axis] != p[axis] or q[axis] != b[axis]:
        return None
    # Retain both neighbouring anchors and the directions of their stubs.
    if (p[other]-a[other])*(p[other]+offset-a[other]) <= 0 or (b[other]-q[other])*(b[other]-q[other]-offset) <= 0:
        return None
    points[index][other] += offset
    points[index+1][other] += offset
    if not simple_path(points):
        return None
    return {**wire, 'points_nm':points}


def plan_push(board, spec, points_nm, movable_wire_ids, managed_wire_ids, grid_nm=127000, max_displacement_nm=1270000):
    if type(grid_nm) is not int or grid_nm % 10 or not 10000 <= grid_nm <= 1000000:
        raise CircuitError('PCB_PUSH_GRID: requires 10 nm aligned integer in 10000..1000000')
    if type(max_displacement_nm) is not int or max_displacement_nm % 10 or not grid_nm <= max_displacement_nm <= 5000000:
        raise CircuitError('PCB_PUSH_BOUND: max displacement must be 10 nm aligned and within grid..5000000')
    if not isinstance(movable_wire_ids,list) or not 1 <= len(movable_wire_ids) <= 16 or any(not isinstance(key,str) for key in movable_wire_ids) or len(set(movable_wire_ids)) != len(movable_wire_ids):
        raise CircuitError('PCB_PUSH_ALLOWLIST: requires 1..16 unique current wire ids')
    inventory = collections.defaultdict(list)
    for wire in board['wires']:
        inventory[wire_id(wire)].append(wire)
    if any(len(inventory.get(key,[])) != 1 for key in movable_wire_ids):
        raise CircuitError('PCB_PUSH_ALLOWLIST: unknown or duplicate native wire signature')
    if not set(movable_wire_ids) <= set(managed_wire_ids):
        raise CircuitError('PCB_PUSH_UNMANAGED_TRACE: only current MCP-created traces may move')
    movable = [inventory[key][0] for key in movable_wire_ids]
    if any(w['net'] == spec['net'] or w['layer'] != spec['layer'] for w in movable):
        raise CircuitError('PCB_PUSH_ALLOWLIST: select foreign-net traces on the requested layer')
    gap = max([spec['clearance_nm']] + [n['clearance_nm'] for n in board['nets'].values()])
    spec = {**spec, 'clearance_nm':gap}
    fixed = {**board, 'wires':[w for w in board['wires'] if wire_id(w) not in movable_wire_ids]}
    route = plan_route(fixed,spec,points_nm,grid_nm)
    if not simple_path(route['points_nm']):
        raise CircuitError('PCB_PUSH_TARGET_SELF_INTERSECTION')
    blockers = [w for w in movable if any(
        segment_distance(a,b,c,d) < (route['width_nm']+w['width_nm'])/2+gap
        for a,b in segments(route) for c,d in segments(w))]
    if len(blockers) != 1:
        raise CircuitError('PCB_PUSH_SINGLE_BLOCKER_REQUIRED: cascade and no-op pushes are not calibrated')
    old = blockers[0]
    ends = isolated_endpoints(board,old)
    old_spec = route_spec(board,old['net'],old['layer'],*ends,width_nm=old['width_nm'],clearance_nm=gap)
    plan_route(board,old_spec,old['points_nm'])
    baseline = {**board, 'wires':[w for w in board['wires'] if w is not old] + [route]}
    candidates, searched = [],0
    for distance in range(grid_nm,max_displacement_nm+1,grid_nm):
        for index in range(1,len(simplify(old['points_nm']))-2):
            for offset in (-distance,distance):
                searched += 1
                if searched > 4096:
                    raise CircuitError('PCB_PUSH_SEARCH_BOUND: no board mutation')
                changed = displace(old,index,offset)
                if changed is None:
                    continue
                try:
                    plan_route(baseline,old_spec,changed['points_nm'])
                    final_board = {**board, 'wires':[w for w in board['wires'] if w is not old] + [changed]}
                    plan_route(final_board,spec,route['points_nm'])
                    # A displacement may not create a new same-net attachment.
                    isolated_endpoints({**final_board,'wires':final_board['wires'] + [route]},changed)
                except CircuitError:
                    continue
                length = sum(math.dist(a,b) for a,b in segments(changed))
                candidates.append((distance,length,index,offset,changed))
        if candidates:
            break
    if not candidates:
        raise CircuitError('PCB_NO_BOUNDED_PUSH: no safe local displacement; board unchanged')
    distance,_,index,offset,changed = min(candidates,key=lambda c:c[:4])
    if changed['points_nm'][0] != old['points_nm'][0] or changed['points_nm'][-1] != old['points_nm'][-1]:
        raise CircuitError('PCB_PUSH_ANCHOR_PRESERVATION_FAILED')
    return {'wires':[changed,route],'vias':[],'remove_wires':[old],
            'displacements':[{'wire_id':wire_id(old),'before':old,'after':changed,
                              'segment_index':index,'offset_nm':offset,'fixed_pad_ids':ends}],
            'planner':'bounded_local_orthogonal_segment_displacement','push_shove':True,
            'vendor_interactive_push_engine':False,'moved_trace_count':1,'searched_candidates':searched,
            'maximum_actual_displacement_nm':distance,'full_drc':False,
            'scope':'One isolated MCP-created trace; fixed endpoints, no via moves, branches, cascade, or native lock certification'}
