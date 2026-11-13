"""Check a user-owned native template; never install or distribute it."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from proteus_mcp.native_backend import native_read, read_project
from proteus_mcp.native9 import Builder

REQUIRED = {'AT89C51', 'RES', 'LED-RED', 'CAP-ELEC', 'CAP', 'CRYSTAL'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    readback = native_read(args.path)
    _, parsed = read_project(args.path)
    components = readback['components']
    terminals = {obj['device'] for obj in parsed['terminals']}
    checks = {
        'all_six_baseline_devices_present': REQUIRED <= {p['device'] for p in components},
        'baseline_transform_is_default': all(p['rotation'] == 0 and p['mirror'] == 0 for p in components if p['device'] in REQUIRED),
        'both_power_terminal_types_present': {'$TERPOWER', '$TERGROUND'} <= terminals,
        'at_least_one_saved_wire': readback['wires'] >= 1,
        'dsn_and_cdb_consistent': readback['database_consistent'],
        'no_diagonal_segments': readback['nonorthogonal_wire_segments'] == 0,
    }
    if all(checks.values()):
        Builder(parsed['objects'])
    print(json.dumps({'status': 'passed' if all(checks.values()) else 'failed',
                      'checks': checks, 'sha256': readback['sha256'],
                      'native_reopen_verified': False, 'simulation_verified': False}))
    return int(not all(checks.values()))


if __name__ == '__main__':
    sys.exit(main())
