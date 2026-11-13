"""Conservative copper routing using native ARES round-trip data.

ISIS positions use nm. ARES exports decimal mm but accepts SES integers in
10 nm units. No rounding is allowed at the write boundary.
"""
import collections
import heapq
import json
import math
import re
import time
import uuid
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .live import Live
from .model import CircuitError, atomic_write, digest, json_bytes


def expressions(text):
    if len(text) > 16000000:
        raise CircuitError('PCB exchange exceeds 16 MB')
    token = re.compile(r'\s*(\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+)')
    root, stack, offset, count = [], [], 0, 0
    current = root
    while offset < len(text):
        match = token.match(text, offset)
        if not match:
            if not text[offset:].strip():
                break
            raise CircuitError('Invalid PCB exchange token')
        value = match[1]; offset = match.end(); count += 1
        if count > 1000000:
            raise CircuitError('PCB token bound exceeded')
        if value == '(':
            child = []; current.append(child); stack.append(current); current = child
            if len(stack) > 80:
                raise CircuitError('PCB nesting bound exceeded')
        elif value == ')':
            if not stack:
                raise CircuitError('Unbalanced PCB exchange')
            current = stack.pop()
        else:
            current.append(json.loads(value) if value.startswith('"') else value)
    if stack or len(root) != 1 or not isinstance(root[0], list):
        raise CircuitError('Invalid PCB exchange root')
    return root[0]


def exchange_payload(text):
    """Keep the entire exchange except its exporter-generated file basename."""
    root = expressions(text)
    if len(root) < 3 or root[0] != 'PCB' or not isinstance(root[1], str):
        raise CircuitError('Invalid native PCB export header')
    return [root[0], *root[2:]]


def children(node, name):
    return [v for v in node if isinstance(v, list) and v and v[0] == name]


def child(node, name, default=None):
    found = children(node, name)
    if len(found) > 1:
        raise CircuitError('Ambiguous PCB section: '+name)
    return found[0] if found else default if default is not None else []


def nm(value):
    try:
        scaled = Decimal(value)*1000000
        if not scaled.is_finite() or scaled != scaled.to_integral_value() or abs(scaled) > 2000000000:
            raise ValueError('Coordinate bounds or precision')
        return int(scaled)
    except (InvalidOperation, ValueError, TypeError) as error:
        raise CircuitError('Invalid decimal millimetre value: '+str(value)) from error


def rotate(point, angle):
    x, y = point
    return {0:(x,y),90:(-y,x),180:(-x,-y),270:(y,-x)}[angle]


