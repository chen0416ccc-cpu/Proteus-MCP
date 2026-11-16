"""Read real installed library definitions; never substitute a lookalike."""
import copy
import re
import struct
from functools import lru_cache
from pathlib import Path

from .geometry import transform_xy
from .library import LibraryIndex
from .model import CircuitError


ANIMATED_PROFILES = {
    'BUTTON': (2, '8e6ab657fd14471da4c48995a51f9f549ae99498563300c75d446466a6a93fa0'),
    'LOGICSTATE': (2, 'de37913231b58642293f584bab632977e922140a166da567d108fbee38ce9eb9'),
    '7SEG-MPX4-CC': (17, '14c276840372dcdae672afa4a1029c988950cd84e2dfd85ed3f57c08dd83b97e'),
    '7SEG-MPX4-CA': (17, 'f18e4fce44b1618cccf721dae5c816578a5be91813bfa228884204005df6c692'),
}

# Static-looking library symbols can still require a native active instance.
ACTIVE_INSTANCE_PROFILES = {
    'BUZZER': ('0c59ac064d2f2058e3763aefb57fa72feb61391bad381c458045ec3fb5827434',
               'SPEAKER,0,DLL', 8, '00'*9),
}


def instance_profile(spec):
    if spec['animation_frames']:
        return 8, '000000001000000000'
    active = spec.get('active_model')
    if active:
        profile = ACTIVE_INSTANCE_PROFILES.get(spec['device'])
        if not profile or profile[:2] != (spec.get('definition_sha256'), active):
            raise CircuitError('ACTIVE_INSTANCE_NOT_CALIBRATED: '+spec['device'])
        return profile[2:]
    return spec['kind'], '00'*9


def animation_frames(blob, begin, end):
    frames, names = [], set()
    while begin < end:
        name, begin = _cstring(blob, begin)
        if not re.fullmatch('[A-Za-z0-9_]{1,96}', name) or name in names or len(frames) >= 256 or begin+12 > end:
            raise CircuitError('Invalid native animation frame name or bounds')
        size, count, version = struct.unpack_from('<III', blob, begin)
        stop, pos = begin+size, begin+12
        if size < 12 or stop > end or count > 10000 or version != 1:
            raise CircuitError('Invalid native animation frame header')
        lengths = []
        for _ in range(count):
            if pos+4 > stop:
                raise CircuitError('Truncated native animation primitive')
            length = struct.unpack_from('<I', blob, pos)[0]
            if length < 8 or pos+length > stop:
                raise CircuitError('Invalid native animation primitive length')
            lengths.append(length)
            pos += length
        if pos != stop:
            raise CircuitError('Native animation frame primitive count mismatch')
        frames.append({'name':name, 'size':size, 'primitive_count':count, 'primitive_sizes':lengths})
        names.add(name)
        begin = stop
    return frames


def _cstring(data, offset):
    end = data.find(b'\0', offset)
    if end < offset or end-offset > 128000:
        raise CircuitError('Invalid device string')
    return data[offset:end].decode('utf-8'), end+1


def pinout_tables(text):
    tables = {}
    for block in re.split(r'(?m)^\*PINOUT\s+', text)[1:]:
        title, _, body = block.partition('\n')
        body = re.split(r'\{\*[A-Z]+\}', body)[0]
        count = re.search(r'\bELEMENTS\s*=\s*(\d+)', body)
        elements = int(count[1]) if count else 1
        if not 1 <= elements <= 26:
            raise CircuitError('Uncalibrated packaging element count')
        pins = []
        pattern = r'^\{?(PIN|IP|OP|IO|PP|TS|OC|OE|PS|NC)\s+("[^"]+"|[^=\r\n]+?)\s*=\s*([^}\r\n]+)\}?$'
        for match in re.finditer(pattern,body,re.M):
            label, numbers = match[2].strip().strip('"'), [n.strip() for n in match[3].split(',')]
            hidden = label.startswith('(') and label.endswith(')')
            label = label[1:-1] if hidden else label
            if any(not re.fullmatch(r'[A-Za-z0-9_+.-]{1,24}',n) for n in numbers) or len(numbers) not in (1,elements):
                raise CircuitError('Uncalibrated packaging pin numbers')
            pins.append({'name':label,'numbers':numbers,'hidden':hidden,'type':match[1]})
        common = re.search(r'\bCOMMON\s*=([^}\r\n]+)',body)
        tables[title.strip()] = {'elements':elements,'pins':pins,
                                'common':common[1].split(',') if common else []}
    return tables


