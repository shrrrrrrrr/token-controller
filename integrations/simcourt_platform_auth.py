"""Platform-authenticated SimCourt handler, separate from legacy account data.

This login/ownership slice keeps trial submissions closed until the delegated
unified-queue transport is installed. Never falls back to the legacy login.
"""
from contextlib import closing
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from urllib.parse import quote, urlsplit

from .platform_identity import IdentityError, checked_file, require, safe_target, verify

BRIDGE_HEADER='X-Platform-Tool-Bridge'


class Ownership:
    def __init__(self,directory):
        self.directory=Path(directory)
        entry=self.directory.lstat()
        require(self.directory.resolve()==self.directory and stat.S_ISDIR(entry.st_mode))
        if os.name!='nt':require(entry.st_uid==os.geteuid() and stat.S_IMODE(entry.st_mode)==0o700)
        self.path=self.directory/'owners.sqlite'
        try:os.close(os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600))
        except FileExistsError:pass
        checked_file(self.path,private=True,maximum=64*1024*1024)
        with closing(self.connect()) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS owners(job_id TEXT PRIMARY KEY,user_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS nonces(nonce TEXT PRIMARY KEY,expires INTEGER NOT NULL);''')

    def connect(self):return sqlite3.connect(self.path,timeout=3)

    def consume(self,nonce,expires):
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM nonces WHERE expires<=?',(time.time(),))
            if db.execute('SELECT COUNT(*) FROM nonces').fetchone()[0]>=8192:raise IdentityError('tool_identity_busy')
            try:db.execute('INSERT INTO nonces VALUES(?,?)',(nonce,expires))
            except sqlite3.IntegrityError:return False
        return True

    def owner(self,job):
        with closing(self.connect()) as db:
            row=db.execute('SELECT user_id FROM owners WHERE job_id=?',(job,)).fetchone()
            return row[0] if row else None

    def claim(self,job,user):
        require(re.fullmatch('[a-f0-9]{32}',job) and re.fullmatch('usr_[a-f0-9]{32}',user))
        with closing(self.connect()) as db,db:
            db.execute('INSERT INTO owners VALUES(?,?)',(job,user))


def load_config(path):
    value=json.loads(checked_file(path,private=True,maximum=8192))
    require(isinstance(value,dict) and set(value)=={'tool_id','platform_origin','public_key','legacy_admin_read'})
    require(isinstance(value['tool_id'],str) and re.fullmatch('[a-z][a-z0-9-]{0,39}',value['tool_id']))
    origin=urlsplit(value['platform_origin'])
    require(origin.scheme=='https' and origin.hostname and not origin.username and not origin.password
            and origin.path=='' and not origin.query and not origin.fragment)
    require(type(value['legacy_admin_read']) is bool)
    checked_file(value['public_key'])
    return value


def make_handler(base,manager,state,*,config=None,openssl='/usr/bin/openssl'):
    config=load_config(Path(state)/'platform-sso.json') if config is None else config
    ownership=Ownership(Path(state)/'platform-sso')
    prefix='/tools/'+config['tool_id']
    login=config['platform_origin']+'/?next='+quote(prefix+'/',safe='')
    audience='tool:'+config['tool_id']

    class PlatformHandler(base):
        def setup(self):
            self.request.settimeout(10)
            super().setup()

        def log_message(self,*unused):pass

        def end_headers(self):
            self.send_header(BRIDGE_HEADER,'1')
            self.send_header('Cache-Control','no-store')
            super().end_headers()

        def denied(self,status,code):
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'error':code,'login_required':status==401,'login_url':login}).encode())

        def authenticated(self):
            self._user=None
            try:
                token=self.headers.get('X-Platform-Tool-Identity','')
                if not token:
                    if self.command in ('GET','HEAD') and not urlsplit(self.path).path.startswith('/api/'):
                        self.send_response(303);self.send_header('Location',login);self.end_headers()
                    else:self.denied(401,'platform_session_required')
                    return False
                require(len(token)<=6000)
                body=b''
                if self.command=='POST':
                    length=self.headers.get('Content-Length','0')
                    require(length.isdigit() and int(length)<=262144)
                    body=self.rfile.read(int(length));require(len(body)==int(length))
                    self.rfile=io.BytesIO(body)
                claims=verify(config['public_key'],token,audience,self.command,safe_target(self.path),body,
                              workdir=ownership.directory,openssl=openssl)
                require(ownership.consume(claims['nonce'],claims['exp']))
                self._user={'id':claims['sub'],'username':claims['username'],'role':claims['role'],'csrf':claims['csrf']}
                return True
            except (IdentityError,ValueError,OSError,sqlite3.Error):
                self.denied(401,'platform_session_required');return False

        def allowed(self,job):
            owner=ownership.owner(job)
            return owner==self._user['id'] or (self._user['role']=='admin' and (owner is not None or config['legacy_admin_read']))

        def respond(self,value,status=200):
            path=urlsplit(self.path).path
            if self._user and path=='/api/history' and status==200:
                if not isinstance(value,dict):return self.denied(502,'history_shape_unavailable')
                # Unknown/legacy ownership is never guessed from an account name.
                value={k:[item for item in v if isinstance(item,dict)
                          and isinstance(item.get('id',item.get('job_id')),str)
                          and re.fullmatch('[a-f0-9]{32}',item.get('id',item.get('job_id')))
                          and self.allowed(item.get('id',item.get('job_id')))] if isinstance(v,list) else v
                       for k,v in value.items() if isinstance(v,list) or type(v) in (int,bool,type(None))}
            return super().respond(value,status)

        def do_GET(self):
            if not self.authenticated():return
            path=urlsplit(self.path).path
            if path=='/api/auth/me':
                return self.respond({'username':self._user['username'],'role':self._user['role'],
                                     'csrf':self._user['csrf'],'csrf_token':self._user['csrf'],'authenticated':True})
            if path.startswith('/api/auth/'):
                return self.denied(403,'use_platform_account')
            match=re.fullmatch('/api/jobs/([a-f0-9]{32})(?:/export)?',path)
            if path.startswith('/api/jobs/') and not match:return self.denied(404,'job_not_found')
            if match and not self.allowed(match[1]):return self.denied(404,'job_not_found')
            return super().do_GET()

        def do_HEAD(self):
            if not self.authenticated():return
            self.send_response(405);self.end_headers()

        def do_POST(self):
            if not self.authenticated():return
            path=urlsplit(self.path).path
            if path.startswith('/api/auth/'):return self.denied(403,'use_platform_account')
            # Explicit hold, independently of the legacy application's limits.
            if path in ('/api/jobs','/api/replay'):
                return self.denied(503,'court_queue_integration_pending')
            match=re.fullmatch('/api/jobs/([a-f0-9]{32})/stop',path)
            if not match or not self.allowed(match[1]):return self.denied(404,'job_not_found')
            return super().do_POST()

    PlatformHandler.platform_ownership=ownership
    return PlatformHandler
