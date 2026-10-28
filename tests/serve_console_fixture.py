"""Temporary synthetic console fixture; no external model or operator configuration."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import uvicorn

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gateway.store import Store
from gateway.accounts import WebAccounts
from gateway.app import create_app

if __name__=='__main__':
    parent=Path(__file__).resolve().parents[1]/'.state'/'browser-fixtures'
    parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as directory:
        store=Store(Path(directory)/'gateway.sqlite')
        models=['glm5.3-flash','qwen3.8-flash-next','qwen3-32b','h3']
        member_id=None
        for username,role in [('fixture-admin','admin'),('fixture-member','caller')]:
            user=store.create_user(username,role,models,[],-1)
            WebAccounts(store).setup(user,username,'Synthetic fixture password 2026')
            if role=='caller':member_id=user
        if '--appearance-fixture' in sys.argv:
            from appearance_fixture import write_gallery
            write_gallery(store.path.parent)
        app=create_app(store,web_accounts=True,background=False)
        if '--queue-fixture' in sys.argv:
            queue=app.state.work_queue
            for i in range(4):
                owner=store.create_user('Other queue member '+str(i),'caller',models,[],-1)
                key=store.issue_key(owner,models,-1)['key']
                queue.create(store.identity(key),{'model':'qwen3-32b','kind':'harness','label':'Private other task'},'fixture-'+str(i))
            key=store.issue_key(member_id,models,-1)['key']
            queue.create(store.identity(key),{'model':'qwen3-32b','kind':'simcourt','label':'我的合成庭审'},'fixture-own')
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
        print(json.dumps({'port':sock.getsockname()[1]}),flush=True)
        uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False)).run(sockets=[sock])
