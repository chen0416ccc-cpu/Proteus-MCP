"""Calibrated through-vias and conservative layer-state routing.

This is not a push/shove engine or a replacement for Proteus DRC. The native
interchange omits drill dimensions, so live PAD_STYLE metadata is required.
"""
import heapq
import json
import math

from .model import CircuitError,json_bytes


def style_key(style):
    return json_bytes({k:v for k,v in style.items() if k!='exchange_name'}).decode('utf-8')


def via_key(via):
    return (via['net'],tuple(via['position_nm']),style_key(via['style']))


def parse_vias(root, library, layers, metadata, blockers):
    from .pcb import child,children,nm
    stacks = {s[1]:s for s in children(library,'padstack')}
    metadata = metadata or {}
    styles = {}
    def resolve(name):
        native = metadata.get(name)
        if not native or not native.get('calibrated') or native.get('native_type')!='CVIA':
            return None
        if native['span_code']!='800000000000ffff' or native['flags'] or native['aux_flags'] or native['secondary_dimension_nm']:
            return None
        width,drill = native['width_nm'],native['drill_nm']
        if not 10000 <= drill < width <= 10000000 or native['height_nm']!=width:
            return None
        radii = {}
        for shape in children(stacks.get(name,[]),'shape'):
            if len(shape)!=2 or not isinstance(shape[1],list):
                return None
            form = shape[1]
            if len(form)!=5 or form[0]!='circle' or nm(form[3]) or nm(form[4]) or nm(form[2])!=width:
                return None
            if form[1] in radii:
                return None
            radii[form[1]] = math.ceil(width/2)
        if not {'TOP','BOT'} <= radii.keys() or not set(layers) <= radii.keys():
            return None
        return {'exchange_name':name,'native':native,'radius_nm':radii,'through_hole':True,
                'drill_nm':drill,'copper_diameter_nm':width,'span':['TOP','BOT']}
    allowed = child(child(root,'structure'),'via')
    names = [v for v in allowed[1:] if isinstance(v,str)]
    for spare in children(allowed,'spare'):
        names.extend(v for v in spare[1:] if isinstance(v,str))
    for name in names:
        if name.startswith('$VIA'):
            style = resolve(name)
            if style:
                styles[name] = style
    vias = []
    for node in children(child(root,'wiring'),'via'):
        net = child(node,'net')
        if len(node)<5 or len(net)!=2:
            raise CircuitError('Invalid native PCB via')
        style = resolve(node[1])
        if not style:
            blockers.append('Uncalibrated native via drill/style/span: '+node[1])
            continue
        vias.append({'net':net[1],'position_nm':[nm(node[2]),nm(node[3])],'style':style})
    return vias,styles


def layered_spec(board, net, from_pad, to_pad, start_layer='TOP', end_layer='BOT',
                 via_style='$VIA#0_SIGNAL', width_nm=None, clearance_nm=None):
    from .pcb import route_spec
    if set(board['layers'])!={'TOP','BOT'}:
        raise CircuitError('PCB_LAYER_STACK_NOT_CALIBRATED: through-via routing currently requires a two-copper-layer board')
    spec = route_spec(board,net,start_layer,from_pad,to_pad,width_nm,clearance_nm)
    pads = {p['id']:p for p in board['pads']}
    if end_layer not in board['nets'][net]['layers'] or end_layer not in ('TOP','BOT') or end_layer not in pads[to_pad]['radius_nm']:
        raise CircuitError('PCB destination pad/layer is not permitted')
    # route_spec checks both endpoints on the starting layer. This initial
    # implementation deliberately requires through-hole endpoint pads.
    style = board.get('via_styles',{}).get(via_style)
    if not style:
        raise CircuitError('PCB_VIA_STYLE_NOT_CALIBRATED: fresh native drill/style/span metadata is required')
    return {**spec,'start_layer':start_layer,'end_layer':end_layer,'via_style':style}


def reusable_via(board, spec, point):
    for via in board.get('vias',[]):
        if via['net']==spec['net'] and via['position_nm']==list(point) and via['style']['through_hole']:
            return via
    return None