def external_pinouts(data, name):
    records = []
    for match in re.finditer(rb'\*PINOUT [^\r\n\0]+\r?\n',data):
        if match.group().decode('utf-8').strip() != '*PINOUT '+name:
            continue
        start, end = match.start(), data.find(b'\0',match.end())
        if start >= 4 and end >= 0 and struct.unpack_from('<I',data,start-4)[0] == end-start+5:
            records.append(data[start:end].decode('utf-8'))
    return pinout_tables('\n'.join(records))


def unit_view(spec, unit=None, package=None):
    result = copy.deepcopy({k:v for k,v in spec.items() if k != 'blob'})
    tables = spec.get('packagings',{})
    package = package or spec.get('defaults',{}).get('PACKAGE')
    table = tables.get(package) or (next(iter(tables.values())) if len(tables) == 1 else None)
    count = table['elements'] if table else 1
    if unit is not None and (not isinstance(unit,str) or not re.fullmatch('[A-Z]',unit) or ord(unit)-65 >= count):
        raise CircuitError('MULTI_UNIT_INVALID_SELECTION: unit must select a real packaging element')
    if count > 1 and unit is None:
        raise CircuitError('MULTI_UNIT_SELECTION_REQUIRED: choose an explicit A..'+chr(64+count))
    index = ord(unit)-65 if unit else 0
    if table:
        mapping = {p['name']:p for p in table['pins']}
        for pin in result['pins']:
            if pin['name'] not in mapping:
                raise CircuitError('Packaging omits a visible device pin: '+pin['name'])
            numbers = mapping[pin['name']]['numbers']
            pin['number'] = numbers[index] if len(numbers) > 1 else numbers[0]
        for pin in table['pins']:
            if pin['hidden'] and pin['name'] not in {p['name'] for p in result['pins']}:
                numbers = pin['numbers']
                result['pins'].append({'name':pin['name'],'number':numbers[index] if len(numbers)>1 else numbers[0],
                                      'xy':None,'anchor':None,'hidden':True,'electrical_type':7,
                                      'source':'native_packaging_hidden_supply'})
    if len({p['number'] for p in result['pins']}) != len(result['pins']):
        raise CircuitError('MULTI_UNIT_AMBIGUOUS_PHYSICAL_PINS')
    result.update(selected_unit=unit,selected_package=package,unit_count=count)
    return result


