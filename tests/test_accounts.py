import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.accounts import WebAccounts
from gateway.app import create_app
from gateway.passwords import hash_password, verify_password
from gateway.store import Store
from gateway.upstream import CloudText

ORIGIN = 'https://models.example.test'
PASSWORD = 'A synthetic fixture passphrase 2026!'
NEXT_PASSWORD = 'A different synthetic passphrase 2027!'
MODEL = 'research-text'


def context(client):
    result = client.get('/api/auth/context')
    assert result.status_code == 200
    return {'Origin': ORIGIN, 'X-CSRF-Token': result.json()['csrf_token']}


def login(client, username='fixture-admin', password=PASSWORD):
    result = client.post('/api/auth/login', headers=context(client), json={'username': username, 'password': password})
    assert result.status_code == 200
    return {'Origin': ORIGIN, 'X-CSRF-Token': result.json()['csrf_token']}


@pytest.fixture
def accounts(tmp_path):
    store = Store(tmp_path/'accounts.sqlite', seed_demo=False)
    admin = store.create_user('fixture admin', 'admin', [MODEL], [], -1)
    legacy = store.issue_key(admin, [MODEL], 10000)
    WebAccounts(store, public_origin=ORIGIN).setup(admin, 'fixture-admin', PASSWORD)
    catalog = tmp_path/'catalog.json'
    catalog.write_text(json.dumps({'models': [{'id': MODEL, 'display_name': '研究模型', 'kind': 'text', 'deployment_status': 'offline'}]}))
    async def upstream(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': MODEL}]})
        return httpx.Response(200, json={'model': MODEL, 'choices': [{'message': {'role': 'assistant', 'content': 'synthetic'}, 'finish_reason': 'stop'}],
                                        'usage': {'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10}})
    provider = CloudText({'alias': MODEL, 'served_model': MODEL, 'base_url': 'http://127.0.0.1:18080/v1'}, tmp_path, transport=httpx.MockTransport(upstream))
    app = create_app(store, provider, cloud_api=True, public_origin=ORIGIN, model_catalog_path=catalog, web_accounts=True)
    with TestClient(app, base_url=ORIGIN) as client:
        yield store, app, client, admin, legacy


def register_and_approve(accounts, username='fixture-member'):
    store, app, admin_client, _, _ = accounts
    admin_headers = login(admin_client)
    member = TestClient(app, base_url=ORIGIN)
    response = member.post('/api/auth/register', headers=context(member), json={'username': username, 'password': PASSWORD, 'display_name': username})
    assert response.status_code == 202
    user = store.one('SELECT user_id FROM login_accounts WHERE username=?', (username,))['user_id']
    response = admin_client.post('/api/admin/registrations/'+user+'/approve', headers=admin_headers, json={'models': [MODEL]})
    assert response.status_code == 200
    return member, login(member, username), user, admin_headers


def test_password_hashes_have_unique_salts_and_bounded_format():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second and PASSWORD not in first
    assert verify_password(PASSWORD, first)
    assert not verify_password('wrong password', first)
    assert not verify_password(PASSWORD, 'scrypt$99999999$8$1$bad$bad')


def test_cookie_session_csrf_and_api_credentials_are_separate(accounts):
    _, _, client, _, legacy = accounts
    before = client.get('/api/auth/context')
    cookie = before.headers['set-cookie']
    assert '__Host-tc-session=' in cookie and 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=lax' in cookie
    assert 'Domain=' not in cookie
    assert client.post('/api/auth/login', json={'username': 'fixture-admin', 'password': PASSWORD}).status_code == 403
    headers = login(client)
    assert client.get('/api/me').json()['auth_mode'] == 'password_session'
    assert client.get('/v1/models').status_code == 401
    other = TestClient(client.app, base_url=ORIGIN)
    assert other.get('/api/me', headers={'Authorization': 'Bearer '+legacy['key']}).status_code == 401
    assert client.post('/api/users', json={'name': 'csrf', 'role': 'caller', 'models': []}).status_code == 403
    assert client.post('/api/users', headers={**headers, 'Origin': 'https://sibling.example.test'}, json={}).status_code == 403
    assert client.post('/api/auth/logout', headers=headers).status_code == 200
    assert client.get('/api/me').status_code == 401


def test_registration_is_pending_and_cannot_self_grant(accounts):
    store, app, admin_client, _, _ = accounts
    member = TestClient(app, base_url=ORIGIN)
    headers = context(member)
    assert member.post('/api/auth/register', headers=headers, json={'username': 'new-member', 'password': PASSWORD, 'display_name': '新成员', 'role': 'admin'}).status_code == 422
    result = member.post('/api/auth/register', headers=headers, json={'username': 'new-member', 'password': PASSWORD, 'display_name': '新成员'})
    assert result.status_code == 202
    headers = login(member, 'new-member')
    assert member.get('/api/account').json()['status'] == 'pending'
    assert member.post('/api/self/keys', headers=headers, json={}).status_code == 403
    assert member.get('/api/model-catalog').status_code == 403
    assert store.one("SELECT models,budget,active FROM users WHERE role='caller'") == {'models': '[]', 'budget': -1, 'active': 0}
    admin_headers = login(admin_client)
    pending = admin_client.get('/api/admin/registrations').json()
    assert len(pending) == 1 and 'password_hash' not in json.dumps(pending)
    assert admin_client.post('/api/admin/registrations/'+pending[0]['id']+'/approve', headers=admin_headers, json={'models': [MODEL]}).status_code == 200
    assert member.get('/api/account').json()['status'] == 'approved'
    assert member.get('/api/model-catalog').status_code == 200


def test_member_owns_keys_and_inference_uses_the_api_key(accounts):
    store, app, admin_client, _, _ = accounts
    member, headers, user, admin_headers = register_and_approve(accounts)
    assert admin_client.post('/api/upstream/credential', headers=admin_headers, json={'value': 'fixture_upstream_credential_only'}).status_code == 200
    response = member.post('/api/self/keys', headers=headers, json={'name': '自己的客户端'})
    assert response.status_code == 201
    issued = response.json()
    listing = member.get('/api/self/keys')
    assert issued['key'] not in listing.text and 'hash' not in listing.text
    assert listing.json()[0]['name'] == '自己的客户端'
    assert member.get('/api/keys').status_code == 403
    assert member.post('/api/self/keys', headers=headers, json={'models': ['forbidden']}).status_code == 403
    another, another_headers, _, _ = register_and_approve(accounts, 'another-member')
    assert another.patch('/api/self/keys/'+issued['id'], headers=another_headers, json={}).status_code == 404
    assert another.delete('/api/self/keys/'+issued['id'], headers=another_headers).status_code == 404
    api_headers = {'Authorization': 'Bearer '+issued['key'], 'Idempotency-Key': 'session-separated'}
    assert another.get('/v1/models', headers=api_headers).status_code == 200
    result = another.post('/v1/chat/completions', headers=api_headers, json={'model': MODEL, 'messages': [{'role': 'user', 'content': 'synthetic'}], 'max_tokens': 32})
    assert result.status_code == 200
    assert store.one('SELECT spent FROM users WHERE id=?', (user,))['spent'] == 10
    assert member.get('/api/usage?limit=1').json()['totals'] == {'input_tokens': 7, 'output_tokens': 3}
    assert member.patch('/api/self/keys/'+issued['id'], headers=headers, json={'name': '重命名', 'rpm': 10}).status_code == 200
    assert member.delete('/api/self/keys/'+issued['id'], headers=headers).status_code == 200
    assert another.get('/v1/models', headers=api_headers).status_code == 401


def test_password_change_rotates_sessions_and_username(accounts):
    store, app, _, _, _ = accounts
    member, headers, user, _ = register_and_approve(accounts)
    old_cookie = member.cookies.get('__Host-tc-session')
    another = TestClient(app, base_url=ORIGIN)
    another.cookies.set('__Host-tc-session', old_cookie, domain='models.example.test', path='/')
    result = member.post('/api/account/profile', headers=headers, json={'current_password': PASSWORD, 'username': 'changed-member', 'new_password': NEXT_PASSWORD})
    assert result.status_code == 200 and member.cookies.get('__Host-tc-session') != old_cookie
    assert another.get('/api/me').status_code == 401
    assert member.get('/api/account').json()['username'] == 'changed-member'
    assert PASSWORD not in json.dumps(store.rows('SELECT * FROM audit'))
    assert verify_password(NEXT_PASSWORD, store.one('SELECT password_hash FROM login_accounts WHERE user_id=?', (user,))['password_hash'])


def test_legacy_member_claim_preserves_id_keys_and_usage(accounts):
    store, app, client, _, _ = accounts
    legacy_user = store.create_user('legacy member', 'caller', [MODEL], [], 5000)
    old_key = store.issue_key(legacy_user, [MODEL], 5000)
    with store.tx() as db:
        db.execute('UPDATE users SET spent=123 WHERE id=?', (legacy_user,))
    headers = login(client)
    result = client.post('/api/admin/accounts/'+legacy_user+'/token', headers=headers, json={'purpose': 'enroll', 'current_password': PASSWORD})
    assert result.status_code == 200
    code = result.json()['code']
    member = TestClient(app, base_url=ORIGIN)
    body = {'code': code, 'username': 'claimed-member', 'password': PASSWORD}
    assert member.post('/api/auth/claim', headers=context(member), json=body).status_code == 200
    assert member.post('/api/auth/claim', headers=context(member), json=body).status_code == 400
    assert store.identity(old_key['key'])['user']['id'] == legacy_user
    assert store.one('SELECT spent FROM users WHERE id=?', (legacy_user,))['spent'] == 123
    assert code not in json.dumps(store.rows('SELECT * FROM account_tokens'))
    assert code not in json.dumps(store.rows('SELECT * FROM audit'))
    login(member, 'claimed-member')


def test_login_limit_and_idle_session_expiry(accounts):
    store, app, client, _, _ = accounts
    headers = context(client)
    for _ in range(8):
        assert client.post('/api/auth/login', headers=headers, json={'username': 'nonexistent', 'password': PASSWORD}).status_code == 401
    assert client.post('/api/auth/login', headers=headers, json={'username': 'nonexistent', 'password': PASSWORD}).status_code == 429
    login(client)
    with store.tx() as db:
        db.execute('UPDATE web_sessions SET last_seen=? WHERE user_id IS NOT NULL', (time.time()-1801,))
    assert client.get('/api/me').status_code == 401


def test_password_recovery_revokes_old_sessions_and_keys(accounts):
    store, app, admin_client, _, _ = accounts
    member, headers, user, admin_headers = register_and_approve(accounts)
    issued = member.post('/api/self/keys', headers=headers, json={}).json()
    body = {'purpose': 'reset', 'current_password': PASSWORD}
    code = admin_client.post('/api/admin/accounts/'+user+'/token', headers=admin_headers, json=body).json()['code']
    recovery = TestClient(app, base_url=ORIGIN)
    result = recovery.post('/api/auth/claim', headers=context(recovery), json={'code': code, 'username': 'recovered-member', 'password': NEXT_PASSWORD})
    assert result.status_code == 200
    assert member.get('/api/me').status_code == 401
    assert store.one('SELECT revoked FROM keys WHERE id=?', (issued['id'],))['revoked'] == 1
    assert login(recovery, 'recovered-member', NEXT_PASSWORD)
    assert recovery.post('/api/auth/claim', headers=context(recovery), json={'code': code, 'username': 'recovered-member', 'password': NEXT_PASSWORD}).status_code == 400


def test_disabling_account_invalidates_session_and_api_key(accounts):
    store, _, admin_client, _, _ = accounts
    member, headers, user, admin_headers = register_and_approve(accounts)
    issued = member.post('/api/self/keys', headers=headers, json={}).json()
    assert admin_client.patch('/api/users/'+user, headers=admin_headers, json={'role': 'caller', 'active': False, 'models': [MODEL], 'profiles': []}).status_code == 200
    assert member.get('/api/me').status_code == 401
    assert admin_client.get('/v1/models', headers={'Authorization': 'Bearer '+issued['key']}).status_code == 401
    assert not store.rows('SELECT * FROM web_sessions WHERE user_id=?', (user,))


def test_navigation_covers_every_original_page_and_new_account_page(accounts):
    from gateway.guide import ROLE_PAGES
    from gateway.navigation import navigation
    for role, pages in ROLE_PAGES.items():
        grouped = [page for group in navigation(role, pages) for page in group['pages']]
        assert len(grouped) == len(set(grouped))
        assert set(grouped) == set(pages)-{'notifications'}
    _, _, client, _, _ = accounts
    login(client)
    guide = client.get('/api/guide').json()
    grouped = [page for group in guide['navigation'] for page in group['pages']]
    assert set(grouped) == set(guide['allowed_pages'])-{'notifications'}
    assert {'registrations', 'users', 'keyaudit', 'account'} <= set(grouped)


def test_password_change_cannot_resurrect_session_after_racing_recovery(accounts):
    from gateway.store import Fault
    store, app, _, admin, _ = accounts
    previous_hash = store.one('SELECT password_hash FROM login_accounts WHERE user_id=?', (admin,))['password_hash']
    with store.tx() as db:
        db.execute('UPDATE login_accounts SET password_hash=? WHERE user_id=?', (hash_password(NEXT_PASSWORD), admin))
    with pytest.raises(Fault) as failure:
        app.state.web_accounts.issue_session(admin, account_hash=previous_hash)
    assert failure.value.code == 'invalid_login'
    assert not store.rows('SELECT * FROM web_sessions WHERE user_id=?', (admin,))


def test_additive_migration_preserves_legacy_keys_requests_and_ledger(tmp_path):
    path = tmp_path/'legacy.sqlite'
    store = Store(path, seed_demo=False)
    user = store.create_user('existing member', 'caller', [MODEL], [], 1000)
    issued = store.issue_key(user, [MODEL], 1000)
    request, fresh = store.reserve(store.identity(issued['key']), 'migration-fixture', {'synthetic': True}, 'chat', MODEL, 100, 'fixture', 'fixture-hash', runtime_required=False)
    assert fresh
    store.settle(request['id'], 10, 7, 3)
    tables = ('users', 'keys', 'requests', 'ledger', 'audit')
    before = {table: store.rows('SELECT * FROM '+table) for table in tables}
    # Remove only new, empty extension tables to represent the prior schema.
    with store.tx() as db:
        for table in ('web_sessions', 'account_tokens', 'key_labels', 'auth_rate', 'login_accounts'):
            db.execute('DROP TABLE '+table)
    reopened = Store(path, seed_demo=False)
    assert {table: reopened.rows('SELECT * FROM '+table) for table in tables} == before
    assert reopened.identity(issued['key'])['user']['id'] == user
    WebAccounts(reopened).setup(user, 'retained-member', PASSWORD)
    assert reopened.one('SELECT spent FROM users WHERE id=?', (user,))['spent'] == 10
    assert reopened.rows('SELECT * FROM ledger') == before['ledger']