def parse_board(text, padstack_metadata=None):
    root = expressions(text)
    if root[:1] != ['PCB'] or child(root, 'unit') != ['unit','mm']:
        raise CircuitError('Expected an ARES PCB export in millimetres')
    structure, library = child(root,'structure'), child(root,'library')
    blockers, layers, rules, nets, pin_nets = [], [], {}, {}, {}
    for layer in children(structure,'layer'):
        if child(layer,'type') == ['type','signal'] and child(layer,'direction') != ['direction','off']:
            layers.append(layer[1])
    rule = child(structure,'rule')
    width = nm(child(rule,'width',['width','0.3048'])[1])
    clearance = max((nm(c[1]) for c in children(rule,'clearance')), default=381000)
    boundary = None
    for node in children(structure,'boundary'):
        shape = child(node,'path')
        if shape and len(shape) >= 12 and shape[1] == 'signal':
            points = [(nm(shape[i]), nm(shape[i+1])) for i in range(3,len(shape),2)]
            corners = set(points)
            xs, ys = sorted({p[0] for p in corners}), sorted({p[1] for p in corners})
            if len(xs) == len(ys) == 2 and corners == {(x,y) for x in xs for y in ys} and all(a[0]==b[0] or a[1]==b[1] for a,b in zip(points,points[1:])) and points[0] == points[-1]:
                boundary = [xs[0],ys[0],xs[1],ys[1]]
            else:
                blockers.append('Nonrectangular board outline')
    if boundary is None:
        blockers.append('No calibrated rectangular signal outline')
    for node in structure:
        if isinstance(node,list) and node and ('keepout' in node[0] or node[0] in ('plane','region')):
            blockers.append('Uncalibrated board obstacle: '+node[0])
    network = child(root,'network')
    for net in children(network,'net'):
        name = net[1]
        if name in nets:
            raise CircuitError('Duplicate PCB net')
        pins = child(net,'pins')[1:]
        allowed = child(child(net,'circuit'),'use_layer')[1:]
        nets[name] = {'pins':pins,'layers':allowed,'minimum_width_nm':width,'clearance_nm':clearance}
        for pin in pins:
            if pin in pin_nets:
                raise CircuitError('PCB pad belongs to multiple nets')
            pin_nets[pin] = name
    for cls in children(network,'class'):
        rule = child(cls,'rule')
        minimum = nm(child(rule,'width',['width',str(Decimal(width)/1000000)])[1])
        gap = max([clearance]+[nm(c[1]) for c in children(rule,'clearance')])
        for name in cls[2:]:
            if isinstance(name,str) and name in nets:
                nets[name].update(minimum_width_nm=max(width,minimum),clearance_nm=gap)
    images = {i[1]:i for i in children(library,'image')}
    stacks = {p[1]:p for p in children(library,'padstack')}
    pads, placements = [], []
    for component in children(child(root,'placement'),'component'):
        image = images.get(component[1])
        if not image:
            raise CircuitError('Missing PCB footprint image')
        for place in children(component,'place'):
            ref, x, y, side, angle = place[1:6]
            try:
                angle = int(angle)
                if angle not in (0,90,180,270) or side != 'front':
                    raise ValueError('Uncalibrated transform')
            except ValueError:
                blockers.append('Uncalibrated PCB placement: '+ref)
                continue
            origin = [nm(x),nm(y)]
            placements.append({'ref':ref,'image':component[1],'position_nm':origin,'side':side,'rotation':angle})
            if any(isinstance(item,list) and item and ('keepout' in item[0] or item[0] in ('plane','region')) for item in image):
                blockers.append('Uncalibrated footprint copper obstacle: '+ref)
            for pin in children(image,'pin'):
                if len(pin) != 6 or not isinstance(pin[2],list):
                    raise CircuitError('Uncalibrated footprint pin record')
                pin_id = ref+'-'+pin[3]
                dx, dy = rotate([nm(pin[4]),nm(pin[5])],angle)
                radii = {}
                for shape in children(stacks.get(pin[1],[]),'shape'):
                    if len(shape) != 2 or not isinstance(shape[1],list):
                        blockers.append('Unsupported pad shape'); continue
                    form = shape[1]; layer = form[1]
                    if form[0] == 'circle' and len(form) == 5:
                        radius = math.ceil(nm(form[2])/2+math.hypot(nm(form[3]),nm(form[4])))
                    elif form[0] == 'rect' and len(form) == 6:
                        radius = math.ceil(max(math.hypot(nm(a),nm(b)) for a in (form[2],form[4]) for b in (form[3],form[5])))
                    else:
                        blockers.append('Unsupported pad geometry: '+form[0]); continue
                    radii[layer] = max(radii.get(layer,0),radius)
                if not radii:
                    blockers.append('No calibrated copper pad: '+pin_id)
                pads.append({'id':pin_id,'ref':ref,'pad_index':pin[3],'net':pin_nets.get(pin_id),
                             'position_nm':[origin[0]+dx,origin[1]+dy],'radius_nm':radii})
    if len({p['id'] for p in pads}) != len(pads):
        raise CircuitError('Duplicate native PCB pad id')
    wires = []
    for wire in child(root,'wiring')[1:]:
        if isinstance(wire,list) and wire[:1] == ['via']:
            continue
        if not isinstance(wire,list) or wire[:1] != ['wire']:
            blockers.append('Uncalibrated existing copper object'); continue
        if any(not isinstance(record,list) or not record or record[0] not in ('path','net','type') for record in wire[1:]):
            blockers.append('Uncalibrated existing wire attributes')
        if child(wire,'type') not in ([],['type','route']):
            blockers.append('Protected or uncalibrated existing wire type')
        path, net = child(wire,'path'), child(wire,'net')
        if len(path) < 7 or len(path)%2 != 1 or len(net) != 2:
            raise CircuitError('Invalid native PCB wire')
        wires.append({'net':net[1],'layer':path[1],'width_nm':nm(path[2]),
                      'points_nm':[[nm(path[i]),nm(path[i+1])] for i in range(3,len(path),2)]})
    from .pcb_vias import parse_vias
    vias,via_styles = parse_vias(root,library,layers,padstack_metadata,blockers)
    return {'units':'nanometres','layers':layers,'boundary_nm':boundary,'nets':nets,
            'placements':placements,'pads':pads,'wires':wires,'routing_blockers':sorted(set(blockers)),
            'vias':vias,'via_styles':via_styles,
            'pad_identity_note':'ARES exported pad indices, not schematic physical pin numbers',
            'full_drc':False}


