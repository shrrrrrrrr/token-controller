#!/usr/bin/env bash
# Campus server, normal operator account. Read-only; no inference or model restart.
set -euo pipefail
printf '%s\n' '[Time and resources]'
date -Is
free -h
df -h -- "$HOME"
nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu --format=csv
nvidia-smi topo -m
printf '\n%s\n' '[Container identity and loopback listeners; no commands or environment]'
sudo docker ps -a --format '{{.Names}} {{.Image}} {{.Status}} {{.Ports}}'
sudo ss -lntp '( sport = :18080 or sport = :28080 or sport = :18765 )'
printf '\n%s\n' '[Network helper metadata only]'
python3 - <<'PY'
from pathlib import Path
import hashlib,json,stat
p=Path.home()/'buaa_gateway_login_checked.py'
if not p.exists():
    print(json.dumps({'exists':False}))
else:
    s=p.lstat()
    print(json.dumps({'exists':True,'regular':stat.S_ISREG(s.st_mode),'symlink':stat.S_ISLNK(s.st_mode),
        'uid':s.st_uid,'mode':oct(stat.S_IMODE(s.st_mode)),'bytes':s.st_size,
        'sha256':hashlib.sha256(p.read_bytes()).hexdigest() if stat.S_ISREG(s.st_mode) and s.st_size<100000 else None}))
PY
printf '\n%s\n' '[Candidate managed services; names and state only]'
systemctl list-units --all --type=service --no-pager --plain | awk 'tolower($1) ~ /tunnel|campus|netguard|token-controller/ { print $1,$3,$4 }'
printf '%s\n' 'SERVING_PREFLIGHT_READ_ONLY_FINISHED'
