"""Optional web accounts: opaque sessions, approval, ownership and scoped API Keys."""
import hmac
import json
import secrets
import time

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .passwords import hash_password, normalize_username, verify_password
from .store import Fault, canonical, current_identity, digest, uid

SESSION_AGE = 12*3600
SESSION_IDLE = 1800
KEY_FIELDS = 'k.id,k.user_id,k.prefix,k.models,k.budget,k.spent,k.reserved,k.rpm,k.tpm,k.concurrency,k.expires,k.revoked'


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Login(Input):
    username: str = Field(min_length=1, max_length=80)
    password: SecretStr = Field(min_length=1, max_length=128)


class Register(Login):
    display_name: str = Field(min_length=1, max_length=60, pattern=r'^[\w\-\u4e00-\u9fff ]+$')


class ProfileChange(Input):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    username: str = Field(min_length=3, max_length=40)
    new_password: SecretStr | None = Field(default=None, min_length=15, max_length=128)


class Claim(Login):
    code: SecretStr = Field(min_length=20, max_length=200)


class Approval(Input):
    models: list[str] = Field(max_length=64)
    budget: int = Field(default=-1, ge=-1, le=1000000000)


class TokenRequest(Input):
    purpose: str = Field(pattern=r'^(enroll|reset)$')
    current_password: SecretStr = Field(min_length=1, max_length=128)


class PersonalKey(Input):
    name: str = Field(default='我的 Key', min_length=1, max_length=50)
    models: list[str] | None = Field(default=None, max_length=64)
    budget: int = Field(default=-1, ge=-1, le=1000000000)
    rpm: int = Field(default=60, ge=1, le=60)
    tpm: int = Field(default=1000000, ge=100, le=1000000)
    concurrency: int = Field(default=1, ge=1, le=1)
    days: int = Field(default=365, ge=1, le=365)


