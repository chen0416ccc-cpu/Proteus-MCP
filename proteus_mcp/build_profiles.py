"""Native ABI profiles and capability-specific, fail-closed build selection."""

import hashlib
from pathlib import Path

from .model import CircuitError


PROFILE = {
    'Qt5Gui.dll': '5e7d2d41b8b92a880e83b8cc0ca173f5da61218604186196787ee1600956be1e',
    'AppFrame.dll': '318cc4602d6a6fec23066261632dadd5f0094664e71e839c725397a898b718a2',
    'PDS.exe': '86c0c9b2e69d6cb06d1b5c3730910c5d8d715a8b01fa5fd285798c11b0c83fbf',
    'Qt5Core.dll': '8d2ff4ce9096ddccc4f4cd62c2e41fc854cfd1b0d6e8d296645a7f5fd4ae565a',
    'Qt5Widgets.dll': '3788c669d4b645e5a576de9fc77fca776bf516d43c89143dc2ca28291ba14358',
    'DSIM.dll': '239892aa14c96fa4a4fd3f8baa055a79eed599409acf54485dc7668de7b84dfb',
}
LIVE_PROFILE = {
    'ISIS.dll': '7c9b902732e8a43cb6176e05bc8649b1611df2012d34b0ad49132fadbe6e08f6',
    'AppFrame.dll': PROFILE['AppFrame.dll'],
    'LxlCore.dll': 'cbc121209ad413f8f4ac016203351159482b739f053bc02c770be844c3dd5251',
    'Prims.dll': '1d1cc0fab42042cbd8cf592524a3ee594fc4261fd44a540f842795755a094ee9',
}
PCB_PROFILE = {**LIVE_PROFILE, 'ARES.dll': 'dc4fa4aac6b5a41dbe46d4d72f48ad1ea2921e9ce1ce74cec1283dcd855058d6'}
SIMULATION_PROFILE = {**LIVE_PROFILE,
    'VSMDEBUG.dll': 'bace6a1f7b2ee37de57cc2cad10ca8b513534521007092fd57a7c58d8b654f70',
    'PROSPICE.dll': 'a5e98b3e522cbb924c39cc4c92becb2f0f7dc93eb966ba568affcb1855290123'}

PROFILES = ({'id': 'proteus-9.0.40482.2-win64', 'version': '9.0.40482.2',
             'architecture': 'x64', 'pointer_size': 8, 'adapter': 'p9-907-win64-v1',
             'capabilities': {'qt': PROFILE, 'live': PROFILE | LIVE_PROFILE,
                              'pcb': PROFILE | PCB_PROFILE, 'simulation': PROFILE | SIMULATION_PROFILE}},)
ADAPTER_SOURCES = {'p9-907-win64-v1': {
    'qt': ('qt_agent.js',), 'live': ('live_agent.js',),
    'pcb': ('pcb_agent.js', 'ppc_agent.js'), 'simulation': ('simulation_agent.js',)}}


def file_sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def diagnose_build(folder, capabilities=('qt', 'live', 'pcb', 'simulation'), *, profiles=None):
    profiles = PROFILES if profiles is None else profiles
    names = sorted({name for p in profiles for cap in capabilities for name in p['capabilities'].get(cap, {})})
    modules = {}
    for name in names:
        path = Path(folder) / name
        try:
            modules[name] = {'sha256': file_sha256(path), 'bytes': path.stat().st_size}
        except OSError as error:
            modules[name] = {'sha256': None, 'error': type(error).__name__}
    results = {}
    for capability in capabilities:
        comparisons = []
        for profile in profiles:
            requirements = profile['capabilities'].get(capability)
            if not requirements:
                continue
            missing = [n for n in requirements if modules[n]['sha256'] is None]
            mismatched = [{'module': n, 'expected_sha256': sha, 'actual_sha256': modules[n]['sha256']}
                          for n, sha in requirements.items() if modules[n]['sha256'] not in (None, sha)]
            comparisons.append({'profile': profile['id'], 'adapter': profile['adapter'],
                                'missing_modules': missing, 'mismatched_modules': mismatched,
                                'matches': not missing and not mismatched})
        matches = [c for c in comparisons if c['matches']]
        selected = matches[0] if len(matches) == 1 else None
        results[capability] = {'available': selected is not None,
                               'status': 'matched' if selected else 'ambiguous' if matches else 'unsupported',
                               'profile': selected['profile'] if selected else None,
                               'adapter': selected['adapter'] if selected else None,
                               'comparisons': comparisons}
    return {'folder': str(Path(folder).resolve()), 'modules': modules, 'capabilities': results,
            'supported_profiles': [{'id': p['id'], 'version': p['version'], 'architecture': p['architecture'],
                                     'adapter': p['adapter']} for p in profiles],
            'unknown_build_action': 'native_calls_blocked; retain this report for a separately validated adapter'}


def select_profile(folder, live=False, pcb=False, simulation=False):
    capabilities = ['qt'] + (['live'] if live else []) + (['pcb'] if pcb else []) + (['simulation'] if simulation else [])
    report = diagnose_build(folder, capabilities)
    selected = {r['profile'] for r in report['capabilities'].values() if r['available']}
    if len(selected) != 1 or not all(r['available'] for r in report['capabilities'].values()):
        failures = sorted({n for r in report['capabilities'].values() if not r['available'] for c in r['comparisons']
                           for n in c['missing_modules'] + [m['module'] for m in c['mismatched_modules']]})
        raise CircuitError('NATIVE_BUILD_NOT_CALIBRATED: no single verified ABI profile; inspect proteus_doctor.native_build; '
                           + ', '.join(failures))
    profile = next(p for p in PROFILES if p['id'] in selected)
    if profile['adapter'] not in ADAPTER_SOURCES:
        raise CircuitError('NATIVE_ADAPTER_MISSING: ' + profile['adapter'])
    return profile, {n: m['sha256'] for n, m in report['modules'].items()}