def board_token(board):
    from .pcb_vias import via_key,style_key
    # ARES may reverse wire direction or reorder records on save/reopen.
    canonical = {**board,'wires':sorted(wire_key(w) for w in board['wires']),
                 'pads':sorted(board['pads'],key=lambda p:p['id']),
                 'placements':sorted(board['placements'],key=lambda p:p['ref']),
                 'vias':sorted(via_key(v) for v in board.get('vias',[])),
                 'via_styles':{k:style_key(v) for k,v in board.get('via_styles',{}).items()}}
    return digest(json_bytes(canonical))


def point_distance(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    if not dx and not dy:
        return math.hypot(p[0]-a[0],p[1]-a[1])
    t = max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy)))
    return math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy)


def segment_distance(a,b,c,d):
    def cross(p,q,r):
        return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
    if max(min(a[0],b[0]),min(c[0],d[0])) <= min(max(a[0],b[0]),max(c[0],d[0])) and max(min(a[1],b[1]),min(c[1],d[1])) <= min(max(a[1],b[1]),max(c[1],d[1])) and cross(a,b,c)*cross(a,b,d) <= 0 and cross(c,d,a)*cross(c,d,b) <= 0:
        return 0
    return min(point_distance(a,c,d),point_distance(b,c,d),point_distance(c,a,b),point_distance(d,a,b))


def route_spec(board, net, layer, from_pad, to_pad, width_nm=None, clearance_nm=None):
    if board['routing_blockers']:
        raise CircuitError('PCB_UNCALIBRATED_GEOMETRY: '+', '.join(board['routing_blockers']))
    if net not in board['nets'] or net in ('{NC}','{VOID}'):
        raise CircuitError('PCB route needs an existing named electrical net')
    rules = board['nets'][net]
    if layer not in ('TOP','BOT') or layer not in board['layers'] or layer not in rules['layers']:
        raise CircuitError('PCB layer is not an allowed calibrated surface copper layer')
    pads = {p['id']:p for p in board['pads']}
    if from_pad == to_pad or any(p not in pads or pads[p]['net'] != net or layer not in pads[p]['radius_nm'] for p in (from_pad,to_pad)):
        raise CircuitError('PCB endpoints must be distinct real pad ids on the requested net/layer')
    width_nm = rules['minimum_width_nm'] if width_nm is None else width_nm
    clearance_nm = rules['clearance_nm'] if clearance_nm is None else clearance_nm
    for value in (width_nm,clearance_nm):
        if type(value) is not int or value % 10 or not 10000 <= value <= 5000000:
            raise CircuitError('PCB width/clearance must be integer nm, 10 nm aligned, within 10000..5000000')
    if width_nm < rules['minimum_width_nm'] or clearance_nm < rules['clearance_nm']:
        raise CircuitError('PCB route would violate exported minimum width/clearance')
    return {'net':net,'layer':layer,'width_nm':width_nm,'clearance_nm':clearance_nm,
            'from_pad':from_pad,'to_pad':to_pad,'start':pads[from_pad]['position_nm'],'end':pads[to_pad]['position_nm']}


