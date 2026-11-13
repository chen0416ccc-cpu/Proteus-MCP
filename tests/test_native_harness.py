import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.verify_native import Client, ROOT


class NativeHarnessTests(unittest.TestCase):
    def launch(self, installed):
        child = MagicMock()
        child.stdout = []
        child.stderr.readlines.return_value = []
        with patch('scripts.verify_native.subprocess.Popen', return_value=child) as spawn:
            client = Client(Path('workspace'), installed=installed)
            client.notify('notifications/initialized')
            client.close()
        child.stdin.write.assert_called_once()
        return spawn.call_args

    def test_installed_mode_isolated_from_checkout_and_pythonpath(self):
        call = self.launch(True)
        self.assertEqual([sys.executable, '-I', '-X', 'utf8', '-m', 'proteus_mcp'], call.args[0][:6])
        self.assertEqual(Path('workspace'), call.kwargs['cwd'])
        self.assertNotIn(str(ROOT/'run_server.py'), call.args[0])

    def test_source_mode_explicitly_uses_the_checkout(self):
        call = self.launch(False)
        self.assertIn(str(ROOT/'run_server.py'), call.args[0])
        self.assertEqual(ROOT, call.kwargs['cwd'])
