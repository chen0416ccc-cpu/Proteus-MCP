"""Explicit channel plans: layout is authored, not inferred from shortest paths."""

import copy
from collections import defaultdict

from .model import CircuitError
from .native9 import GRID, movable_copy, checked_sheet_size
from .routing import place_components
from .schematic import STEP, arrange_labels, audit_crossings, body_box, bounds_report


def grid_point(value):
    if (not isinstance(value, list) or len(value)!=2 or
            any(type(v) not in (int,float) or abs(v)>500 or v*2 != int(v*2) for v in value)):
        raise CircuitError('WIRE_PLAN_COORDINATE: exact half-grid pair required')
    return [int(v*GRID) for v in value]


def dense(points):
    result=[]
    for a,b in zip(points,points[1:]):
        dx=(b[0]>a[0])-(b[0]<a[0]);dy=(b[1]>a[1])-(b[1]<a[1])
        length=(abs(b[0]-a[0])+abs(b[1]-a[1]))//STEP
        if not length or dx and dy:
            raise CircuitError('WIRE_PLAN_ORTHOGONAL: every segment must be horizontal or vertical')
        result.extend((a[0]//STEP+dx*i,a[1]//STEP+dy*i) for i in range(length))
    result.append(tuple(v//STEP for v in points[-1]))
    return result


def route_plan(templates, model, firmware, placements, layout, sheet_size, plan):
    if not isinstance(plan,dict) or set(plan)-{'junctions','terminals','wires','labels','component_labels'}:
        raise CircuitError('WIRE_PLAN_SCHEMA: junctions, terminals, wires, labels, component_labels only')
    if not isinstance(plan.get('wires'),list) or not 1<=len(plan['wires'])<=2000:
        raise CircuitError('WIRE_PLAN_SCHEMA: 1..2000 explicit wires required')
    size=checked_sheet_size(sheet_size or [100,70])
    b,positions=place_components(templates,model,firmware,placements,layout)
    boxes={ref:body_box(p) for ref,p in b.parts.items()}
    for part in b.parts.values():arrange_labels(part)
    from .label_layout import apply_component_labels, apply_label
    apply_component_labels(b.parts,plan.get('component_labels',{}))
    for ref,box in boxes.items():
        for other,ob in boxes.items():
            if ref<other and max(box[0],ob[0])<=min(box[2],ob[2]) and max(box[1],ob[1])<=min(box[3],ob[3]):
                raise CircuitError('WIRE_PLAN_BODY_COLLISION: '+ref+' '+other)
    nodes,coordinates={},{}
    for name,point in plan.get('junctions',{}).items():
        if not name.startswith('@') or name in nodes:raise CircuitError('WIRE_PLAN_NODE_ID')
        coordinates[name]=grid_point(point)
        nodes[name]={'type':'junction','origin':coordinates[name],'wire_keys':[None]*4}
        b.objects.append(nodes[name])
    for name,spec in plan.get('terminals',{}).items():
        if (not name.startswith('@') or name in nodes or not isinstance(spec,dict) or
                set(spec)-{'net','at','label'} or not {'net','at'}<=set(spec) or
                spec['net'] not in ('VCC','GND')):
            raise CircuitError('WIRE_PLAN_TERMINAL')
        connection=coordinates[name]=grid_point(spec['at'])
        net=spec['net']
        origin=[connection[0],connection[1]+(-GRID if net=='GND' else GRID)]
        node=movable_copy(b.templates['$TERGROUND' if net=='GND' else '$TERPOWER'],origin)
        node['label']['text']=net
        if 'label' in spec:apply_label(node['label'],spec['label'])
        nodes[name]=node;b.objects.append(node)
    def point(endpoint):
        if endpoint in coordinates:return coordinates[endpoint]
        try:value=b.pin(endpoint)
        except (ValueError,KeyError):raise CircuitError('WIRE_PLAN_UNKNOWN_ENDPOINT: '+str(endpoint))
        if value is None:raise CircuitError('WIRE_PLAN_HIDDEN_PIN: '+endpoint)
        return value
    usages=defaultdict(list)
    net_keys=defaultdict(list)
    for index,spec in enumerate(plan['wires']):
        net=spec['net'];ends=spec['ends'];points=[grid_point(p) for p in spec['points']]
        if net not in model['nets'] or len(ends)!=2 or ends[0]==ends[1]:raise CircuitError('WIRE_PLAN_NET_OR_ENDPOINTS')
        if points[0]!=point(ends[0]) or points[-1]!=point(ends[1]):raise CircuitError('WIRE_PLAN_ENDPOINT_GEOMETRY: '+str(ends))
        route=dense(points)
        # Never route through a symbol body, even if the pin references connect.
        for ref,box in boxes.items():
            if any(box[0]<=x*STEP<=box[2] and box[1]<=y*STEP<=box[3] for x,y in route):
                raise CircuitError('WIRE_PLAN_BODY_CONTACT: '+net+' '+ref)
        key='planned_'+str(index)
        for endpoint in ends:
            if endpoint in nodes:
                node=nodes[endpoint]
                if node['type']=='junction':
                    slots=node['wire_keys']
                    if None not in slots:raise CircuitError('WIRE_PLAN_NODE_DEGREE')
                    slots[slots.index(None)]=key
                else:
                    if node['wire_key'] is not None:raise CircuitError('WIRE_PLAN_TERMINAL_DEGREE')
                    if node['label']['text']!=net:raise CircuitError('WIRE_PLAN_POWER_NET')
                    node['wire_key']=key
            elif endpoint not in model['nets'][net]:raise CircuitError('WIRE_PLAN_PIN_NET_MISMATCH: '+endpoint)
        b.wire(key,[e for e in ends if e not in nodes],points)
        net_keys[net].append(key);usages[net].append(route)
    if any(n['type']=='junction' and sum(k is not None for k in n['wire_keys'])<2 or
           n['type']=='terminal' and n['wire_key'] is None for n in nodes.values()):
        raise CircuitError('WIRE_PLAN_UNUSED_NODE')
    crossings=audit_crossings(usages)
    from .native_backend import label_nets
    label_nets(b.objects,model)
    for net,position in plan.get('labels',{}).items():
        labels=[label for w in b.objects if w['type']=='wire' for label in w['labels'] if label['text']==net]
        if len(labels)!=1:raise CircuitError('WIRE_PLAN_LABEL_TREE: '+net)
        apply_label(labels[0],position)
    audit=bounds_report(b.objects,size)
    if not audit['all_geometry_inside_frame']:raise CircuitError('WIRE_PLAN_OUTSIDE_SHEET: '+str(audit))
    return b.objects,{'algorithm':'explicit_ordered_channel_plan','placements':positions,'sheet':audit,
        'signal_connections':'continuous_wires_and_explicit_junctions','grid_pitch':.5,
        'crossing_count':crossings,'illegal_wire_contacts':0,
        'wire_branches':len(plan['wires']),'bends':sum(len(w['points'])-2 for w in plan['wires']),
        'quality_scope':'authored channels, orthogonal geometry and wire/body contact audit; native geometry roundtrip still required'}
