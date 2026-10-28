import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.store import Store,Fault,uid
from gateway.providers import DemoText
from gateway.runtime_bridge import RuntimeBridge
from gateway.passwords import hash_password,verify_password
from gateway.runtime_agent import Agent,OperationInput,create_agent,validate_manifest


@pytest.fixture
def system(tmp_path):
    store=Store(tmp_path/'gateway.sqlite')
    user=store.create_user('member','caller',['glm5.3-flash','qwen3.8-flash-next','qwen3-32b','h3'],[],-1)
    key=store.issue_key(user,['glm5.3-flash','qwen3.8-flash-next','qwen3-32b','h3'],-1,tpm=1000000)
    admin=store.create_user('administrator','admin',[],[],-1)
    ak=store.issue_key(admin,[],-1)
    with TestClient(create_app(store,background=False)) as client:
        yield store,client,{'Authorization':'Bearer '+key['key']},{'Authorization':'Bearer '+ak['key']},user,key


def test_dashboard_identity_isolation_and_four_model_states(system):
    store,c,h,ah,user,key=system
    assert c.get('/api/console').status_code==401
    d=c.get('/api/console',headers=h).json()
    assert len(d['models'])==4 and d['quota']['unlimited'] and d['quota']['metering']=='actual'
    assert {m['status_label'] for m in d['models']}=={'正在运行','暂未运行'}
    assert [m['id'] for m in d['models'] if m['running']]==['qwen3-32b']
    assert [m['id'] for m in d['models'] if m['resident']]==['glm5.3-flash']
    assert c.get('/api/model-control',headers=h).status_code==403
    public=c.get('/api/service-info').json()
    assert 'accessible' not in public['models'][0]
    assert key['key'] not in json.dumps(d)
    assert c.post('/api/model-requests',headers=h,json={'model':'qwen3-32b','purpose':'synthetic test','estimated_minutes':30,'client_ack':True,'client_request_id':'test-request'}).status_code==503


def test_actual_usage_no_reservation_and_quota_stops_next_call(system):
    store,c,h,ah,user,key=system
    with store.tx() as db:db.execute('UPDATE users SET budget=1 WHERE id=?',(user,))
    body=dict(model='qwen3-32b',messages=[dict(role='user',content='test')],max_tokens=256)
    r=c.post('/v1/chat/completions',headers={**h,'Idempotency-Key':'same-call'},json=body)
    assert r.status_code==200,r.text
    assert store.one('SELECT reserved,spent FROM users WHERE id=?',(user,))=={'reserved':0,'spent':20}
    assert store.rows('SELECT event,points FROM ledger')==[{'event':'settle','points':20}]
    assert c.post('/v1/chat/completions',headers={**h,'Idempotency-Key':'same-call'},json=body).status_code==200
    assert c.post('/v1/chat/completions',headers=h,json=body).status_code==402
    assert len(store.rows('SELECT * FROM requests'))==1


def test_no_quota_frozen_even_while_request_is_inflight(system):
    store,_,h,ah,user,key=system
    who=store.identity(key['key'])
    req,fresh=store.admit(who,'inflight',{},'chat','qwen3-32b',90000,'qwen-tp2-v1','test',runtime_required=False)
    assert fresh and req['reserved']==0
    assert store.rows('SELECT * FROM ledger')==[]
    store.mark_dispatched(req['id']);store.unknown(req['id'],'unknown')
    assert store.one('SELECT reserved FROM users WHERE id=?',(user,))['reserved']==0
    assert store.settle(req['id'],100000,60000,40000)
    assert not store.settle(req['id'],100000,60000,40000)


def test_dashboard_does_not_expose_another_members_usage(system):
    store,c,h,ah,user,key=system
    other=store.create_user('other','caller',['qwen3-32b'],[],-1)
    k=store.issue_key(other,['qwen3-32b'],-1)
    req,_=store.admit(store.identity(k['key']),'other',{},'chat','qwen3-32b',100,'x','h',runtime_required=False)
    store.settle(req['id'],22,10,12)
    assert c.get('/api/console',headers=h).json()['totals']['requests']==0
    assert c.get('/api/console/requests',headers=h).json()==[]


def test_six_character_password_still_hashed():
    encoded=hash_password('abcdef','member')
    assert 'abcdef' not in encoded and verify_password('abcdef',encoded)
    with pytest.raises(Fault):hash_password('abcde','member')


def test_simulated_switch_is_persistent_exclusive_and_idempotent(system):
    store,c,h,ah,user,key=system
    op=uid('op_');body=dict(operation_id=op,model='glm5.3-flash',action='switch')
    assert c.post('/api/model-control/operations',headers=h,json=body).status_code==403
    assert c.post('/api/model-control/operations',headers=ah,json=body).status_code==202
    assert c.post('/api/model-control/operations',headers=ah,json=body).json()['id']==op
    assert c.post('/api/model-control/operations',headers=ah,json={**body,'operation_id':uid('op_')}).status_code==409
    with store.tx() as db:db.execute('UPDATE model_switches SET created=? WHERE id=?',(time.time()-10,op))
    # The worker completes outside the GET handler; no public GET runs lifecycle commands.
    for _ in range(25):
        if c.get('/api/console',headers=h).json()['operation'] is None:break
        time.sleep(.1)
    assert c.get('/api/console',headers=h).json()['models'][0]['running']


def test_unknown_remote_post_not_replayed(tmp_path):
    class Client:
        calls=[]
        async def request(self,method,path,body=None):
            self.calls.append(method)
            raise Fault('runtime_agent_unreachable',503)
    class Provider:
        rotating=False
    async def run():
        store=Store(tmp_path/'test.sqlite');p=Provider();client=Client();bridge=RuntimeBridge(store,p,client=client)
        await bridge.submit('glm5.3-flash','switch',uid('op_'),'test')
        await bridge.tick();await bridge.tick()
        assert client.calls.count('POST')==1
        assert bridge.active()['phase']=='recovery_required' and p.rotating
    asyncio.run(run())


def test_actual_mode_does_not_change_historical_reserved_settlement(tmp_path):
    store=Store(tmp_path/'legacy.sqlite');u=store.create_user('legacy','caller',['qwen3-32b'],[],1000);k=store.issue_key(u,['qwen3-32b'],1000)
    req,_=store.reserve(store.identity(k['key']),'legacy',{},'chat','qwen3-32b',100,'x','y',runtime_required=False)
    assert store.one('SELECT reserved FROM users')['reserved']==100
    assert store.settle(req['id'],20,10,10)
    assert [r['event'] for r in store.rows('SELECT event FROM ledger ORDER BY id')]==['reserve','settle','release']


def test_unconfigured_cloud_control_refuses_before_any_mutation(tmp_path):
    async def run():
        store=Store(tmp_path/'empty.sqlite');bridge=RuntimeBridge(store,DemoText())
        with pytest.raises(Fault) as caught:await bridge.submit('glm5.3-flash','switch',uid('op_'),'x')
        assert caught.value.code=='runtime_agent_not_connected'
        assert not store.rows('SELECT * FROM model_switches')
    asyncio.run(run())