def segment_clear(board, spec, a, b):
    x0,y0,x1,y1 = board['boundary_nm']
    margin = spec['width_nm']/2+spec['clearance_nm']
    if any(not (x0+margin <= p[0] <= x1-margin and y0+margin <= p[1] <= y1-margin) for p in (a,b)):
        return False
    for pad in board['pads']:
        if pad['net'] != spec['net'] and spec['layer'] in pad['radius_nm']:
            if point_distance(pad['position_nm'],a,b) < pad['radius_nm'][spec['layer']]+margin:
                return False
    for wire in board['wires']:
        if wire['net'] != spec['net'] and wire['layer'] == spec['layer']:
            if any(segment_distance(a,b,c,d) < (wire['width_nm']+spec['width_nm'])/2+spec['clearance_nm'] for c,d in zip(wire['points_nm'],wire['points_nm'][1:])):
                return False
    for via in board.get('vias',[]):
        radius = via['style']['radius_nm'].get(spec['layer'])
        if via['net'] != spec['net'] and radius is not None:
            if point_distance(via['position_nm'],a,b) < radius+margin:
                return False
    return True


def simplify(points):
    result = []
    for p in points:
        if len(result) >= 2:
            a,b = result[-2:]
            if (b[0]-a[0])*(p[1]-b[1]) == (b[1]-a[1])*(p[0]-b[0]) and (b[0]-a[0])*(p[0]-b[0])+(b[1]-a[1])*(p[1]-b[1]) > 0:
                result.pop()
        result.append(list(p))
    return result


def plan_route(board, spec, points_nm=None, grid_nm=127000):
    searched = 0
    if points_nm is None:
        if type(grid_nm) is not int or grid_nm % 10 or not 10000 <= grid_nm <= 1000000:
            raise CircuitError('PCB grid must be 10 nm aligned within 10000..1000000 nm')
        start,end = tuple(spec['start']),tuple(spec['end'])
        if any((end[i]-start[i]) % grid_nm for i in (0,1)):
            raise CircuitError('PCB endpoint grid mismatch; use explicit points_nm or another grid_nm')
        def heuristic(p):
            dx,dy = abs(p[0]-end[0]),abs(p[1]-end[1])
            return max(dx,dy)+(math.sqrt(2)-1)*min(dx,dy)
        start_state = (start,-1)
        heap = [(heuristic(start)/grid_nm*1000,0,start_state)]; costs = {start_state:0}; parents = {}
        while heap and searched < 100000:
            _,cost,state = heapq.heappop(heap)
            if cost != costs[state]:
                continue
            p,heading = state
            searched += 1
            if p == end:
                points_nm = [p]
                while state != start_state:
                    state = parents[state]; points_nm.append(state[0])
                points_nm.reverse(); break
            for direction,(dx,dy) in enumerate(((1,0),(0,1),(-1,0),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1))):
                q = (p[0]+dx*grid_nm,p[1]+dy*grid_nm)
                next_state = (q,direction)
                updated = cost+(1414 if dx and dy else 1000)+(180 if heading not in (-1,direction) else 0)
                if updated < costs.get(next_state,float('inf')) and segment_clear(board,spec,p,q):
                    costs[next_state] = updated; parents[next_state] = state
                    dx_end,dy_end = abs(q[0]-end[0])/grid_nm,abs(q[1]-end[1])/grid_nm
                    estimate = 1000*max(dx_end,dy_end)+414*min(dx_end,dy_end)
                    heapq.heappush(heap,(updated+estimate,updated,next_state))
        if points_nm is None:
            raise CircuitError('PCB_NO_CONSERVATIVE_ROUTE: search exhausted without changing the board')
    if not isinstance(points_nm,list) or not 2 <= len(points_nm) <= 2048:
        raise CircuitError('PCB points_nm must contain 2..2048 points')
    for p in points_nm:
        if not isinstance(p,(list,tuple)) or len(p) != 2 or any(type(n) is not int or n%10 or abs(n)>500000000 for n in p):
            raise CircuitError('PCB points require 10 nm aligned bounded integer coordinates')
    if list(points_nm[0]) != spec['start'] or list(points_nm[-1]) != spec['end']:
        raise CircuitError('PCB path endpoints must equal the real pad centres')
    for a,b in zip(points_nm,points_nm[1:]):
        dx,dy = abs(b[0]-a[0]),abs(b[1]-a[1])
        if (not dx and not dy) or (dx and dy and dx != dy):
            raise CircuitError('PCB path must use nonzero orthogonal or 45 degree segments')
        if not segment_clear(board,spec,a,b):
            raise CircuitError('PCB path crosses foreign copper/pad clearance or board edge')
    return {k:spec[k] for k in ('net','layer','width_nm')} | {'points_nm':simplify(points_nm),'searched_nodes':searched}


