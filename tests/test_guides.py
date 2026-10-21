import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.guide import ROLE_NAMES
from gateway.release import UI_VERSION
from gateway.store import Store


@pytest.fixture
def guides(tmp_path):
    store=Store(tmp_path/'guides.sqlite')
    users={}
    headers={}
    for role in ROLE_NAMES:
        users[role]=store.create_user(role,role,['qwen3-32b','h3'],[],10000)
        key=store.issue_key(users[role],['qwen3-32b'],10000)
        headers[role]={'Authorization':'Bearer '+key['key']}
    with TestClient(create_app(store,background=False)) as client:
        yield store,users,headers,client


@pytest.mark.parametrize('role',list(ROLE_NAMES))
def test_guides_follow_actual_role_and_never_grant_access(guides,role):
    store,users,headers,client=guides
    assert client.get('/api/guide').status_code==401
    result=client.get('/api/guide',headers=headers[role])
    assert result.status_code==200
    guide=result.json()
    assert guide['role']==role
    assert guide['version']==UI_VERSION
    assert guide['needs_attention'] is True
    assert guide['models']==([] if role=='auditor' else ['qwen3-32b'])
    assert all(step['target'] in guide['allowed_pages'] for step in guide['steps'])
    assert headers[role]['Authorization'][7:] not in result.text
    if role=='caller':
        assert not {'users','runtime','assets'} & set(guide['allowed_pages'])
    if role=='auditor':
        assert 'play' not in guide['allowed_pages']
    assert client.post('/api/guide/ack',headers=headers[role],json={'version':UI_VERSION}).status_code==200
    assert client.get('/api/guide',headers=headers[role]).json()['needs_attention'] is False
    if role!='admin':
        assert client.post('/api/users',headers=headers[role],json={'name':'not-authorized','role':'caller','models':[],'budget':10}).status_code==403


def test_ack_is_account_scoped_idempotent_and_role_sensitive(guides):
    store,users,headers,client=guides
    body={'version':UI_VERSION}
    for _ in range(2):
        assert client.post('/api/guide/ack',headers=headers['caller'],json=body).status_code==200
    assert len(store.rows("SELECT * FROM audit WHERE actor=? AND action='guide.ack'",(users['caller'],)))==1
    another=store.issue_key(users['caller'],[],0)
    assert client.get('/api/guide',headers={'Authorization':'Bearer '+another['key']}).json()['needs_attention'] is False
    assert client.get('/api/guide',headers=headers['operator']).json()['needs_attention'] is True
    with store.tx() as db:
        db.execute("UPDATE users SET role='operator' WHERE id=?",(users['caller'],))
    guide=client.get('/api/guide',headers=headers['caller']).json()
    assert guide['role']=='operator'
    assert guide['needs_attention'] is True


def test_guide_releases_invalidate_old_confirmation(guides,monkeypatch):
    store,users,headers,client=guides
    assert client.post('/api/guide/ack',headers=headers['caller'],json={'version':UI_VERSION}).status_code==200
    monkeypatch.setattr('gateway.guide.UI_VERSION','99.0.0')
    monkeypatch.setattr('gateway.app.UI_VERSION','99.0.0')
    assert client.get('/api/guide',headers=headers['caller']).json()['needs_attention'] is True
    assert client.post('/api/guide/ack',headers=headers['caller'],json={'version':UI_VERSION}).status_code==409
    assert client.post('/api/guide/ack',headers=headers['caller'],json={'version':'99.0.0','user_id':users['admin']}).status_code==422
    assert client.post('/api/guide/ack',headers=headers['caller'],json={'version':'99.0.0'}).status_code==200
