import json

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.store import Store


@pytest.fixture
def notices(tmp_path):
    store=Store(tmp_path/'notices.sqlite')
    users,headers={},{}
    for role in ('admin','caller','other','operator','auditor'):
        users[role]=store.create_user(role,'caller' if role=='other' else role,['qwen3-32b'],[],-1)
        issued=store.issue_key(users[role],['qwen3-32b'],-1)
        headers[role]={'Authorization':'Bearer '+issued['key']}
    with TestClient(create_app(store,background=False)) as client:
        yield store,users,headers,client


def draft(client,headers,title='合成公告',body='Synthetic announcement body'):
    result=client.post('/api/announcements',headers=headers,json={'title':title,'body':body})
    assert result.status_code==201
    return result.json()


def publish(client,headers,row):
    result=client.post('/api/announcements/'+row['id']+'/publish',headers=headers,json={'expected_revision':row['revision']})
    assert result.status_code==200
    return result.json()


def test_only_admin_can_manage_drafts_and_published_snapshots_are_immutable(notices):
    store,_,h,c=notices
    for role in ('caller','other','operator','auditor'):
        assert c.get('/api/announcements',headers=h[role]).status_code==403
        assert c.post('/api/announcements',headers=h[role],json={'title':'Forbidden','body':'Not allowed'}).status_code==403
    row=draft(c,h['admin'])
    assert c.get('/api/notifications',headers=h['caller']).json()['unread']==0
    first=publish(c,h['admin'],row)
    assert publish(c,h['admin'],row)['replayed'] is True
    assert len(store.rows('SELECT * FROM announcement_publications'))==1
    revised=c.patch('/api/announcements/'+row['id'],headers=h['admin'],json={'title':'修改草稿','body':'private unpublished revision','expected_revision':1}).json()
    inbox=c.get('/api/notifications',headers=h['caller'])
    assert 'private unpublished revision' not in inbox.text
    assert inbox.json()['unread']==1
    assert c.patch('/api/announcements/'+row['id'],headers=h['admin'],json={'title':'Stale','body':'Stale body','expected_revision':1}).status_code==409
    second=publish(c,h['admin'],revised)
    assert first['publication_id']!=second['publication_id']
    assert c.get('/api/notifications',headers=h['caller']).json()['unread']==2
    assert 'private unpublished revision' not in json.dumps(store.rows('SELECT * FROM audit'))


def test_read_state_is_personal_and_read_all_does_not_read_future_publications(notices):
    store,u,h,c=notices
    row=draft(c,h['admin']);publication=publish(c,h['admin'],row)['publication_id']
    assert c.post('/api/notifications/'+publication+'/read',headers=h['caller']).status_code==200
    assert c.post('/api/notifications/'+publication+'/read',headers=h['caller']).status_code==200
    assert c.get('/api/notifications',headers=h['caller']).json()['unread']==0
    assert c.get('/api/notifications',headers=h['other']).json()['unread']==1
    assert len(store.rows('SELECT * FROM announcement_reads WHERE user_id=?',(u['caller'],)))==1
    assert c.post('/api/notifications/read-all',headers=h['other']).status_code==200
    publish(c,h['admin'],draft(c,h['admin'],'另一篇公告'))
    assert c.get('/api/notifications',headers=h['other']).json()['unread']==1
    assert c.post('/api/notifications/annpub_missing/read',headers=h['other']).status_code==404


def test_withdrawal_hides_member_history_and_preserves_admin_records(notices):
    store,_,h,c=notices
    row=draft(c,h['admin']);pub=publish(c,h['admin'],row)
    result=c.post('/api/announcements/'+row['id']+'/withdraw',headers=h['admin'],json={'expected_revision':1})
    assert result.status_code==200
    assert c.get('/api/notifications',headers=h['caller']).json()['items']==[]
    assert c.post('/api/notifications/'+pub['publication_id']+'/read',headers=h['caller']).status_code==404
    assert c.get('/api/announcements',headers=h['admin']).json()['items'][0]['status']=='withdrawn'
    assert store.one('SELECT COUNT(*) AS n FROM announcement_publications')['n']==1
    assert c.post('/api/announcements/'+row['id']+'/publish',headers=h['admin'],json={'expected_revision':1}).status_code==409


def test_history_pagination_and_input_limits(notices):
    _,_,h,c=notices
    for index in range(4):
        publish(c,h['admin'],draft(c,h['admin'],f'Synthetic {index}'))
    first=c.get('/api/notifications?limit=2',headers=h['caller']).json()
    second=c.get('/api/notifications?limit=2&offset=2',headers=h['caller']).json()
    assert first['total']==4 and first['unread']==4
    assert not set(row['id'] for row in first['items']) & set(row['id'] for row in second['items'])
    assert c.post('/api/announcements',headers=h['admin'],json={'title':' ','body':' '}).status_code==400
    assert c.post('/api/announcements',headers=h['admin'],json={'title':'x','body':'x'*6001}).status_code==422


def test_guide_auto_display_is_once_per_account_not_per_release_or_key(notices,monkeypatch):
    store,u,h,c=notices
    assert c.get('/api/guide',headers=h['caller']).json()['first_visit'] is True
    assert c.post('/api/guide/seen',headers=h['caller']).json()=={'first_visit':True}
    assert c.post('/api/guide/seen',headers=h['caller']).json()=={'first_visit':False}
    assert c.get('/api/guide',headers=h['other']).json()['first_visit'] is True
    another=store.issue_key(u['caller'],['qwen3-32b'],-1)
    headers={'Authorization':'Bearer '+another['key']}
    monkeypatch.setattr('gateway.guide.UI_VERSION','99.0.0')
    view=c.get('/api/guide',headers=headers).json()
    assert view['needs_attention'] is True and view['first_visit'] is False
    assert len(store.rows("SELECT * FROM audit WHERE actor=? AND action='guide.seen'",(u['caller'],)))==1
