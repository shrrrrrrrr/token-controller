from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.accounts import WebAccounts
from gateway.app import create_app
from gateway.store import Store
from gateway.tool_proxy import ToolProxy,CourtProxy,load_config
from integrations.platform_identity import IdentityError,issue,verify,safe_target
from integrations.simcourt_platform_auth import Ownership,make_handler

ORIGIN='https://models.example.test'
PASSWORD='A synthetic platform login phrase 2026'
CSRF='C'*43
PRINCIPAL={'id':'usr_'+'1'*32,'username':'synthetic-member','role':'caller'}
OPENSSL=os.environ.get('TEST_OPENSSL') or shutil.which('openssl')


@pytest.fixture
def keys(tmp_path):
    if not OPENSSL:pytest.skip('OpenSSL required for real Ed25519 verification')
    private=tmp_path/'private.pem';public=tmp_path/'public.pem'
    subprocess.run([OPENSSL,'genpkey','-algorithm','Ed25519','-out',str(private)],check=True,capture_output=True)
    subprocess.run([OPENSSL,'pkey','-in',str(private),'-pubout','-out',str(public)],check=True,capture_output=True)
    private.chmod(0o600);return private,public


def test_real_ed25519_binds_identity_method_path_body_and_expiry(keys,tmp_path):
    private,public=keys;now=int(time.time())
    token=issue(private,'tool:case-tool',PRINCIPAL,CSRF,'POST','/api/jobs',b'{}',now=now,workdir=tmp_path,openssl=OPENSSL)
    claim=verify(public,token,'tool:case-tool','POST','/api/jobs',b'{}',now=now,workdir=tmp_path,openssl=OPENSSL)
    assert claim['sub']==PRINCIPAL['id'] and claim['exp']==now+15
    for audience,method,path,body,stamp in [('tool:other','POST','/api/jobs',b'{}',now),
        ('tool:case-tool','GET','/api/jobs',b'{}',now),('tool:case-tool','POST','/api/history',b'{}',now),
        ('tool:case-tool','POST','/api/jobs',b'{"changed":true}',now),('tool:case-tool','POST','/api/jobs',b'{}',now+15)]:
        with pytest.raises(IdentityError):verify(public,token,audience,method,path,body,now=stamp,workdir=tmp_path,openssl=OPENSSL)
    pieces=token.split('.');pieces[2]=('A' if pieces[2][0]!='A' else 'B')+pieces[2][1:]
    with pytest.raises(IdentityError):verify(public,'.'.join(pieces),'tool:case-tool','POST','/api/jobs',b'{}',now=now,openssl=OPENSSL)
    assert not list(tmp_path.glob('tool-identity-*'))


@pytest.mark.parametrize('target',['//other.test/x','/../secret','/%2e%2e/secret','/%252e%252e/secret',
                                   '/%5c../secret','/x\r\nheader:bad','/x#fragment','/%252525252e%252525252e/x'])
def test_paths_fail_closed(target):
    with pytest.raises(IdentityError):safe_target(target)


class Chunks(httpx.AsyncByteStream):
    def __init__(self,data):self.data=data
    async def __aiter__(self):yield self.data


