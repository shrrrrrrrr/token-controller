import json
import os

from fastapi.testclient import TestClient

from appearance_fixture import write_gallery
from test_accounts import accounts, login, PASSWORD, ORIGIN
from gateway.accounts import WebAccounts
from gateway.store import Store


def test_global_background_is_public_but_only_admin_can_change_it(accounts):
    store, app, client, admin, legacy = accounts
    images=write_gallery(store.path.parent)
    keys=store.rows('SELECT * FROM keys')
    assert client.get('/api/appearance').json()['background']['id']==images[0]['id']
    assert client.get('/api/admin/appearance').status_code==401
    headers=login(client)
    assert client.get('/api/admin/appearance').json()['images'][1]['url']=='/background-assets/'+images[1]['id']
    assert client.post('/api/admin/appearance',json={'background_id':images[1]['id'],'revision':0}).status_code==403
    assert client.post('/api/admin/appearance',headers=headers,json={'background_id':'a'*64,'revision':0}).status_code==400
    result=client.post('/api/admin/appearance',headers=headers,json={'background_id':images[1]['id'],'revision':0})
    assert result.status_code==200 and result.json()['revision']==1
    with TestClient(app,base_url=ORIGIN) as anonymous:
        document=anonymous.get('/api/appearance').json()
        assert document['background']['id']==images[1]['id']
        assert set(document)=={'background','revision'}
        asset=anonymous.get(document['background']['url'])
        assert asset.status_code==200 and asset.headers['content-type']=='image/png'
        assert 'immutable' in asset.headers['cache-control']
    member=store.create_user('appearance member','caller',[],[],-1)
    WebAccounts(store,public_origin=ORIGIN).setup(member,'appearance-member',PASSWORD)
    with TestClient(app,base_url=ORIGIN) as caller:
        member_headers=login(caller,'appearance-member')
        assert caller.get('/api/admin/appearance').status_code==403
        assert caller.post('/api/admin/appearance',headers=member_headers,json={'background_id':images[0]['id'],'revision':1}).status_code==403
    assert store.rows('SELECT * FROM keys')==keys
    assert Store(store.path,seed_demo=False).one('SELECT background_id FROM site_appearance WHERE id=1')['background_id']==images[1]['id']
    assert store.one("SELECT COUNT(*) n FROM audit WHERE action='site.appearance_change'")['n']==1


def test_stale_admin_choice_does_not_overwrite_newer_selection(accounts):
    store, app, client, admin, legacy=accounts
    images=write_gallery(store.path.parent)
    headers=login(client)
    assert client.post('/api/admin/appearance',headers=headers,json={'background_id':images[1]['id'],'revision':0}).status_code==200
    stale=client.post('/api/admin/appearance',headers=headers,json={'background_id':images[0]['id'],'revision':0})
    assert stale.status_code==409
    assert client.get('/api/appearance').json()['background']['id']==images[1]['id']


def test_thumbnail_is_a_separate_bounded_asset(accounts):
    store, app, client, admin, legacy=accounts
    images=write_gallery(store.path.parent)
    path=store.path.parent/'backgrounds'/'manifest.json'
    document=json.loads(path.read_text())
    document['images'][0]['thumbnail']={'id':images[1]['id'],'file':images[1]['file']}
    path.write_text(json.dumps(document))
    login(client)
    entry=client.get('/api/admin/appearance').json()['images'][0]
    assert entry['thumbnail_url']=='/background-assets/'+images[1]['id']
    assert client.get(entry['thumbnail_url']).status_code==200
    document['images'][0]['thumbnail']['file']='../gateway.sqlite'
    path.write_text(json.dumps(document))
    assert client.get('/api/appearance').status_code==503


def test_gallery_does_not_expose_paths_or_unregistered_or_linked_files(accounts):
    store, app, client, admin, legacy=accounts
    assert client.get('/api/appearance').json()['background']['url']=='/site-assets/campus.jpg'
    images=write_gallery(store.path.parent)
    assert client.get('/background-assets/not-a-hash').status_code==404
    assert client.get('/background-assets/'+'0'*64).status_code==404
    directory=store.path.parent/'backgrounds'
    os.link(directory/images[0]['file'],directory/'linked.png')
    assert client.get('/background-assets/'+images[0]['id']).status_code==503
    (directory/'linked.png').unlink()
    document=json.loads((directory/'manifest.json').read_text())
    document['images'][0]['file']='../gateway.sqlite'
    (directory/'manifest.json').write_text(json.dumps(document))
    assert client.get('/api/appearance').status_code==503
