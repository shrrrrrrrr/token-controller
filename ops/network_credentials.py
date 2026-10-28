"""Operator-only, interactive, no-overwrite campus credential enrollment on Linux."""
import getpass
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


def main():
    if os.geteuid()!=0 or not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError('run as root in an interactive server terminal')
    directory=Path('/etc/token-controller-network')
    for path in (directory,*directory.parents):
        entry=path.lstat()
        if not stat.S_ISDIR(entry.st_mode) or entry.st_uid!=0 or entry.st_mode&0o022:
            raise ValueError('protected configuration directory required')
    destination=directory/'campus-login.json'
    if os.path.lexists(destination):
        raise ValueError('credential file already exists; not overwritten')
    print('Use the CAMPUS NETWORK account, not Linux, SSH or model credentials',flush=True)
    username=input('Campus account: ').strip()
    password=getpass.getpass('Campus password (hidden): ')
    confirmation=getpass.getpass('Repeat campus password (hidden): ')
    if (not username or not password or password!=confirmation or
            any('\n' in value or '\r' in value or '\0' in value for value in (username,password))):
        raise ValueError('empty, invalid or mismatched input; nothing saved')
    raw=json.dumps({'username':username,'password':password},ensure_ascii=False).encode('utf-8')
    if len(raw)>4096:
        raise ValueError('credential input too long; nothing saved')
    staged=None
    try:
        with tempfile.NamedTemporaryFile(prefix='.credential-',dir=directory,delete=False) as stream:
            staged=Path(stream.name)
            os.fchmod(stream.fileno(),0o600)
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.link(staged,destination,follow_symlinks=False)
    finally:
        if staged is not None:
            staged.unlink()
    print('CAMPUS_CREDENTIAL_SAVED: root-only; no password printed; not yet authenticated',flush=True)


if __name__=='__main__':
    try:
        main()
    except (OSError,ValueError,EOFError,KeyboardInterrupt):
        print('STOP: credential enrollment incomplete; no overwrite; do not post credentials',file=sys.stderr)
        raise SystemExit(1) from None
