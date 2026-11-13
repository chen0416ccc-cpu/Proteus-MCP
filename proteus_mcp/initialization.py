"""Per-workspace automatic native writer initialization, with private assets."""

import json
import os
import struct
import uuid
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager
from pathlib import Path

from .build_profiles import file_sha256
from .discovery import discover
from .library import library_scope
from .model import CircuitError, atomic_write, digest, json_bytes
from .native_seed import baseline_definitions, baseline_objects, empty_container

SCHEMA = 1
WRITER_FILES = ('initialization.py', 'native_seed.py', 'native9.py', 'database.py', 'devices.py',
                'initialization_verify.py', 'bridge.py', 'live_agent.js', 'qt_agent.js', 'build_profiles.py',
                'runtime.py', 'live.py', 'geometry.py', 'notices.py', 'notice_watcher.ps1')
SOURCE_ERRORS = (OSError, KeyError, ValueError, TypeError, UnicodeError, struct.error, ET.ParseError, zipfile.BadZipFile, CircuitError)


def validate_template(path):
    from .native9 import Builder, PIN_ORDER
    from .native_backend import native_read, read_project
    report = native_read(path)
    _, parsed = read_project(path)
    checks = {'baseline_devices': set(PIN_ORDER) <= {p['device'] for p in report['components']},
              'default_transforms': all(p['rotation'] == 0 and p['mirror'] == 0 for p in report['components']
                                        if p['device'] in PIN_ORDER),
              'power_terminals': {'$TERPOWER', '$TERGROUND'} <= {p['device'] for p in parsed['terminals']},
              'real_wires': report['wires'] > 0, 'dsn_cdb_consistent': report['database_consistent'],
              'orthogonal': report['nonorthogonal_wire_segments'] == 0}
    if not all(checks.values()):
        raise CircuitError('NATIVE_TEMPLATE_INVALID: ' + ', '.join(k for k, value in checks.items() if not value))
    Builder(parsed['objects'])
    # Check the directory and empty default sheet used by subsequent rewrites.
    from .native9 import relocate_dsn
    relocate_dsn(read_project(path)[0], [])
    return {'sha256': report['sha256'], 'checks': checks, 'native_reopen_verified': False, 'simulation_verified': False}