def parse_device(blob, name, allow_unresolved_pins=False):
    if len(blob) < 40:
        raise CircuitError('Truncated device definition')
    size, prop_at, pin_count, kind, elements, units = struct.unpack_from('<IIHHHH', blob)
    if size != len(blob) or not 16 <= prop_at < size or pin_count > 1024:
        raise CircuitError('Unsupported device header')
    pos = 16+struct.unpack_from('<I', blob, 16)[0]
    pins = []
    for index in range(pin_count):
        if pos+16 > prop_at:
            raise CircuitError('Device pin table exceeds record')
        flags = blob[pos:pos+4]
        x, y, angle, mirror = struct.unpack_from('<iihH', blob, pos+4)
        pos += 16
        symbol, pos = _cstring(blob, pos)
        label, pos = _cstring(blob, pos)
        native_number, pos = _cstring(blob, pos)
        number = native_number or label
        hidden = not bool(flags[1] & 2)
        lengths = {'$PINSHORT':254000, '$PINDEFAULT':508000, '$PININVERT':508000,
                   '$PINPOSCLK':508000, '$PINNEGCLK':508000}
        if symbol not in lengths and not allow_unresolved_pins:
            raise CircuitError('Unsupported pin symbol '+symbol)
        pin_length = lengths.get(symbol)
        delta = transform_xy([-pin_length, 0], angle, mirror) if pin_length is not None else None
        pins.append({'number': number, 'native_number': native_number, 'name': label, 'anchor': [x,y],
                     'xy': None if hidden or delta is None else [x+delta[0], y+delta[1]],
                     'direction': transform_xy([-1,0], angle, mirror) if delta is not None else None,
                     'geometry_source': 'calibrated_pin_glyph' if delta is not None else 'requires_native_instance',
                     'symbol': symbol, 'hidden': hidden, 'electrical_type': flags[0]})
    # Animated symbols contain extra frame records between pins and properties.
    frames = []
    if kind in (2, 17):
        from .model import digest
        if ANIMATED_PROFILES.get(name) != (kind, digest(blob)):
            raise CircuitError('ANIMATED_DEVICE_NOT_CALIBRATED: '+name)
        frames = animation_frames(blob, pos, prop_at)
        expected = (['BUTTON_0','BUTTON_1'] if name == 'BUTTON' else ['LS_0','LS_1'] if name == 'LOGICSTATE'
                    else ['7SEG_C']+[f'7SEG_{i}_{j}' for i in range(8) for j in range(2)])
        if [frame['name'] for frame in frames] != expected:
            raise CircuitError('Native animation frame sequence mismatch')
    elif kind not in (0, 8) or (kind == 0 and pos != prop_at):
        raise CircuitError('Unsupported native device kind or pin table')
    if len({p['number'] for p in pins}) != len(pins) or any(not p['number'] for p in pins):
        raise CircuitError('Ambiguous physical pin numbering; select an explicit unit')
    prop_size = struct.unpack_from('<I', blob, prop_at)[0]
    if prop_at+4+prop_size != size:
        raise CircuitError('Unsupported property payload')
    text = blob[prop_at+4:].rstrip(b'\0').decode('utf-8')
    sections = re.split(r'\{\*([A-Z]+)\}\r?\n?', text)
    sections = dict(zip(sections[1::2], sections[2::2]))
    defaults = sections.get('COMPONENT','').split('*PINOUT',1)[0].strip()+'\n'
    values = dict(re.findall(r'^\{?([A-Z0-9_]+)=([^\r\n}]+)\}?$', defaults, re.M))
    meta = dict(re.findall(r'^\{?([A-Z0-9_]+)=([^\r\n}]+)\}?$', sections.get('DEVICE',''), re.M))
    return {'device':name, 'kind':kind, 'elements':elements, 'units':units,
            'animation_frames':frames, 'active_model':meta.get('ACTIVE'),
            'pins':pins, 'properties':defaults, 'defaults':values,
            'unresolved_pin_glyphs':sorted({p['symbol'] for p in pins if p['geometry_source']=='requires_native_instance'}),
            'prefix':meta.get('PREFIX','U'), 'raw_properties':text,
            'packagings':pinout_tables(text),'pinout_name':meta.get('PINOUT'),
            'source':'installed_native_library_definition'}


def definition(device, library_path=None, allow_unresolved_pins=False):
    from .library import default_library
    root = default_library()
    files = [Path(library_path)] if library_path is not None else sorted(root.glob('*.LIB'))
    signature = tuple((str(p.resolve()), p.stat().st_mtime_ns, p.stat().st_size) for p in files if p.is_file())
    return _definition(device, library_path, allow_unresolved_pins, str(root), signature)


@lru_cache(maxsize=512)
def _definition(device, library_path, allow_unresolved_pins, library_root, signature=()):
    index = LibraryIndex(library_root)
    results = index.search(device, 200)['results']
    matches = [r for r in results if r['device'] == device and
               (library_path is None or Path(r['library_path']).resolve() == Path(library_path).resolve())]
    if not matches:
        raise CircuitError('Native library device not found: '+device)
    from .native9 import PIN_ORDER
    if library_path is None and len(matches) > 1 and device not in PIN_ORDER:
        raise CircuitError('Ambiguous device library; supply library_path: '+device)
    # Library order is the same deterministic preference as library_search.
    match = matches[0]
    data = Path(match['library_path']).read_bytes()
    at = match['object_offset']
    size = struct.unpack_from('<I', data, at)[0]
    if not 40 <= size <= 16*1024*1024 or at+size > len(data):
        raise CircuitError('Invalid device payload size')
    result = parse_device(data[at:at+size], device, allow_unresolved_pins=allow_unresolved_pins)
    if result['pinout_name']:
        table = external_pinouts(data,result['pinout_name']).get(result['pinout_name'])
        if table:
            result['packagings'] = {result['defaults'].get('PACKAGE',result['pinout_name']):table}
        else:
            raise CircuitError('External packaging table not found: '+result['pinout_name'])
    from .model import digest
    return {**result, **match, 'blob':data[at:at+size],
            'library_sha256':digest(data), 'definition_sha256':digest(data[at:at+size])}