@pytest.fixture
def platform(tmp_path,keys):
    private,public=keys
    store=Store(tmp_path/'gateway.sqlite',seed_demo=False)
    admin=store.create_user('Synthetic admin','admin',[],[],-1)
    caller=store.create_user('Synthetic member','caller',[],[],-1)
    web=WebAccounts(store,public_origin=ORIGIN)
    web.setup(admin,'synthetic-admin',PASSWORD);web.setup(caller,'synthetic-member',PASSWORD)
    config=tmp_path/'tools.json';config.write_text(json.dumps({'tools':[{'id':'case-tool','url':'https://tool.example.test/','title':'Case tool','embed':True}]}))
    observed=[];fault={'old_auth':False,'cookie':False}
    def upstream(request):
        assert not any(name in request.headers for name in ('cookie','authorization','x-api-key','x-forwarded-user'))
        assert request.headers['accept-encoding']=='identity' and request.extensions['sni_hostname']=='tool.example.test'
        claims=verify(public,request.headers['x-platform-tool-identity'],'tool:case-tool',request.method,
                      request.url.raw_path.decode(),request.content,openssl=OPENSSL)
        observed.append(claims)
        headers={'content-type':'text/html; charset=utf-8','x-platform-tool-bridge':'1','set-cookie':'must_not_forward=secret'}
        if fault['old_auth']:headers.pop('x-platform-tool-bridge')
        return httpx.Response(200,headers=headers,stream=Chunks(b'<html><head></head><body>Synthetic tool</body></html>'))
    proxy=ToolProxy({'tool_id':'case-tool','upstream':'https://127.0.0.1:28443','server_name':'tool.example.test','private_key':str(private)},
                    transport=httpx.MockTransport(upstream),openssl=OPENSSL)
    app=create_app(store,web_accounts=True,project_tools_path=config,tool_proxy=proxy,background=False)
    # Local account mode uses localhost cookies; test origin follows the request.
    with TestClient(app,base_url='https://localhost') as client:
        yield store,client,caller,admin,observed,fault,proxy


def sign_in(client,username='synthetic-member'):
    context=client.get('/api/auth/context').json()
    response=client.post('/api/auth/login',headers={'Origin':'https://localhost','X-CSRF-Token':context['csrf_token']},
                         json={'username':username,'password':PASSWORD})
    assert response.status_code==200,response.text
    return {'Origin':'https://localhost','X-CSRF-Token':response.json()['csrf_token']}


def test_tool_login_redirect_real_session_and_stripped_credentials(platform):
    store,client,caller,admin,observed,fault,proxy=platform
    before=client.get('/tools/case-tool/',follow_redirects=False)
    assert before.status_code==303 and before.headers['location']=='/?next=%2Ftools%2Fcase-tool%2F'
    assert client.get('/tools/case-tool/api/history').status_code==401 and not observed
    headers=sign_in(client)
    response=client.get('/tools/case-tool/',headers={'Authorization':'Bearer caller-must-not-forward','X-Forwarded-User':'forged'})
    assert response.status_code==200 and '/tools/case-tool/__bridge.js' in response.text
    assert 'set-cookie' not in response.headers and 'x-platform-tool-bridge' not in response.headers
    assert observed[-1]['sub']==caller and observed[-1]['username']=='synthetic-member'
    entry=client.get('/api/project-tools').json()['tools'][0]
    assert entry['url']=='/tools/case-tool/' and entry['authentication']=='platform_session'
    assert entry['embed'] is False and entry['execution']=='held'
    assert 'tool.example.test' not in json.dumps(entry)
    assert client.post('/tools/case-tool/api/jobs',json={}).status_code==403
    assert client.get('/tools/case-tool/__bridge.js').status_code==200
    assert client.get('/tools/case-tool/__bridge.css').status_code==200
    assert client.post('/tools/case-tool/api/auth/logout',headers=headers,json={}).status_code==200
    assert client.get('/tools/case-tool/',follow_redirects=False).status_code==303


@pytest.mark.parametrize('state',['disabled','pending','expired','logged-out'])
def test_recheck_platform_session_and_approval_every_request(platform,state):
    store,client,caller,admin,observed,fault,proxy=platform;headers=sign_in(client)
    with store.tx() as db:
        if state=='disabled':db.execute('UPDATE users SET active=0 WHERE id=?',(caller,))
        if state=='pending':db.execute("UPDATE login_accounts SET status='pending' WHERE user_id=?",(caller,))
        if state=='expired':db.execute('UPDATE web_sessions SET expires=0')
        if state=='logged-out':db.execute('DELETE FROM web_sessions')
    response=client.get('/tools/case-tool/',follow_redirects=False)
    assert response.status_code in (303,401,403) and not observed


def test_old_backend_login_is_not_misrepresented_as_sso_and_paths_not_proxied(platform):
    store,client,caller,admin,observed,fault,proxy=platform;sign_in(client)
    fault['old_auth']=True
    assert client.get('/tools/case-tool/').status_code==503
    before=len(observed)
    for path in ('/.git/config','/state/owners.sqlite','/api/admin','/api/jobs/not-a-job'):
        assert client.get('/tools/case-tool'+path).status_code in (400,404)
    assert len(observed)==before


