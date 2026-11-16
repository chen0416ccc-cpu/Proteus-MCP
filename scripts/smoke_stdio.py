"""Probe an installed distribution in an isolated process; never launch Proteus."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', default=sys.executable)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='proteus-mcp-smoke-') as temporary:
        root = Path(temporary)
        env = dict(os.environ)
        env.update(PROTEUS_MCP_WORKSPACE=str(root / 'work'), PROTEUS_MCP_EXE=str(root / 'missing/PDS.exe'),
                   PROTEUS_MCP_LIBRARY=str(root / 'missing/Library'), PROTEUS_MCP_KEIL=str(root / 'missing/Keil'),
                   PROTEUS_MCP_CALIBRATION=str(root / 'missing/template.pdsprj'))
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-06-18', 'capabilities': {},
                'clientInfo': {'name': 'release-smoke', 'version': '1'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
             'params': {'name': 'proteus_doctor', 'arguments': {}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'ping', 'params': {}},
        ]
        process = subprocess.run([args.python, '-I', '-X', 'utf8', '-m', 'proteus_mcp', 'serve'],
                                 cwd=root, env=env, input=''.join(json.dumps(r) + '\n' for r in requests),
                                 text=True, encoding='utf-8', capture_output=True, timeout=30)
        assert process.returncode == 0, 'installed server exited unsuccessfully'
        assert not process.stderr, 'installed server emitted stderr'
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        assert len(replies) == 4 and [r['id'] for r in replies] == [1, 2, 3, 4]
        assert all('result' in reply for reply in replies)
        assert replies[0]['result']['protocolVersion'] == '2025-06-18'
        tools = replies[1]['result']['tools']
        assert len(tools) == 57 and len({t['name'] for t in tools}) == 57
        assert {'simulation_audio','simulation_diagnose','simulation_cold_start'} <= {t['name'] for t in tools}
        doctor = json.loads(replies[2]['result']['content'][0]['text'])
        assert not replies[2]['result']['isError']
        assert doctor['capabilities']['semantic_circuit']['available']
        assert not doctor['capabilities']['native_file_generation']['available']
        assert not doctor['capabilities']['native_simulation_control']['available']
        assert not doctor['capabilities']['keil_build']['available']
    print(json.dumps({'status': 'passed', 'tools': 57, 'isolated_installed_package': True,
                      'missing_native_dependencies_reported': True, 'native_processes_started': False}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
