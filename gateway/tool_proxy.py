"""Opt-in, session-authenticated proxy to one fixed TLS loopback tool."""
import asyncio
import json
from pathlib import Path
import re
import ssl
from urllib.parse import quote, urlsplit

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from integrations.platform_identity import IdentityError, checked_file, issue, safe_target
from .store import Fault

MAX_RESPONSE=4*1024*1024


def load_config(path):
    config=json.loads(checked_file(path,private=True,maximum=8192))
    return validate_config(config)


def validate_config(config):
    fields={'tool_id','upstream','server_name','certificate','private_key'}
    if (not isinstance(config,dict) or set(config) not in (fields,fields|{'kind'})
            or config.get('kind','simcourt') not in ('simcourt','court-transcript')
            or not isinstance(config['tool_id'],str) or not re.fullmatch('[a-z][a-z0-9-]{0,39}',config['tool_id'])):
        raise ValueError('invalid project-tool proxy configuration')
    origin=urlsplit(config['upstream'])
    if (origin.scheme!='https' or origin.hostname!='127.0.0.1' or not origin.port or origin.port<1024
            or origin.path or origin.username or origin.password or origin.query or origin.fragment):
        raise ValueError('tool upstream must be one fixed TLS IPv4 loopback origin')
    if not isinstance(config['server_name'],str) or not re.fullmatch('[A-Za-z0-9.-]{1,253}',config['server_name']):
        raise ValueError('invalid tool TLS server name')
    checked_file(config['private_key'],private=True)
    certificate=checked_file(config['certificate']).decode('ascii')
    context=ssl.create_default_context(cadata=certificate)
    return config,context


def load_proxies(path):
    value=json.loads(checked_file(path,private=True,maximum=65536))
    configs=value.get('tools') if isinstance(value,dict) and set(value)=={'tools'} else [value]
    if not isinstance(configs,list) or not 1<=len(configs)<=8:
        raise ValueError('expected one to eight explicitly configured tools')
    result=[]
    for item in configs:
        config,context=validate_config(item)
        cls=CourtProxy if config.get('kind')=='court-transcript' else ToolProxy
        result.append(cls(config,ssl_context=context))
    if len({p.tool_id for p in result})!=len(result):
        raise ValueError('duplicate tool identity')
    return result


class ToolProxy:
    kind='simcourt'
    request_maximum=262144
    methods=['GET','HEAD','POST']
    def __init__(self,config,*,ssl_context=None,transport=None,openssl='/usr/bin/openssl'):
        self.config,self.ssl_context,self.transport,self.openssl=config,ssl_context,transport,openssl
        self.tool_id=config['tool_id'];self.prefix='/tools/'+self.tool_id

    def accepts(self,target):
        safe_target(target);path=urlsplit(target).path
        return (path in ('/','/index.html','/api/cases','/api/history','/api/auth/me')
                or re.fullmatch('/api/jobs/[a-f0-9]{32}(?:/export|/stop)?',path) is not None
                or path in ('/api/jobs','/api/replay')
                or re.fullmatch(r'/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.(?:css|js|svg|png|jpg|ico|woff2)',path) is not None)

    async def forward(self,request,who,csrf):
        try:
            raw_path=request.scope['raw_path'].decode('ascii')
            if not raw_path.startswith(self.prefix+'/'):raise Fault('tool_path_invalid',400)
            target=raw_path[len(self.prefix):]
            if request.scope['query_string']:target+='?'+request.scope['query_string'].decode('ascii')
            if not self.accepts(target):raise Fault('not_found',404)
        except (IdentityError,UnicodeError):raise Fault('tool_path_invalid',400) from None
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>262144:raise Fault('body_too_large',413)
        body=bytes(body)
        if request.method in ('GET','HEAD') and body:raise Fault('tool_body_not_allowed',400)
        principal={'id':who['user']['id'],'username':who['account']['username'],'role':who['user']['role']}
        try:
            # The browser's cookies, Authorization and identity headers are not forwarded.
            token=await asyncio.to_thread(issue,self.config['private_key'],'tool:'+self.tool_id,principal,csrf,
                                         request.method,target,body,openssl=self.openssl)
            headers={'Accept-Encoding':'identity','X-Platform-Tool-Identity':token}
            if request.headers.get('content-type'):headers['Content-Type']=request.headers['content-type']
            async with httpx.AsyncClient(verify=self.ssl_context,transport=self.transport,trust_env=False,
                    follow_redirects=False,timeout=httpx.Timeout(20,connect=5)) as client:
                async with client.stream(request.method,self.config['upstream']+target,content=body,headers=headers,
                        extensions={'sni_hostname':self.config['server_name']}) as response:
                    if response.headers.get('x-platform-tool-bridge')!='1' or response.status_code in range(300,400) or response.status_code==401:
                        raise Fault('tool_platform_auth_unavailable',503)
                    if response.headers.get('content-encoding','identity')!='identity':raise Fault('tool_response_invalid',502)
                    raw=bytearray()
                    async for chunk in response.aiter_raw():
                        raw.extend(chunk)
                        if len(raw)>MAX_RESPONSE:raise Fault('tool_response_too_large',502)
                    outgoing={k:v for k,v in response.headers.items() if k.lower() in ('content-type','content-disposition')}
                    if outgoing.get('content-type','').startswith('text/html'):
                        page=bytes(raw).decode('utf-8')
                        page,count=re.subn(r'(<head(?:\s[^>]*)?>)',r'\1<link rel="stylesheet" href="'+self.prefix+'/__bridge.css"><script src="'+self.prefix+'/__bridge.js"></script>',page,count=1,flags=re.I)
                        if count!=1:raise Fault('tool_document_invalid',502)
                        raw=page.encode('utf-8')
                    return Response(bytes(raw),status_code=response.status_code,headers=outgoing)
        except (httpx.HTTPError,IdentityError,OSError,UnicodeError):
            raise Fault('tool_outcome_unknown' if request.method=='POST' else 'tool_unavailable',502) from None


