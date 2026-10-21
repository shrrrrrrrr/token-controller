"""Check a release diff keeps the UI and role guide versions together."""
import argparse
import ast
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.run(['git',*args],cwd=ROOT,capture_output=True,check=True).stdout.decode('utf-8')


def version(source):
    for node in ast.parse(source).body:
        if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='UI_VERSION' for target in node.targets):
            return ast.literal_eval(node.value)
    return None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',default='HEAD',help='Git ref before this release')
    args=parser.parse_args()
    changed=set(git('diff','--name-only',args.base).splitlines())
    changed.update(git('ls-files','--others','--exclude-standard').splitlines())
    relevant=any(path.startswith('gateway/static/') for path in changed) or 'gateway/guide.py' in changed
    if not relevant:
        print('Guide sync: no UI or guide changes in this diff')
        return 0
    if not {'gateway/guide.py','gateway/release.py'}.issubset(changed):
        print('Guide sync: UI changes require updating gateway/guide.py and gateway/release.py')
        return 1
    current=version((ROOT/'gateway/release.py').read_text(encoding='utf-8'))
    prior=subprocess.run(['git','show',args.base+':gateway/release.py'],cwd=ROOT,capture_output=True)
    old=version(prior.stdout.decode('utf-8')) if prior.returncode==0 else None
    if not isinstance(current,str) or current==old:
        print('Guide sync: bump UI_VERSION so existing accounts receive the updated guide')
        return 1
    print('Guide sync: UI, role guidance and release version updated together')
    return 0


if __name__=='__main__':
    sys.exit(main())
