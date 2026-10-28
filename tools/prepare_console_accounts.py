"""Archive other accounts and create synthetic testers, retaining usage/audit history.

Run on the owning host after stopping ONLY the gateway. Defaults to a read-only
plan; --apply takes a consistent SQLite backup first. Never touches model services.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gateway.store import Store,canonical,uid
from gateway.accounts import WebAccounts
from gateway.passwords import hash_password
from gateway.catalog import load_model_catalog


def prepare(state,keep_username,apply=False):
    state=Path(state).resolve()
    if state.is_symlink() or not (state/'gateway.sqlite').is_file():raise ValueError('expected existing gateway state')
    # Do not instantiate Store for read-only plans: schema migrations are writes.
    with sqlite3.connect((state/'gateway.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute("SELECT u.id FROM users u JOIN login_accounts a ON a.user_id=u.id WHERE a.username=? AND a.status='approved' AND u.role='admin' AND u.active=1",(keep_username,)).fetchone()
        if not row:raise ValueError('selected active administrator login not found')
        keeper=row[0]
        pending=db.execute("SELECT COUNT(*) FROM requests WHERE status IN ('reserved','dispatched','reconciliation')").fetchone()[0]
        if pending:raise ValueError('inflight or unresolved calls must be reconciled before account cleanup')
        count=db.execute('SELECT COUNT(*) FROM users WHERE id<>?',(keeper,)).fetchone()[0]
    receipt=state/'console-test-accounts.local.json'
    if receipt.exists():raise ValueError('account preparation already recorded; do not repeat')
    if not apply:return {'plan_only':True,'other_accounts_to_archive':count,'test_accounts_to_create':3,'kept_admin':keep_username}
    stamp=str(time.time_ns())
    backup=state/('accounts-before-console-'+stamp+'.sqlite')
    with sqlite3.connect(state/'gateway.sqlite') as src,sqlite3.connect(backup) as dst:src.backup(dst)
    os.chmod(backup,0o600)
    store=Store(state/'gateway.sqlite',seed_demo=False)
    models=[m['id'] for m in load_model_catalog(state/'model-catalog.local.json' if (state/'model-catalog.local.json').exists() else None)]
    credentials=[]
    for i in range(1,4):
        username='test-member-'+str(i);password=secrets.token_urlsafe(12)
        credentials.append({'username':username,'password':password,'user_id':uid('usr_'),'hash':hash_password(password,username)})
    with store.tx() as db:
        if db.execute("SELECT 1 FROM requests WHERE status IN ('reserved','dispatched','reconciliation')").fetchone():raise ValueError('a new request appeared; close admission first')
        db.execute('CREATE TABLE IF NOT EXISTS archived_accounts (user_id TEXT PRIMARY KEY,archived REAL NOT NULL)')
        db.execute('INSERT OR IGNORE INTO archived_accounts SELECT id,? FROM users WHERE id<>?',(time.time(),keeper))
        db.execute('UPDATE users SET active=0 WHERE id<>?',(keeper,))
        db.execute('UPDATE keys SET revoked=1 WHERE user_id<>?',(keeper,))
        db.execute('DELETE FROM web_sessions WHERE user_id<>?',(keeper,))
        db.execute('UPDATE account_tokens SET consumed=1 WHERE user_id<>?',(keeper,))
        # Remove old login identifiers so new testers can reuse the names safely.
        db.execute('DELETE FROM login_accounts WHERE user_id<>?',(keeper,))
        for c in credentials:
            db.execute("INSERT INTO users(id,name,role,models,profiles,budget) VALUES(?,?,'caller',?,'[]',-1)",(c['user_id'],'测试成员 '+str(credentials.index(c)+1)+' · '+stamp[-4:],canonical(models)))
            db.execute("INSERT INTO login_accounts VALUES(?,?,?,'approved',?,?)",(c['user_id'],c['username'],c['hash'],time.time(),time.time()))
        store.audit(db,keeper,'accounts.console_reset',keeper,dict(archived=count,test_accounts=3,historical_usage_retained=True))
    payload={'scope':'operator-only synthetic testers; no administrator password or API key',
             'backup':str(backup),'accounts':[{k:c[k] for k in ('username','password')} for c in credentials]}
    fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(payload,f,ensure_ascii=False,indent=2)
    return {'prepared':True,'credentials_file':str(receipt),'backup':str(backup),'admin_password_unchanged':True,'historical_usage_retained':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--keep-admin',required=True)
    parser.add_argument('--apply',action='store_true')
    a=parser.parse_args()
    print(json.dumps(prepare(a.state,a.keep_admin,a.apply),ensure_ascii=False))
