"""Run explicit portable or local-fixture test profiles without native writes."""
import argparse
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE_MODULES = {
    'test_database', 'test_label_layout', 'test_native_backend', 'test_routing',
}
PORTABLE_METHODS = {
    'test_native9': {'test_truncated_data', 'test_reference_junction_record'},
    'test_orderly': {'test_plan_coordinates_and_diagonals_fail_closed'},
    'test_schematic': {'test_complete_wire_contacts_are_audited_independently',
                       'test_crossings_are_only_perpendicular_straight_interiors',
                       'test_crossing_must_continue_straight'},
}
FIXTURE_TESTS = {'test_model.ModelTests.test_native_capabilities_distinguish_file_edits_from_live_control'}


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def needs_local_fixture(case):
    module, _, method = case.id().rsplit('.', 2)
    return (module in FIXTURE_MODULES or case.id() in FIXTURE_TESTS or
            (module in PORTABLE_METHODS and method not in PORTABLE_METHODS[module]))


def skip_local_fixture():
    raise unittest.SkipTest('requires local Proteus libraries or private calibration fixtures')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=('portable', 'full'), default='portable')
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    if args.profile == 'portable':
        for case in cases(suite):
            if needs_local_fixture(case):
                case.setUp = skip_local_fixture
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({'profile': args.profile, 'collected': result.testsRun,
                      'passed': result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
                      'skipped': len(result.skipped), 'failures': len(result.failures),
                      'errors': len(result.errors), 'native_processes_started': False}))
    return int(not result.wasSuccessful())


if __name__ == '__main__':
    sys.exit(main())