def via_clear(board, spec, point):
    from .pcb import point_distance
    if reusable_via(board,spec,point):
        return True
    style = spec['via_style']
    radius = max(style['radius_nm'].values())
    margin = radius+spec['clearance_nm']
    x0,y0,x1,y1 = board['boundary_nm']
    if not (x0+margin<=point[0]<=x1-margin and y0+margin<=point[1]<=y1-margin):
        return False
    for pad in board['pads']:
        # Also forbid same-net via-in-pad; its manufacturing requirements are
        # not encoded by this through-hole exchange contract.
        if math.dist(point,pad['position_nm']) < max(pad['radius_nm'].values(),default=0)+margin:
            return False
    for wire in board['wires']:
        if wire['net']!=spec['net'] and wire['layer'] in style['radius_nm']:
            if any(point_distance(point,a,b)<radius+wire['width_nm']/2+spec['clearance_nm'] for a,b in zip(wire['points_nm'],wire['points_nm'][1:])):
                return False
    for via in board.get('vias',[]):
        if math.dist(point,via['position_nm']) < radius+max(via['style']['radius_nm'].values())+spec['clearance_nm']:
            return False
    return True


def validate_path(board, spec, path, max_vias):
    from .pcb import segment_clear,simplify
    if not isinstance(path,list) or not 2<=len(path)<=4096:
        raise CircuitError('PCB layered waypoints must contain 2..4096 points')
    for item in path:
        if not isinstance(item,dict) or set(item)!={'layer','position_nm'} or item['layer'] not in ('TOP','BOT'):
            raise CircuitError('Each layered waypoint requires only layer and position_nm')
        p = item['position_nm']
        if not isinstance(p,(list,tuple)) or len(p)!=2 or any(type(n) is not int or n%10 or abs(n)>500000000 for n in p):
            raise CircuitError('PCB waypoints need bounded 10 nm aligned integer coordinates')
        if item['layer'] not in board['nets'][spec['net']]['layers']:
            raise CircuitError('PCB waypoint layer is not allowed for this net')
    if path[0]!={'layer':spec['start_layer'],'position_nm':spec['start']} or path[-1]!={'layer':spec['end_layer'],'position_nm':spec['end']}:
        raise CircuitError('PCB layered endpoints must equal real pad centres and chosen layers')
    wires,vias,points,transitions = [],[],[path[0]['position_nm']],0
    def finish(layer):
        if len(points)<2:
            raise CircuitError('PCB transition at an endpoint or consecutive zero-length layer changes are not supported')
        wires.append({'net':spec['net'],'layer':layer,'width_nm':spec['width_nm'],'points_nm':simplify(points)})
    for a,b in zip(path,path[1:]):
        p,q = a['position_nm'],b['position_nm']
        if a['layer']==b['layer']:
            dx,dy = abs(p[0]-q[0]),abs(p[1]-q[1])
            if not dx and not dy or dx and dy and dx!=dy:
                raise CircuitError('PCB layered path needs nonzero orthogonal/45 degree wire segments')
            if not segment_clear(board,{**spec,'layer':a['layer']},p,q):
                raise CircuitError('PCB layered path crosses foreign copper/pad/edge clearance')
            points.append(q)
        else:
            if p!=q or not via_clear(board,spec,p):
                raise CircuitError('PCB layer transition needs a clear full-diameter through-via at one position')
            finish(a['layer']); points=[q];transitions+=1
            if transitions>max_vias:
                raise CircuitError('PCB route exceeds max_vias transition limit')
            if not reusable_via(board,spec,p):
                candidate = {'net':spec['net'],'position_nm':list(p),'style':spec['via_style']}
                radius = max(candidate['style']['radius_nm'].values())
                if any(math.dist(p,v['position_nm'])<radius+max(v['style']['radius_nm'].values())+spec['clearance_nm'] for v in vias):
                    raise CircuitError('PCB planned via holes/annular rings are too close')
                vias.append(candidate)
    finish(path[-1]['layer'])
    return {'wires':wires,'vias':vias,'waypoints':path,'transition_count':transitions,
            'full_drc':False,'push_shove':False,'planner':'conservative_layer_state_A_star_or_explicit_path'}