class CourtProxy(ToolProxy):
    """4 MiB signed parts; streamed media. Never buffer an entire video upload."""
    kind='court-transcript'
    request_maximum=4*1024*1024
    methods=['GET','HEAD','POST','PATCH']

    def accepts(self,target):
        safe_target(target);path=urlsplit(target).path
        return (path in ('/','/api/auth/session','/api/health','/api/tasks','/api/tasks/demo','/api/uploads')
            or re.fullmatch(r'/api/uploads/[a-f0-9]{32}(?:/parts/\d{1,4}|/finish|/abort)?',path) is not None
            or re.fullmatch(r'/api/tasks/[a-f0-9]{32}(?:/[A-Za-z0-9_.-]+){0,4}',path) is not None
            or re.fullmatch(r'/static/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.(?:css|js|svg|png|jpg|ico|woff2)',path) is not None)

    async def forward(self,request,who,csrf):
        try:
            raw=request.scope['raw_path'].decode('ascii')
            if not raw.startswith(self.prefix+'/'):raise IdentityError('invalid_path')
            target=raw[len(self.prefix):]
            if request.scope['query_string']:target+='?'+request.scope['query_string'].decode('ascii')
            if not self.accepts(target):raise Fault('not_found',404)
            # The full-file legacy upload is deliberately unavailable through this proxy.
            if request.method=='POST' and urlsplit(target).path=='/api/tasks':raise Fault('use_signed_upload_parts',413)
        except (IdentityError,UnicodeError):raise Fault('tool_path_invalid',400) from None
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>self.request_maximum:raise Fault('body_too_large',413)
        body=bytes(body)
        if request.method in ('GET','HEAD') and body:raise Fault('tool_body_not_allowed',400)
        principal={'id':who['user']['id'],'username':who['account']['username'],'role':who['user']['role']}
        client=None;response=None;owns_stream=False
        try:
            token=await asyncio.to_thread(issue,self.config['private_key'],'tool:'+self.tool_id,principal,csrf,
                request.method,target,body,openssl=self.openssl)
            headers={'Accept-Encoding':'identity','X-Platform-Tool-Identity':token}
            if request.headers.get('content-type'):headers['Content-Type']=request.headers['content-type']
            byte_range=request.headers.get('range')
            if byte_range:
                if request.method not in ('GET','HEAD') or not re.fullmatch(r'bytes=(?:\d+-\d*|-\d+)',byte_range):
                    raise Fault('invalid_media_range',416)
                headers['Range']=byte_range
            client=httpx.AsyncClient(verify=self.ssl_context,transport=self.transport,trust_env=False,
                follow_redirects=False,timeout=httpx.Timeout(180,connect=5))
            outgoing=client.build_request(request.method,self.config['upstream']+target,content=body,headers=headers,
                extensions={'sni_hostname':self.config['server_name']})
            response=await client.send(outgoing,stream=True)
            if (response.headers.get('x-platform-tool-bridge')!='1' or response.status_code in range(300,400)
                    or response.status_code==401):raise Fault('tool_platform_auth_unavailable',503)
            if response.headers.get('content-encoding','identity')!='identity':raise Fault('tool_response_invalid',502)
            selected={k:v for k,v in response.headers.items() if k.lower() in
                ('content-type','content-disposition','content-length','content-range','accept-ranges')}
            if selected.get('content-type','').startswith('text/html'):
                page=bytearray()
                async for chunk in response.aiter_raw():
                    page.extend(chunk)
                    if len(page)>MAX_RESPONSE:raise Fault('tool_response_too_large',502)
                html,count=re.subn(r'(<head(?:\s[^>]*)?>)',r'\1<link rel="stylesheet" href="'+self.prefix+
                    '/__bridge.css"><script src="'+self.prefix+'/__bridge.js"></script>',page.decode('utf-8'),count=1,flags=re.I)
                if count!=1:raise Fault('tool_document_invalid',502)
                selected.pop('content-length',None)
                return Response(html,status_code=response.status_code,headers=selected)
            async def chunks():
                used=0
                try:
                    async for chunk in response.aiter_raw():
                        used+=len(chunk)
                        if used>5*1024**3:raise ValueError('tool response exceeds configured media bound')
                        yield chunk
                finally:
                    await response.aclose();await client.aclose()
            result=StreamingResponse(chunks(),status_code=response.status_code,headers=selected)
            owns_stream=True
            return result
        except (httpx.HTTPError,IdentityError,OSError,UnicodeError):
            raise Fault('tool_outcome_unknown' if request.method in ('POST','PATCH') else 'tool_unavailable',502) from None
        finally:
            # A streaming response retains these resources until consumption/disconnect.
            if not owns_stream:
                if response is not None:await response.aclose()
                if client is not None:await client.aclose()