class NativeInitialization:
    def __init__(self, workspace, config=None, calibration=None):
        self.workspace = Path(workspace).resolve()
        self.config = config if config is not None else discover()
        self.override = calibration if calibration is not None else os.getenv('PROTEUS_MCP_CALIBRATION')
        self.override = Path(self.override).expanduser().resolve() if self.override is not None else None
        self.base = self.workspace / '_local/native-initialization'
        self.receipt = self.base / 'current.json'

    def source(self):
        rejected = []
        scanned = 0
        for root in self.config['data_roots']:
            samples = (root / 'Samples').resolve()
            if not samples.is_dir():
                continue
            for path in sorted(samples.rglob('*.pdsprj')):
                scanned += 1
                if scanned > 1024:
                    break
                if not path.resolve().is_relative_to(samples):
                    continue
                try:
                    data, info = empty_container(path)
                    return path, data, info
                except SOURCE_ERRORS as error:
                    if len(rejected) < 8:
                        rejected.append({'path': str(path), 'reason': str(error)[:240]})
        raise CircuitError('BOOTSTRAP_SOURCE_NOT_FOUND: install standard Proteus 9 Samples or provide an explicitly validated '
                           'PROTEUS_MCP_CALIBRATION; rejected=' + json.dumps(rejected, ensure_ascii=True))

    def inputs(self):
        with library_scope(self.config['library']):
            specs = baseline_definitions()
        source, container, info = self.source()
        libraries = {spec['library_path']: file_sha256(spec['library_path']) for spec in specs.values()}
        if any(libraries[spec['library_path']] != spec['library_sha256'] for spec in specs.values()):
            raise CircuitError('BOOTSTRAP_LIBRARY_CHANGED_DURING_SCAN: retry with stable library files')
        package = Path(__file__).resolve().parent
        code = {name: file_sha256(package / name) for name in WRITER_FILES}
        inputs = {'schema': SCHEMA, 'source': str(source), 'source_sha256': file_sha256(source),
                  'library_root': str(self.config['library']), 'libraries': libraries, 'writer': code,
                  'library_inventory': self.library_inventory(),
                  'definitions': {name: spec['definition_sha256'] for name, spec in specs.items()}}
        return inputs, digest(json_bytes(inputs)), container, specs, info

    def cached(self):
        if not self.receipt.is_file():
            return None
        if self.receipt.stat().st_size > 256 * 1024:
            raise CircuitError('NATIVE_TEMPLATE_CACHE_INVALID: oversized receipt')
        record = json.loads(self.receipt.read_text(encoding='utf-8'))
        if (not isinstance(record, dict) or not isinstance(record.get('template'), str) or
                not isinstance(record.get('inputs'), dict) or not isinstance(record.get('verification'), dict)):
            raise CircuitError('NATIVE_TEMPLATE_CACHE_INVALID: malformed receipt')
        if not all(isinstance(record['inputs'].get(k), dict) for k in ('writer', 'libraries', 'definitions')):
            raise CircuitError('NATIVE_TEMPLATE_CACHE_INVALID: malformed input fingerprints')
        if set(record['inputs']['writer']) != set(WRITER_FILES):
            raise CircuitError('NATIVE_TEMPLATE_CACHE_STALE: writer schema changed')
        path = Path(record['template']).resolve()
        if not path.is_relative_to(self.base.resolve()) or record['schema'] != SCHEMA:
            raise CircuitError('NATIVE_TEMPLATE_CACHE_INVALID: foreign path or schema')
        inputs = record['inputs']
        package = Path(__file__).resolve().parent
        # Recompute from trusted paths. A receipt cannot make us fingerprint an
        # arbitrary path, or retain verification after local inputs have changed.
        for name, expected in inputs['writer'].items():
            if file_sha256(package/name) != expected:
                raise CircuitError('NATIVE_TEMPLATE_CACHE_STALE: writer changed')
        if Path(inputs['library_root']).resolve() != Path(self.config['library']).resolve():
            raise CircuitError('NATIVE_TEMPLATE_CACHE_STALE: selected library changed')
        if inputs.get('library_inventory') != self.library_inventory():
            raise CircuitError('NATIVE_TEMPLATE_CACHE_STALE: library changed or index inventory changed')
        for library, expected in inputs['libraries'].items():
            if not Path(library).resolve().is_relative_to(Path(self.config['library']).resolve()) or file_sha256(library) != expected:
                raise CircuitError('NATIVE_TEMPLATE_CACHE_STALE: library changed')
        source = Path(inputs['source']).resolve()
        if not any(source.is_relative_to((p/'Samples').resolve()) for p in self.config['data_roots']) or file_sha256(source) != inputs['source_sha256']:
            raise CircuitError('NATIVE_TEMPLATE_CACHE_STALE: installation source changed')
        if digest(json_bytes(inputs)) != record['cache_key'] or file_sha256(path) != record['sha256']:
            raise CircuitError('NATIVE_TEMPLATE_CACHE_INVALID: checksum changed')
        validate_template(path)
        return record

    def library_inventory(self):
        return [[p.name, p.stat().st_mtime_ns, p.stat().st_size] for p in sorted(Path(self.config['library']).glob('*.LIB'))]

    def inspect(self):
        """Read-only: no mkdir, template writes, process launch or native calls."""
        with library_scope(self.config['library']):
            return self._inspect()

    def _inspect(self):
        if self.override is not None:
            try:
                check = validate_template(self.override)
                return {'status': 'ready', 'available': True, 'automatic': False, 'path': str(self.override), **check}
            except SOURCE_ERRORS as error:
                return {'status': 'invalid_override', 'available': False, 'automatic': False,
                        'path': str(self.override), 'reason': str(error)}
        stale = None
        try:
            record = self.cached()
            if record:
                return {'status': 'ready', 'available': True, 'automatic': True, 'path': record['template'],
                        'sha256': record['sha256'], 'cache_key': record['cache_key'],
                        'native_reopen_verified': record['verification'].get('status') == 'verified' and
                                                  record['verification'].get('native_reopen_verified') is True,
                        'simulation_verified': False}
        except SOURCE_ERRORS as error:
            stale = str(error)
        try:
            inputs, key, _, _, info = self.inputs()
            return {'status': 'initialization_required', 'available': True, 'automatic': True,
                    'can_initialize': True, 'cache_stale_reason': stale, 'source': inputs['source'],
                    'cache_key': key, 'native_reopen_verified': False, 'simulation_verified': False, **info}
        except SOURCE_ERRORS as error:
            return {'status': 'unavailable', 'available': False, 'automatic': True,
                    'cache_stale_reason': stale, 'reason': str(error), 'native_reopen_verified': False}

    @contextmanager
    def lock(self):
        self.base.mkdir(parents=True, exist_ok=True)
        path = self.base / 'initialize.lock'
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            raise CircuitError('NATIVE_INITIALIZATION_BUSY: another initializer owns this workspace') from None
        try:
            os.close(fd)
            yield
        finally:
            path.unlink()

    def initialize(self, verify_native=False, runtime=None, bridge=None):
        if self.override is not None:
            with library_scope(self.config['library']):
                check = validate_template(self.override)
            if verify_native:
                raise CircuitError('NATIVE_OVERRIDE_READ_ONLY: verify a workspace copy with project_open/save/reopen')
            return {'status': 'ready', 'template': str(self.override), 'automatic': False, **check}
        with self.lock(), library_scope(self.config['library']):
            try:
                record = self.cached()
            except SOURCE_ERRORS:
                record = None
            if record is None:
                inputs, key, container, specs, info = self.inputs()
                folder = self.base / (key[:16] + '-' + uuid.uuid4().hex[:8])
                empty = folder / 'container.pdsprj'
                atomic_write(empty, container)
                path = folder / 'template.pdsprj'
                from .native9 import rewrite_project
                rewrite_project(empty, path, lambda _: baseline_objects(specs), database_mode='generate')
                check = validate_template(path)
                record = {'schema': SCHEMA, 'template': str(path), 'cache_key': key, 'inputs': inputs,
                          'sha256': check['sha256'], 'structure': check['checks'], 'source_format': info,
                          'verification': {'native_reopen_verified': False, 'simulation_verified': False}}
            if verify_native:
                if runtime is None or bridge is None:
                    raise CircuitError('NATIVE_INITIALIZATION_VERIFIER_REQUIRED')
                from .initialization_verify import verify_initialization
                verification = verify_initialization(Path(record['template']), runtime, bridge)
                record['verification'] = verification
                record['sha256'] = file_sha256(record['template'])
            atomic_write(self.receipt, json_bytes(record))
        verification = record['verification']
        return {'status': verification.get('status', 'ready'), 'template': record['template'],
                'sha256': record['sha256'], 'cache_key': record['cache_key'], 'automatic': True,
                'receipt': str(self.receipt), 'verification': verification, 'desktop_input': False,
                'native_reopen_verified': verification.get('native_reopen_verified', False), 'simulation_verified': False}

    def ensure(self):
        return Path(self.initialize()['template'])
