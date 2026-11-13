import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('release_check', Path(__file__).resolve().parents[1]/'scripts/check_release.py')
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class ReleaseAuditTests(unittest.TestCase):
    def test_private_files_and_traversal_are_rejected(self):
        for name in ('calibration/sample.pdsprj', 'research/code.py', 'x/a.DLL', '.env',
                     'notes/HANDOFF.md', '../escape.py', '/absolute.py', 'a\\b.py'):
            self.assertTrue(AUDIT.path_issues(name), name)
        self.assertEqual([], AUDIT.path_issues('proteus_mcp/qt_agent.js'))

    def test_credential_scanner_does_not_print_values(self):
        value = ('ghp_' + 'x'*36).encode()
        issues = AUDIT.content_issues(value)
        self.assertIn('github_credential', issues)
        self.assertNotIn(value.decode(), str(issues))

    def test_binary_and_private_user_paths_are_rejected(self):
        self.assertTrue(AUDIT.content_issues(b'a\0b'))
        self.assertTrue(AUDIT.content_issues(b'\xff\xfe'))
        self.assertIn('personal_windows_path', AUDIT.content_issues(b'C:/Users/' + b'ExampleUser/project'))
        self.assertEqual([], AUDIT.content_issues(b'C:/Users/Public/Desktop/Proteus.lnk'))

    def test_wheel_private_assets_and_missing_resources_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'example.whl'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('calibration/private.pdsprj', 'example')
            errors = AUDIT.audit_artifact(path, set())
            self.assertTrue(any('private directory' in error for error in errors))
            self.assertTrue(any('resources missing' in error for error in errors))

    def test_modified_package_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'example.whl'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('proteus_mcp/server.py', '# different source')
            self.assertTrue(any('differs from checkout' in error for error in AUDIT.audit_artifact(path, set())))


if __name__ == '__main__':
    unittest.main()