def test_proxy_requires_web_accounts_and_matching_registration(tmp_path,keys):
    proxy=ToolProxy({'tool_id':'case-tool'})
    with pytest.raises(ValueError):create_app(Store(tmp_path/'a.sqlite'),tool_proxy=proxy)
    with pytest.raises(ValueError):create_app(Store(tmp_path/'b.sqlite'),web_accounts=True,tool_proxy=proxy)


def test_calling_key_cannot_replace_browser_session(platform):
    store,client,caller,admin,observed,fault,proxy=platform
    token=store.issue_key(caller,[],-1)['key']
    client.cookies.clear()
    assert client.get('/tools/case-tool/',headers={'Authorization':'Bearer '+token},follow_redirects=False).status_code==303
    assert not observed


def test_bad_paths_and_streamed_get_bodies_do_not_reach_tool(platform):
    store,client,caller,admin,observed,fault,proxy=platform;sign_in(client)
    assert client.get('/tools/case-tool/%2e%2e/secret').status_code==400
    assert client.request('GET','/tools/case-tool/',content=b'synthetic body').status_code==400
    assert client.request('GET','/tools/case-tool/',content=iter([b'a'*131072,b'b'*131073])).status_code==413
    assert not observed


def test_nonce_persistence_and_owner_namespace_preserve_legacy_data(tmp_path):
    directory=tmp_path/'platform-sso';directory.mkdir(mode=0o700)
    legacy=tmp_path/'legacy-auth.sqlite';legacy.write_bytes(b'legacy accounts preserved')
    owners=Ownership(directory);nonce='f'*32
    assert owners.consume(nonce,int(time.time())+15) and not Ownership(directory).consume(nonce,int(time.time())+15)
    owners.claim('a'*32,PRINCIPAL['id'])
    assert owners.owner('a'*32)==PRINCIPAL['id'] and owners.owner('b'*32) is None
    assert legacy.read_bytes()==b'legacy accounts preserved'


@contextmanager
def court_server(tmp_path,public,tls=None):
    directory=tmp_path/'platform-sso';directory.mkdir(mode=0o700)
    class Base(BaseHTTPRequestHandler):
        def respond(self,value,status=200):
            raw=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if self.path=='/api/history':self.respond({'history':[{'id':'a'*32},{'id':'b'*32},{'id':'c'*32}]})
            else:self.respond({'base':'synthetic'})
        def do_POST(self):self.respond({'base':'would start legacy inference'})
    cls=make_handler(Base,object(),tmp_path,config={'tool_id':'case-tool','platform_origin':ORIGIN,'public_key':str(public),'legacy_admin_read':False},openssl=OPENSSL)
    cls.platform_ownership.claim('a'*32,PRINCIPAL['id']);cls.platform_ownership.claim('b'*32,'usr_'+'2'*32)
    server=ThreadingHTTPServer(('127.0.0.1',0),cls)
    if tls:
        import ssl
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(*tls)
        server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield ('https' if tls else 'http')+'://127.0.0.1:'+str(server.server_port),cls
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


def test_real_tool_server_signed_login_owner_isolation_and_generation_hold(tmp_path,keys):
    private,public=keys
    with court_server(tmp_path,public) as (url,cls),httpx.Client(base_url=url,trust_env=False,follow_redirects=False) as client:
        assert client.get('/').status_code==303 and client.get('/api/auth/me').status_code==401
        def signed(method,path,body=b''):
            return {'X-Platform-Tool-Identity':issue(private,'tool:case-tool',PRINCIPAL,CSRF,method,path,body,openssl=OPENSSL)}
        headers=signed('GET','/api/auth/me')
        response=client.get('/api/auth/me',headers=headers)
        assert response.status_code==200 and response.json()['username']=='synthetic-member'
        assert client.get('/api/auth/me',headers=headers).status_code==401 # replay across independent HTTP requests
        history=client.get('/api/history',headers=signed('GET','/api/history'))
        assert history.json()['history']==[{'id':'a'*32}]
        assert client.get('/api/jobs/'+'b'*32,headers=signed('GET','/api/jobs/'+'b'*32)).status_code==404
        assert client.get('/api/jobs/'+'c'*32,headers=signed('GET','/api/jobs/'+'c'*32)).status_code==404
        assert client.get('/api/jobs/'+'a'*32,headers=signed('GET','/api/jobs/'+'a'*32)).status_code==200
        assert client.post('/api/jobs',content=b'{}',headers=signed('POST','/api/jobs',b'{}')).status_code==503
        assert client.post('/api/auth/login',content=b'{}').status_code==401