class WebAccounts:
    def __init__(self, store, *, public_origin=None):
        self.store = store
        self.public_origin = public_origin
        self.secure = bool(public_origin)
        self.cookie = '__Host-tc-session' if self.secure else 'tc-dev-session'

    def rate(self, request, action, username=None):
        now = time.time()
        source = request.client.host if request.client else 'unknown'
        limits = [(action+':global', 120, 60), (action+':ip:'+digest(source), 60 if action != 'register' else 10, 60)]
        if username is not None:
            limits.append((action+':name:'+digest(username), 8, 900))
        with self.store.tx() as db:
            db.execute('DELETE FROM auth_rate WHERE reset<?', (now-86400,))
            for bucket, limit, seconds in limits:
                row = db.execute('SELECT * FROM auth_rate WHERE bucket=?', (bucket,)).fetchone()
                if row and row['reset'] > now and row['count'] >= limit:
                    raise Fault('authentication_rate_limit', 429)
            for bucket, _, seconds in limits:
                db.execute('INSERT INTO auth_rate(bucket,reset,count) VALUES(?,?,1) ON CONFLICT(bucket) DO UPDATE SET '
                           'reset=CASE WHEN reset<=? THEN excluded.reset ELSE reset END,'
                           'count=CASE WHEN reset<=? THEN 1 ELSE count+1 END', (bucket, now+seconds, now, now))

    def session(self, request):
        value = request.cookies.get(self.cookie, '')
        if not 32 <= len(value) <= 128:
            return None
        row = self.store.one('SELECT * FROM web_sessions WHERE token_hash=?', (digest(value),))
        now = time.time()
        if not row or row['expires'] <= now or row['last_seen'] <= now-SESSION_IDLE:
            return None
        return row

    def csrf(self, request, session):
        expected = self.public_origin or str(request.base_url).rstrip('/')
        if request.headers.get('origin') != expected:
            raise Fault('origin_forbidden', 403)
        token = request.headers.get('x-csrf-token', '')
        if not session or not hmac.compare_digest(token, session['csrf']):
            raise Fault('csrf_required', 403)

    def issue_session(self, user_id=None, previous=None, account_hash=None):
        token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), time.time()
        with self.store.tx() as db:
            db.execute('DELETE FROM web_sessions WHERE expires<? OR last_seen<?', (now, now-SESSION_IDLE))
            if user_id:
                account = db.execute('SELECT a.password_hash,a.status,u.active,u.role FROM login_accounts a JOIN users u ON u.id=a.user_id WHERE user_id=?', (user_id,)).fetchone()
                if (not account or account['role'] not in ('admin', 'caller')
                        or not account['active'] and account['status'] != 'pending'
                        or account_hash is not None and account['password_hash'] != account_hash):
                    raise Fault('invalid_login', 401)
            if previous and not db.execute('SELECT 1 FROM web_sessions WHERE token_hash=?', (previous['token_hash'],)).fetchone():
                raise Fault('session_expired', 401)
            if previous:
                db.execute('DELETE FROM web_sessions WHERE token_hash=?', (previous['token_hash'],))
            if db.execute('SELECT COUNT(*) FROM web_sessions').fetchone()[0] >= 8192:
                raise Fault('authentication_busy', 429)
            db.execute('INSERT INTO web_sessions VALUES(?,?,?,?,?,?)', (digest(token), user_id, csrf, now, now, now+SESSION_AGE))
        response = JSONResponse({'authenticated': bool(user_id), 'csrf_token': csrf})
        response.set_cookie(self.cookie, token, max_age=SESSION_AGE, secure=self.secure, httponly=True, samesite='lax', path='/')
        return response

    def identity(self, request):
        session = self.session(request)
        if not session or not session['user_id']:
            raise Fault('session_required', 401)
        user = self.store.one('SELECT * FROM users WHERE id=?', (session['user_id'],))
        account = self.store.one('SELECT username,status FROM login_accounts WHERE user_id=?', (session['user_id'],))
        if not user or not account or user['role'] not in ('admin', 'caller'):
            raise Fault('session_expired', 401)
        pending = account['status'] == 'pending'
        if not user['active'] and not pending:
            raise Fault('account_disabled', 401)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            self.csrf(request, session)
        if pending and request.url.path not in ('/api/me', '/api/guide', '/api/guide/ack', '/api/account', '/api/account/profile', '/api/notifications'):
            raise Fault('registration_pending', 403)
        if time.time()-session['last_seen'] >= 60:
            with self.store.tx() as db:
                db.execute('UPDATE web_sessions SET last_seen=? WHERE token_hash=?', (time.time(), session['token_hash']))
        key = dict(id='web-session', user_id=user['id'], models=user['models'], budget=user['budget'],
                   spent=user['spent'], reserved=user['reserved'], hash='')
        return {'user': user, 'key': key, 'session': session['token_hash'], 'account': account}

    def setup(self, user_id, username, password, *, reset=False):
        username = normalize_username(username)
        encoded = hash_password(password, username)
        with self.store.tx() as db:
            user = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if not user or not user['active'] or user['role'] not in ('admin', 'caller'):
                raise Fault('user_not_found', 404)
            existing = db.execute('SELECT user_id FROM login_accounts WHERE user_id=?', (user_id,)).fetchone()
            if existing and not reset:
                raise Fault('login_already_configured', 409)
            self.unique_username(db, username, user_id)
            now = time.time()
            db.execute("INSERT INTO login_accounts VALUES(?,?,?,'approved',?,?) ON CONFLICT(user_id) DO UPDATE SET "
                       "username=excluded.username,password_hash=excluded.password_hash,changed=excluded.changed,status='approved'",
                       (user_id, username, encoded, now, now))
            db.execute('DELETE FROM web_sessions WHERE user_id=?', (user_id,))
            self.store.audit(db, 'local-account-setup', 'account.login_setup', user_id, {'reset': reset, 'password_printed': False})

    @staticmethod
    def unique_username(db, username, user_id=None):
        row = db.execute('SELECT user_id FROM login_accounts WHERE username=?', (username,)).fetchone()
        if row and row['user_id'] != user_id:
            raise Fault('username_unavailable', 409)


