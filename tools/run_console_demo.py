"""Start the v0.9 console in an isolated loopback simulation, keeping the demo admin."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import secrets
import sys

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from gateway.store import Store
from gateway.accounts import WebAccounts
from prepare_console_accounts import prepare


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8767)
    args=parser.parse_args()
    state=BASE/'.state'/'console-v090'
    marker=state/'demo.local.json'
    if not marker.exists():
        if state.exists():raise SystemExit('Unrecognized demo state; files retained')
        source=BASE/'.state'/'accounts-demo'
        state.mkdir(mode=0o700)
        if (source/'demo-accounts.local.json').is_file() and (source/'gateway.sqlite').is_file():
            source_credentials=json.loads((source/'demo-accounts.local.json').read_text(encoding='utf-8'))
            admin=next(c for c in source_credentials['accounts'] if c['username']=='preview-admin')
            with sqlite3.connect(source/'gateway.sqlite') as src,sqlite3.connect(state/'gateway.sqlite') as dst:src.backup(dst)
        else:
            store=Store(state/'gateway.sqlite')
            models=['glm5.3-flash','qwen3.8-flash-next','qwen3-32b','h3']
            user=store.create_user('演示管理员','admin',models,[],-1)
            admin={'username':'preview-admin','password':secrets.token_urlsafe(24)}
            WebAccounts(store).setup(user,admin['username'],admin['password'])
        result=prepare(state,admin['username'],True)
        store=Store(state/'gateway.sqlite',seed_demo=False)
        from gateway.runtime_bridge import RuntimeBridge
        from gateway.providers import DemoText
        bridge=RuntimeBridge(store,DemoText(),simulation=True)
        with store.tx() as db:db.execute("UPDATE console_runtime SET running='glm5.3-flash' WHERE id=1")
        fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump({'scope':'loopback synthetic preview only','admin':admin,'test_accounts_file':result['credentials_file']},f,ensure_ascii=False,indent=2)
    print('LOCAL_CONSOLE_PREVIEW: http://127.0.0.1:'+str(args.port),flush=True)
    print('CREDENTIALS_FILE: '+str(marker),flush=True)
    os.execv(sys.executable,[sys.executable,'-m','gateway','serve','--web-accounts','--state',str(state),'--port',str(args.port)])


if __name__=='__main__':main()
