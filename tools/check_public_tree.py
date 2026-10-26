"""Conservative public-source scan. Never echoes matched values."""
from pathlib import Path
import hashlib
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRS = {'.git', '.state', '.venv', '__pycache__', '.pytest_cache', 'node_modules'}
# Reviewed font and approved generated brand mark; changed bytes fail closed.
PUBLIC_BINARIES = {
    'gateway/static/brand-mark.png': '375d881389744d1b9e625f53e0a36eea350bf109aaee48883371028dbeae35fc',
    'gateway/static/fonts/ChillReunion-Round.woff2': '4f4e36dd0601c5fa325c6634649494767e9e7a0da8cc7b1fc9496ba1f7438288',
}
PATTERNS = {
    'credential': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{25,}|lg_[A-Za-z0-9_-]{32,})'),
    'private_ipv4': re.compile(r'(?<![\d.])(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![\d.])'),
    'gpu_uuid': re.compile(r'GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'personal_home': re.compile(r'(?:[A-Z]:[\\/]Users[\\/][^\s"\']+|' + '/' + r'home/(?!example(?:/|\b))[^\s/"\']+/)'),
}


def candidate_paths():
    result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                            cwd=ROOT, capture_output=True, check=True)
    return sorted(set(p for p in result.stdout.decode('utf-8').split('\0') if p))


def contents(name, path):
    # Check index bytes as well as working-tree bytes; the index is what gets committed.
    staged = subprocess.run(['git', 'show', ':'+name], cwd=ROOT, capture_output=True)
    if staged.returncode == 0:
        yield staged.stdout
    if path.is_file():
        yield path.read_bytes()


def main():
    findings = []
    paths = candidate_paths()
    for name in paths:
        path = ROOT / name
        parts = set(Path(name).parts)
        if parts & IGNORED_DIRS or (path.name.startswith('.env') and path.name != '.env.example') or path.suffix.lower() in {'.db','.sqlite','.log','.pem','.key','.p12','.pfx','.mp4','.zip'}:
            findings.append((name, 'forbidden_file'))
            continue
        for raw in contents(name, path):
            if name in PUBLIC_BINARIES:
                if hashlib.sha256(raw).hexdigest() != PUBLIC_BINARIES[name]:
                    findings.append((name, 'unreviewed_binary'))
                continue
            try:
                data = raw.decode('utf-8')
            except UnicodeError:
                findings.append((name, 'unexpected_binary'))
                continue
            for rule, regex in PATTERNS.items():
                if regex.search(data):
                    findings.append((name, rule))
    findings = sorted(set(findings))
    for name, rule in findings:
        print(f'{name}: {rule}')
    print(f'Public source scan: {len(paths)} files, {len(findings)} findings (matched values suppressed).')
    return int(bool(findings))


if __name__ == '__main__':
    sys.exit(main())
