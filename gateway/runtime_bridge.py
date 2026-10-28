"""Persistent gateway-side switching coordinator; GPU commands stay in the agent."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit

import httpx

from .store import Fault, canonical

BUSY = ('queued','draining','stopping','starting','verifying','routing','recovery_required')


class AgentClient:
    def __init__(self, url, token, transport=None):
        parsed=urlsplit(url)
        if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not parsed.port or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username is not None or parsed.password is not None:
            raise ValueError('runtime agent must use a fixed IPv4 loopback endpoint')
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',token):
            raise ValueError('invalid runtime agent credential')
        self.url,self.token,self.transport=url.rstrip('/'),token,transport

    async def request(self, method, path, body=None):
        try:
            async with httpx.AsyncClient(base_url=self.url,headers={'Authorization':'Bearer '+self.token},
                    timeout=5,trust_env=False,follow_redirects=False,transport=self.transport) as client:
                response=await client.request(method,path,json=body)
                if response.status_code>=400:
                    raise Fault('runtime_agent_refused',response.status_code)
                if len(response.content)>65536:
                    raise Fault('runtime_agent_invalid_response',502)
                result=response.json()
                if not isinstance(result,dict):
                    raise ValueError
                return result
        except (httpx.HTTPError,ValueError):
            raise Fault('runtime_agent_unreachable',503) from None


class RuntimeBridge:
    def __init__(self,store,provider,*,client=None,simulation=False):
        self.store,self.provider,self.client,self.simulation=store,provider,client,simulation
        self.lock=asyncio.Lock()
        self.cached=None
        with store.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS model_switches (id TEXT PRIMARY KEY,model TEXT NOT NULL,action TEXT NOT NULL,'
                       'actor TEXT NOT NULL,phase TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,error TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS console_runtime (id INTEGER PRIMARY KEY CHECK(id=1),running TEXT,resident TEXT NOT NULL)')
            db.execute("INSERT OR IGNORE INTO console_runtime VALUES(1,?,'glm5.3-flash')",('qwen3-32b' if simulation else None,))
        if self.active() and hasattr(provider,'rotating'):
            provider.rotating=True

    def active(self):
        return self.store.one("SELECT * FROM model_switches WHERE phase NOT IN ('completed','failed','restored') ORDER BY created DESC LIMIT 1")

    def update(self,operation,phase,error=None):
        with self.store.tx() as db:
            db.execute('UPDATE model_switches SET phase=?,updated=?,error=? WHERE id=?',(phase,time.time(),error,operation['id']))

    async def submit(self,model,action,operation_id,actor,authorization=None,allow_resident_stop=False):
        async with self.lock:
            with self.store.tx() as db:
                if authorization is not None:
                    self.store.check_admin(db,authorization)
                old=db.execute('SELECT * FROM model_switches WHERE id=?',(operation_id,)).fetchone()
                if old:
                    if (old['model'],old['action'],old['actor'])!=(model,action,actor):
                        raise Fault('idempotency_conflict',409)
                    return dict(old)
                if not self.client and not self.simulation:
                    raise Fault('runtime_agent_not_connected',503)
                resident=db.execute('SELECT resident FROM console_runtime WHERE id=1').fetchone()[0]
                if action=='stop' and model==resident and not allow_resident_stop:
                    raise Fault('resident_service_protected',409)
                if db.execute("SELECT 1 FROM model_switches WHERE phase NOT IN ('completed','failed','restored')").fetchone():
                    raise Fault('resource_operation_locked',409)
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_jobs'").fetchone() and db.execute(
                        "SELECT 1 FROM work_jobs WHERE status IN ('running','reconciliation','cancelling')").fetchone():
                    raise Fault('active_workflows_prevent_switch',409)
                if self.simulation and model=='h3':
                    # Video switching is shown separately; no text compatibility is implied.
                    pass
                db.execute('INSERT INTO model_switches VALUES(?,?,?,?,?,?,?,NULL)',(operation_id,model,action,actor,'queued',time.time(),time.time()))
                self.store.audit(db,actor,'model.switch.submit',operation_id,dict(model=model,action=action,source='simulation' if self.simulation else 'live'))
            if hasattr(self.provider,'rotating'):
                self.provider.rotating=True
        return self.store.one('SELECT * FROM model_switches WHERE id=?',(operation_id,))

    async def tick(self):
        async with self.lock:
            op=self.active()
            if not op:
                return
            pending=self.store.one("SELECT COUNT(*) AS n FROM requests WHERE status IN ('reserved','dispatched','reconciliation')")['n']
            if pending:
                self.update(op,'draining')
                return
            if self.simulation:
                phases=['queued','draining','stopping','starting','verifying','routing']
                phase=phases[min(len(phases)-1,int((time.time()-op['created'])/0.5))]
                if time.time()-op['created']<3:
                    self.update(op,phase)
                    return
                with self.store.tx() as db:
                    db.execute('UPDATE console_runtime SET running=? WHERE id=1',(None if op['action']=='stop' else op['model'],))
                    db.execute("UPDATE model_switches SET phase='completed',updated=? WHERE id=?",(time.time(),op['id']))
                    self.store.audit(db,op['actor'],'model.switch.completed',op['id'],{'source':'simulation'})
                return
            if op['phase'] in ('queued','draining'):
                # Persist intent before sending. Unknown POST is NEVER replayed automatically.
                self.update(op,'stopping')
                try:
                    await self.client.request('POST','/operations',dict(operation_id=op['id'],model=op['model'],action=op['action'],allow_resident_stop=op['action']=='stop'))
                except Fault:
                    self.update(op,'recovery_required','agent_submission_outcome_unknown')
                    return
            try:
                state=await self.client.request('GET','/state')
                remote=state.get('operation')
                if not isinstance(remote,dict) or remote.get('id')!=op['id']:
                    raise Fault('runtime_operation_identity_mismatch',502)
                phase=remote.get('phase')
                if phase not in (*BUSY,'completed','failed','restored'):
                    raise Fault('runtime_agent_invalid_response',502)
                if phase=='completed':
                    running=state.get('running_model')
                    if running != (None if op['action']=='stop' else op['model']):
                        raise Fault('runtime_model_identity_mismatch',502)
                    if running and running!='h3':
                        config={**self.provider.config,'alias':running,'served_model':running}
                        original=dict(self.provider.config)
                        self.provider.config=config;self.provider.alias=running;self.provider.verified_at=0
                        try:
                            await self.provider.probe()
                            self.persist_route(config)
                            self.provider.profile_hash=hashlib.sha256(canonical(config).encode()).hexdigest()
                        except BaseException:
                            self.provider.config=original;self.provider.alias=original['alias'];self.provider.verified_at=0
                            raise
                    elif hasattr(self.provider,'verified_at'):
                        self.provider.verified_at=0
                    self.provider.rotating=running in (None,'h3')
                elif phase in ('failed','restored'):
                    self.provider.rotating=phase!='restored'
                self.update(op,phase,remote.get('error'))
                self.cached=state
            except Fault:
                # Polling can be retried: it is read-only and never repeats a model action.
                self.update(op,'recovery_required','runtime_verification_unavailable')

    def persist_route(self,config):
        path=Path(self.store.path).parent/'runtime-route.local.json'
        fd,name=tempfile.mkstemp(prefix='.runtime-route-',dir=path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                os.chmod(name,0o600);stream.write(canonical(config));stream.flush();os.fsync(stream.fileno())
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)

    async def snapshot(self):
        if not self.simulation and not self.client:
            return None
        if self.simulation:
            state=self.store.one('SELECT * FROM console_runtime WHERE id=1')
            return dict(source='simulation',running_model=state['running'],resident_model=state['resident'],
                        models={model:{'configured':True} for model in ('glm5.3-flash','qwen3.8-flash-next','qwen3-32b','h3')},
                        operation=self.active(),last_operation=self.store.one('SELECT * FROM model_switches ORDER BY created DESC LIMIT 1'))
        try:
            state=await self.client.request('GET','/state')
            resident=state.get('resident_model')
            if isinstance(resident,str) and re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,95}',resident):
                with self.store.tx() as db:db.execute('UPDATE console_runtime SET resident=? WHERE id=1',(resident,))
            return {**state,'source':'live','operation':self.active(),
                    'last_operation':self.store.one('SELECT * FROM model_switches ORDER BY created DESC LIMIT 1')}
        except Fault:
            return {**(self.cached or {}),'source':'unavailable','operation':self.active(),'error':'runtime_agent_unreachable'}
