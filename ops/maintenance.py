#!/usr/bin/python3 -I
"""Root-owned, fixed maintenance vocabulary for a restricted SSH account."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


def main():
    if os.geteuid()!=0 or len(sys.argv)!=2 or sys.argv[1] not in ('status','network-check','tunnel-reconnect'):
        raise ValueError('not allowed')
    path=Path('/etc/token-controller-maintenance/config.json')
    for p in [path,*path.parents]:
        entry=p.lstat()
        if entry.st_uid!=0 or entry.st_mode&0o022 or stat.S_ISLNK(entry.st_mode):raise ValueError('unsafe config path')
    if path.stat().st_size>2048:raise ValueError('invalid config')
    config=json.loads(path.read_text())
    if set(config)!={'tunnel_unit'} or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}\.service',config['tunnel_unit']):
        raise ValueError('invalid config')
    units=['token-controller-network.service',config['tunnel_unit']]
    if sys.argv[1]=='network-check':
        subprocess.run(['/usr/bin/systemctl','start','token-controller-network.service'],stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=115,check=True)
    if sys.argv[1]=='tunnel-reconnect':
        subprocess.run(['/usr/bin/systemctl','restart',config['tunnel_unit']],stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=True)
    result=subprocess.run(['/usr/bin/systemctl','show',*units,'--property=Id,ActiveState,SubState,Result,ExecMainStatus'],
                          stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=8,check=True)
    print(result.stdout[:8192],end='')


if __name__=='__main__':
    try:main()
    except Exception:
        print('MAINTENANCE_REFUSED: no credential or raw command output printed')
        sys.exit(1)
