"""Isolated operator-installed agent for approved, retained Docker containers.

The public gateway never receives Docker access. Configuration and image/container
identities are supplied by the operator in a private manifest, never by web users.
No shell, image pulls, container creation, forced kills or weight deletion.
"""
import argparse
import asyncio
import hmac
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import sqlite3
import stat
import subprocess
import tempfile
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
from pydantic import BaseModel, ConfigDict, Field

from .store import Fault

PHASES=('queued','draining','stopping','starting','verifying','completed','failed','recovery_required')


def private_json(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        item=os.fstat(stream.fileno())
        if not stat.S_ISREG(item.st_mode) or item.st_nlink!=1 or item.st_size>65536 or item.st_uid!=os.geteuid() or stat.S_IMODE(item.st_mode)!=0o600:
            raise ValueError('agent configuration must be an owned mode0600 regular file')
        return json.loads(stream.read(65537))


def validate_manifest(doc):
    if not isinstance(doc,dict) or set(doc)!={'profiles','resident_model','credential_file','simcourt_config','simcourt_container'}:
        raise ValueError('invalid agent manifest fields')
    if not isinstance(doc['profiles'],dict) or not 1<=len(doc['profiles'])<=16:
        raise ValueError('invalid profile list')
    for model,p in doc['profiles'].items():
        if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,95}',model) or set(p)!={'container_id','image_id','configuration_sha256','gpus','minimum_free_mib'}:
            raise ValueError('invalid approved profile')
        if not re.fullmatch(r'[a-f0-9]{64}',p['container_id']) or not re.fullmatch(r'sha256:[a-f0-9]{64}',p['image_id']):
            raise ValueError('immutable container and image identities required')
        if not re.fullmatch(r'[a-f0-9]{64}',p['configuration_sha256']):raise ValueError('profile configuration fingerprint required')
        if not isinstance(p['gpus'],list) or not p['gpus'] or len(set(p['gpus']))!=len(p['gpus']) or any(type(i)!=int or not 0<=i<=15 for i in p['gpus']):
            raise ValueError('invalid GPUs')
        if type(p['minimum_free_mib'])!=int or not 0<=p['minimum_free_mib']<=1000000:
            raise ValueError('invalid resource margin')
    for name in ('credential_file','simcourt_config'):
        if not isinstance(doc[name],str) or not PurePosixPath(doc[name]).is_absolute():
            raise ValueError('operator must select absolute private paths')
    if not re.fullmatch(r'[a-f0-9]{64}',doc['simcourt_container']):
        raise ValueError('immutable SimCourt container identity required')
    if doc['resident_model'] not in doc['profiles']:
        raise ValueError('resident profile missing')
    return doc


