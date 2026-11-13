"""Revision-checked live schematic edits on owned, version-pinned sessions."""
import copy
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .geometry import orientation
from .model import CircuitError, atomic_write, digest, identifier, json_bytes

BASE_KEYS = ['CLOCK', 'PROGRAM']
RESERVED = {'REF', 'DEVICE', 'CHILD', 'PARENT', 'PREFIX', 'PINOUT', 'UNIT', 'ELEMENT'}
DELETE_KINDS = {'delete', 'delete_wire', 'delete_junction'}


def state_token(state):
    return digest(json_bytes(state))


def geometry_issues(state):
    """Check real pin links and cached wire ends before certifying a batch."""
    wires = {w['address']:w for w in state.get('wires',[])}
    issues = []
    for address, wire in wires.items():
        points, endpoints = wire['points'], wire['endpoints']
        if len(points)<2 or len(endpoints)!=2 or points[0]!=endpoints[0]['xy'] or points[-1]!=endpoints[-1]['xy']:
            issues.append({'wire':address,'error':'LIVE_WIRE_POLYLINE_ENDPOINT_MISMATCH'})
        if any(a == b or a[0] != b[0] and a[1] != b[1] for a, b in zip(points, points[1:])):
            issues.append({'wire':address,'error':'LIVE_NONORTHOGONAL_OR_ZERO_SEGMENT'})
    for part in state['components']:
        for pin in part['pins']:
            if pin['wire']=='0x0':
                continue
            wire = wires.get(pin['wire'])
            if not wire or not any(e['owner']==part['address'] and e['xy']==pin['xy'] for e in wire['endpoints']):
                issues.append({'ref':part['ref'],'pin':pin['number'],'error':'LIVE_PIN_WIRE_LINK_MISMATCH'})
    return issues


def preflight_transforms(state, operations):
    """Predict the calibrated endpoint-only update, not an unimplemented rerouter."""
    from .geometry import transform_xy
    projected = copy.deepcopy(state)
    parts = {part['ref']: part for part in projected['components']}
    wires = {wire['address']: wire for wire in projected.get('wires', [])}
    for op in operations:
        if op['op'] != 'transform':
            continue
        part = parts.get(op['ref'])
        if part is None:
            # An added symbol's native instance has not been observed yet.
            continue

        def move(point):
            local = [point[axis]-part['position_nm'][axis] for axis in (0, 1)]
            local = transform_xy(local, -part.get('rotation', 0)*10)
            local = transform_xy(local, 0, part.get('mirror', 0))
            result = transform_xy(local, op['rotation']*10, op['mirror'])
            return [result[axis]+op['position_nm'][axis] for axis in (0, 1)]

        for pin in part['pins']:
            if pin.get('wire', '0x0') != '0x0':
                wire = wires.get(pin['wire'])
                if not wire or 'xy' not in pin or 'points' not in wire or len(wire['points']) < 2:
                    raise CircuitError('LIVE_TRANSFORM_GEOMETRY_UNAVAILABLE: '+op['ref'])
                ends = [index for index, end in enumerate(wire['endpoints'])
                        if end['owner'] == part['address'] and end.get('xy') == pin['xy']]
                if len(ends) != 1:
                    raise CircuitError('LIVE_TRANSFORM_ENDPOINT_AMBIGUOUS: '+op['ref'])
                index = ends[0]
                point = move(pin['xy'])
                wire['endpoints'][index]['xy'] = point
                wire['points'][0 if index == 0 else -1] = point
            if 'xy' in pin:
                pin['xy'] = move(pin['xy'])
        part.update({key: op[key] for key in ('position_nm', 'rotation', 'mirror')})
    if any(op['op'] == 'transform' for op in operations):
        issues = geometry_issues(projected)
        if issues:
            raise CircuitError('LIVE_TRANSFORM_REQUIRES_REROUTE: '+json.dumps(issues, ensure_ascii=True))
    return projected


def property_projection(state, keys):
    result = copy.deepcopy(state)
    for part in result['components']:
        part['properties'] = {k:v for k,v in part.get('properties',{}).items() if k in keys}
    return result


