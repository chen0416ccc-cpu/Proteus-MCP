"""Explicit native round-trip verification; never captures or controls a screen."""

from pathlib import Path

from .model import CircuitError, atomic_write, json_bytes


def verify_initialization(path, runtime, bridge):
    from .bridge import frida_module
    from .build_profiles import select_profile
    from .live import Live, geometry_issues
    from .native_backend import circuit_signature, native_read, read_project
    from .native9 import PIN_ORDER, pin_xy
    profile, hashes = select_profile(runtime.executable.parent, live=True)
    frida_module()
    before = native_read(path)
    result = {'status': 'unconfirmed', 'native_reopen_verified': False, 'simulation_verified': False,
              'desktop_input': False, 'profile': profile['id'], 'modules': hashes, 'sessions': []}
    session = None
    try:
        for phase in ('initial_open', 'reopen'):
            result['phase'] = phase
            opened = runtime.open(path, 30)
            if opened['status'] != 'project_window_observed':
                result.update(status='busy' if opened['status'] == 'busy' else 'unconfirmed', open_result=opened)
                session = opened.get('session')
                break
            session = opened['session']
            result['sessions'].append(session)
            if phase == 'initial_open':
                saved = bridge.control(session, 'save')
                result['save'] = {'status': saved['status'], 'save_file_written': saved.get('save_file_written'),
                                  'circuit_preserved': saved.get('circuit_preserved')}
                if saved['status'] != 'verified':
                    break
            else:
                state = Live(bridge, None).read(session)['state']
                file = native_read(path)
                _, parsed = read_project(path)
                expected = {part['ref']: part for part in parsed['components']}
                actual = {part['ref']: part for part in state['components']}
                issues = geometry_issues(state)
                if set(actual) != set(expected) or len(actual) != len(state['components']):
                    issues.append({'error': 'INITIALIZATION_NATIVE_COMPONENT_SET_MISMATCH'})
                for ref, part in expected.items():
                    observed = actual.get(ref)
                    if observed is None:
                        continue
                    if observed['device'] != part['device'] or observed['position_nm'] != part['origin']:
                        issues.append({'ref': ref, 'error': 'INITIALIZATION_NATIVE_COMPONENT_MISMATCH'})
                    if sorted(pin['number'] for pin in observed['pins']) != sorted(PIN_ORDER[part['device']]):
                        issues.append({'ref': ref, 'error': 'INITIALIZATION_NATIVE_PIN_SET_MISMATCH'})
                        continue
                    for pin in observed['pins']:
                        xy = pin_xy(part, pin['number'])
                        if xy is not None and xy != pin['xy']:
                            issues.append({'ref': ref, 'pin': pin['number'], 'error': 'INITIALIZATION_NATIVE_PIN_MISMATCH'})
                wires = sorted(min(w['points'], w['points'][::-1]) for w in state['wires'])
                if wires != file['wire_geometry']:
                    issues.append({'error': 'INITIALIZATION_NATIVE_WIRE_GEOMETRY_MISMATCH'})
                if circuit_signature(before) != circuit_signature(file):
                    issues.append({'error': 'INITIALIZATION_NATIVE_ROUNDTRIP_MISMATCH'})
                result.update(issues=issues, observed_components=len(actual), observed_wires=len(wires))
                result.update(status='verified' if not issues else 'unconfirmed', native_reopen_verified=not issues)
            closed = runtime.close(session)
            result['close'] = closed['status']
            if closed['status'] not in ('closed', 'already_closed'):
                result.update(status='unconfirmed', native_reopen_verified=False)
                break
            diagnostic = runtime.diagnostics(session)
            result.setdefault('diagnostics', []).append(diagnostic)
            if diagnostic['events'] or diagnostic['crash_dumps'] or diagnostic['collection_errors']:
                result.update(status='unconfirmed', native_reopen_verified=False)
                break
            session = None
    except (CircuitError, OSError, ValueError) as error:
        result.update(error=str(error), status='unconfirmed', native_reopen_verified=False)
    finally:
        if session is not None:
            try:
                result['cleanup'] = runtime.close(session)
                if result['cleanup']['status'] not in ('closed', 'already_closed'):
                    result.update(status='unconfirmed', native_reopen_verified=False)
            except (CircuitError, OSError, ValueError) as error:
                result.update(cleanup_error=str(error), status='unconfirmed', native_reopen_verified=False)
        atomic_write(Path(path).parent / 'native-verification.json', json_bytes(result))
    return result