class DockerDriver:
    def __init__(self,manifest):self.manifest=validate_manifest(manifest)

    def command(self,args,timeout=12):
        return subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,check=True,timeout=timeout).stdout

    def inspect(self,profile):
        p=self.manifest['profiles'][profile]
        d=json.loads(self.command(['/usr/bin/docker','--host','unix:///var/run/docker.sock','inspect','--type=container',p['container_id']]))[0]
        if d['Id']!=p['container_id'] or d['Image']!=p['image_id'] or d['HostConfig'].get('AutoRemove'):
            raise Fault('approved_container_identity_changed',409)
        configuration={name:d[name] for name in ('Config','HostConfig','Mounts')}
        fingerprint=hashlib.sha256(json.dumps(configuration,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if fingerprint!=p['configuration_sha256']:raise Fault('approved_container_configuration_changed',409)
        bindings=d['HostConfig'].get('PortBindings',{}).get('8080/tcp',[])
        if not any(b.get('HostIp')=='127.0.0.1' and b.get('HostPort')=='18080' for b in bindings):
            raise Fault('model_loopback_binding_changed',409)
        requests=d['HostConfig'].get('DeviceRequests') or []
        devices={str(i) for request in requests for i in request.get('DeviceIDs') or []}
        if devices!={str(i) for i in p['gpus']}:
            raise Fault('approved_gpu_assignment_changed',409)
        return d

    def running(self):
        running=[m for m in self.manifest['profiles'] if self.inspect(m)['State']['Running']]
        if len(running)>1:raise Fault('multiple_model_containers_running',409)
        return running[0] if running else None

    def court_config(self):
        path=Path(self.manifest['simcourt_config'])
        if path.is_symlink() or path.resolve()!=path:raise Fault('court_config_path_changed',409)
        item=path.stat()
        if not stat.S_ISREG(item.st_mode) or item.st_nlink!=1 or item.st_size>65536:
            raise Fault('court_config_metadata_changed',409)
        raw=path.read_bytes();return path,item,json.loads(raw)

    def write_court(self,doc):
        path,item,_=self.court_config();fd,name=tempfile.mkstemp(prefix='.model-control-',dir=path.parent)
        try:
            with os.fdopen(fd,'w') as f:
                os.fchown(f.fileno(),item.st_uid,item.st_gid);os.fchmod(f.fileno(),stat.S_IMODE(item.st_mode))
                json.dump(doc,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)

    def pause_court(self):
        _,_,cfg=self.court_config();old=cfg.get('max_concurrent_trials')
        if type(old)!=int or not 0<=old<=1:raise Fault('court_limit_not_recognized',409)
        self.write_court({**cfg,'max_concurrent_trials':0});return old

    def court_idle(self):
        raw=self.command(['/usr/bin/docker','--host','unix:///var/run/docker.sock','top',self.manifest['simcourt_container'],'-eo','args']).decode()
        return not any(re.search(r'(^|[ /])worker\.py(?:\s|$)',line) for line in raw.splitlines()[1:])

    def credential(self):
        fd=os.open(self.manifest['credential_file'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'r',encoding='ascii') as f:
            info=os.fstat(f.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>1024
                    or stat.S_IMODE(info.st_mode)!=0o600):raise ValueError('invalid model credential metadata')
            key=f.read(1025).strip()
        if not re.fullmatch(r'[A-Za-z0-9_.-]{24,256}',key):raise ValueError('invalid model credential')
        return key

    async def slots_idle(self):
        try:
            key=self.credential()
            async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=4) as c:
                response=await c.get('http://127.0.0.1:18080/slots',headers={'Authorization':'Bearer '+key})
                if response.status_code!=200:return False
                slots=response.json()
                return isinstance(slots,list) and bool(slots) and all(isinstance(x,dict) and x.get('is_processing') is False for x in slots)
        except (OSError,ValueError,httpx.HTTPError):return False

    def resources(self,model):
        p=self.manifest['profiles'][model]
        raw=self.command(['/usr/bin/nvidia-smi','--query-gpu=index,uuid,memory.free','--format=csv,noheader,nounits']).decode()
        devices={int(row.split(',')[0]):(row.split(',')[1].strip(),int(row.split(',')[2])) for row in raw.splitlines()}
        free={i:value[1] for i,value in devices.items()}
        if any(free.get(i,0)<p['minimum_free_mib'] for i in p['gpus']):
            raise Fault('gpu_resource_conflict',409)
        consumers=self.command(['/usr/bin/nvidia-smi','--query-compute-apps=gpu_uuid','--format=csv,noheader,nounits']).decode().splitlines()
        if {devices[i][0] for i in p['gpus']} & {row.strip() for row in consumers}:
            raise Fault('gpu_compute_process_conflict',409)
        # Foreign GPU consumers are never killed. Any occupied API port also prevents start.
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',18080))

    def stop(self,model):
        d=self.inspect(model)
        if d['State']['Running']:
            self.command(['/usr/bin/docker','--host','unix:///var/run/docker.sock','stop','--timeout','-1',d['Id']],90)
        if self.inspect(model)['State']['Running']:raise Fault('old_model_not_stopped',409)

    def start(self,model):
        d=self.inspect(model)
        if d['State']['Running']:raise Fault('target_already_running',409)
        self.command(['/usr/bin/docker','--host','unix:///var/run/docker.sock','start',d['Id']],45)

    async def healthy(self,model):
        try:
            key=self.credential()
            if not self.inspect(model)['State']['Running']:return False
            async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=4) as c:
                r=await c.get('http://127.0.0.1:18080/v1/models',headers={'Authorization':'Bearer '+key})
                return r.status_code==200 and any(m.get('id')==model for m in r.json().get('data',[]))
        except (OSError,ValueError,httpx.HTTPError):return False

    def resume_court(self,model,limit):
        _,_,cfg=self.court_config()
        if cfg.get('max_concurrent_trials')!=0:raise Fault('court_gate_changed_during_switch',409)
        if model not in ('glm5.3-flash','qwen3.8-flash-next'):return False
        self.write_court({**cfg,'model':model,'thinking':model=='glm5.3-flash','max_concurrent_trials':limit})
        return True


class OperationInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    operation_id:str=Field(pattern=r'^op_[a-f0-9]{32}$')
    model:str=Field(pattern=r'^[a-z0-9][a-z0-9._-]{0,95}$')
    action:str=Field(pattern=r'^(switch|stop)$')
    allow_resident_stop:bool=False


class Agent:
    def __init__(self,state,driver):
        self.state=Path(state);self.state.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.driver,self.task=driver,None
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY,model TEXT,action TEXT,phase TEXT,old TEXT,court_limit INTEGER,error TEXT)')
            # Never replay a lifecycle command after process restart.
            db.execute("UPDATE operations SET phase='recovery_required',error='agent_restarted' WHERE phase NOT IN ('completed','failed','recovery_required')")

    def db(self):
        db=sqlite3.connect(self.state/'agent.sqlite',timeout=5);db.row_factory=sqlite3.Row;return db

    def operation(self):
        with self.db() as db:
            row=db.execute('SELECT * FROM operations ORDER BY rowid DESC LIMIT 1').fetchone()
            return dict(row) if row else None

    def phase(self,id,phase,error=None):
        with self.db() as db:db.execute('UPDATE operations SET phase=?,error=? WHERE id=?',(phase,error,id))

    async def submit(self,body):
        if body.model not in self.driver.manifest['profiles']:raise Fault('profile_not_approved',409)
        self.driver.inspect(body.model)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM operations WHERE id=?',(body.operation_id,)).fetchone()
            if old:
                if (old['model'],old['action'])!=(body.model,body.action):raise Fault('idempotency_conflict',409)
                return dict(old)
            if db.execute("SELECT 1 FROM operations WHERE phase NOT IN ('completed','failed')").fetchone():raise Fault('resource_operation_locked',409)
            current=self.driver.running()
            if body.action=='stop' and current==self.driver.manifest['resident_model'] and not body.allow_resident_stop:
                raise Fault('resident_service_protected',409)
            if body.action=='stop' and current!=body.model:raise Fault('model_not_running',409)
            if body.action=='switch' and current==body.model:raise Fault('model_already_running',409)
            db.execute("INSERT INTO operations VALUES(?,?,?,'queued',?,NULL,NULL)",(body.operation_id,body.model,body.action,current))
        self.task=asyncio.create_task(self.run(body,current))
        return self.operation()

    async def run(self,body,old):
        id=body.operation_id
        try:
            self.phase(id,'draining')
            limit=await asyncio.to_thread(self.driver.pause_court)
            with self.db() as db:db.execute('UPDATE operations SET court_limit=? WHERE id=?',(limit,id))
            quiet=0
            for _ in range(900):
                idle=await asyncio.to_thread(self.driver.court_idle)
                if old:idle=idle and await self.driver.slots_idle()
                quiet=quiet+1 if idle else 0
                if quiet>=7:break
                await asyncio.sleep(2)
            else:raise Fault('inflight_drain_timeout',409)
            if old:
                self.phase(id,'stopping');await asyncio.to_thread(self.driver.stop,old)
            if body.action=='stop':
                self.phase(id,'completed');return
            await asyncio.to_thread(self.driver.resources,body.model)
            self.phase(id,'starting');await asyncio.to_thread(self.driver.start,body.model)
            self.phase(id,'verifying')
            for _ in range(900):
                if await self.driver.healthy(body.model):break
                await asyncio.sleep(2)
            else:raise Fault('model_health_timeout',503)
            resumed=await asyncio.to_thread(self.driver.resume_court,body.model,limit)
            self.phase(id,'completed','simcourt_adaptation_required' if resumed is False else None)
        except BaseException as error:
            # Preserve both containers, the paused court and the journal. Operators
            # can reconcile exact container states; no forced cleanup or blind rollback.
            self.phase(id,'recovery_required',error.code if isinstance(error,Fault) else 'lifecycle_outcome_unknown')
            if isinstance(error,asyncio.CancelledError):raise