def mount_accounts(app, store, web, registered_models):
    def identity(request: Request):
        return web.identity(request)

    def admin(who=Depends(identity)):
        if who['user']['role'] != 'admin':
            raise Fault('role_forbidden', 403)
        return who

    @app.get('/api/auth/context')
    def context(request: Request):
        web.rate(request, 'context')
        session = web.session(request)
        configured = bool(store.one("SELECT 1 FROM login_accounts a JOIN users u ON u.id=a.user_id WHERE u.role='admin' AND u.active=1 AND a.status='approved'"))
        if not session:
            response = web.issue_session()
            document = json.loads(response.body)
            document.update(setup_required=not configured, registration='approval')
            response.body = json.dumps(document).encode()
            response.headers['content-length'] = str(len(response.body))
            return response
        return {'authenticated': bool(session['user_id']), 'csrf_token': session['csrf'], 'setup_required': not configured, 'registration': 'approval'}

    @app.post('/api/auth/register', status_code=202)
    def register(body: Register, request: Request):
        web.csrf(request, web.session(request))
        username = normalize_username(body.username)
        web.rate(request, 'register')
        encoded = hash_password(body.password.get_secret_value(), username)
        with store.tx() as db:
            web.unique_username(db, username)
            now, user_id = time.time(), uid('usr_')
            # The public display name need not be globally unique; the stored
            # member name includes a random suffix to preserve existing users.
            name = body.display_name.strip()
            if db.execute('SELECT 1 FROM users WHERE name=?', (name,)).fetchone():
                name += ' '+user_id[-6:]
            db.execute("INSERT INTO users(id,name,role,models,profiles,budget,active) VALUES(?,?,'caller','[]','[]',-1,0)", (user_id, name))
            db.execute("INSERT INTO login_accounts VALUES(?,?,?,'pending',?,?)", (user_id, username, encoded, now, now))
            store.audit(db, user_id, 'account.register', user_id, {'approval_required': True})
        return {'registered': True, 'status': 'pending'}

    @app.post('/api/auth/login')
    def login(body: Login, request: Request):
        previous = web.session(request)
        web.csrf(request, previous)
        try:
            username = normalize_username(body.username)
        except Fault:
            username = body.username.casefold()[:80]
        web.rate(request, 'login', username)
        account = store.one('SELECT * FROM login_accounts WHERE username=?', (username,))
        valid = verify_password(body.password.get_secret_value(), account['password_hash'] if account else None)
        if not valid or not account:
            raise Fault('invalid_login', 401)
        user = store.one('SELECT * FROM users WHERE id=?', (account['user_id'],))
        if not user or user['role'] not in ('admin', 'caller') or not user['active'] and account['status'] != 'pending':
            raise Fault('invalid_login', 401)
        with store.tx() as db:
            store.audit(db, user['id'], 'account.login', user['id'])
        return web.issue_session(user['id'], previous, account_hash=account['password_hash'])

    @app.post('/api/auth/logout')
    def logout(request: Request):
        session = web.session(request)
        web.csrf(request, session)
        with store.tx() as db:
            db.execute('DELETE FROM web_sessions WHERE token_hash=?', (session['token_hash'],))
        response = JSONResponse({'logged_out': True})
        response.delete_cookie(web.cookie, secure=web.secure, httponly=True, samesite='lax', path='/')
        return response

    @app.get('/api/account')
    def profile(who=Depends(identity)):
        return {'username': who['account']['username'], 'status': who['account']['status'], 'user': who['user']}

    @app.post('/api/account/profile')
    def change_profile(body: ProfileChange, request: Request, who=Depends(identity)):
        web.rate(request, 'profile', who['user']['id'])
        account = store.one('SELECT * FROM login_accounts WHERE user_id=?', (who['user']['id'],))
        if not verify_password(body.current_password.get_secret_value(), account['password_hash']):
            raise Fault('invalid_login', 401)
        username = normalize_username(body.username)
        encoded = hash_password(body.new_password.get_secret_value(), username) if body.new_password else account['password_hash']
        with store.tx() as db:
            current = db.execute('SELECT * FROM web_sessions WHERE token_hash=? AND user_id=?', (who['session'], who['user']['id'])).fetchone()
            latest = db.execute('SELECT * FROM login_accounts WHERE user_id=?', (who['user']['id'],)).fetchone()
            user = db.execute('SELECT active FROM users WHERE id=?', (who['user']['id'],)).fetchone()
            if (not current or current['expires'] <= time.time() or current['last_seen'] <= time.time()-SESSION_IDLE
                    or not latest or latest['password_hash'] != account['password_hash']
                    or not user or not user['active'] and latest['status'] != 'pending'):
                raise Fault('session_expired', 401)
            web.unique_username(db, username, who['user']['id'])
            db.execute('UPDATE login_accounts SET username=?,password_hash=?,changed=? WHERE user_id=?', (username, encoded, time.time(), who['user']['id']))
            db.execute('DELETE FROM web_sessions WHERE user_id=?', (who['user']['id'],))
            store.audit(db, who['user']['id'], 'account.credentials_change', who['user']['id'], {'password_changed': bool(body.new_password), 'sessions_revoked': True})
        return web.issue_session(who['user']['id'], account_hash=encoded)

    @app.get('/api/admin/registrations')
    def registrations(who=Depends(admin)):
        return store.rows("SELECT u.id,u.name,a.username,a.created FROM login_accounts a JOIN users u ON u.id=a.user_id WHERE a.status='pending' ORDER BY a.created LIMIT 100")

    @app.post('/api/admin/registrations/{user_id}/approve')
    def approve(user_id: str, body: Approval, who=Depends(admin)):
        known = {model['id'] for model in registered_models()}
        if not set(body.models).issubset(known):
            raise Fault('model_not_registered', 400)
        with store.tx() as db:
            store.check_admin(db, who)
            account = db.execute("SELECT * FROM login_accounts WHERE user_id=? AND status='pending'", (user_id,)).fetchone()
            if not account:
                raise Fault('registration_not_pending', 409)
            db.execute("UPDATE login_accounts SET status='approved',changed=? WHERE user_id=?", (time.time(), user_id))
            db.execute("UPDATE users SET active=1,models=?,budget=? WHERE id=? AND role='caller'", (canonical(body.models), body.budget, user_id))
            store.audit(db, who['user']['id'], 'account.approve', user_id, {'models': body.models, 'budget': body.budget})
        return {'approved': True}

    @app.post('/api/admin/accounts/{user_id}/token')
    def issue_token(user_id: str, body: TokenRequest, request: Request, who=Depends(admin)):
        web.rate(request, 'account-token', who['user']['id'])
        account = store.one('SELECT password_hash FROM login_accounts WHERE user_id=?', (who['user']['id'],))
        if not verify_password(body.current_password.get_secret_value(), account['password_hash']):
            raise Fault('invalid_login', 401)
        code = secrets.token_urlsafe(32)
        with store.tx() as db:
            store.check_admin(db, who)
            user = db.execute("SELECT * FROM users WHERE id=? AND role='caller' AND active=1", (user_id,)).fetchone()
            existing = db.execute('SELECT 1 FROM login_accounts WHERE user_id=?', (user_id,)).fetchone()
            if not user or (body.purpose == 'enroll' and existing) or (body.purpose == 'reset' and not existing):
                raise Fault('account_token_not_allowed', 409)
            db.execute('DELETE FROM account_tokens WHERE user_id=? AND purpose=?', (user_id, body.purpose))
            db.execute('INSERT INTO account_tokens VALUES(?,?,?,?,0)', (digest(code), user_id, body.purpose, time.time()+(86400 if body.purpose=='enroll' else 600)))
            store.audit(db, who['user']['id'], 'account.token_issue', user_id, {'purpose': body.purpose, 'token_printed_in_audit': False})
        return {'code': code, 'purpose': body.purpose, 'reveal': '仅显示一次，请通过可信渠道交给对应成员'}

    @app.post('/api/auth/claim')
    def claim(body: Claim, request: Request):
        web.csrf(request, web.session(request))
        web.rate(request, 'claim')
        username = normalize_username(body.username)
        code_hash = digest(body.code.get_secret_value())
        token = store.one('SELECT * FROM account_tokens WHERE token_hash=? AND consumed=0 AND expires>?', (code_hash, time.time()))
        if not token:
            raise Fault('invalid_account_token', 400)
        encoded = hash_password(body.password.get_secret_value(), username)
        with store.tx() as db:
            token = db.execute('SELECT * FROM account_tokens WHERE token_hash=? AND consumed=0 AND expires>?', (code_hash, time.time())).fetchone()
            user = db.execute('SELECT * FROM users WHERE id=?', (token['user_id'],)).fetchone() if token else None
            if not token or not user or not user['active'] or user['role'] != 'caller':
                raise Fault('invalid_account_token', 400)
            existing = db.execute('SELECT 1 FROM login_accounts WHERE user_id=?', (user['id'],)).fetchone()
            if token['purpose'] == 'enroll' and existing or token['purpose'] == 'reset' and not existing:
                raise Fault('invalid_account_token', 400)
            web.unique_username(db, username, user['id'])
            now = time.time()
            db.execute("INSERT INTO login_accounts VALUES(?,?,?,'approved',?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,password_hash=excluded.password_hash,changed=excluded.changed",
                       (user['id'], username, encoded, now, now))
            db.execute('UPDATE account_tokens SET consumed=1 WHERE token_hash=?', (code_hash,))
            db.execute('DELETE FROM web_sessions WHERE user_id=?', (user['id'],))
            if token['purpose'] == 'reset':
                db.execute('UPDATE keys SET revoked=1 WHERE user_id=?', (user['id'],))
            store.audit(db, user['id'], 'account.'+token['purpose'], user['id'], {'sessions_revoked': True, 'api_keys_revoked': token['purpose']=='reset'})
        return {'completed': True, 'sign_in_required': True}

    @app.get('/api/self/keys')
    def own_keys(who=Depends(identity)):
        return store.rows('SELECT '+KEY_FIELDS+",COALESCE(l.name,'历史 Key') AS name FROM keys k LEFT JOIN key_labels l ON l.key_id=k.id WHERE k.user_id=? ORDER BY k.expires DESC LIMIT 100", (who['user']['id'],))

    def key_grants(db, who, body):
        user, _ = current_identity(db, who)
        if user['role'] != 'caller':
            raise Fault('member_calling_key_required', 403)
        granted = set(json.loads(user['models'])) & {model['id'] for model in registered_models()}
        models = sorted(granted) if body.models is None else body.models
        if not set(models).issubset(granted):
            raise Fault('key_models_exceed_user_grant', 403)
        return user, models

    @app.post('/api/self/keys', status_code=201)
    def create_key(body: PersonalKey, who=Depends(identity)):
        token, key_id, now = 'lg_'+secrets.token_urlsafe(32), uid('key_'), time.time()
        with store.tx() as db:
            user, models = key_grants(db, who, body)
            if db.execute('SELECT COUNT(*) FROM keys WHERE user_id=? AND revoked=0 AND expires>?', (user['id'], now)).fetchone()[0] >= 10:
                raise Fault('personal_key_limit', 429)
            if db.execute('SELECT COUNT(*) FROM key_labels l JOIN keys k ON k.id=l.key_id WHERE k.user_id=? AND l.created>?', (user['id'], now-86400)).fetchone()[0] >= 20:
                raise Fault('personal_key_daily_limit', 429)
            db.execute('INSERT INTO keys(id,user_id,hash,prefix,models,budget,rpm,tpm,concurrency,expires) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (key_id, user['id'], digest(token), token[:7]+'…', canonical(models), body.budget, body.rpm, body.tpm, body.concurrency, now+body.days*86400))
            db.execute('INSERT INTO key_labels VALUES(?,?,?)', (key_id, body.name, now))
            store.audit(db, user['id'], 'key.self_issue', key_id, {'models': models, 'budget': body.budget})
        return {'id': key_id, 'key': token, 'reveal': '完整 Key 仅显示这一次，请自行保存'}

    @app.patch('/api/self/keys/{key_id}')
    def update_key(key_id: str, body: PersonalKey, who=Depends(identity)):
        with store.tx() as db:
            user, models = key_grants(db, who, body)
            key = db.execute('SELECT * FROM keys WHERE id=? AND user_id=? AND revoked=0', (key_id, user['id'])).fetchone()
            if not key:
                raise Fault('key_not_found', 404)
            if body.budget >= 0 and body.budget < key['spent']+key['reserved']:
                raise Fault('budget_below_committed_amount', 409)
            db.execute('UPDATE keys SET models=?,budget=?,rpm=?,tpm=?,concurrency=?,expires=? WHERE id=?',
                       (canonical(models), body.budget, body.rpm, body.tpm, body.concurrency, time.time()+body.days*86400, key_id))
            db.execute('INSERT INTO key_labels VALUES(?,?,?) ON CONFLICT(key_id) DO UPDATE SET name=excluded.name', (key_id, body.name, time.time()))
            store.audit(db, user['id'], 'key.self_update', key_id, {'models': models})
        return {'updated': True}

    @app.delete('/api/self/keys/{key_id}')
    def revoke_key(key_id: str, who=Depends(identity)):
        with store.tx() as db:
            user, _ = current_identity(db, who)
            if not db.execute('SELECT 1 FROM keys WHERE id=? AND user_id=?', (key_id, user['id'])).fetchone():
                raise Fault('key_not_found', 404)
            db.execute('UPDATE keys SET revoked=1 WHERE id=?', (key_id,))
            store.audit(db, user['id'], 'key.self_revoke', key_id)
        return {'revoked': True}

    return identity
