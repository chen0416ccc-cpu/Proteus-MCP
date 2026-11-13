"""Explicit, unattended stdio acceptance on fresh workspace projects. No screen APIs."""

import argparse
import json
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from proteus_mcp.capabilities import source_fingerprint
from proteus_mcp import __version__
from proteus_mcp.discovery import default_workspace
from proteus_mcp.live import disk_matches, geometry_issues
from proteus_mcp.model import atomic_write, digest, json_bytes


class Client:
    def __init__(self, workspace, installed=False):
        command = ([sys.executable, '-I', '-X', 'utf8', '-m', 'proteus_mcp'] if installed else
                   [sys.executable, '-X', 'utf8', str(ROOT/'run_server.py')])
        self.process = subprocess.Popen(command + ['--workspace', str(workspace), 'serve'],
                                        cwd=workspace if installed else ROOT,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding='utf-8', bufsize=1)
        self.queue, self.errors, self.serial = queue.Queue(), [], 0
        self.out = threading.Thread(target=self._output, daemon=True)
        self.err = threading.Thread(target=self._errors, daemon=True)
        self.out.start()
        self.err.start()

    def _output(self):
        for line in self.process.stdout:
            self.queue.put(line)
        self.queue.put(None)

    def _errors(self):
        self.errors.extend(self.process.stderr.readlines())

    def request(self, method, params=None, timeout=180):
        self.serial += 1
        request = {'jsonrpc': '2.0', 'id': self.serial, 'method': method, 'params': params or {}}
        self.process.stdin.write(json.dumps(request)+'\n')
        self.process.stdin.flush()
        line = self.queue.get(timeout=timeout)
        if line is None:
            raise RuntimeError('MCP server exited before replying')
        reply = json.loads(line)
        if reply.get('id') != self.serial or 'result' not in reply:
            raise RuntimeError('Invalid MCP reply: ' + line[:1000])
        return reply['result']

    def notify(self, method):
        self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': method})+'\n')
        self.process.stdin.flush()

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            # Terminate only this harness's Python child, never a Proteus process.
            self.process.terminate()
            self.process.wait(timeout=10)
        self.out.join(timeout=2)
        self.err.join(timeout=2)
        return self.process.returncode, ''.join(self.errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=default_workspace())
    parser.add_argument('--case', choices=('all', 'schematic', 'led'), default='all')
    parser.add_argument('--installed-server', action='store_true',
                        help='Use this Python environment\'s installed package in isolation, not run_server.py')
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    identifier = time.strftime('%Y%m%d-%H%M%S')
    folder = workspace/'_acceptance'/identifier
    folder.mkdir(parents=True, exist_ok=False)
    report_path = folder/'report.json'
    report = {'status': 'running', 'checks': {}, 'calls': [], 'source_before': {},
              'harness_checkout_before': source_fingerprint(), 'isolated_installed_server': args.installed_server,
              'harness_sha256': digest(Path(__file__).read_bytes()), 'desktop_input': False,
              'screen_captured': False, 'visual_layout_verified': False, 'errors': [], 'sessions': []}
    client, session = None, None

    def persist():
        atomic_write(report_path, json_bytes(report))

    def check(name, condition):
        report['checks'][name] = bool(condition)
        persist()
        if not condition:
            raise RuntimeError('Acceptance check failed: ' + name)

    def call(name, **arguments):
        item = {'tool': name, 'arguments': arguments}
        report['calls'].append(item)
        persist()
        response = client.request('tools/call', {'name': name, 'arguments': arguments})
        item['response'] = response
        persist()
        if response.get('isError'):
            raise RuntimeError(name+': '+json.dumps(response, ensure_ascii=True)[:2000])
        return json.loads(response['content'][0]['text'])

    def verify_memory(name, exported, current):
        state = call('live_read', session=current)['state']
        disk = call('native_read', path=exported['path'])
        wires = sorted(min(w['points'], w['points'][::-1]) for w in state['wires'])
        check(name+'_live_geometry', not geometry_issues(state) and wires == disk['wire_geometry'])
        check(name+'_live_connectivity', disk_matches(state, disk))
        check(name+'_file_quality', exported['routing']['file_readback_quality']['passed'])
        check(name+'_actual_sheet_size', disk['sheet_size_grid'] == exported['routing']['sheet']['sheet_size_grid'])

    def roundtrip(name, exported):
        nonlocal session
        opened = call('project_open', path=exported['path'], wait_seconds=30)
        session = opened.get('session')
        if session:
            report['sessions'].append(session)
        check(name+'_open', opened['status'] == 'project_window_observed')
        verify_memory(name, exported, session)
        reopened = call('project_reopen', session=session)
        session = reopened.get('session', session)
        check(name+'_reopen', reopened['status'] == 'project_window_observed')
        report['sessions'].append(session)
        verify_memory(name+'_reopened', exported, session)
        closed = call('project_close', session=session)
        check(name+'_close', closed['status'] == 'closed')
        diagnostic = call('runtime_diagnostics', session=session)
        check(name+'_no_crash', not diagnostic['events'] and not diagnostic['crash_dumps'] and not diagnostic['collection_errors'])
        session = None

    try:
        client = Client(workspace, installed=args.installed_server)
        client.request('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                     'clientInfo': {'name': 'native-acceptance', 'version': '1'}})
        client.notify('notifications/initialized')
        doctor = call('proteus_doctor')
        report['source_before'] = {'sha256': doctor['adapter_source_sha256']}
        report['server_version'] = doctor['server_version']
        check('server_version_matches', doctor['server_version'] == __version__)
        initialized = call('native_initialize', verify_native=True)
        check('automatic_initialization_native_roundtrip', initialized['native_reopen_verified'])
        if args.case in ('all', 'schematic'):
            name = 'timer_'+identifier.replace('-', '_')
            call('project_create', project=name)
            matches = call('library_search', query='NE555', limit=200)['results']
            chosen = [r for r in matches if r['device'] == 'NE555']
            chosen.sort(key=lambda r: (Path(r['library_path']).name != 'DEVICE.LIB', r['library_path']))
            devices = {'TIMER': 'NE555', 'RA': 'RES', 'RB': 'RES', 'RL': 'RES', 'CT': 'CAP', 'CV': 'CAP'}
            values = {'RA': '10k', 'RB': '100k', 'RL': '1k', 'CT': '10uF', 'CV': '10nF'}
            operations = [{'op': 'add', 'ref': ref, 'device': device, 'position': [0, 0],
                           'properties': {'value': values[ref]} if ref in values else {},
                           **({'library_path': chosen[0]['library_path']} if device == 'NE555' else {})}
                          for ref, device in devices.items()]
            nets = {'VCC': ['TIMER.8', 'TIMER.4', 'RA.1'], 'GND': ['TIMER.1', 'CT.2', 'CV.2', 'RL.2'],
                    'CHARGE': ['TIMER.7', 'RA.2', 'RB.1'], 'TIMING': ['TIMER.2', 'TIMER.6', 'RB.2', 'CT.1'],
                    'CONTROL': ['TIMER.5', 'CV.1'], 'OUT': ['TIMER.3', 'RL.1']}
            operations.extend({'op': 'connect', 'name': net, 'pins': pins} for net, pins in nets.items())
            call('circuit_edit', project=name, expected_revision=0, operations=operations)
            roundtrip('ne555', call('native_export', project=name))
        if args.case in ('all', 'led'):
            name = 'led_'+identifier.replace('-', '_')
            call('example_led_chaser', project=name)
            built = call('firmware_build', project=name)
            check('c51_compiled', built['status'] == 'compiled')
            exported = call('native_export', project=name)
            roundtrip('led', exported)
            simulated = call('native_verify', path=exported['path'], seconds=8)
            check('led_native_simulation', simulated['status'] == 'passed' and simulated['simulation_verified'])
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', traceback=traceback.format_exc())
        report['errors'].append(type(error).__name__+': '+str(error))
    finally:
        if client is not None:
            if session is not None:
                try:
                    report['cleanup'] = call('project_close', session=session)
                    if report['cleanup']['status'] not in ('closed', 'already_closed'):
                        report['status'] = 'failed'
                except Exception as error:
                    report['errors'].append('cleanup: '+str(error))
                    report['status'] = 'failed'
            try:
                doctor = call('proteus_doctor')
                report['source_after'] = {'sha256': doctor['adapter_source_sha256']}
            except Exception as error:
                report['errors'].append('final source check: '+str(error))
                report['status'] = 'failed'
            report['server_exit_code'], report['stderr'] = client.close()
            if report['server_exit_code'] != 0 or report['stderr']:
                report['status'] = 'failed'
        report['harness_checkout_after'] = source_fingerprint()
        if (not report['source_before'].get('sha256') or
                report['source_before'].get('sha256') != report.get('source_after', {}).get('sha256') or
                report['harness_checkout_before'] != report['harness_checkout_after']):
            report['status'] = 'failed'
            report['errors'].append('Source changed during acceptance')
        persist()
    print(json.dumps({'status': report['status'], 'report_path': str(report_path),
                      'checks': report['checks'], 'errors': report['errors'], 'desktop_input': False}, ensure_ascii=True, indent=2))
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