def test_load_config_rejects_non_loopback_and_insecure_origins(tmp_path,keys):
    private,public=keys;path=tmp_path/'proxy.json'
    for upstream in ('http://127.0.0.1:2443','https://other.example.test','https://user:password@127.0.0.1:2443','https://127.0.0.1:2443/path'):
        path.write_text(json.dumps({'tool_id':'case-tool','upstream':upstream,'server_name':'tool.example.test',
                                   'certificate':str(public),'private_key':str(private)}));path.chmod(0o600)
        with pytest.raises(ValueError):load_config(path)


def test_real_loopback_tls_with_verified_tool_server_name(platform,keys,tmp_path):
    import ssl
    private,public=keys;cert=tmp_path/'tool.crt'
    subprocess.run([OPENSSL,'req','-new','-x509','-key',str(private),'-out',str(cert),'-days','1',
                    '-subj','/CN=tool.example.test','-addext','subjectAltName=DNS:tool.example.test'],check=True,capture_output=True)
    store,client,caller,admin,observed,fault,proxy=platform
    with court_server(tmp_path,public,tls=(str(cert),str(private))) as (url,cls):
        path=tmp_path/'valid-proxy.json';path.write_text(json.dumps({'tool_id':'case-tool','upstream':url,'server_name':'tool.example.test',
            'certificate':str(cert),'private_key':str(private)}));path.chmod(0o600)
        config,context=load_config(path)
        proxy.config=config;proxy.ssl_context=context;proxy.transport=None
        sign_in(client)
        response=client.get('/tools/case-tool/')
        assert response.status_code==200 and response.json()=={'base':'synthetic'}
        proxy.config['server_name']='wrong.example.test'
        assert client.get('/tools/case-tool/').status_code==502
        proxy.config['server_name']='tool.example.test';proxy.ssl_context=ssl.create_default_context()
        assert client.get('/tools/case-tool/').status_code==502


@pytest.fixture
def court_platform(tmp_path,keys):
    private,public=keys
    store=Store(tmp_path/'two-tools.sqlite',seed_demo=False)
    admin=store.create_user('Synthetic admin','admin',[],[],-1)
    caller=store.create_user('Synthetic member','caller',[],[],-1)
    web=WebAccounts(store,public_origin=ORIGIN)
    web.setup(admin,'synthetic-admin',PASSWORD);web.setup(caller,'synthetic-member',PASSWORD)
    legacy=ToolProxy({'tool_id':'case-tool','upstream':'https://127.0.0.1:28443',
        'server_name':'tool.example.test','private_key':str(private)},
        transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/html',
            'x-platform-tool-bridge':'1'},stream=Chunks(b'<html><head></head><body>Original</body></html>'))),openssl=OPENSSL)
    config=tmp_path/'two-tools.json'
    config.write_text(json.dumps({'tools':[
        {'id':'case-tool','url':'https://tool.example.test/','embed':False},
        {'id':'court-transcript','url':'https://transcript.example.test/','embed':False}]}))
    observed=[]
    def upstream(request):
        assert not any(name in request.headers for name in ('cookie','authorization','x-api-key','x-forwarded-user'))
        claims=verify(public,request.headers['x-platform-tool-identity'],'tool:court-transcript',request.method,
            request.url.raw_path.decode(),request.content,openssl=OPENSSL)
        observed.append((claims,len(request.content),request.headers.get('range')))
        headers={'x-platform-tool-bridge':'1','set-cookie':'not-forwarded=secret'}
        if request.url.path.endswith('/media'):
            headers.update({'content-type':'video/mp4','accept-ranges':'bytes',
                'content-range':'bytes 0-5242879/10485760','content-length':str(5*1024*1024)})
            return httpx.Response(206,headers=headers,stream=Chunks(b'x'*(5*1024*1024)))
        if request.url.path=='/':
            headers['content-type']='text/html'
            return httpx.Response(200,headers=headers,stream=Chunks(b'<html><head></head><body>Court</body></html>'))
        headers['content-type']='application/json'
        return httpx.Response(200,headers=headers,stream=Chunks(b'{"ok":true}'))
    court=CourtProxy({'tool_id':'court-transcript','upstream':'https://127.0.0.1:28444',
        'server_name':'transcript.example.test','private_key':str(private)},
        transport=httpx.MockTransport(upstream),openssl=OPENSSL)
    app=create_app(store,web_accounts=True,project_tools_path=config,tool_proxy=[legacy,court],background=False)
    with TestClient(app,base_url='https://localhost') as client:
        yield store,client,observed,court,legacy