def mount_tool_proxy(app,store,web,proxy):
    login='/?next='+quote(proxy.prefix+'/',safe='')

    @app.api_route(proxy.prefix,methods=['GET','HEAD'])
    @app.api_route(proxy.prefix+'/{tail:path}',methods=proxy.methods)
    async def tool(request:Request):
        try:who=web.identity(request)
        except Fault as error:
            if error.status==401:
                if request.method in ('GET','HEAD') and '/api/' not in request.url.path:
                    return RedirectResponse(login,status_code=303)
                return JSONResponse({'error':{'code':error.code},'login_url':login},status_code=401)
            raise
        if request.url.path==proxy.prefix:return RedirectResponse(proxy.prefix+'/',status_code=307)
        if request.url.path in (proxy.prefix+'/__bridge.js',proxy.prefix+'/__bridge.css'):
            if request.method!='GET':raise Fault('not_found',404)
            css=request.url.path.endswith('.css')
            return Response((Path(__file__).parent/('static/tool_bridge.css' if css else 'static/tool_bridge.js')).read_bytes(),
                            media_type='text/css' if css else 'application/javascript')
        if request.url.path==proxy.prefix+'/api/auth/logout':
            if request.method!='POST':raise Fault('not_found',404)
            with store.tx() as db:db.execute('DELETE FROM web_sessions WHERE token_hash=?',(who['session'],))
            response=JSONResponse({'ok':True})
            response.delete_cookie(web.cookie,path='/',secure=web.secure,httponly=True,samesite='lax')
            return response
        if request.url.path.endswith(('/login','/login.html','/api/auth/login')):
            return RedirectResponse(proxy.prefix+'/',status_code=303)
        session=web.session(request)
        if not session:raise Fault('session_expired',401)
        return await proxy.forward(request,who,session['csrf'])
