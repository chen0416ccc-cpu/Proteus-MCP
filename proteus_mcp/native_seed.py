"""Construct local writer records from an installed native-format container."""

import io
import struct
import time
import xml.etree.ElementTree as ET
import zipfile

from .model import CircuitError
from .native9 import Builder, EMPTY_CDB, GRID, OBJECTS, PIN_ORDER, Reader, SIGNATURE, inspect_components, pin_xy

BASELINE_REFS = {'AT89C51': 'U1', 'RES': 'R1', 'LED-RED': 'D1', 'CAP': 'C1', 'CAP-ELEC': 'C2', 'CRYSTAL': 'X1'}


def empty_container(path):
    """Discard a whole known root sheet, not individual unknown object records."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or sum(i.file_size for i in archive.infolist()) > 64 * 1024 * 1024:
            raise CircuitError('BOOTSTRAP_SOURCE_TOO_LARGE_OR_DUPLICATE_MEMBERS')
        xml = ET.fromstring(archive.read('PROJECT.XML'))
        stamp = xml.find('TIMESTAMP')
        if (xml.tag != 'PROJECT' or stamp is None or stamp.get('RELEASE') != '900' or
                not stamp.get('FILEVER', '').isdigit() or not 900 <= int(stamp.get('FILEVER')) <= 907):
            raise CircuitError('BOOTSTRAP_SOURCE_FORMAT_UNSUPPORTED: requires a recognized Proteus 9 container')
        if archive.read('ROOT.CDB')[:95] != EMPTY_CDB[:95]:
            raise CircuitError('BOOTSTRAP_SOURCE_DATABASE_UNSUPPORTED')
        data = archive.read('ROOT.DSN')
    from .devices import cache_entries
    _, symbols_end = cache_entries(data, symbols=True)
    _, devices_end = cache_entries(data)
    first = devices_end + 6
    if (data[devices_end:devices_end+2] != b'\0\0' or data[first:first+len(SIGNATURE)] != SIGNATURE or
            data.find(SIGNATURE) != first):
        raise CircuitError('BOOTSTRAP_SOURCE_DIRECTORY_UNSUPPORTED')
    header = SIGNATURE + bytes.fromhex('00010001000100')
    end_header = first+len(SIGNATURE)+29
    sheet = data[first:end_header]
    if not (sheet.startswith(header) and sheet.endswith(b'\0\0'+OBJECTS)):
        raise CircuitError('BOOTSTRAP_SOURCE_SHEET_UNSUPPORTED')
    width, height = struct.unpack_from('<ii', sheet, len(header))
    if width <= 0 or height <= 0:
        raise CircuitError('BOOTSTRAP_SOURCE_SHEET_DIMENSIONS')
    directory = struct.unpack_from('<I', data, first-4)[0]
    second = directory-len(sheet)-1
    if second <= end_header or data[second:directory] != sheet+b'\xff' or data[second-1:second] != b'\xff':
        raise CircuitError('BOOTSTRAP_SOURCE_DEFAULT_SHEET_UNSUPPORTED')
    reader = Reader(data, directory)
    if reader.integer(2) != 2:
        raise CircuitError('BOOTSTRAP_SOURCE_MULTISHEET_UNSUPPORTED')
    entries = []
    for expected in ((3, b'CCT000', 2, first), (2, b'__DEFAULT__', 0, second)):
        entry = (reader.integer(), reader.read(reader.integer(2)), reader.integer(2), reader.integer())
        if entry != expected:
            raise CircuitError('BOOTSTRAP_SOURCE_DIRECTORY_UNSUPPORTED')
        entries.append(entry)
    # The symbol/style tables come from the local installation. No sample
    # components, firmware, scripts, graph objects or project metadata survive.
    prefix = data[:symbols_end+4] + b'\0\0'
    new_first = len(prefix)+4
    new_second = new_first+len(sheet)+1
    new_directory = new_second+len(sheet)+1
    table = struct.pack('<H', 2)
    for identity, name, flags, pointer in entries:
        table += (struct.pack('<IH', identity, len(name))+name+struct.pack('<HI', flags,
                  new_first if pointer == first else new_second))
    generated = (prefix+struct.pack('<I', new_directory)+(sheet+b'\xff')*2+table+data[reader.offset:])
    if inspect_components(generated)['objects']:
        raise CircuitError('BOOTSTRAP_SOURCE_ROOT_NOT_EMPTY')
    project = ET.Element('PROJECT')
    now = str(int(time.time()))
    ET.SubElement(project, 'TIMESTAMP', FILEVER=stamp.get('FILEVER'), RELEASE='900', CREATED=now, MODIFIED=now)
    ET.SubElement(project, 'VARIANTS')
    ET.SubElement(project, 'DEBUG')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('ROOT.DSN', generated)
        archive.writestr('ROOT.CDB', EMPTY_CDB)
        archive.writestr('PROJECT.XML', ET.tostring(project, encoding='utf-8', xml_declaration=True))
    return buffer.getvalue(), {'file_version': int(stamp.get('FILEVER')), 'source_objects_copied': False,
                              'source_non_dsn_members_copied': False, 'local_symbol_styles_retained': True}


def baseline_definitions():
    from .devices import definition
    result = {}
    for device, order in PIN_ORDER.items():
        spec = definition(device)
        if (spec['elements'] != 1 or spec['units'] != 1 or spec['animation_frames'] or len(spec['pins']) != len(order) or
                spec['kind'] != (8 if device == 'LED-RED' else 0) or
                any(pin.get('native_number') and pin['native_number'] != number for pin, number in zip(spec['pins'], order))):
            raise CircuitError('BOOTSTRAP_BASELINE_DEFINITION_CHANGED: ' + device)
        probe = {'device': device, 'origin': [0, 0], 'transform': '00000000'}
        if any(pin['xy'] != pin_xy(probe, number) for pin, number in zip(spec['pins'], order)):
            raise CircuitError('BOOTSTRAP_BASELINE_PIN_GEOMETRY_CHANGED: ' + device)
        result[device] = spec
    return result


def label(text, name, position, visible=True):
    flags = 0x01ff2001 if visible else 0x00ff2000
    return {'text': text, 'xy': list(position), 'transform': '000000000000',
            'style': (struct.pack('<iiiI', GRID, GRID*4//5, 0, flags)+b'Default Font\0'+
                      name.encode('ascii')+b'\0'+b'\0'*4).hex()}


def baseline_objects(specs):
    templates = []
    for device, spec in specs.items():
        labels = [label('X', 'COMPONENT ID', [-GRID, GRID]),
                  label(spec['defaults'].get('VALUE', device), 'COMPONENT VALUE', [-GRID, -GRID]),
                  label(device, 'SUBCKT NAME', [-GRID, -2*GRID], False),
                  label(spec['properties'], 'PROPERTIES', [-GRID, -2*GRID], False)]
        templates.append({'type': 'component', 'ref': 'X', 'device': device, 'identity': 1,
                          'origin': [0, 0], 'transform': '00000000', 'kind': spec['kind'],
                          'labels': labels, 'pin_count': len(spec['pins']), 'pin_header': '00'*9,
                          'pointers': [0]*len(spec['pins'])})
    for device in ('$TERGROUND', '$TERPOWER'):
        templates.append({'type': 'terminal', 'device': device, 'origin': [0, 0], 'transform': '00000000',
                          'attributes': struct.pack('<IH', 13, 0).hex(),
                          'label': label('', 'TERMINAL LABEL', [-GRID, GRID]), 'pointer': 0})
    templates.append({'type': 'wire', 'flags': 0, 'labels': [],
                      'style': '00c09e00000040000001ffffff00ffffff00027f5749524500', 'points': [[0, 0], [GRID, 0]]})
    builder = Builder(templates)
    positions = {'AT89C51': [-30, 20], 'RES': [0, 0], 'LED-RED': [15, 0],
                 'CAP': [-12, -20], 'CAP-ELEC': [0, -20], 'CRYSTAL': [15, -20]}
    for device, ref in BASELINE_REFS.items():
        builder.component(ref, device, positions[device])
    builder.rail('initialization_power', 'R1.1', 'VCC', [-6, 0])
    builder.rail('initialization_ground', 'R1.2', 'GND', [6, 0])
    return builder.objects