def test_court_and_existing_tool_have_separate_routes_and_limits(court_platform):
    store,client,observed,court,legacy=court_platform
    assert client.get('/tools/court-transcript/',follow_redirects=False).status_code==303
    headers=sign_in(client)
    assert client.get('/tools/case-tool/').status_code==200
    result=client.get('/tools/court-transcript/')
    assert result.status_code==200 and '/tools/court-transcript/__bridge.js' in result.text
    assert 'media-src' in result.headers['content-security-policy']
    assert 'media-src' not in client.get('/').headers['content-security-policy']
    rows=client.get('/api/project-tools').json()['tools']
    assert {row['url'] for row in rows}=={'/tools/case-tool/','/tools/court-transcript/'}
    data=b'a'*(4*1024*1024)
    path='/tools/court-transcript/api/uploads/'+'a'*32+'/parts/0'
    result=client.post(path,headers={**headers,'Content-Type':'application/octet-stream',
        'Authorization':'Bearer do-not-forward','X-Forwarded-User':'forged'},content=data)
    assert result.status_code==200,result.text
    assert observed[-1][1]==len(data)
    assert client.post('/tools/case-tool/api/jobs',headers=headers,content=data).status_code==413
    assert client.post('/api/not-a-tool',headers=headers,content=data).status_code==413
    assert client.post(path,headers=headers,content=data+b'!').status_code==413
    assert client.post('/tools/court-transcript/api/tasks',headers=headers,content=b'{}').status_code==413
    invalid_headers=[(b'origin',b'https://localhost'),(b'x-csrf-token',b'\xff')]
    assert client.post('/tools/court-transcript/api/uploads',headers=invalid_headers,content=b'{}').status_code==403


def test_court_patch_range_and_streamed_large_response(court_platform):
    store,client,observed,court,legacy=court_platform
    headers=sign_in(client)
    path='/tools/court-transcript/api/tasks/'+'a'*32
    result=client.patch(path+'/segments/seg-1',headers=headers,json={'text':'逐字核听'})
    assert result.status_code==200,result.text
    assert observed[-1][0]['method']=='PATCH'
    result=client.get(path+'/media',headers={'Range':'bytes=0-'})
    assert result.status_code==206 and len(result.content)==5*1024*1024
    assert result.headers['accept-ranges']=='bytes' and 'content-range' in result.headers
    assert 'set-cookie' not in result.headers and observed[-1][2]=='bytes=0-'
    assert client.get(path+'/media',headers={'Range':'bytes=0-1,4-6'}).status_code==416


@pytest.mark.parametrize('state',['expired','disabled','pending'])
def test_court_upload_rechecks_platform_authority_before_forward(court_platform,state):
    store,client,observed,court,legacy=court_platform
    headers=sign_in(client)
    with store.tx() as db:
        if state=='expired':db.execute('UPDATE web_sessions SET expires=0')
        if state=='disabled':db.execute("UPDATE users SET active=0 WHERE role='caller'")
        if state=='pending':db.execute("UPDATE login_accounts SET status='pending' WHERE username='synthetic-member'")
    result=client.post('/tools/court-transcript/api/uploads',headers=headers,json={})
    assert result.status_code in (401,403) and not observed
