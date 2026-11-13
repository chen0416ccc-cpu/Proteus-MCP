"""Original synthetic format fixtures, not captured vendor assets."""

import io
import json
import os
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from proteus_mcp.initialization import NativeInitialization
from proteus_mcp.library import INDEX_START, RECORD_SIZE, library_scope
from proteus_mcp.model import CircuitError, digest
from proteus_mcp.native9 import EMPTY_CDB, GRID, OBJECTS, PIN_ORDER, SIGNATURE, pin_xy
from proteus_mcp.native_backend import native_read
from proteus_mcp.native_seed import baseline_definitions, empty_container
from test_library import library_fixture


def cache_record(name):
    return struct.pack('<H', len(name))+name.encode()+b'\0'*13+struct.pack('<I', 4)


def project_fixture(version=903, bad_default=False):
    prefix = b'ISIS SCHEMATIC FILE\x1a'+b'\0'*20+b'\xfb\xff'
    prefix += cache_record('$TERPOWER')+cache_record('$TERGROUND')+b'\0\0\xfb\xff'
    prefix += cache_record('SAMPLE_DEVICE')+b'\0\0'
    sheet = SIGNATURE+bytes.fromhex('00010001000100')+struct.pack('<ii', 100*GRID, 70*GRID)+b'\0\0'+OBJECTS
    first = len(prefix)+4
    content = sheet+b'\x22SAMPLE_OBJECT_CONTENT\xff'
    second = first+len(content)
    default = sheet+(b'\x01' if bad_default else b'')+b'\xff'
    directory = second+len(default)
    table = (struct.pack('<HIH', 2, 3, 6)+b'CCT000'+struct.pack('<HI', 2, first)
             +struct.pack('<IH', 2, 11)+b'__DEFAULT__'+struct.pack('<HI', 0, second))
    dsn = prefix+struct.pack('<I', directory)+content+default+table+b'\0'*4
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('PROJECT.XML', '<PROJECT><TIMESTAMP RELEASE="900" FILEVER="'+str(version)+
                   '" CUSTID="sample-customer"/><DEBUG><setting>sample</setting></DEBUG><SCRIPTS/></PROJECT>')
        z.writestr('ROOT.CDB', EMPTY_CDB)
        z.writestr('ROOT.DSN', dsn)
        z.writestr('FIRMWARE/sample.c', 'sample firmware must not be copied')
    return buffer.getvalue()


def definition_blob(device):
    pins = bytearray()
    probe = {'device': device, 'origin': [0, 0], 'transform': '00000000'}
    for number in PIN_ORDER[device]:
        xy = pin_xy(probe, number)
        hidden = xy is None
        xy = xy or [0, 0]
        label = {'LED-RED': {'1': 'A', '2': 'K'}, 'CAP-ELEC': {'1': '+', '2': '-'}}.get(device, {}).get(number, number)
        raw_number = '' if device in ('LED-RED', 'CAP-ELEC') else number
        pins += bytes([0, 0 if hidden else 2, 0, 0])+struct.pack('<iihH', xy[0]+2*GRID, xy[1], 0, 0)
        pins += b'$PINDEFAULT\0'+label.encode()+b'\0'+raw_number.encode()+b'\0'
    props = b'{*DEVICE}\nPREFIX=U\n{*COMPONENT}\n{VALUE=10k}\n\0'
    at = 20+len(pins)
    return struct.pack('<IIHHHHI', at+4+len(props), at, len(PIN_ORDER[device]),
                       8 if device == 'LED-RED' else 0, 1, 1, 4)+pins+struct.pack('<I', len(props))+props


def install_fixture(root):
    library = root/'Library'
    library.mkdir()
    names = list(PIN_ORDER)
    data = bytearray(library_fixture(names)[:INDEX_START+len(names)*RECORD_SIZE])
    for i, name in enumerate(names):
        struct.pack_into('<I', data, INDEX_START+i*RECORD_SIZE+128, len(data))
        data += definition_blob(name)
    (library/'FIXTURE.LIB').write_bytes(data)
    sample = root/'Samples/Original/sample.pdsprj'
    sample.parent.mkdir(parents=True)
    sample.write_bytes(project_fixture())
    return {'library': library, 'data_roots': [root], 'proteus': root/'Bin/PDS.exe'}, sample


class NativeInitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config, self.sample = install_fixture(self.root)
        self.manager = NativeInitialization(self.root/'workspace', self.config)

    def test_source_objects_firmware_customer_metadata_and_devices_are_not_copied(self):
        data, _ = empty_container(self.sample)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual({'PROJECT.XML', 'ROOT.DSN', 'ROOT.CDB'}, set(z.namelist()))
            self.assertNotIn(b'sample-customer', z.read('PROJECT.XML'))
            self.assertNotIn(b'SAMPLE_OBJECT_CONTENT', z.read('ROOT.DSN'))
            self.assertNotIn(b'SAMPLE_DEVICE', z.read('ROOT.DSN'))

    def test_inspect_is_read_only_and_distinguishes_automatic_readiness_from_verification(self):
        result = self.manager.inspect()
        self.assertEqual('initialization_required', result['status'])
        self.assertTrue(result['available'])
        self.assertFalse(result['native_reopen_verified'])
        self.assertFalse(self.manager.workspace.exists())

    def test_initialize_builds_actual_records_and_reuses_a_verified_checksum(self):
        source_hash = digest(self.sample.read_bytes())
        first = self.manager.initialize()
        second = self.manager.initialize()
        self.assertEqual(first['template'], second['template'])
        self.assertFalse(first['native_reopen_verified'])
        with library_scope(self.config['library']):
            read = native_read(first['template'])
        self.assertEqual(6, len(read['components']))
        self.assertEqual(2, read['wires'])
        self.assertTrue(read['database_consistent'])
        self.assertEqual(source_hash, digest(self.sample.read_bytes()))
        self.assertEqual('ready', self.manager.inspect()['status'])

    def test_corrupt_template_is_rebuilt_without_overwriting_the_old_bundle(self):
        first = self.manager.initialize()
        path = Path(first['template'])
        path.write_bytes(b'damaged')
        self.assertEqual('initialization_required', self.manager.inspect()['status'])
        second = self.manager.initialize()
        self.assertNotEqual(path, Path(second['template']))
        self.assertEqual(b'damaged', path.read_bytes())

    def test_library_update_invalidates_definition_cache_and_initialization(self):
        first = self.manager.initialize()
        library = self.config['library']/'FIXTURE.LIB'
        previous = library.stat()
        library.write_bytes(library.read_bytes().replace(b'VALUE=10k', b'VALUE=22k'))
        os.utime(library, ns=(previous.st_atime_ns, previous.st_mtime_ns+1000000))
        status = self.manager.inspect()
        self.assertEqual('initialization_required', status['status'])
        self.assertIn('library changed', status['cache_stale_reason'])
        second = self.manager.initialize()
        self.assertNotEqual(first['cache_key'], second['cache_key'])
        self.assertEqual('22k', native_read(second['template'])['components'][0]['value'])

    def test_missing_source_does_not_silently_reuse_a_stale_template(self):
        self.manager.initialize()
        self.sample.unlink()
        result = self.manager.inspect()
        self.assertFalse(result['available'])
        self.assertIn('BOOTSTRAP_SOURCE_NOT_FOUND', result['reason'])

    def test_malformed_receipt_or_foreign_path_is_rejected_and_never_overwritten(self):
        self.manager.initialize()
        foreign = self.root/'foreign.pdsprj'
        foreign.write_bytes(b'protected')
        for payload in ('[]', '{}', '{"template": null}', 'invalid-json'):
            self.manager.receipt.write_text(payload, encoding='utf-8')
            self.assertEqual('initialization_required', self.manager.inspect()['status'])
        self.manager.initialize()
        record = json.loads(self.manager.receipt.read_text())
        record['template'] = str(foreign)
        self.manager.receipt.write_text(json.dumps(record))
        self.assertEqual('initialization_required', self.manager.inspect()['status'])
        self.manager.initialize()
        self.assertEqual(b'protected', foreign.read_bytes())

    def test_explicit_missing_override_is_not_replaced_with_an_auto_template(self):
        manager = NativeInitialization(self.root/'another', self.config, self.root/'missing.pdsprj')
        self.assertEqual('invalid_override', manager.inspect()['status'])
        with self.assertRaises(OSError):
            manager.initialize()
        self.assertFalse(manager.base.exists())

    def test_concurrent_initialization_fails_without_removing_another_owners_lock(self):
        with self.manager.lock():
            with self.assertRaisesRegex(CircuitError, 'NATIVE_INITIALIZATION_BUSY'):
                self.manager.initialize()
            self.assertTrue((self.manager.base/'initialize.lock').exists())
        self.assertFalse((self.manager.base/'initialize.lock').exists())

    def test_failed_native_verification_cannot_advertise_verified_reopen(self):
        with patch('proteus_mcp.initialization_verify.verify_initialization', return_value={
                'status': 'unconfirmed', 'native_reopen_verified': False, 'simulation_verified': False}):
            result = self.manager.initialize(verify_native=True, runtime=object(), bridge=object())
        self.assertEqual('unconfirmed', result['status'])
        self.assertFalse(self.manager.inspect()['native_reopen_verified'])

    def test_unsupported_source_versions_and_nonempty_default_sheets_are_rejected(self):
        for payload in (project_fixture(version=908), project_fixture(bad_default=True)):
            self.sample.write_bytes(payload)
            with self.assertRaises(CircuitError):
                empty_container(self.sample)

    def test_changed_baseline_geometry_does_not_use_hardcoded_pin_positions(self):
        with library_scope(self.config['library']):
            specs = baseline_definitions()
        specs['RES']['pins'][0]['xy'][0] += GRID
        with patch('proteus_mcp.devices.definition', side_effect=lambda device: specs[device]):
            with self.assertRaisesRegex(CircuitError, 'BASELINE_PIN_GEOMETRY_CHANGED'):
                baseline_definitions()