def reference(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}(?::[A-Z])?',value):
        raise CircuitError('Invalid native reference; use a package reference or REF:A unit')
    return value


def semantic_state(state, keys=None):
    """Compare undo snapshots without depending on reallocatable pointers."""
    owners = {p['address']:'component:'+p['ref'] for p in state['components']}
    owners.update({p['address']:'object:'+json.dumps({k:v for k,v in p.items() if k!='address'},sort_keys=True)
                   for p in state.get('objects',[])})
    wire_ids = {w['address']:i for i,w in enumerate(state.get('wires',[]))}
    def wire_record(wire):
        points = wire.get('points',[])
        points = min(points,points[::-1])
        ends = sorted((owners.get(e['owner'],'junction:'+str(e.get('xy'))),e.get('xy')) for e in wire['endpoints'])
        return {'points':points,'endpoints':ends,'labels':wire.get('labels',[])}
    wires = [wire_record(w) for w in state.get('wires',[])]
    parts = []
    for part in state['components']:
        props = part.get('properties',{})
        value = {k:v for k,v in part.items() if k not in ('address','pins','properties')}
        value['properties'] = {k:v for k,v in props.items() if keys is None or k in keys}
        value['pins'] = [{k:v for k,v in p.items() if k!='wire'} | {
            'connected_wire':wires[wire_ids[p['wire']]] if p['wire'] in wire_ids else None} for p in part['pins']]
        parts.append(value)
    return {'components':sorted(parts,key=lambda p:p['ref']),
            'objects':sorted((o for a,o in owners.items() if o.startswith('object:'))),
            'wires':sorted(wires,key=lambda w:json.dumps(w,sort_keys=True))}


def coordinate(value):
    if not isinstance(value, list) or len(value) != 2 or any(type(n) is not int or abs(n) > 500000000 for n in value):
        raise CircuitError('position_nm must contain two integers within +/-500000000 nm')
    return value


def property_values(value):
    if not isinstance(value, dict) or not 1 <= len(value) <= 32:
        raise CircuitError('Provide 1..32 native properties')
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,31}', key):
            raise CircuitError('Invalid native property key')
        key = key.upper()
        if key in RESERVED:
            raise CircuitError('Structural property is not exposed: '+key)
        if not isinstance(item, str) or len(item.encode('utf-8')) > 4096 or any(c in item for c in '\0\r\n{}'):
            raise CircuitError('Native property must be a bounded single-line string')
        if key in result:
            raise CircuitError('Duplicate case-insensitive native property')
        result[key] = item
    return result


def deletion_projection(state, operations):
    """Project exact removals; no net flood-fill or implicit junction cleanup."""
    refs = {op['ref'] for op in operations if op['op']=='delete'}
    removed = {p['address'] for p in state['components'] if p['ref'] in refs}
    owners = {p['address'] for p in state['components']}
    objects = {o['address']:o for o in state.get('objects', [])}
    junctions = {op['junction_address'] for op in operations if op['op']=='delete_junction'}
    if any(address not in objects or objects[address]['type']!='.?AVDOT@@' for address in junctions):
        raise CircuitError('LIVE_DELETE_UNKNOWN_JUNCTION')
    wires = {w['address'] for w in state.get('wires',[])
             if any(e['owner'] in removed for e in w['endpoints'])}
    for wire in state.get('wires',[]):
        if wire['address'] in wires and any(e['owner'] not in owners for e in wire['endpoints']):
            raise CircuitError('LIVE_DELETE_JUNCTION_NOT_CALIBRATED: only direct component-to-component wires')
    for part in state['components']:
        if part['ref'] in refs:
            attached = {p['wire'] for p in part['pins'] if p['wire'] != '0x0'}
            if not attached <= wires:
                raise CircuitError('LIVE_DELETE_UNRESOLVED_WIRE')
            op = next(op for op in operations if op.get('ref')==part['ref'])
            if attached and not op['delete_connected_wires']:
                raise CircuitError('LIVE_DELETE_CONNECTED: explicitly set delete_connected_wires=true')
    known_wires = {w['address']:w for w in state.get('wires', [])}
    for op in operations:
        if op['op']=='delete_wire':
            if op['wire_address'] not in known_wires:
                raise CircuitError('LIVE_DELETE_UNKNOWN_WIRE')
            wires.add(op['wire_address'])
        elif op['op']=='delete_junction':
            incident = {w['address'] for w in known_wires.values()
                        if any(e['owner']==op['junction_address'] for e in w['endpoints'])}
            if incident != set(op['connected_wire_addresses']):
                raise CircuitError('LIVE_DELETE_JUNCTION_WIRE_CONSENT_MISMATCH')
            wires.update(incident)
    for address in wires:
        for endpoint in known_wires[address]['endpoints']:
            owner = endpoint['owner']
            if owner not in owners and owner != '0x0' and objects.get(owner, {}).get('type') not in ('.?AVDOT@@', '.?AVTERMINAL@@'):
                raise CircuitError('LIVE_DELETE_UNKNOWN_ENDPOINT_OWNER')
    projected = copy.deepcopy(state)
    projected['components'] = [p for p in projected['components'] if p['ref'] not in refs]
    projected['wires'] = [w for w in projected.get('wires',[]) if w['address'] not in wires]
    if 'objects' in projected:
        projected['objects'] = [o for o in projected['objects'] if o['address'] not in junctions]
    for part in projected['components']:
        for pin in part['pins']:
            if pin['wire'] in wires:
                pin['wire'] = '0x0'
    return projected


