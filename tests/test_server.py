import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from proteus_mcp.server import Server, TOOLS
from proteus_mcp.service import Service


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = Server(Service(self.temp.name))

    def request(self, method, params=None, request_id=1):
        return self.server.dispatch({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})

    def test_initialize_and_list(self):
        result = self.request("initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertEqual("2025-06-18", result["protocolVersion"])
        self.assertEqual(len(TOOLS), len(self.request("tools/list")["result"]["tools"]))

    def test_call_before_initialize(self):
        self.assertEqual(-32002, self.request("tools/list")["error"]["code"])

    def test_notification_no_response(self):
        self.assertIsNone(self.server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_unknown_method(self):
        self.request("initialize")
        self.assertEqual(-32601, self.request("unknown")["error"]["code"])

    def test_unknown_tool(self):
        self.request("initialize")
        self.assertEqual(-32602, self.request("tools/call", {"name": "nonexistent"})["error"]["code"])

    def test_schema_required_and_unknown_fields(self):
        self.request("initialize")
        for arguments in ({}, {"project": 123}, {"project": "foo", "unexpected": True}):
            result = self.request("tools/call", {"name": "project_create", "arguments": arguments})
            self.assertEqual(-32602, result["error"]["code"])

    def test_failed_tool_iserror(self):
        self.request("initialize")
        result = self.request("tools/call", {"name": "native_apply", "arguments": {"project": "foo"}})
        self.assertTrue(result["result"]["isError"])

    def test_parse_error_then_ping(self):
        target = io.StringIO()
        source = io.StringIO('bad json\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n')
        self.server.serve(source, target)
        lines = [json.loads(line) for line in target.getvalue().splitlines()]
        self.assertEqual(-32700, lines[0]["error"]["code"])
        self.assertEqual(7, lines[1]["id"])

    def test_invalid_request(self):
        self.assertEqual(-32600, self.server.dispatch([])["error"]["code"])

    def test_actual_stdio_process(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "example_led_chaser", "arguments": {"project": "stdio_test"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "project_validate", "arguments": {"project": "stdio_test"}}},
        ]
        result = subprocess.run([sys.executable, "-m", "proteus_mcp", "--workspace", self.temp.name, "serve"],
                                input="".join(json.dumps(request)+"\n" for request in requests),
                                capture_output=True, text=True, timeout=20, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([1, 2, 3, 4], [response["id"] for response in responses])
        checked = json.loads(responses[-1]["result"]["content"][0]["text"])
        self.assertTrue(checked["ok"])
        self.assertEqual("not_applied", checked["native_status"])


if __name__ == "__main__":
    unittest.main()
