"""Start an isolated loopback account demo. No real model or cloud connection."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from gateway.accounts import WebAccounts
from gateway.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    state = BASE / '.state' / 'accounts-demo'
    marker = state / 'demo-accounts.local.json'
    if state.exists() and not marker.is_file():
        raise SystemExit('Expected isolated demo marker; existing files were preserved.')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not marker.exists():
        store = Store(state / 'gateway.sqlite', seed_demo=True)
        web = WebAccounts(store)
        credentials = {'scope': 'isolated loopback demo; synthetic responses only', 'accounts': []}
        for username, name, role in [('preview-admin', '演示管理员', 'admin'), ('preview-member', '演示成员', 'caller')]:
            user_id = store.create_user(name, role, ['qwen3-32b', 'h3'], [], -1)
            password = secrets.token_urlsafe(24)
            web.setup(user_id, username, password)
            credentials['accounts'].append({'username': username, 'password': password})
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(credentials, stream, ensure_ascii=False, indent=2)
    print('DEMO_CREDENTIALS_FILE: '+str(marker), flush=True)
    print('LOCAL_ACCOUNTS_DEMO: http://127.0.0.1:'+str(args.port), flush=True)
    os.execv(sys.executable, [sys.executable, '-m', 'gateway', 'serve', '--web-accounts', '--state', str(state), '--port', str(args.port)])


if __name__ == '__main__':
    main()
