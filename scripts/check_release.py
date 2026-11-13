"""Audit Git-tracked sources and distribution archives without loading projects."""
import argparse
import json
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PRIVATE_DIRS = {'calibration', 'research', 'workspaces', 'work', 'reports', '.venv', 'venv',
                '__pycache__', '.git', 'node_modules'}
PRIVATE_SUFFIXES = {'.pdsprj', '.workspace', '.dsn', '.cdb', '.lib', '.dll', '.exe', '.hex',
                    '.dmp', '.log', '.pem', '.key', '.pyc', '.pyo', '.sqlite', '.db'}
ALLOWED_ROOT_FILES = {'.gitattributes', '.gitignore', 'LICENSE', 'MANIFEST.in', 'README.md',
                      'NOTICE.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md',
                      'Start-ProteusMcp.ps1', 'mcp-config.example.toml', 'pyproject.toml', 'run_server.py'}
RESOURCES = {'qt_agent.js', 'live_agent.js', 'pcb_agent.js', 'ppc_agent.js',
             'simulation_agent.js', 'notice_watcher.ps1'}
PRIVATE_CONTENT = {
    'github_credential': re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{30,}\b'),
    'service_credential': re.compile(r'\bsk-[A-Za-z0-9_-]{24,}\b|\bAKIA[A-Z0-9]{16}\b'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'personal_windows_path': re.compile(r'(?i)[A-Z]:[\\/]+Users[\\/]+(?!Public\b|Default\b)[^\s\x22\x27\\/]+'),
    'developer_workspace': re.compile(r'(?i)[A-Z]:[\\/]+(?:' + '\u5355\u7247\u673aCodex|' + '\u4ee3\u7801video' + r')[\\/]'),
    'private_email': re.compile(r'[A-Za-z0-9._%+-]+@(?:gmail|qq|outlook|hotmail)\.com\b', re.I),
}


def path_issues(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        return ['unsafe archive path']
    issues = []
    if any(part.lower() in PRIVATE_DIRS for part in path.parts):
        issues.append('private directory')
    if path.suffix.lower() in PRIVATE_SUFFIXES or path.name.lower().startswith('.env'):
        issues.append('private or vendor file type')
    if path.name.upper() == 'HANDOFF.MD':
        issues.append('private development handoff')
    return issues


def content_issues(data):
    if len(data) > 4 * 1024 * 1024:
        return ['oversized source member']
    try:
        value = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return ['unexpected binary content']
    if '\0' in value:
        return ['unexpected binary content']
    return [label for label, pattern in PRIVATE_CONTENT.items() if pattern.search(value)]


def markdown_issues(name, data):
    issues = []
    for raw in re.findall(r'\[[^\]]*\]\(([^\s)]+)', data.decode('utf-8-sig')):
        target = urlsplit(unquote(raw))
        if target.scheme or not target.path:
            continue
        destination = (ROOT / name).parent / target.path
        resolved = destination.resolve()
        if not resolved.is_relative_to(ROOT) or not resolved.is_file():
            issues.append('broken local documentation link: ' + target.path)
    return issues


def audit_sources():
    listed = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True)
    names = [p for p in listed.stdout.decode('utf-8').split('\0') if p]
    errors = []
    if not names:
        errors.append('No tracked files; stage the intended public source files first.')
    for name in names:
        path = ROOT / name
        issues = path_issues(name)
        if path.is_symlink():
            issues.append('tracked symlink')
        if not path.resolve().is_relative_to(ROOT):
            issues.append('path escapes checkout')
        if len(PurePosixPath(name).parts) == 1 and name not in ALLOWED_ROOT_FILES:
            issues.append('unexpected root file')
        if not path.is_file():
            issues.append('tracked file missing from working tree')
        elif not issues:
            data = path.read_bytes()
            issues.extend(content_issues(data))
            if name.endswith('.md'):
                issues.extend(markdown_issues(name, data))
        errors.extend(name + ': ' + issue for issue in issues)
    if names:
        from proteus_mcp.server import TOOLS
        text = (ROOT / 'docs/tools.md').read_text(encoding='utf-8')
        documented = set(re.findall(r'^\| `([a-z][a-z0-9_]+)` \|', text, re.M))
        actual = {tool['name'] for tool in TOOLS}
        if actual != documented or len(actual) != 54:
            errors.append('Tool documentation does not match the 54 registered tools.')
        if not {'proteus_mcp/' + name for name in RESOURCES} <= set(names):
            errors.append('One or more runtime bridge resources are not tracked.')
    return names, errors


def archive_entries(path):
    if path.suffix == '.whl':
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if not member.is_dir():
                    if member.file_size > 4 * 1024 * 1024:
                        raise ValueError('oversized wheel member')
                    yield member.filename, archive.read(member)
    elif path.name.endswith('.tar.gz'):
        with tarfile.open(path, 'r:gz') as archive:
            for member in archive.getmembers():
                if member.isdir():
                    continue
                if not member.isfile() or member.size > 4 * 1024 * 1024:
                    raise ValueError('non-regular or oversized sdist member')
                parts = PurePosixPath(member.name).parts
                yield '/'.join(parts[1:]), archive.extractfile(member).read()
    else:
        raise ValueError('unsupported distribution type')


def audit_artifact(path, tracked):
    errors, names = [], set()
    wheel = path.suffix == '.whl'
    for name, data in archive_entries(path):
        names.add(name)
        issues = path_issues(name) + content_issues(data)
        if wheel:
            if not name.startswith('proteus_mcp/') and '.dist-info/' not in name:
                issues.append('unexpected wheel member')
        elif name not in tracked and not (name == 'PKG-INFO' or name == 'setup.cfg' or '.egg-info/' in name):
            issues.append('sdist contains an untracked file')
        source = ROOT / name
        if name.startswith('proteus_mcp/') and (not source.is_file() or source.read_bytes() != data):
            issues.append('package source differs from checkout')
        errors.extend(path.name + ': ' + name + ': ' + issue for issue in issues)
    if not {'proteus_mcp/' + name for name in RESOURCES} <= names:
        errors.append(path.name + ': runtime resources missing')
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifacts', type=Path)
    args = parser.parse_args()
    names, errors = audit_sources()
    artifacts = []
    if args.artifacts:
        artifacts = sorted([*args.artifacts.glob('*.whl'), *args.artifacts.glob('*.tar.gz')])
        if not any(p.suffix == '.whl' for p in artifacts) or not any(p.name.endswith('.tar.gz') for p in artifacts):
            errors.append('Both wheel and sdist must be present.')
        for path in artifacts:
            errors.extend(audit_artifact(path, set(names)))
    print(json.dumps({'status': 'failed' if errors else 'passed', 'tracked_files': len(names),
                      'artifacts': [p.name for p in artifacts], 'errors': errors}, ensure_ascii=True, indent=2))
    return int(bool(errors))


if __name__ == '__main__':
    sys.exit(main())
