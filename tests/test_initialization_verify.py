import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from proteus_mcp.initialization_verify import verify_initialization
from proteus_mcp.model import CircuitError


class InitializationVerificationTests(unittest.TestCase):
    def test_failed_verification_retains_cleanup_failure_without_claiming_success(self):
        for cleanup in ({'status': 'close_blocked'}, CircuitError('fixture close error')):
            with self.subTest(cleanup=cleanup), tempfile.TemporaryDirectory() as folder:
                runtime, bridge = MagicMock(), MagicMock()
                runtime.open.return_value = {'status': 'project_window_observed', 'session': 'owned'}
                if isinstance(cleanup, Exception):
                    runtime.close.side_effect = cleanup
                else:
                    runtime.close.return_value = cleanup
                bridge.control.return_value = {'status': 'unconfirmed'}
                with patch('proteus_mcp.build_profiles.select_profile', return_value=({'id': 'fixture'}, {})), patch(
                        'proteus_mcp.bridge.frida_module'), patch('proteus_mcp.native_backend.native_read', return_value={}):
                    result = verify_initialization(Path(folder)/'template.pdsprj', runtime, bridge)
                self.assertEqual('unconfirmed', result['status'])
                self.assertFalse(result['native_reopen_verified'])
                written = json.loads((Path(folder)/'native-verification.json').read_text())
                self.assertEqual(result, written)
                runtime.close.assert_called_once_with('owned')

    def test_unknown_build_cannot_launch_a_verification_project(self):
        runtime = MagicMock()
        with patch('proteus_mcp.build_profiles.select_profile', side_effect=CircuitError('unsupported build')):
            with self.assertRaisesRegex(CircuitError, 'unsupported build'):
                verify_initialization(Path('not-opened.pdsprj'), runtime, MagicMock())
        runtime.open.assert_not_called()
