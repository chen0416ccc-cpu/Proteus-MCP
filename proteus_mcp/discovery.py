"""Read-only installation discovery. Explicit paths never silently fall back."""

import os
import re
import shutil
from pathlib import Path


def user_data_dir():
    if os.name == 'nt':
        return Path(os.getenv('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'Proteus-MCP'
    return Path(os.getenv('XDG_DATA_HOME') or Path.home() / '.local/share') / 'proteus-mcp'


def default_workspace():
    return Path(os.getenv('PROTEUS_MCP_WORKSPACE') or user_data_dir() / 'workspaces').resolve()


def registry_products():
    """Only installation metadata, never licensing keys or recent projects."""
    try:
        import winreg
    except ImportError:
        return []
    products = []
    roots = ((r'SOFTWARE\Labcenter Electronics', 'labcenter'),
             (r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'uninstall'))
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for location, kind in roots:
                try:
                    key = winreg.OpenKey(hive, location, 0, winreg.KEY_READ | view)
                except OSError:
                    continue
                with key:
                    for index in range(min(winreg.QueryInfoKey(key)[0], 4096)):
                        try:
                            name = winreg.EnumKey(key, index)
                            with winreg.OpenKey(key, name) as child:
                                record = {}
                                for field in ('Path', 'ProgramData', 'InstallLocation', 'DisplayIcon',
                                              'DisplayName', 'DisplayVersion', 'InstallVersion', 'SingleUser'):
                                    try:
                                        value = winreg.QueryValueEx(child, field)[0]
                                        if isinstance(value, str):
                                            record[field] = os.path.expandvars(value)
                                    except OSError:
                                        pass
                            title = record.get('DisplayName', name)
                            if (kind == 'labcenter' and title.startswith('Proteus ')) or (
                                    kind == 'uninstall' and re.search(r'(?i)proteus|keil', title)):
                                products.append(dict(record, name=title, source='registry:' + location + '\\' + name))
                        except OSError:
                            continue
    return products


def _version(value):
    return tuple(int(n) for n in re.findall(r'\d+', value))


def _unique(paths):
    result = []
    for path in paths:
        path = Path(path).expanduser().resolve()
        if path not in result:
            result.append(path)
    return result


def _icon_path(value):
    value = value.strip()
    if value.startswith('"'):
        return value[1:].split('"', 1)[0]
    return re.sub(r',-?\d+$', '', value)


def discover(proteus=None, library=None, keil=None, *, products=None, environ=None, which=None):
    """Resolve one installation, preferring arguments, then env, then discovery."""
    env = os.environ if environ is None else environ
    products = registry_products() if products is None else products
    which = shutil.which if which is None else which
    candidates = []
    for record in products:
        if not record['name'].startswith('Proteus '):
            continue
        location = record.get('Path') or record.get('InstallLocation')
        if not location:
            continue
        executable = (Path(location) / 'Bin/PDS.exe').resolve()
        if not executable.is_file():
            continue
        data = [Path(record['ProgramData'])] if record.get('ProgramData') else []
        candidates.append({'executable': executable, 'data': data, 'source': record['source'],
                           'version': record.get('InstallVersion') or record.get('DisplayVersion') or ''})
    for variable in ('ProgramFiles', 'ProgramFiles(x86)'):
        if not env.get(variable):
            continue
        base = Path(env[variable]) / 'Labcenter Electronics'
        for executable in sorted(base.glob('Proteus */Bin/PDS.exe')):
            candidates.append({'executable': executable.resolve(), 'data': [],
                               'source': variable, 'version': executable.parents[1].name})
    on_path = which('PDS.exe')
    if on_path and Path(on_path).is_file():
        candidates.append({'executable': Path(on_path).resolve(), 'data': [], 'source': 'PATH', 'version': ''})
    candidates.sort(key=lambda item: (_version(item['version']), str(item['executable'])), reverse=True)
    explicit = proteus if proteus is not None else env.get('PROTEUS_MCP_EXE')
    executable = Path(explicit).expanduser().resolve() if explicit is not None else (
        candidates[0]['executable'] if candidates else None)
    matches = [c for c in candidates if c['executable'] == executable]
    data_roots = [p for c in matches for p in c['data']]
    if executable is not None:
        install = executable.parent.parent
        data_roots.append(install)
        for variable in ('ProgramData', 'LOCALAPPDATA'):
            if env.get(variable):
                data_roots.append(Path(env[variable]) / 'Labcenter Electronics' / install.name)
    data_roots = _unique(data_roots)
    explicit_library = library if library is not None else env.get('PROTEUS_MCP_LIBRARY')
    library_path = Path(explicit_library).expanduser().resolve() if explicit_library is not None else next(
        (p / 'Library' for p in data_roots if (p / 'Library').is_dir()), None)
    explicit_keil = keil if keil is not None else env.get('PROTEUS_MCP_KEIL')
    keil_roots = []
    for record in products:
        if not re.search(r'(?i)keil', record['name']):
            continue
        if record.get('InstallLocation'):
            keil_roots.append(Path(record['InstallLocation']))
        if record.get('DisplayIcon'):
            icon = Path(_icon_path(record['DisplayIcon']))
            keil_roots.append(icon.parent.parent)
    c51 = which('C51.exe')
    if c51:
        keil_roots.append(Path(c51).parent.parent.parent)
    for variable in ('ProgramFiles', 'ProgramFiles(x86)', 'SystemDrive'):
        if env.get(variable):
            for name in ('Keil_v5', 'Keil5', 'Keil'):
                keil_roots.append(Path(env[variable] + '/' if variable == 'SystemDrive' else env[variable]) / name)
    keil_path = Path(explicit_keil).expanduser().resolve() if explicit_keil is not None else next(
        (p for p in _unique(keil_roots) if (p / 'C51/BIN/C51.exe').is_file()), None)
    missing = user_data_dir() / 'unconfigured'
    return {'proteus': executable or missing / 'PDS.exe', 'library': library_path or missing / 'Library',
            'keil': keil_path or missing / 'Keil', 'data_roots': data_roots,
            'sources': {'proteus': 'argument' if proteus is not None else 'PROTEUS_MCP_EXE' if explicit is not None else
                        matches[0]['source'] if matches else 'not_found',
                        'library': 'argument' if library is not None else 'PROTEUS_MCP_LIBRARY' if explicit_library is not None else
                        'selected_installation' if library_path else 'not_found',
                        'keil': 'argument' if keil is not None else 'PROTEUS_MCP_KEIL' if explicit_keil is not None else
                        'installation_metadata' if keil_path else 'not_found'},
            'detected_version': matches[0]['version'] if matches else None,
            'candidates': [{'path': str(c['executable']), 'version': c['version'], 'source': c['source']} for c in candidates]}


def configuration_report(config):
    return {key: str(config[key]) for key in ('proteus', 'library', 'keil')} | {
        'sources': config['sources'], 'detected_version': config['detected_version'],
        'data_roots': [str(p) for p in config['data_roots']], 'candidates': config['candidates']}
