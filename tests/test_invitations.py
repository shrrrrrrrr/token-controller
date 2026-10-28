import json
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from test_accounts import accounts, login, context, PASSWORD, ORIGIN
from gateway.accounts import WebAccounts


def issue(client):
    response=client.post('/api/admin/invitations',headers=login(client),json={})
    assert response.status_code==201
    return response.json()


def register(client, code=None, username='invited-member'):
    body={'username':username,'password':PASSWORD,'display_name':'Invited member'}
    if code is not None:body['invitation_code']=code
    return client.post('/api/auth/register',headers=context(client),json=body)


def test_invitation_approves_one_default_member_and_never_stores_the_code(accounts):
    store,app,client,admin,legacy=accounts
    invite=issue(client)
    row=store.one('SELECT * FROM invitation_codes WHERE id=?',(invite['id'],))
    assert row['expires']-row['created']==86400
    assert invite['code'] not in str(store.rows('SELECT * FROM invitation_codes'))
    assert invite['code'] not in client.get('/api/admin/invitations').text
    assert invite['code'] not in str(store.rows('SELECT * FROM audit'))
    with TestClient(app,base_url=ORIGIN) as member:
        response=register(member,invite['code'])
        assert response.status_code==202 and response.json()['status']=='approved'
        login(member,'invited-member')
        account=member.get('/api/account').json()
        assert account['status']=='approved'
        user=account['user'];assert user['role']=='caller' and user['active']==1 and user['budget']==-1
        assert json.loads(user['models'])==['research-text'] and json.loads(user['profiles'])==[]
    with TestClient(app,base_url=ORIGIN) as replay:
        assert register(replay,invite['code'],'second-invited').status_code==400
    assert not store.one("SELECT 1 FROM login_accounts WHERE username='second-invited'")


def test_empty_invite_stays_pending_and_expired_invite_does_not_create_an_account(accounts):
    store,app,client,admin,legacy=accounts
    invite=issue(client)
    with store.tx() as db:db.execute('UPDATE invitation_codes SET expires=?',(time.time()-1,))
    with TestClient(app,base_url=ORIGIN) as expired:
        assert register(expired,invite['code']).status_code==400
    assert not store.one("SELECT 1 FROM login_accounts WHERE username='invited-member'")
    with TestClient(app,base_url=ORIGIN) as pending:
        assert register(pending,'  ','pending-no-invite').json()['status']=='pending'
        login(pending,'pending-no-invite')
        assert pending.get('/api/account').json()['user']['active']==0


def test_only_admin_can_create_or_list_and_csrf_is_required(accounts):
    store,app,client,admin,legacy=accounts
    assert client.get('/api/admin/invitations').status_code==401
    login(client)
    assert client.post('/api/admin/invitations',json={}).status_code==403
    user=store.create_user('ordinary inviter','caller',[],[],-1)
    WebAccounts(store,public_origin=ORIGIN).setup(user,'ordinary-inviter',PASSWORD)
    with TestClient(app,base_url=ORIGIN) as member:
        headers=login(member,'ordinary-inviter')
        assert member.get('/api/admin/invitations').status_code==403
        assert member.post('/api/admin/invitations',headers=headers,json={}).status_code==403


def test_registration_conflict_does_not_consume_the_invite(accounts):
    store,app,client,admin,legacy=accounts
    invite=issue(client)
    assert register(client,invite['code'],'fixture-admin').status_code==409
    assert store.one('SELECT used_at FROM invitation_codes WHERE id=?',(invite['id'],))['used_at'] is None
    with TestClient(app,base_url=ORIGIN) as member:
        assert register(member,invite['code']).json()['status']=='approved'


def test_two_simultaneous_registrations_cannot_reuse_one_code(accounts):
    store,app,client,admin,legacy=accounts
    invite=issue(client)
    def attempt(index):
        with TestClient(app,base_url=ORIGIN) as member:
            return register(member,invite['code'],'racing-member-'+str(index)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[1,2]))
    assert sorted(results)==[202,400]
    assert store.one("SELECT COUNT(*) n FROM login_accounts WHERE username LIKE 'racing-member-%'")['n']==1
