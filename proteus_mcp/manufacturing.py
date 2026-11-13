"""Audited assembly BOM output from a consistent saved native schematic."""
import collections
import csv
import io
import re
import time
import uuid
from pathlib import Path

from .model import CircuitError, atomic_write, digest, json_bytes


def natural_ref(value):
    return [(0, int(p)) if p.isdigit() else (1, p) for p in re.split(r'(\d+)', value)]


def bom_rows(readback):
    if not readback.get('database_consistent'):
        raise CircuitError('BOM_REQUIRES_CONSISTENT_NATIVE_DATABASE')
    from .native_backend import property_map
    packages = collections.defaultdict(list)
    for component in readback['components']:
        packages[component['ref'].split(':')[0]].append(component)
    physical, excluded = [], []
    for ref, units in sorted(packages.items(), key=lambda item: natural_ref(item[0])):
        packages_in_units = {property_map(p['native_properties']).get('PACKAGE', '') for p in units}
        values = {p['value'] for p in units}
        devices = {p['device'] for p in units}
        if len(packages_in_units) != 1 or len(values) != 1:
            raise CircuitError('BOM_PACKAGE_UNIT_CONFLICT: '+ref)
        footprint = next(iter(packages_in_units))
        record = {'ref': ref, 'device': readback.get('package_device_names', {}).get(ref) or '|'.join(sorted(devices)),
                  'value': next(iter(values)), 'package': footprint,
                  'units': sorted(p['ref'] for p in units), 'symbols': sorted(devices)}
        if not footprint:
            excluded.append({**record, 'reason': 'No assigned native PCB package; not silently counted as a physical part'})
        else:
            physical.append(record)
    grouped = collections.defaultdict(list)
    for part in physical:
        grouped[(part['device'], part['value'], part['package'])].append(part['ref'])
    rows = [{'device': key[0], 'value': key[1], 'package': key[2], 'quantity': len(refs),
             'references': sorted(refs, key=natural_ref)} for key, refs in sorted(grouped.items())]
    return {'rows': rows, 'physical_parts': physical, 'excluded_unassigned': excluded,
            'physical_part_count': len(physical), 'group_count': len(rows),
            'instance_count': len(readback['components']), 'package_count': len(packages)}


def safe_csv_cell(value):
    text = str(value)
    # Library/user property strings are data, never spreadsheet expressions.
    return "'"+text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text


def export_bom(root, path, expected_sha256):
    from .native_backend import native_read
    path = Path(path).resolve(strict=True)
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-fA-F]{64}', expected_sha256):
        raise CircuitError('BOM_REQUIRES_SOURCE_SHA256')
    before = digest(path.read_bytes())
    if before != expected_sha256.lower():
        raise CircuitError('BOM_SOURCE_REVISION_CONFLICT')
    readback = native_read(path)
    result = bom_rows(readback)
    if readback['sha256'] != before or digest(path.read_bytes()) != before:
        raise CircuitError('BOM_SOURCE_CHANGED_DURING_READ')
    folder = Path(root)/'_outputs'/('bom-'+time.strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\r\n')
    writer.writerow(['Quantity', 'References', 'Device', 'Value', 'PCB Package'])
    for row in result['rows']:
        writer.writerow([row['quantity'], *map(safe_csv_cell, [', '.join(row['references']), row['device'], row['value'], row['package']])])
    csv_data = b'\xef\xbb\xbf'+stream.getvalue().encode('utf-8')
    output = {**result, 'status': 'exported', 'source_path': str(path), 'source_sha256': before,
              'source_scope': 'saved_native_schematic_not_unsaved_session_or_verified_PCB_assembly',
              'desktop_input': False, 'fabrication_certified': False, 'gerber_exported': False,
              'csv_formula_escaping': True, 'csv_path': str(folder/'bom.csv'), 'csv_sha256': digest(csv_data)}
    atomic_write(folder/'bom.csv', csv_data)
    atomic_write(folder/'bom.json', json_bytes(output))
    return output | {'evidence_path': str(folder/'bom.json')}
