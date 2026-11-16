"""Join live physical wires, named DSIM networks and native device states."""
import re
from pathlib import Path

from .model import CircuitError, digest


def references(refs):
    if (not isinstance(refs,list) or not 1 <= len(refs) <= 16 or
            any(not isinstance(r,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}(?::[A-Z])?',r) for r in refs) or
            len(set(refs)) != len(refs)):
        raise CircuitError('Provide 1..16 distinct component references')
    return refs


def topology(state):
    wires = {w['address']:w for w in state['wires']}
    parent = {w:w for w in wires}
    parts = {p['ref']:p for p in state['components']}
    owners = {p['address'] for p in parts.values()}
    objects = {o['address']:o for o in state.get('objects',[])}

    def root(w):
        while parent[w] != w:
            w = parent[w]
        return w

    def join(a,b):
        parent[root(a)] = root(b)

    joins, named = {}, {}
    names = {w:set() for w in wires}
    for key,wire in wires.items():
        for end in wire['endpoints']:
            owner = end['owner']
            if owner == '0x0' or owner in owners:
                continue
            obj = objects.get(owner)
            if not obj or obj['type'] not in ('.?AVDOT@@','.?AVTERMINAL@@'):
                raise CircuitError('DIAGNOSTIC_UNKNOWN_WIRE_OWNER')
            if owner in joins:
                join(key,joins[owner])
            joins[owner] = key
            if obj['type'] == '.?AVTERMINAL@@' and obj.get('net'):
                names[key].add(obj['net'])
        names[key].update(l['text'] for l in wire.get('labels',[]) if l.get('text'))
        for name in names[key]:
            if name in named:
                join(key,named[name])
            named[name] = key
    groups = {}
    for key in wires:
        group = groups.setdefault(root(key),{'names':set(),'pins':[]})
        group['names'].update(names[key])
    result = {}
    for part in parts.values():
        for pin in part['pins']:
            endpoint = part['ref']+'.'+pin['number']
            if pin['wire'] == '0x0':
                result[endpoint] = {'names':[],'pins':[endpoint],'connected':False}
            elif pin['wire'] not in wires:
                raise CircuitError('DIAGNOSTIC_PIN_WIRE_MISSING: '+endpoint)
            else:
                group = groups[root(pin['wire'])]
                group['pins'].append(endpoint)
                result[endpoint] = group
    return {key:{'names':sorted(group['names']),'pins':sorted(group['pins']),
                 'connected':group.get('connected',True)} for key,group in result.items()}


def diagnostic_names(state, refs):
    mapping = topology(state)
    parts = {p['ref']:p for p in state['components']}
    if set(refs)-parts.keys():
        raise CircuitError('DIAGNOSTIC_COMPONENT_NOT_FOUND: '+','.join(sorted(set(refs)-parts.keys())))
    return sorted({n for ref in refs for pin in parts[ref]['pins']
                   for n in mapping[ref+'.'+pin['number']]['names']})


def diagnostic_topology(state):
    return {'connections':topology(state),'components':{p['ref']:{
        'device':p['device'],'pins':[(q['number'],q['name']) for q in p['pins']],
        'properties':{k:p.get('properties',{}).get(k) for k in ('PROGRAM','CLOCK')}}
        for p in state['components']}}


def join_diagnostic(capture, refs, levels):
    state = capture['state']
    mapping = topology(state)
    nets = {n['name']:n for n in capture['vector']['nets']}
    parts = []
    for part in state['components']:
        if part['ref'] not in refs:
            continue
        pins = []
        for pin in part['pins']:
            group = mapping[part['ref']+'.'+pin['number']]
            readings = [{**nets[n],'level':levels(nets[n]['state'])} for n in group['names'] if n in nets]
            known = {r['level'] for r in readings if r['level'] is not None}
            pins.append({'number':pin['number'],'name':pin['name'],**group,'readings':readings,
                         'level':next(iter(known)) if len(known)==1 else None,
                         'measurement':'digital' if known else 'unavailable_or_analogue_not_measured'})
        parts.append({'ref':part['ref'],'device':part['device'],'properties':part['properties'],
                      'native_state':part['properties'].get('STATE'),'pins':pins})
    return {'components':parts,'simulation_time_before':capture['simulation_time_before'],
            'simulation_time_after':capture['simulation_time_after'],
            'same_native_clock':capture['simulation_time_before']==capture['simulation_time_after'],
            'digital_vector_stable':capture['vector']['stable_snapshot'],
            'optical_verified':False,'analogue_measured':False,
            'display_guidance':'If screen differs from native state, pause and redraw before changing firmware or wires'}


def file_provenance(state):
    project = Path(state['path'])
    firmware = []
    for part in state['components']:
        program = part.get('properties',{}).get('PROGRAM')
        if not program:
            continue
        path = Path(program)
        if not path.is_absolute():
            path = project.parent/path
        exists = path.is_file()
        firmware.append({'ref':part['ref'],'program':program,'resolved_path':str(path.resolve()),
                         'file_exists':exists,'sha256':digest(path.read_bytes()) if exists and path.stat().st_size<=16777216 else None,
                         'loaded_image_verified':False})
    return {'project_path':str(project),'project_sha256':digest(project.read_bytes()),
            'firmware':firmware,'scope':'Hashes describe current disk files, not a readback of loaded MCU memory'}