def device_info(device, library_path=None, unit=None):
    result = definition(device, library_path, allow_unresolved_pins=True)
    if unit is not None:
        return unit_view(result,unit)
    return {key:copy.deepcopy(value) for key,value in result.items() if key != 'blob'}


def selected_definition(part):
    """Carry an explicit library choice through every offline drawing stage."""
    provenance = part.get('native_definition', {})
    spec = definition(part['device'], provenance.get('library_path'))
    if provenance and any(provenance.get(k) != spec[k] for k in ('library_sha256', 'definition_sha256')):
        raise CircuitError('NATIVE_LIBRARY_CHANGED: recreate the component from the current library')
    return spec


def native_order(device, native_definition=None):
    from .native9 import PIN_ORDER
    if device in PIN_ORDER:
        return PIN_ORDER[device]
    return [p['number'] for p in selected_definition({'device':device,'native_definition':native_definition or {}})['pins']]


def cache_entries(data, symbols=False):
    from .native9 import SIGNATURE
    first = data.find(b'\xfb\xff')
    limit = data.find(SIGNATURE)
    if first < 0 or limit < first:
        raise CircuitError('No calibrated embedded library')
    if not symbols:
        _, previous_end = cache_entries(data, symbols=True)
        first = previous_end+2
        if data[first:first+2] != b'\xfb\xff':
            raise CircuitError('No native device cache following symbol cache')
    pos, entries = first+2, {}
    while pos < limit:
        start = pos
        size = struct.unpack_from('<H', data, pos)[0]
        pos += 2
        if size == 0:
            return entries, start
        name = data[pos:pos+size].decode('utf-8'); pos += size+8
        for _ in range(2):
            size = struct.unpack_from('<H', data, pos)[0]
            pos += 2+size
        pos += 1
        size = struct.unpack_from('<I', data, pos)[0]
        if not 4 <= size <= limit-pos:
            raise CircuitError('Invalid embedded library record')
        entries[name] = {'start':start,'end':pos+size,'blob':data[pos:pos+size]}
        pos += size
    raise CircuitError('Missing embedded library terminator')


def ensure_cache(data, devices):
    from .native9 import inspect_components, OBJECTS, Reader
    entries, end = cache_entries(data)
    specs = {}
    for item in devices:
        part = {'device':item} if isinstance(item, str) else item
        name = part['device']
        if part.get('native_definition') or name not in entries:
            spec = selected_definition(part)
            if name in specs and specs[name]['definition_sha256'] != spec['definition_sha256']:
                raise CircuitError('NATIVE_CACHE_NAME_COLLISION: different definitions share '+name)
            if name in entries and entries[name]['blob'] != spec['blob']:
                raise CircuitError('NATIVE_CACHE_NAME_COLLISION: template definition differs for '+name)
            specs[name] = spec
        else:
            specs.setdefault(name, None)
    missing = sorted(set(specs)-entries.keys())
    if not missing:
        return data
    payload = bytearray()
    for name in missing:
        spec = specs[name]
        encoded = name.encode('utf-8')
        payload += struct.pack('<H',len(encoded))+encoded+b'\0'*13+spec['blob']
    parsed = inspect_components(data)
    delta = len(payload)
    result = bytearray(data[:end]+payload+data[end:])
    circuit = parsed['circuit_start']
    directory = struct.unpack_from('<I',data,circuit-4)[0]
    struct.pack_into('<I',result,circuit-4+delta,directory+delta)
    for obj in parsed['objects']:
        if obj['type'] == 'component':
            fields = [(obj['pointers_offset']+4*i,p) for i,p in enumerate(obj['pointers'])]
        elif obj['type'] == 'junction':
            fields = [(obj['start']+9+4*i,p) for i,p in enumerate(obj['pointers'])]
        elif obj['type'] == 'terminal':
            fields = [(obj['pointer_offset'],obj['pointer'])]
        else:
            fields = []
        for at,pointer in fields:
            if pointer:
                struct.pack_into('<I',result,at+delta,pointer+delta)
    r = Reader(data,directory)
    count = r.integer(2)
    if count != 2:
        raise CircuitError('Only root/default sheet cache relocation is calibrated')
    for _ in range(count):
        r.integer();r.read(r.integer(2));r.integer(2)
        at = r.offset; pointer = r.integer()
        struct.pack_into('<I',result,at+delta,pointer+delta)
    inspect_components(bytes(result))
    return bytes(result)
