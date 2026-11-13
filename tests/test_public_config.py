import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proteus_mcp.devices import definition, _definition
from proteus_mcp.library import LibraryIndex, default_library, library_scope
from proteus_mcp.service import Service
from test_extensions import device_blob
from test_library import library_fixture


class PublicConfigurationTests(unittest.TestCase):
    def test_environment_library_is_resolved_when_an_index_is_created(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'PROTEUS_MCP_LIBRARY': folder}):
                self.assertEqual(Path(folder).resolve(), LibraryIndex().root)

    def test_scope_overrides_environment_and_is_restored_after_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.dict(os.environ, {'PROTEUS_MCP_LIBRARY': str(root / 'environment')}):
                with self.assertRaisesRegex(RuntimeError, 'stop'):
                    with library_scope(root / 'service'):
                        self.assertEqual((root / 'service').resolve(), default_library())
                        raise RuntimeError('stop')
                self.assertEqual((root / 'environment').resolve(), default_library())

    def test_service_calls_keep_independent_library_roots(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = Service(root / 'one', library=root / 'library-one')
            second = Service(root / 'two', library=root / 'library-two')
            with patch.object(Service, '_call', lambda self, *args: str(LibraryIndex().root)):
                self.assertEqual(str(first.library.root), first.call('fixture', {}))
                self.assertEqual(str(second.library.root), second.call('fixture', {}))
                self.assertEqual(str(first.library.root), first.call('fixture', {}))

    def test_device_cache_cannot_return_a_definition_from_another_root(self):
        _definition.cache_clear()
        self.addCleanup(_definition.cache_clear)
        with tempfile.TemporaryDirectory() as folder:
            paths = []
            for name in ('one', 'two'):
                root = Path(folder) / name
                root.mkdir()
                data = bytearray(library_fixture(['CUSTOM']))
                data[-1:] = device_blob()
                source = root / 'CUSTOM.LIB'
                source.write_bytes(data)
                paths.append(source.resolve())
            for path in (paths[0], paths[1], paths[0]):
                with library_scope(path.parent):
                    self.assertEqual(str(path), definition('CUSTOM')['library_path'])

    def test_interrupt_example_uses_the_configured_active_library(self):
        with tempfile.TemporaryDirectory() as folder:
            service = Service(Path(folder) / 'work', library=Path(folder) / 'library')
            with patch('proteus_mcp.interrupts.definition', side_effect=RuntimeError('stop')) as resolve:
                with self.assertRaisesRegex(RuntimeError, 'stop'):
                    service.call('example_external_interrupt', {'project': 'Example'})
            resolve.assert_called_once_with('BUZZER', str(service.library.root / 'ACTIVE.LIB'))

    def test_calibration_environment_is_captured_by_service_not_package_import(self):
        with tempfile.TemporaryDirectory() as folder:
            template = str(Path(folder) / 'local.pdsprj')
            environment = dict(os.environ, PROTEUS_MCP_CALIBRATION=template)
            result = subprocess.run(
                [sys.executable, '-c', 'import json,sys; from proteus_mcp.service import Service; '
                 'print(json.dumps(str(Service(sys.argv[1]).initialization.override)))', str(Path(folder)/'work')],
                env=environment, capture_output=True, text=True, check=True)
            self.assertEqual(template, json.loads(result.stdout))

    def test_doctor_does_not_advertise_export_without_a_local_template(self):
        with tempfile.TemporaryDirectory() as folder:
            service = Service(Path(folder) / 'work', calibration=Path(folder) / 'missing.pdsprj')
            report = service.doctor()
            self.assertFalse(report['capabilities']['native_file_generation']['available'])
            self.assertFalse(report['capabilities']['native_schematic_write']['available'])


if __name__ == '__main__':
    unittest.main()
