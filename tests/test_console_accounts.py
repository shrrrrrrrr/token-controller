import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.accounts import WebAccounts
from gateway.passwords import verify_password
from gateway.store import Store


def tool():
    spec=importlib.util.spec_from_file_location('prepare_console_accounts',Path(__file__).resolve().parents[1]/'tools'/'prepare_console_accounts.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_cleanup_preserves_admin_and_usage_but_removes_other_logins_and_keys(tmp_path):
    store=Store(tmp_path/'gateway.sqlite')
    admin=store.create_user('admin','admin',['qwen3-32b'],[],-1);web=WebAccounts(store)
    web.setup(admin,'keeper','Synthetic administrator passphrase')
    old=store.create_user('old','caller',['qwen3-32b'],[],-1);web.setup(old,'old-member','Synthetic old member passphrase')
    ak=store.issue_key(admin,[],-1);oldkey=store.issue_key(old,['qwen3-32b'],-1)
    request,_=store.admit(store.identity(oldkey['key']),'history',{},'chat','qwen3-32b',100,'x','h',runtime_required=False)
    store.settle(request['id'],20,12,8)
    admin_before=store.one('SELECT * FROM login_accounts WHERE user_id=?',(admin,))
    history_before=store.rows('SELECT * FROM requests'),store.rows('SELECT * FROM ledger')
    module=tool()
    assert module.prepare(tmp_path,'keeper')['plan_only']
    assert store.one('SELECT active FROM users WHERE id=?',(old,))['active']==1
    result=module.prepare(tmp_path,'keeper',True)
    assert result['admin_password_unchanged'] and Path(result['backup']).exists()
    assert store.one('SELECT * FROM login_accounts WHERE user_id=?',(admin,))==admin_before
    assert store.one('SELECT revoked FROM keys WHERE id=?',(ak['id'],))['revoked']==0
    assert store.one('SELECT revoked FROM keys WHERE id=?',(oldkey['id'],))['revoked']==1
    assert not store.one('SELECT * FROM login_accounts WHERE user_id=?',(old,))
    assert (store.rows('SELECT * FROM requests'),store.rows('SELECT * FROM ledger'))==history_before
    credentials=json.loads(Path(result['credentials_file']).read_text(encoding='utf-8'))['accounts']
    assert len(credentials)==3
    for c in credentials:
        account=store.one('SELECT * FROM login_accounts WHERE username=?',(c['username'],))
        assert verify_password(c['password'],account['password_hash'])
        user=store.one('SELECT * FROM users WHERE id=?',(account['user_id'],))
        assert user['role']=='caller' and user['budget']==-1 and user['active']==1
        assert len(json.loads(user['models']))==4
    with TestClient(create_app(store,web_accounts=False,background=False)) as client:
        rows=client.get('/api/users',headers={'Authorization':'Bearer '+ak['key']}).json()
        assert len(rows)==4 and not any(row['id']==old for row in rows)