def wire_key(wire):
    points = tuple(map(tuple,simplify(wire['points_nm'])))
    return (wire['net'],wire['layer'],wire['width_nm'],min(points,points[::-1]))


def session_text(route, existing=(), vias=()):
    # ARES replaces the route set, it does not append a partial SES. Carry all
    # calibrated existing copper forward or a later net would erase earlier nets.
    from .pcb_vias import session_text as serialize
    return serialize([*existing,route],vias)


class PCB:
    def __init__(self, bridge, library):
        self.bridge, self.runtime = bridge, bridge.runtime
        self.locks = Live(bridge,library)

    def folder(self, session):
        return self.runtime.session_path(session).parent

    def read_attached(self, api, hwnd, session):
        path = self.folder(session)/('pcb-'+time.strftime('%H%M%S-')+uuid.uuid4().hex[:8]+'.edf')
        result = api.pcbexport(hwnd,str(path),self.runtime.load(session)['path'])
        if not result['ok'] or not path.is_file():
            raise CircuitError('PCB native export failed')
        board = parse_board(path.read_text(encoding='utf-8'),result.get('padstacks'))
        return {'status':'read','session':session,'token':board_token(board),'board':board,
                'native':result,'evidence_path':str(path),'desktop_input':False}

    def read(self, session):
        with self.locks.lock(session), self.bridge.attach(session,pcb=True) as (api,hwnd):
            result = self.read_attached(api,hwnd,session)
            receipt = self.folder(session)/'pcb-state.json'
            if not receipt.is_file():
                atomic_write(receipt,json_bytes({'session':session,'pending':False,'saved':False,
                                                'mode':'native_PCB','disk_roundtrip_verified':False}))
            from .pcb_push import wire_id
            data = json.loads(receipt.read_text(encoding='utf-8'))
            managed = set(data.get('managed_wire_ids',[])) if data.get('status')=='applied' and data.get('token')==result['token'] else set()
            return result | {'wire_inventory':[{'wire_id':wire_id(w),'mcp_managed':wire_id(w) in managed,'wire':w}
                                               for w in result['board']['wires']]}

    def save(self, session):
        """Verify native memory and archive write; disk copper needs reopen."""
        from .bridge import LABELS, upgrade_confirmation_allowed
        folder = self.folder(session)
        live = folder/'live-state.json'
        if live.is_file() and json.loads(live.read_text()).get('pending'):
            raise CircuitError('MIXED_PENDING_LIVE_AND_PCB_SAVE_NOT_CALIBRATED')
        path = Path(self.runtime.load(session)['path'])
        before_hash, before_stat = digest(path.read_bytes()), path.stat()
        atomic_write(folder/f'before-pcb-save-{before_hash}.pdsprj',path.read_bytes())
        with self.locks.lock(session), self.bridge.attach(session,pcb=True) as (api,hwnd):
            before = self.read_attached(api,hwnd,session)
            response = api.command(hwnd,LABELS['save'],'')
            confirmations, previous, stable = [], None, 0
            deadline = time.monotonic()+15
            while True:
                time.sleep(0.2)
                state = api.snapshot(hwnd)
                if not confirmations and upgrade_confirmation_allowed(state['modal'],path,self.runtime.root.parent):
                    confirmations.append(api.confirmupgrade(hwnd,state['modal'])); continue
                stat = path.stat(); signature = (stat.st_mtime_ns,stat.st_size)
                stable = stable+1 if signature == previous else 0; previous = signature
                written = signature != (before_stat.st_mtime_ns,before_stat.st_size)
                if state['modal'] or (written and stable >= 2) or time.monotonic() > deadline:
                    break
            after = self.read_attached(api,hwnd,session) if not state['modal'] else None
        with zipfile.ZipFile(path) as archive:
            archive_ok = archive.testzip() is None and any(n.upper().endswith('.LYT') for n in archive.namelist())
        same = after is not None and before['token'] == after['token']
        success = response.get('dispatched') and written and stable >= 2 and same and archive_ok and not state['modal']
        result = {'status':'verified' if success else 'unconfirmed','session':session,'action':'save',
                  'command':response,'before_sha256':before_hash,'after_sha256':digest(path.read_bytes()),
                  'save_file_written':written,'pcb_memory_preserved':same,'archive_readable':archive_ok,
                  'verification_scope':'native_PCB_memory_preserved_and_archive_written; disk_copper_requires_reopen',
                  'pcb_disk_roundtrip_verified':False,'known_dialog_confirmations':confirmations,
                  'modal':state['modal'],'desktop_input':False,'simulation_verified':False}
        if success:
            receipt = folder/'pcb-state.json'
            data = json.loads(receipt.read_text()) if receipt.is_file() else {}
            atomic_write(receipt,json_bytes({**data,'pending':False,'saved':True,'saved_board':after['board'],
                                            'saved_token':after['token'],'disk_roundtrip_verified':False}))
        evidence = folder/('pcb-save-'+uuid.uuid4().hex[:8]+'.json')
        atomic_write(evidence,json_bytes(result))
        return result | {'evidence_path':str(evidence)}

    def route(self, session, expected_token, net, layer, from_pad, to_pad, width_nm=None,
              clearance_nm=None, points_nm=None, grid_nm=127000):
        with self.locks.lock(session), self.bridge.attach(session,pcb=True) as (api,hwnd):
            live = self.folder(session)/'live-state.json'
            if live.is_file() and json.loads(live.read_text()).get('pending'):
                raise CircuitError('SAVE_PENDING_SCHEMATIC_EDITS_BEFORE_PCB_ROUTING')
            before = self.read_attached(api,hwnd,session)
            if before['token'] != expected_token:
                raise CircuitError('PCB_REVISION_CONFLICT: refresh pcb_read token')
            board = before['board']
            spec = route_spec(board,net,layer,from_pad,to_pad,width_nm,clearance_nm)
            route = plan_route(board,spec,points_nm,grid_nm)
            if wire_key(route) in {wire_key(w) for w in board['wires']}:
                raise CircuitError('PCB route already exists; not importing a duplicate')
            return self.import_plan(api,hwnd,session,before,{'wires':[route],'vias':[]},route)

    def route_layers(self, session, expected_token, net, from_pad, to_pad, start_layer='TOP',
                     end_layer='BOT', via_style='$VIA#0_SIGNAL', width_nm=None, clearance_nm=None,
                     waypoints=None, grid_nm=127000, max_vias=4):
        from .pcb_vias import layered_spec,plan_layered
        with self.locks.lock(session),self.bridge.attach(session,pcb=True) as (api,hwnd):
            live = self.folder(session)/'live-state.json'
            if live.is_file() and json.loads(live.read_text()).get('pending'):
                raise CircuitError('SAVE_PENDING_SCHEMATIC_EDITS_BEFORE_PCB_ROUTING')
            before = self.read_attached(api,hwnd,session)
            if before['token']!=expected_token:
                raise CircuitError('PCB_REVISION_CONFLICT: refresh pcb_read token')
            spec = layered_spec(before['board'],net,from_pad,to_pad,start_layer,end_layer,via_style,width_nm,clearance_nm)
            plan = plan_layered(before['board'],spec,waypoints,grid_nm,max_vias)
            old = collections.Counter(wire_key(w) for w in before['board']['wires'])
            if any(wire_key(w) in old for w in plan['wires']):
                raise CircuitError('PCB layered route contains an existing duplicate wire')
            return self.import_plan(api,hwnd,session,before,plan,plan)

    def route_push(self, session, expected_token, net, layer, from_pad, to_pad, points_nm,
                   movable_wire_ids, mode='plan', width_nm=None, clearance_nm=None,
                   grid_nm=127000, max_displacement_nm=1270000, chain=False):
        from .pcb_push import plan_push
        if mode not in ('plan','apply'):
            raise CircuitError('PCB_PUSH_MODE: choose plan or apply')
        if not Path(self.runtime.load(session)['path']).resolve().is_relative_to(self.runtime.root.parent.resolve()):
            raise CircuitError('PCB_PUSH_REQUIRES_WORKSPACE_COPY')
        with self.locks.lock(session),self.bridge.attach(session,pcb=True) as (api,hwnd):
            live = self.folder(session)/'live-state.json'
            if live.is_file() and json.loads(live.read_text()).get('pending'):
                raise CircuitError('SAVE_PENDING_SCHEMATIC_EDITS_BEFORE_PCB_ROUTING')
            before = self.read_attached(api,hwnd,session)
            if before['token']!=expected_token:
                raise CircuitError('PCB_REVISION_CONFLICT: refresh pcb_read token')
            receipt = self.folder(session)/'pcb-state.json'
            previous = json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else {}
            managed = previous.get('managed_wire_ids',[]) if previous.get('status')=='applied' and previous.get('token')==before['token'] else []
            spec = route_spec(before['board'],net,layer,from_pad,to_pad,width_nm,clearance_nm)
            if chain:
                from .pcb_chain import plan_chain
                plan = plan_chain(before['board'],spec,points_nm,movable_wire_ids,managed,grid_nm,max_displacement_nm)
            else:
                plan = plan_push(before['board'],spec,points_nm,movable_wire_ids,managed,grid_nm,max_displacement_nm)
            if mode=='apply':
                return self.import_plan(api,hwnd,session,before,plan,plan,require_native_clearance=True)
            path = self.folder(session)/('push-plan-'+uuid.uuid4().hex[:8]+'.json')
            result = {'status':'planned','applied':False,'session':session,'token':before['token'],
                      'plan':plan,'desktop_input':False,'native_copper_verified':False,
                      'full_drc':False,'push_shove':False,'proposed_push_shove':True}
            atomic_write(path,json_bytes(result))
            return result | {'evidence_path':str(path)}

    def import_plan(self, api, hwnd, session, before, plan, route, require_native_clearance=False):
        from .pcb_vias import session_text as serialize,via_key
        from .pcb_push import wire_id
        board = before['board']
        receipt = self.folder(session)/'pcb-state.json'
        previous = json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else {}
        managed = set(previous.get('managed_wire_ids',[])) if previous.get('status')=='applied' and previous.get('token')==before['token'] else set()
        removed = collections.Counter(wire_key(w) for w in plan.get('remove_wires',[]))
        old = collections.Counter(wire_key(w) for w in board['wires'])
        if removed-old:
            raise CircuitError('PCB_REPLACEMENT_NOT_PRESENT')
        remaining, consumption = [],removed.copy()
        for wire in board['wires']:
            key = wire_key(wire)
            if consumption[key]:
                consumption[key] -= 1
            else:
                remaining.append(wire)
        path = self.folder(session)/('route-'+uuid.uuid4().hex[:8]+'.ses')
        atomic_write(path,serialize([*remaining,*plan['wires']],[*board.get('vias',[]),*plan['vias']]).encode('utf-8'))
        result = {'status':'unconfirmed','session':session,'pending':True,'saved':False,
                  'may_have_applied':True,'atomic':False,'route':route,'before':before,
                  'full_drc':False,'push_shove':False,'desktop_input':False,
                  'managed_wire_ids':sorted(managed),'intent_exchange_path':str(path)}
        atomic_write(receipt,json_bytes(result))
        try:
            from .notices import drc_notice_watcher
            guard_path = Path(before['evidence_path'])
            with drc_notice_watcher(self.runtime,session) as notice_watch:
                response = api.pcbimport(hwnd,str(path),self.runtime.load(session)['path'],str(guard_path),
                                         guard_path.read_bytes().decode('utf-8'),
                                         json.dumps(before['native']['padstacks'],ensure_ascii=False,separators=(',',':')))
            result['native_notice_watch'] = notice_watch
            result['native_drc_notices'] = notice_watch['notices']
            result['native_drc_notice'] = api.acknowledgedrcnotice(hwnd)
            after = self.read_attached(api,hwnd,session)
            new = collections.Counter(wire_key(w) for w in after['board']['wires'])
            old_vias = collections.Counter(via_key(v) for v in board.get('vias',[]))
            new_vias = collections.Counter(via_key(v) for v in after['board'].get('vias',[]))
            skeleton = lambda value:board_token({**value,'wires':[],'vias':[]})
            preserved = skeleton(board)==skeleton(after['board']) and not ((old-removed)-new) and not (old_vias-new_vias)
            matches = new==old-removed+collections.Counter(wire_key(w) for w in plan['wires'])
            vias_match = new_vias==old_vias+collections.Counter(via_key(v) for v in plan['vias'])
            native_clearance_ok = not require_native_clearance
            if require_native_clearance and response['ok'] and matches and vias_match and preserved:
                from .checks import drc_report
                guard = Path(after['evidence_path'])
                native_check = api.pcbdrc(hwnd,self.runtime.load(session)['path'],str(guard),guard.read_text(encoding='utf-8'),
                                         json.dumps(after['native']['padstacks'],ensure_ascii=False,separators=(',',':')))
                report = result['native_clearance_report'] = drc_report(native_check)
                checked = self.read_attached(api,hwnd,session)
                native_clearance_ok = report['completed'] and report['passed'] and checked['token']==after['token'] and checked['native']['document']==after['native']['document']
            applied = response['ok'] and preserved and matches and vias_match and not notice_watch['errors'] and native_clearance_ok
            result.update(native_response=response,after=after,token=after['token'],
                          status='applied' if applied else 'unconfirmed',
                          board_preserved=preserved,native_copper_verified=matches,native_vias_verified=vias_match,
                          replacement_delta_verified=bool(removed) and matches,
                          push_shove=bool(applied and plan.get('push_shove')),
                          native_clearance_verified=bool(require_native_clearance and native_clearance_ok))
            if applied:
                current_ids = {wire_id(w) for w in after['board']['wires']}
                managed = (managed & {wire_id(w) for w in remaining}) | {wire_id(w) for w in plan['wires']}
                result['managed_wire_ids'] = sorted(managed & current_ids)
        except Exception as error:
            result['error'] = str(error)
        evidence = self.folder(session)/('pcb-route-'+uuid.uuid4().hex[:8]+'.json')
        atomic_write(receipt,json_bytes(result));atomic_write(evidence,json_bytes(result))
        return {k:v for k,v in result.items() if k not in ('before','after')} | {'evidence_path':str(evidence)}