def plan_layered(board, spec, waypoints=None, grid_nm=127000, max_vias=4):
    from .pcb import segment_clear
    if type(max_vias) is not int or not 0<=max_vias<=8:
        raise CircuitError('PCB max_vias must be an integer within 0..8')
    if type(grid_nm) is not int or grid_nm%10 or not 10000<=grid_nm<=1000000:
        raise CircuitError('PCB grid must be 10 nm aligned within 10000..1000000 nm')
    searched = 0
    if waypoints is None:
        start,end = tuple(spec['start']),tuple(spec['end'])
        if any((end[i]-start[i])%grid_nm for i in (0,1)):
            raise CircuitError('PCB endpoint grid mismatch; use explicit waypoints or another grid_nm')
        layers = ('TOP','BOT')
        allowed = {i for i,l in enumerate(layers) if l in board['nets'][spec['net']]['layers']}
        start_state = (*start,layers.index(spec['start_layer']),-1,0)
        target = layers.index(spec['end_layer'])
        via_cost = max(1000,round(5000000/grid_nm*1000))
        def heuristic(state):
            dx,dy = abs(state[0]-end[0])/grid_nm,abs(state[1]-end[1])/grid_nm
            return 1000*max(dx,dy)+414*min(dx,dy)+(via_cost if state[2]!=target else 0)
        heap = [(heuristic(start_state),0,start_state)]
        costs,parents,clear_vias = {start_state:0},{},{}
        while heap and searched<100000:
            _,cost,state = heapq.heappop(heap)
            if costs[state]!=cost:
                continue
            x,y,layer,heading,used = state
            searched+=1
            if (x,y)==end and layer==target:
                path = [state]
                while state!=start_state:
                    state=parents[state];path.append(state)
                waypoints=[{'position_nm':[s[0],s[1]],'layer':layers[s[2]]} for s in reversed(path)]
                break
            neighbours = []
            for direction,(dx,dy) in enumerate(((1,0),(0,1),(-1,0),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1))):
                q=(x+dx*grid_nm,y+dy*grid_nm)
                updated=cost+(1414 if dx and dy else 1000)+(180 if heading not in (-1,direction) else 0)
                next_state=(*q,layer,direction,used)
                if updated<costs.get(next_state,float('inf')) and segment_clear(board,{**spec,'layer':layers[layer]},(x,y),q):
                    neighbours.append((next_state,updated))
            if used<max_vias and 1-layer in allowed and heading!=-1 and (x,y)!=end:
                point=(x,y)
                if point not in clear_vias:
                    clear_vias[point]=via_clear(board,spec,point)
                if clear_vias[point]:
                    neighbours.append(((x,y,1-layer,-1,used+1),cost+via_cost))
            for next_state,updated in neighbours:
                if updated<costs.get(next_state,float('inf')):
                    costs[next_state]=updated;parents[next_state]=state
                    heapq.heappush(heap,(updated+heuristic(next_state),updated,next_state))
        if waypoints is None:
            raise CircuitError('PCB_NO_CONSERVATIVE_LAYER_ROUTE: search exhausted without changing the board')
    result=validate_path(board,spec,waypoints,max_vias)
    result['searched_nodes']=searched
    return result


def session_text(wires, vias=()):
    nets = {}
    def name(value):
        if not isinstance(value,str) or not 1<=len(value)<=128 or any(ord(c)<32 or c in '\\"' for c in value):
            raise CircuitError('PCB native name contains unsupported exchange characters')
        return value
    for wire in wires:
        net,layer=name(wire['net']),name(wire['layer'])
        if wire['width_nm']%10 or any(n%10 for p in wire['points_nm'] for n in p):
            raise CircuitError('ARES SES requires exact 10 nm units')
        path=' '.join(str(n//10) for p in wire['points_nm'] for n in p)
        nets.setdefault(net,[]).append(f'(wire (path {layer} {wire["width_nm"]//10} {path}) (type route))')
    for via in vias:
        net,style=name(via['net']),name(via['style']['exchange_name'])
        if any(n%10 for n in via['position_nm']):
            raise CircuitError('ARES SES via coordinates require exact 10 nm units')
        x,y=(n//10 for n in via['position_nm'])
        nets.setdefault(net,[]).append(f'(via "{style}" {x} {y} (type route))')
    payload=' '.join(f'(net "{net}" {" ".join(records)})' for net,records in sorted(nets.items()))
    return '(session native_mcp (routes (resolution mm 100000) (network_out '+payload+')))\n'