def native_address(value):
    if not isinstance(value, str) or not re.fullmatch(r'0x[1-9a-f][0-9a-f]{0,15}', value):
        raise CircuitError('LIVE_DELETE_INVALID_ADDRESS: use an exact address from the current live_read')
    return value


def prepare_operations(state, operations, library):
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        raise CircuitError('Provide 1..100 live operations')
    if any(isinstance(op,dict) and op.get('op') in DELETE_KINDS for op in operations) and any(
            not isinstance(op,dict) or op.get('op') not in DELETE_KINDS for op in operations):
        raise CircuitError('LIVE_DELETE_SEPARATE_BATCH_REQUIRED')
    parts = {p['ref']: copy.deepcopy(p) for p in state['components']}
    result, deleted_targets = [], set()
    for original in operations:
        if not isinstance(original, dict):
            raise CircuitError('A live operation must be an object')
        op = copy.deepcopy(original)
        kind = op.get('op')
        fields = {'add': {'op','ref','device','position_nm','rotation','mirror','value'},
                  'transform': {'op','ref','position_nm','rotation','mirror'},
                  'set': {'op','ref','properties'}, 'connect': {'op','from_ref','from_pin','to_ref','to_pin'},
                  'delete': {'op','ref','delete_connected_wires'},
                  'delete_wire': {'op','wire_address'},
                  'delete_junction': {'op','junction_address','connected_wire_addresses'}}
        if kind not in fields or op.keys()-fields[kind]:
            raise CircuitError('Unknown live operation or fields: '+str(kind))
        if kind == 'add':
            ref = reference(op.get('ref'))
            if ref in parts:
                raise CircuitError('Duplicate native reference: '+ref)
            device = op.get('device')
            if not isinstance(device, str) or not re.fullmatch(r'[A-Za-z0-9_+./() -]{1,96}', device):
                raise CircuitError('Invalid native device name')
            matches = [r for r in library.search(device, 200)['results'] if r['device'] == device]
            from .native9 import PIN_ORDER
            if not matches or (len({r['library_path'] for r in matches}) != 1 and device not in PIN_ORDER):
                raise CircuitError('Native device missing or duplicated across libraries: '+device)
            op['position_nm'] = coordinate(op.get('position_nm'))
            op.setdefault('rotation', 0); op.setdefault('mirror', 0)
            orientation(op['rotation'], op['mirror'])
            op.setdefault('value', None)
            if op['value'] is not None:
                property_values({'VALUE': op['value']})
            from .devices import definition, unit_view
            # Native instances resolve custom Node/Busnode glyph geometry.
            # Static exporters still use the strict definition path.
            spec = definition(device, matches[0]['library_path'], allow_unresolved_pins=True)
            if spec['units'] > 1 or spec['elements'] > 1:
                raise CircuitError('MULTI_UNIT_NOT_CALIBRATED: explicit native unit selection is required')
            unit = ref.partition(':')[2] or None
            selected = unit_view(spec,unit)
            baseline_numbers = {'LED-RED':{'A':'1','K':'2'},'CAP-ELEC':{'+':'1','-':'2'}}
            for pin in selected['pins']:
                if device in baseline_numbers and pin['name'] in baseline_numbers[device]:
                    pin['number'] = baseline_numbers[device][pin['name']]
            siblings = [p for key,p in parts.items() if key.split(':')[0] == ref.split(':')[0]]
            if any(p['device'] != device for p in siblings):
                raise CircuitError('MULTI_UNIT_PACKAGE_DEVICE_CONFLICT')
            if siblings and (unit is None or any(':' not in p['ref'] for p in siblings)):
                raise CircuitError('MULTI_UNIT_PACKAGE_REFERENCE_CONFLICT')
            parts[ref] = {**op, 'pins': selected['pins'], 'properties': {}}
            op['expected_pins'] = [{'name':p['name'],'number':p['number'],'hidden':p['hidden']}
                                   for p in selected['pins'] if p.get('source') != 'native_packaging_hidden_supply']
            op['implicit_package_pins'] = [p for p in selected['pins'] if p.get('source') == 'native_packaging_hidden_supply']
            op['source_library'] = matches[0]['library_path']
            op['source_definition_sha256'] = digest(spec['blob'])
            op['unresolved_pin_glyphs'] = spec['unresolved_pin_glyphs']
            # Provenance is a receipt field, not an argument to native creation.
        elif kind == 'delete':
            ref = reference(op.get('ref'))
            if ref not in parts:
                raise CircuitError('Native component not found or repeated: '+ref)
            op.setdefault('delete_connected_wires',False)
            if type(op['delete_connected_wires']) is not bool:
                raise CircuitError('delete_connected_wires must be boolean')
            parts.pop(ref)
        elif kind in ('delete_wire', 'delete_junction'):
            field = 'wire_address' if kind=='delete_wire' else 'junction_address'
            address = native_address(op.get(field))
            if address in deleted_targets:
                raise CircuitError('LIVE_DELETE_REPEATED_TARGET')
            deleted_targets.add(address)
            if kind=='delete_junction':
                attached = op.get('connected_wire_addresses')
                if not isinstance(attached, list) or len(attached)>4 or len(set(map(str, attached)))!=len(attached):
                    raise CircuitError('LIVE_DELETE_JUNCTION_REQUIRES_EXACT_WIRE_LIST')
                op['connected_wire_addresses'] = [native_address(value) for value in attached]
        elif kind in ('transform', 'set'):
            ref = op.get('ref')
            if ref not in parts:
                raise CircuitError('Native component not found: '+str(ref))
            if kind == 'transform':
                part = parts[ref]
                op['position_nm'] = coordinate(op.get('position_nm', part['position_nm']))
                op.setdefault('rotation', part.get('rotation', 0)); op.setdefault('mirror', part.get('mirror', 0))
                orientation(op['rotation'], op['mirror'])
                part.update({k:op[k] for k in ('position_nm','rotation','mirror')})
            else:
                op['properties'] = property_values(op.get('properties'))
        else:
            for side in ('from', 'to'):
                ref, pin = op.get(side+'_ref'), op.get(side+'_pin')
                if ref not in parts or not isinstance(pin, str):
                    raise CircuitError('Invalid native endpoint')
                pins = parts[ref]['pins']
                matches = [p for p in pins if p['number'] == pin]
                if not matches:
                    matches = [p for p in pins if p['name'] == pin]
                if len(matches) != 1 or sum(p['name'] == matches[0]['name'] for p in pins) != 1:
                    raise CircuitError('Native pin does not exist or has ambiguous name')
                if matches[0].get('hidden'):
                    raise CircuitError('Hidden supply pins cannot be explicitly wired')
                op[side+'_pin'] = matches[0]['number']
            if (op['from_ref'], op['from_pin']) == (op['to_ref'], op['to_pin']):
                raise CircuitError('Cannot connect a pin to itself')
        result.append(op)
    if result[0]['op'] in DELETE_KINDS:
        deletion_projection(state,result)
    else:
        preflight_transforms(state, result)
    return result


