import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proteus_mcp.build_profiles import diagnose_build, select_profile
from proteus_mcp.model import CircuitError, digest


class BuildProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        base = {'PDS.exe': digest(b'pds'), 'Qt.dll': digest(b'qt')}
        self.profile = {'id': 'test-build', 'version': '0.0', 'architecture': 'x64', 'pointer_size': 8,
                        'adapter': 'p9-907-win64-v1', 'capabilities': {
                            'qt': base, 'live': base | {'ISIS.dll': digest(b'isis')},
                            'pcb': base | {'ARES.dll': digest(b'ares')},
                            'simulation': base | {'VSM.dll': digest(b'vsm')}}}
        for name, data in (('PDS.exe', b'pds'), ('Qt.dll', b'qt'), ('ISIS.dll', b'isis'),
                           ('ARES.dll', b'ares'), ('VSM.dll', b'vsm')):
            (self.folder/name).write_bytes(data)

    def test_each_capability_has_an_independent_profile_result(self):
        (self.folder/'ARES.dll').write_bytes(b'changed')
        report = diagnose_build(self.folder, profiles=[self.profile])
        self.assertTrue(report['capabilities']['live']['available'])
        self.assertFalse(report['capabilities']['pcb']['available'])
        mismatch = report['capabilities']['pcb']['comparisons'][0]['mismatched_modules'][0]
        self.assertEqual('ARES.dll', mismatch['module'])
        self.assertEqual(digest(b'changed'), mismatch['actual_sha256'])

    def test_missing_modules_are_named_not_a_file_not_found_traceback(self):
        (self.folder/'ISIS.dll').unlink()
        report = diagnose_build(self.folder, profiles=[self.profile])
        self.assertEqual(['ISIS.dll'], report['capabilities']['live']['comparisons'][0]['missing_modules'])
        self.assertTrue(report['capabilities']['qt']['available'])

    def test_wrong_executable_never_falls_back_to_a_compatible_qt_library(self):
        (self.folder/'PDS.exe').write_bytes(b'unknown-build')
        with patch('proteus_mcp.build_profiles.PROFILES', (self.profile,)):
            with self.assertRaisesRegex(CircuitError, 'NATIVE_BUILD_NOT_CALIBRATED.*PDS.exe'):
                select_profile(self.folder, live=True)

    def test_selection_pins_the_adapter_implementation_as_well_as_hashes(self):
        with patch('proteus_mcp.build_profiles.PROFILES', (self.profile,)):
            profile, hashes = select_profile(self.folder, live=True)
        self.assertEqual('test-build', profile['id'])
        self.assertEqual({'PDS.exe', 'Qt.dll', 'ISIS.dll'}, set(hashes))
        self.profile['adapter'] = 'not-shipped'
        with patch('proteus_mcp.build_profiles.PROFILES', (self.profile,)):
            with self.assertRaisesRegex(CircuitError, 'NATIVE_ADAPTER_MISSING'):
                select_profile(self.folder)

    def test_ambiguous_profiles_are_rejected(self):
        second = copy.deepcopy(self.profile)
        second['id'] = 'duplicate-test'
        with patch('proteus_mcp.build_profiles.PROFILES', (self.profile, second)):
            with self.assertRaisesRegex(CircuitError, 'NATIVE_BUILD_NOT_CALIBRATED'):
                select_profile(self.folder)

    def test_a_combined_operation_cannot_mix_two_different_build_adapters(self):
        first, second = copy.deepcopy(self.profile), copy.deepcopy(self.profile)
        first['capabilities'].pop('live')
        second['id'] = 'second'
        second['capabilities'].pop('qt')
        with patch('proteus_mcp.build_profiles.PROFILES', (first, second)):
            with self.assertRaisesRegex(CircuitError, 'NATIVE_BUILD_NOT_CALIBRATED'):
                select_profile(self.folder, live=True)