def create_agent(agent,token):
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    @app.middleware('http')
    async def guard(request:Request,call_next):
        if not hmac.compare_digest(request.headers.get('authorization','').encode(),('Bearer '+token).encode()):
            return JSONResponse({'error':{'code':'agent_unauthorized'}},status_code=401)
        return await call_next(request)
    @app.exception_handler(Fault)
    async def fault(request,error):return JSONResponse({'error':{'code':error.code}},status_code=error.status)
    @app.get('/state')
    async def state():
        return {'source':'live','running_model':await asyncio.to_thread(agent.driver.running),
                'resident_model':agent.driver.manifest['resident_model'],
                'models':{m:{'configured':True} for m in agent.driver.manifest['profiles']},'operation':agent.operation()}
    @app.post('/operations',status_code=202)
    async def operation(body:OperationInput):return await agent.submit(body)
    return app


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--token-file',type=Path,required=True)
    parser.add_argument('--port',type=int,default=18090)
    args=parser.parse_args()
    if os.name!='posix' or os.geteuid()!=0:raise SystemExit('This isolated agent requires the operator-installed Linux service')
    os.umask(0o077)
    token=private_json(args.token_file)['token']
    if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',token):raise SystemExit('Invalid agent credential')
    import fcntl
    args.state.mkdir(mode=0o700,parents=True,exist_ok=True)
    state_info=args.state.lstat()
    if args.state.is_symlink() or not stat.S_ISDIR(state_info.st_mode) or state_info.st_uid!=0 or stat.S_IMODE(state_info.st_mode)!=0o700:
        raise SystemExit('Agent state must be a root-owned mode0700 directory')
    lock=(args.state/'process.lock').open('a+b');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    agent=Agent(args.state,DockerDriver(private_json(args.manifest)))
    import uvicorn
    uvicorn.run(create_agent(agent,token),host='127.0.0.1',port=args.port,workers=1,access_log=False)


if __name__=='__main__':main()
