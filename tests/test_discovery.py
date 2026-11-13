import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proteus_mcp.discovery import default_workspace, discover, user_data_dir
from proteus_mcp.service import Service


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def product(self, version='9.0.1.0', name='Proteus 9 Professional'):
        install = self.root / version / name
        (install / 'Bin').mkdir(parents=True)
        (install / 'Bin/PDS.exe').write_bytes(b'fixture')
        data = self.root / version / 'Data'
        (data / 'Library').mkdir(parents=True)
        return {'name': name, 'Path': str(install), 'ProgramData': str(data),
                'InstallVersion': version, 'source': 'registry-fixture'}

    def resolve(self, products=(), env=None, **kwargs):
        return discover(products=list(products), environ=env or {}, which=lambda _: None, **kwargs)

    def test_registered_nonstandard_drive_and_data_location_are_associated(self):
        product = self.product()
        result = self.resolve([product])
        self.assertEqual(Path(product['Path']) / 'Bin/PDS.exe', result['proteus'])
        self.assertEqual(Path(product['ProgramData']) / 'Library', result['library'])
        self.assertEqual('registry-fixture', result['sources']['proteus'])

    def test_arguments_beat_environment_and_invalid_explicit_paths_do_not_fall_back(self):
        product = self.product()
        result = self.resolve([product], {'PROTEUS_MCP_EXE': str(self.root/'env/PDS.exe'),
                                         'PROTEUS_MCP_LIBRARY': str(self.root/'env/lib'),
                                         'PROTEUS_MCP_KEIL': str(self.root/'env/keil')},
                              proteus=self.root/'explicit/PDS.exe', library=self.root/'explicit/lib', keil=self.root/'explicit/keil')
        for key, expected in (('proteus', 'explicit/PDS.exe'), ('library', 'explicit/lib'), ('keil', 'explicit/keil')):
            self.assertEqual(self.root/expected, result[key])
            self.assertEqual('argument', result['sources'][key])
        self.assertNotIn(Path(product['ProgramData']), result['data_roots'])

    def test_latest_installation_is_selected_without_mixing_libraries(self):
        old, new = self.product('8.17.1', 'Proteus 8 Professional'), self.product('9.0.40482.2')
        result = self.resolve([new, old])
        self.assertEqual(Path(new['ProgramData'])/'Library', result['library'])
        chosen = self.resolve([new, old], proteus=Path(old['Path'])/'Bin/PDS.exe')
        self.assertEqual(Path(old['ProgramData'])/'Library', chosen['library'])

    def test_uninstall_icon_discovers_keil_without_assuming_a_drive(self):
        root = self.root/'Keil_v5'
        (root/'C51/BIN').mkdir(parents=True)
        (root/'C51/BIN/C51.exe').write_bytes(b'fixture')
        product = {'name': 'Keil uVision', 'DisplayIcon': '"'+str(root/'UV4/UV4.exe')+'",0', 'source': 'uninstall'}
        self.assertEqual(root, self.resolve([product])['keil'])

    def test_standard_program_files_and_program_data_are_discovered(self):
        files = self.root/'Programs'
        data = self.root/'Data'
        exe = files/'Labcenter Electronics/Proteus 9 Professional/Bin/PDS.exe'
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'fixture')
        library = data/'Labcenter Electronics/Proteus 9 Professional/Library'
        library.mkdir(parents=True)
        result = self.resolve(env={'ProgramFiles': str(files), 'ProgramData': str(data)})
        self.assertEqual(exe, result['proteus'])
        self.assertEqual(library, result['library'])

    def test_missing_installation_reports_missing_not_a_developer_drive(self):
        result = self.resolve()
        self.assertEqual([], result['candidates'])
        self.assertEqual({'proteus': 'not_found', 'library': 'not_found', 'keil': 'not_found'}, result['sources'])
        self.assertEqual(user_data_dir()/'unconfigured/PDS.exe', result['proteus'])

    def test_default_workspace_is_user_writable_not_in_the_installed_package(self):
        with patch.dict(os.environ, {'PROTEUS_MCP_WORKSPACE': str(self.root/'chosen')}):
            self.assertEqual(self.root/'chosen', default_workspace())
        with patch.dict(os.environ, {'USERPROFILE': str(self.root/'home'), 'HOME': str(self.root/'home')}, clear=True):
            self.assertTrue(default_workspace().is_relative_to(user_data_dir()))

    def test_services_capture_environment_independently(self):
        with patch.dict(os.environ, {'PROTEUS_MCP_EXE': str(self.root/'one/PDS.exe'),
                                    'PROTEUS_MCP_CALIBRATION': str(self.root/'one/template.pdsprj')}):
            first = Service(self.root/'work-one')
        with patch.dict(os.environ, {'PROTEUS_MCP_EXE': str(self.root/'two/PDS.exe'),
                                    'PROTEUS_MCP_CALIBRATION': str(self.root/'two/template.pdsprj')}):
            second = Service(self.root/'work-two')
        self.assertEqual(self.root/'one/PDS.exe', first.proteus)
        self.assertEqual(self.root/'two/PDS.exe', second.proteus)
        self.assertEqual(self.root/'one/template.pdsprj', first.initialization.override)
        self.assertEqual(self.root/'two/template.pdsprj', second.initialization.override)
