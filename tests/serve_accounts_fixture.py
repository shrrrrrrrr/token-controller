"""Loopback browser fixture. All identities and responses are synthetic."""
import json
from pathlib import Path
import socket
import sys
import tempfile

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway.accounts import WebAccounts
from gateway.app import create_app
from gateway.store import Store


if __name__ == '__main__':
    parent = Path(__file__).resolve().parents[1] / '.state' / 'browser-fixtures'
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as directory:
        store = Store(Path(directory) / 'gateway.sqlite', seed_demo=True)
        user = store.create_user('合成管理员', 'admin', ['qwen3-32b', 'h3'], [], -1)
        WebAccounts(store).setup(user, 'fixture-admin', 'A synthetic browser passphrase 2026!')
        app = create_app(store, background=False, web_accounts=True)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        sock.listen(128)
        print(json.dumps({'port': sock.getsockname()[1]}), flush=True)
        uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False)).run(sockets=[sock])
