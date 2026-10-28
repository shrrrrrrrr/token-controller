import time

from fastapi.testclient import TestClient
import pytest

from gateway.app import create_app
from gateway.store import Store


@pytest.fixture
def scheduling(tmp_path):
    store=Store(tmp_path/'queue.sqlite')
    headers={}
    users={}
    for role in ['admin','operator','auditor','caller','other']:
        users[role]=store.create_user(role,'caller' if role=='other' else role,['qwen3-32b'],[],10000)
        credential=store.issue_key(users[role],['qwen3-32b'],10000)
        headers[role]={'Authorization':'Bearer '+credential['key']}
    with TestClient(create_app(store,background=False,model_requests_enabled=True)) as client:
        yield store,users,headers,client


def application(client,headers,role='caller',request='request-001',**extra):
    return client.post('/api/model-requests',headers=headers[role],json=dict(model='qwen3-32b',purpose='synthetic private purpose '+role,preferred_start=None,estimated_minutes=30,client_ack=True,client_request_id=request,**extra))


def approval(row,**extra):
    return dict(status='approved',priority=10,scheduled_start=time.time()+3600,estimated_minutes=30,reason='Verified synthetic availability',expected_revision=row['revision'])|extra


def test_application_auth_scope_idempotency_and_no_execution(scheduling):
    s,u,h,c=scheduling
    assert c.get('/api/model-requests').status_code==401
    assert application(c,h,'auditor').status_code==403
    body=dict(model='h3',purpose='synthetic purpose',client_ack=True,client_request_id='unauthorized-001')
    assert c.post('/api/model-requests',headers=h['caller'],json=body).status_code==403
    body['model']='qwen3-32b';body['client_ack']=False
    assert c.post('/api/model-requests',headers=h['caller'],json=body).status_code==400
    first=application(c,h)
    assert first.status_code==202
    notice_count=len(s.rows('SELECT id FROM notifications'))
    assert application(c,h).json()['id']==first.json()['id']
    assert len(s.rows('SELECT id FROM notifications'))==notice_count
    assert not s.rows('SELECT * FROM operations')
    assert not s.rows('SELECT * FROM requests')
    assert s.one('SELECT spent,reserved FROM users WHERE id=?',(u['caller'],))=={'spent':0,'reserved':0}


def test_shared_board_redacts_other_members_and_cancellation_is_owned(scheduling):
    s,u,h,c=scheduling
    row=application(c,h).json()
    view=c.get('/api/model-requests',headers=h['other']).json()
    item=view['queue'][0]
    assert 'purpose' not in item and 'user_id' not in item and 'id' not in item and 'reason' not in item
    assert view['manage']==[] and view['mine']==[]
    assert c.post('/api/model-requests/'+row['id']+'/cancel',headers=h['other']).status_code==404
    assert c.post('/api/model-requests/'+row['id']+'/cancel',headers=h['admin']).status_code==404
    assert c.post('/api/model-requests/'+row['id']+'/cancel',headers=h['caller']).status_code==200
    notices=len(s.rows('SELECT id FROM notifications'))
    assert c.post('/api/model-requests/'+row['id']+'/cancel',headers=h['caller']).status_code==200
    assert len(s.rows('SELECT id FROM notifications'))==notices


def test_admin_time_order_conflicts_revision_and_terminal_states(scheduling):
    s,u,h,c=scheduling
    first=application(c,h).json()
    path='/api/model-requests/'+first['id']
    update=approval(first)
    for role in ['caller','other','operator','auditor']:
        assert c.patch(path,headers=h[role],json=update).status_code==403
    assert c.patch(path,headers=h['admin'],json=update).status_code==200
    assert c.patch(path,headers=h['admin'],json=update).status_code==409
    second=application(c,h,'other').json()
    assert c.patch('/api/model-requests/'+second['id'],headers=h['admin'],json=approval(second,scheduled_start=update['scheduled_start']+60)).status_code==409
    newer=c.patch(path,headers=h['admin'],json=approval(first,expected_revision=2,priority=1,scheduled_start=update['scheduled_start'])).json()
    assert newer['revision']==3 and newer['priority']==1
    assert c.patch(path,headers=h['admin'],json=approval(newer,status='closed')).status_code==200
    assert c.patch(path,headers=h['admin'],json=approval(newer,expected_revision=4)).status_code==409
    assert not s.rows('SELECT * FROM operations')


def test_notifications_are_personal_and_cannot_be_marked_for_other_users(scheduling):
    s,u,h,c=scheduling
    row=application(c,h).json()
    notice=c.get('/api/notifications',headers=h['caller']).json()
    assert notice['unread']==1
    notice_id=notice['items'][0]['id']
    for role in ['other','admin','auditor']:
        assert c.post('/api/notifications/'+notice_id+'/read',headers=h[role]).status_code==404
    assert c.post('/api/notifications/read-all',headers=h['other']).status_code==200
    assert c.get('/api/notifications',headers=h['caller']).json()['unread']==1
    assert c.post('/api/notifications/'+notice_id+'/read',headers=h['caller']).status_code==200
    assert c.get('/api/notifications',headers=h['caller']).json()['unread']==0
    assert c.get('/api/notifications?limit=10000',headers=h['caller']).status_code==422


def test_pending_limit_and_additive_upgrade_preserve_accounts(scheduling):
    s,u,h,c=scheduling
    for i in range(3):
        assert application(c,h,request='request-'+str(i)).status_code==202
    assert application(c,h,request='request-excess').status_code==429
    s2=Store(s.path)
    assert s2.one('SELECT name FROM users WHERE id=?',(u['caller'],))['name']=='caller'
    assert len(s2.rows('SELECT id FROM model_requests'))==3
    with s2.tx() as db:
        db.execute('DROP TABLE notifications')
        db.execute('DROP TABLE model_requests')
    s3=Store(s.path)
    assert s3.one('SELECT name FROM users WHERE id=?',(u['caller'],))['name']=='caller'
    assert not s3.rows('SELECT id FROM model_requests')


def test_private_approval_reason_does_not_leak_to_shared_audit(scheduling):
    s,u,h,c=scheduling
    row=application(c,h).json()
    private_reason='Private synthetic scheduling detail'
    response=c.patch('/api/model-requests/'+row['id'],headers=h['admin'],json=approval(row,reason=private_reason))
    assert response.status_code==200
    assert private_reason in c.get('/api/notifications',headers=h['caller']).text
    assert private_reason not in c.get('/api/audit',headers=h['operator']).text
    assert c.post('/api/model-requests',headers=h['caller'],json=dict(model='qwen3-32b',purpose='synthetic request',client_ack=True,client_request_id='extra-fields',priority=0)).status_code==422