def disk_matches(state, readback):
    from .native9 import GRID
    live = {p['ref']:p for p in state['components']}
    disk = {p['ref']:p for p in readback['components']}
    if live.keys() != disk.keys():
        return False
    for ref, part in live.items():
        target = disk[ref]
        if any(part[key] != target[key] for key in ('device','value','rotation','mirror')):
            return False
        if part['position_nm'] != [round(v*GRID) for v in target['origin_grid']]:
            return False
        from .native_backend import property_map
        stored = property_map(target['native_properties'])
        for key, value in part.get('properties', {}).items():
            if value not in (None, '') and stored.get(key) != value:
                return False
    # Native wires have explicit owner objects at both ends. Join branches
    # through non-component junction owners, never through a component body.
    parents = {w['address']:w['address'] for w in state.get('wires', [])}
    def root(key):
        while parents[key] != key:
            key = parents[key]
        return key
    addresses = {p['address'] for p in live.values()}
    terminals = {o['address']:o.get('net') for o in state.get('objects', []) if o.get('type')=='.?AVTERMINAL@@'}
    junctions, labels = {}, {}
    for wire in state.get('wires', []):
        for endpoint in wire['endpoints']:
            owner = endpoint['owner']
            if owner != '0x0' and owner not in addresses:
                if owner in junctions:
                    parents[root(wire['address'])] = root(junctions[owner])
                junctions[owner] = wire['address']
            name = terminals.get(owner)
            if name:
                if name in labels:
                    parents[root(wire['address'])] = root(labels[name])
                labels[name] = wire['address']
        for label in wire.get('labels', []):
            name = label.get('text')
            if name:
                if name in labels:
                    parents[root(wire['address'])] = root(labels[name])
                labels[name] = wire['address']
    groups = {}
    for part in live.values():
        for pin in part.get('pins', []):
            if pin['wire'] != '0x0':
                if pin['wire'] not in parents:
                    return False
                groups.setdefault(root(pin['wire']), set()).add(part['ref']+'.'+pin['number'])
    disk_groups = [set(pins) for pins in readback['stored_connectivity'].values()]
    explicit_pins = set().union(*groups.values()) if groups else set()
    if any(not any(group == stored & explicit_pins for stored in disk_groups) for group in groups.values()):
        return False
    return True


