import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from proteus_mcp.bridge import Bridge, simulation_state
from proteus_mcp.model import CircuitError, atomic_write, json_bytes
from proteus_mcp.runtime import Runtime, diagnostic_events
from proteus_mcp.server import check_schema, TOOLS
from proteus_mcp.verification import assess_sequence


class RuntimeBridgeTests(unittest.TestCase):
    def test_session_path_and_reused_pid_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Runtime(folder, "PDS.exe")
            with self.assertRaises(CircuitError):
                runtime.session_path("../../foreign")
            session = "20261112-000000-1234abcd"
            runtime.save({"session": session, "path": "test.pdsprj", "owned_processes": [{"ProcessId": 123, "created": "original"}]})
            with patch("proteus_mcp.runtime.process_list", return_value=[{"ProcessId": 123, "created": "reused"}]):
                self.assertFalse(runtime.status(session)["alive"])

    def test_event_single_object_is_normalized_to_list(self):
        with patch("proteus_mcp.runtime.powershell", return_value={"Id": 1000}):
            self.assertEqual([{"Id": 1000}], diagnostic_events(0))

    def test_states_and_ambiguous_actions(self):
        def snapshot(run, pause, stop):
            return {"actions": [{"text": "Run", "enabled": run, "checked": False},
                                {"text": "Pause", "enabled": stop, "checked": pause},
                                {"text": "Stop", "enabled": stop, "checked": False}]}
        self.assertEqual("stopped", simulation_state(snapshot(True, False, False)))
        self.assertEqual("running", simulation_state(snapshot(False, False, True)))
        self.assertEqual("paused", simulation_state(snapshot(True, True, True)))
        self.assertEqual("unknown", simulation_state({"actions": []}))

    def test_sequence_checks_reject_reverse_skip_unknown_and_missing_nets(self):
        def sample(indices):
            return {"native_nets": ["A", "B", "C"], "transitions": [
                {"states": [5 if n == i else 7 for n in range(3)]} for i in indices]}
        self.assertTrue(assess_sequence(sample([0, 1, 2, 0, 1]))["passed"])
        self.assertFalse(assess_sequence(sample([0, 2, 1, 0]))["passed"])
        self.assertFalse(assess_sequence(sample([0, 1, 2]))["passed"])
        value = sample([0, 1, 2, 0])
        value["transitions"][1]["states"] = [0, 7, 7]
        self.assertFalse(assess_sequence(value)["passed"])
        value = sample([0, 1, 2, 0]) | {"missing_nets": ["A"]}
        self.assertFalse(assess_sequence(value)["passed"])
        value = sample([0, 1, 2, 0]) | {"unstable_snapshot_count": 1}
        self.assertFalse(assess_sequence(value)["passed"])

    def test_runtime_schema_bounds(self):
        schema = next(t["inputSchema"] for t in TOOLS if t["name"] == "project_open")
        for wait in (0, 31, True):
            with self.subTest(wait=wait), self.assertRaises(ValueError):
                check_schema({"path": "a.pdsprj", "wait_seconds": wait}, schema)

    def test_cli_closes_optional_bridge_on_normal_exit(self):
        from proteus_mcp.__main__ import main
        with patch("proteus_mcp.__main__.run", return_value=0), patch("proteus_mcp.bridge.shutdown_bridge") as close:
            self.assertEqual(0, main())
            close.assert_called_once_with()

    def test_cli_closes_optional_bridge_on_error(self):
        from proteus_mcp.__main__ import main
        with patch("proteus_mcp.__main__.run", side_effect=ValueError("fixture")), patch("proteus_mcp.bridge.shutdown_bridge") as close:
            with self.assertRaisesRegex(ValueError, "fixture"):
                main()
            close.assert_called_once_with()

    def test_cli_partial_result_is_not_exit_zero(self):
        from proteus_mcp.__main__ import run
        with patch('sys.argv',['proteus-mcp','call','simulation_inputs','{"session":"fixture"}']),patch('proteus_mcp.__main__.Service') as service,patch('builtins.print'):
            service.return_value.call.return_value={'status':'partial','error':'fixture'}
            self.assertEqual(1,run())

    def test_pending_live_save_is_not_misrouted_to_old_pcb_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = MagicMock()
            runtime.session_path.return_value = Path(folder)/'session.json'
            bridge = Bridge(runtime)
            atomic_write(Path(folder)/'pcb-state.json',json_bytes({'pending':False}))
            atomic_write(Path(folder)/'live-state.json',json_bytes({'pending':True}))
            with patch.object(bridge,'_control',return_value={'status':'fixture'}) as control, patch('proteus_mcp.pcb.PCB.save') as pcb_save:
                self.assertEqual('fixture',bridge.control('fixture','save')['status'])
                control.assert_called_once_with('fixture','save')
                pcb_save.assert_not_called()
            self.assertFalse((Path(folder)/'live.lock').exists())

    def test_simulation_command_respects_structure_edit_session_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = MagicMock()
            runtime.session_path.return_value = Path(folder)/'session.json'
            (Path(folder)/'live.lock').touch()
            with self.assertRaisesRegex(CircuitError,'SESSION_BUSY'):
                Bridge(runtime).control('fixture','run')

    @unittest.skipUnless(os.name == "nt", "Windows handle inheritance")
    def test_helpers_do_not_inherit_redirected_standard_handles(self):
        from proteus_mcp.bridge import isolate_standard_handles
        streams = [MagicMock() for _ in range(3)]
        for index, stream in enumerate(streams):
            stream.fileno.return_value = index
        with patch("proteus_mcp.bridge.sys.stdin", streams[0]), patch("proteus_mcp.bridge.sys.stdout", streams[1]), patch("proteus_mcp.bridge.sys.stderr", streams[2]), patch("msvcrt.get_osfhandle", side_effect=[100, 101, 102]), patch("proteus_mcp.bridge.os.set_handle_inheritable") as set_flag:
            isolate_standard_handles()
            self.assertEqual([(100, False), (101, False), (102, False)], [item.args for item in set_flag.call_args_list])


if __name__ == "__main__":
    unittest.main()