class Live:
    def __init__(self, bridge, library):
        self.bridge, self.runtime, self.library = bridge, bridge.runtime, library

    def receipt_path(self, session):
        return self.runtime.session_path(session).parent/'live-state.json'

    def history_path(self, session):
        return self.receipt_path(session).with_name('live-history.json')

    def record_history(self, session, before, after, keys):
        path = self.history_path(session)
        history = json.loads(path.read_text()) if path.is_file() else {'frames':[],'cursor':0}
        if semantic_state(before,keys) == semantic_state(after,keys):
            # No-op native commands do not provide a verifiable undo boundary.
            if path.is_file():
                path.unlink()
            return
        if history['frames']:
            cursor = history['cursor']
            previous = history['frames'][cursor-1 if cursor else 0]
            source = previous['after' if cursor else 'before']
            if semantic_state(before,previous['keys']) != semantic_state(source,previous['keys']):
                history = {'frames':[],'cursor':0}
        frames = history['frames'][:history['cursor']]+[{'before':before,'after':after,'keys':keys}]
        frames = frames[-50:]
        atomic_write(path,json_bytes({'frames':frames,'cursor':len(frames)}))

    def history(self, session, expected_token, action):
        from .bridge import simulation_state
        if action not in ('undo','redo'):
            raise CircuitError('History action must be undo or redo')
        with self.lock(session), self.bridge.attach(session,live=True) as (api,hwnd):
            pcb = self.receipt_path(session).with_name('pcb-state.json')
            if pcb.is_file() and json.loads(pcb.read_text()).get('pending'):
                raise CircuitError('SAVE_PENDING_PCB_EDITS_BEFORE_SCHEMATIC_EDITING')
            if simulation_state(api.snapshot(hwnd)) != 'stopped':
                raise CircuitError('LIVE_EDIT_REQUIRES_STOPPED_SIMULATION')
            keys = self.keys(session)
            before = api.liveread(hwnd,keys)
            self.check_document(session,before)
            if state_token(before) != expected_token:
                raise CircuitError('LIVE_REVISION_CONFLICT: refresh live_read token')
            path = self.history_path(session)
            history = json.loads(path.read_text()) if path.is_file() else {'frames':[],'cursor':0}
            cursor, frames = history['cursor'],history['frames']
            index = cursor-1 if action == 'undo' else cursor
            if not 0 <= index < len(frames):
                raise CircuitError('LIVE_HISTORY_EMPTY: only recorded MCP edits can be verified')
            frame = frames[index]
            source,target = ('after','before') if action == 'undo' else ('before','after')
            if semantic_state(before,frame['keys']) != semantic_state(frame[source],frame['keys']):
                raise CircuitError('LIVE_HISTORY_DIVERGED: current sheet differs from recorded frame')
            intent = {'status':'unconfirmed','session':session,'action':action,'pending':True,
                      'saved':False,'property_keys':keys,'may_have_applied':True,'desktop_input':False}
            atomic_write(self.receipt_path(session),json_bytes(intent))
            labels = ['Undo Changes','撤销'] if action == 'undo' else ['Redo Changes','恢复']
            try:
                response = api.livehistory(hwnd,keys,json.dumps(before,ensure_ascii=False,separators=(',',':')),labels)
            except Exception as error:
                result = intent | {'error':str(error),'atomic':False}
                atomic_write(self.receipt_path(session),json_bytes(result))
                return result
            deadline = time.monotonic()+5
            while True:
                time.sleep(.1)
                after = api.liveread(hwnd,keys)
                self.check_document(session,after)
                matched = semantic_state(after,frame['keys']) == semantic_state(frame[target],frame['keys'])
                if matched or not response.get('dispatched') or time.monotonic()>deadline:
                    break
            success = response.get('dispatched') and matched and before['document']==after['document']
            if success:
                history['cursor'] += -1 if action=='undo' else 1
                atomic_write(path,json_bytes(history))
            elif path.is_file():
                path.unlink()
            result = intent | {'status':'applied' if success else 'unconfirmed','command':response,
                               'semantic_state_verified':matched,'state':after,'token':state_token(after),
                               'atomic':False,'undo_depth':history['cursor'],'redo_depth':len(frames)-history['cursor']}
            evidence = self.receipt_path(session).with_name('live-'+action+'-'+uuid.uuid4().hex[:8]+'.json')
            atomic_write(evidence,json_bytes(result | {'before':before}))
            atomic_write(self.receipt_path(session),json_bytes(result))
            return result | {'evidence_path':str(evidence)}

    def keys(self, session):
        path = self.receipt_path(session)
        data = json.loads(path.read_text()) if path.is_file() else {}
        return sorted(set(BASE_KEYS+data.get('property_keys', [])))

    @contextmanager
    def lock(self, session):
        path = self.runtime.session_path(session).parent/'live.lock'
        try:
            fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL)
        except FileExistsError:
            raise CircuitError('LIVE_SESSION_BUSY: inspect lock before retrying') from None
        try:
            os.close(fd)
            yield
        finally:
            path.unlink()

    def read(self, session):
        with self.bridge.attach(session, live=True) as (api, hwnd):
            state = api.liveread(hwnd, self.keys(session))
            self.check_document(session, state)
        return {'status':'read', 'session':session, 'token':state_token(state), 'state':state,
                'units':'nanometres', 'desktop_input':False,
                'token_scope':'current sheet objects, component transforms, queried properties, pins and wire geometry'}

    def check_document(self, session, state):
        if not state.get('path') or Path(state['path']).resolve() != Path(self.runtime.load(session)['path']).resolve():
            raise CircuitError('LIVE_DOCUMENT_CONFLICT: active document differs from tracked project')

    def edit(self, session, expected_token, operations):
        from .bridge import simulation_state
        with self.lock(session), self.bridge.attach(session, live=True) as (api, hwnd):
            pcb = self.runtime.session_path(session).parent/'pcb-state.json'
            if pcb.is_file() and json.loads(pcb.read_text()).get('pending'):
                raise CircuitError('SAVE_PENDING_PCB_EDITS_BEFORE_SCHEMATIC_EDITING')
            status = api.snapshot(hwnd)
            if simulation_state(status) != 'stopped':
                raise CircuitError('LIVE_EDIT_REQUIRES_STOPPED_SIMULATION')
            keys = self.keys(session)
            before = api.liveread(hwnd, keys)
            self.check_document(session, before)
            if expected_token != state_token(before):
                raise CircuitError('LIVE_REVISION_CONFLICT: refresh live_read token')
            prepared = prepare_operations(before, operations, self.library)
            extra = [key for op in prepared if op['op'] == 'set' for key in op['properties'] if key != 'VALUE']
            original_keys, original = keys, before
            keys = sorted(set(keys+extra))
            before = api.liveread(hwnd,keys)
            self.check_document(session,before)
            if property_projection(before,original_keys) != original:
                raise CircuitError('LIVE_REVISION_CONFLICT: sheet changed during operation preflight')
            expected = json.dumps(before, ensure_ascii=False, separators=(',', ':'))
            intent = {'status':'unconfirmed', 'session':session, 'pending':True, 'saved':False,
                      'property_keys':keys, 'operations':prepared, 'before':before,
                      'may_have_applied':True, 'desktop_input':False}
            atomic_write(self.receipt_path(session), json_bytes(intent))
            # String ordering is retained by JSON transport and the JS reader.
            try:
                response = api.liveedit(hwnd, keys, expected, prepared)
            except Exception as error:
                result = {**intent, 'error':str(error), 'atomic':False}
                atomic_write(self.receipt_path(session), json_bytes(result))
                if self.history_path(session).is_file():
                    self.history_path(session).unlink()
                return result
            extra = [key for op in prepared if op['op'] == 'set' for key in op['properties'] if key != 'VALUE']
            keys = sorted(set(keys+extra))
            after = api.liveread(hwnd, keys)
            geometry = geometry_issues(after)
            if geometry and not response['failure']:
                response['failure'] = {'index':None,'error':'LIVE_GEOMETRY_POSTCONDITION_MISMATCH'}
            deletion_verified = None
            deletion_identity_verified = None
            if prepared[0]['op'] in DELETE_KINDS and not response['failure']:
                projection = deletion_projection(before,prepared)
                deletion_verified = semantic_state(after)==semantic_state(projection)
                # Native deletion must not reallocate or retarget surviving
                # objects; address-independent history comparison alone could
                # miss swapped coincident junctions.
                deletion_identity_verified = after == projection
                if not deletion_verified or not deletion_identity_verified:
                    response['failure'] = {'index':None,'error':'LIVE_DELETE_POSTCONDITION_MISMATCH'}
            result = {'status':'partial' if response['failure'] else 'applied', 'session':session,
                      'token':state_token(after), 'state':after, 'completed':response['completed'],
                      'failure':response['failure'], 'atomic':False, 'saved':False, 'desktop_input':False,
                      'property_keys':keys, 'operations':prepared, 'pending':True,
                      'geometry_verified':not geometry,'geometry_issues':geometry,
                      'geometry_scope':'observed pin/wire links, matching polyline ends and orthogonal nonzero segments',
                      'layout_quality_verified':False, 'automatic_live_reroute':False}
            if deletion_verified is not None:
                result['deletion_projection_verified'] = deletion_verified
                result['deletion_survivor_identity_verified'] = deletion_identity_verified
            evidence = self.receipt_path(session).parent/(time.strftime('live-edit-%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8]+'.json')
            atomic_write(evidence, json_bytes({**result, 'before':before, 'native_response':response}))
            atomic_write(self.receipt_path(session), json_bytes(result))
            if not response['failure']:
                self.record_history(session,before,after,keys)
            elif self.history_path(session).is_file():
                self.history_path(session).unlink()
            return {**result, 'evidence_path':str(evidence)}
