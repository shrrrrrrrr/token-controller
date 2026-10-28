"""One persistent workflow queue and bounded inference dispatcher.

Only one gateway process may own a state directory. No shell or tool execution.
Queued work survives restart; dispatched work is never automatically replayed.
"""
import asyncio
from contextlib import suppress
import json
import os
import stat
import time

from fastapi import Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field

from .protocols import Chat, Input, StreamAccumulator, completion_chunks, sse, validate_completion
from .store import Fault, canonical, current_identity, digest, uid

ACTIVE=('running','reconciliation','cancelling')
TERMINAL=('completed','failed','cancelled','expired')
SCHEMA="""
CREATE TABLE IF NOT EXISTS work_jobs (
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),key_id TEXT NOT NULL REFERENCES keys(id),
 idem TEXT NOT NULL,body_hash TEXT NOT NULL,model TEXT NOT NULL,kind TEXT NOT NULL,label TEXT NOT NULL,
 implicit INTEGER NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,
 lease_until REAL NOT NULL,started REAL,finished REAL,error TEXT,UNIQUE(key_id,idem));
CREATE INDEX IF NOT EXISTS work_jobs_queue ON work_jobs(status,created);
CREATE TABLE IF NOT EXISTS work_calls (
 id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES work_jobs(id),key_id TEXT NOT NULL REFERENCES keys(id),
 idem TEXT NOT NULL,body_hash TEXT NOT NULL,body TEXT,status TEXT NOT NULL,
 request_id TEXT REFERENCES requests(id),result TEXT,error TEXT,created REAL NOT NULL,updated REAL NOT NULL,
 UNIQUE(key_id,idem));
CREATE INDEX IF NOT EXISTS work_calls_queue ON work_calls(status,created);
CREATE TABLE IF NOT EXISTS work_fairness(user_id TEXT PRIMARY KEY,last_started REAL NOT NULL);
"""


class JobInput(Input):
    model: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')
    kind: str = Field(default='harness',pattern=r'^(harness|simcourt)$')
    label: str = Field(default='模型任务',min_length=1,max_length=80)


class WorkQueue:
    def __init__(self,store,provider,resolve,registered,*,slots=1,queue_wait=25):
        if type(slots) is not int or not 1<=slots<=3:raise ValueError('inference slots must be 1..3')
        self.store,self.provider,self.resolve,self.registered=store,provider,resolve,registered
        self.slots,self.queue_wait=slots,queue_wait
        self.runners={};self.events={};self.subscribers={};self.stopped=False;self.task=None;self.loop_ref=None;self.owner=None
        with store.connect() as db:db.executescript(SCHEMA)

    def who(self,key_id):
        key=self.store.one('SELECT * FROM keys WHERE id=?',(key_id,))
        if not key:raise Fault('invalid_or_expired_key',401)
        user=self.store.one('SELECT * FROM users WHERE id=?',(key['user_id'],))
        return {'user':user,'key':key}

    def authorize(self,db,who,model=None,*,budget=True):
        user,key=current_identity(db,who)
        if user['role']!='caller':raise Fault('member_calling_key_required',403)
        if model and (model not in json.loads(user['models']) or model not in json.loads(key['models'])):
            raise Fault('model_forbidden',403)
        if model and model not in {x['id'] for x in self.registered() if x['kind']=='text'}:
            raise Fault('model_not_ready_or_draining',503)
        if budget and any(row['budget']>=0 and row['spent']+row['reserved']>=row['budget'] for row in (user,key)):
            raise Fault('insufficient_quota',402)
        return user,key

    def owned(self,db,job_id,who,*,write=False):
        user,_=current_identity(db,who)
        job=db.execute('SELECT * FROM work_jobs WHERE id=?',(job_id,)).fetchone()
        if not job or (job['user_id']!=user['id'] and user['role']!='admin'):
            raise Fault('task_not_found',404)
        if write and user['role']!='admin' and 'session' not in who and job['key_id']!=who['key']['id']:
            raise Fault('task_not_found',404)
        return dict(job)

    def create(self,who,body,idem,*,implicit=False):
        if not idem:idem=uid('auto_')
        fingerprint=digest(canonical(dict(body=body,implicit=implicit)))
        now=time.time()
        with self.store.tx() as db:
            user,key=self.authorize(db,who,body['model'])
            old=db.execute('SELECT * FROM work_jobs WHERE key_id=? AND idem=?',(key['id'],idem)).fetchone()
            if old:
                if old['body_hash']!=fingerprint:raise Fault('idempotency_conflict',409)
                return dict(old)
            if db.execute("SELECT COUNT(*) FROM work_jobs WHERE status NOT IN ('completed','failed','cancelled','expired')").fetchone()[0]>=100:
                raise Fault('task_queue_full',429)
            if db.execute("SELECT COUNT(*) FROM work_jobs WHERE user_id=? AND status NOT IN ('completed','failed','cancelled','expired')",(user['id'],)).fetchone()[0]>=20:
                raise Fault('personal_task_queue_full',429)
            job_id=uid('work_')
            db.execute("INSERT INTO work_jobs VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?,?,NULL,NULL,NULL)",
                       (job_id,user['id'],key['id'],idem,fingerprint,body['model'],body.get('kind','api'),body.get('label','API 请求'),int(implicit),now,now,now+(120 if implicit else 300)))
            self.store.audit(db,user['id'],'task.create',job_id,{'kind':body.get('kind','api'),'model':body['model']})
            return dict(db.execute('SELECT * FROM work_jobs WHERE id=?',(job_id,)).fetchone())

    def enqueue(self,who,body,idem,job_id=None):
        data=body.payload();encoded=canonical(data)
        points=len(canonical({'messages':data['messages'],'tools':data.get('tools',[])}).encode())+body.max_tokens+512
        if points>16384:raise Fault('conservative_context_limit',400)
        idem=idem or uid('auto_')
        # Account-wide idempotency prevents a retry from creating a second implicit job.
        old=self.store.one('SELECT * FROM work_calls WHERE key_id=? AND idem=?',(who['key']['id'],idem))
        fingerprint=digest(canonical({'body':data,'task_id':job_id}))
        if old:
            with self.store.connect() as db:self.authorize(db,who,body.model,budget=False)
            if old['body_hash']!=fingerprint:raise Fault('idempotency_conflict',409)
            return old
        if self.store.one("SELECT 1 FROM requests WHERE user_id=? AND kind='chat' AND status='reconciliation' LIMIT 1",(who['user']['id'],)):
            raise Fault('request_reconciliation',409)
        job=self.create(who,{'model':body.model,'kind':'api','label':'API 请求'},'call:'+idem,implicit=True) if not job_id else None
        now=time.time()
        with self.store.tx() as db:
            self.authorize(db,who,body.model)
            job=self.owned(db,job_id,who,write=True) if job_id else dict(db.execute('SELECT * FROM work_jobs WHERE id=?',(job['id'],)).fetchone())
            if job['model']!=body.model or job['status'] in TERMINAL or job['status'] in ('reconciliation','cancelling'):
                raise Fault('task_not_accepting_calls',409)
            if job['lease_until']<=now:raise Fault('task_lease_expired',409)
            if job['key_id']!=who['key']['id']:raise Fault('task_key_mismatch',403)
            if db.execute("SELECT COUNT(*) FROM work_calls WHERE job_id=? AND status IN ('queued','preparing','running','reconciliation')",(job['id'],)).fetchone()[0]>=8:
                raise Fault('task_call_queue_full',429)
            old=db.execute('SELECT * FROM work_calls WHERE key_id=? AND idem=?',(who['key']['id'],idem)).fetchone()
            if old:
                if old['body_hash']!=fingerprint:raise Fault('idempotency_conflict',409)
                return dict(old)
            call_id=uid('call_')
            db.execute("INSERT INTO work_calls VALUES(?,?,?,?,?,?,'queued',NULL,NULL,NULL,?,?)",
                       (call_id,job['id'],who['key']['id'],idem,fingerprint,encoded,now,now))
            return dict(db.execute('SELECT * FROM work_calls WHERE id=?',(call_id,)).fetchone())

    def change(self,job_id,who,action):
        now=time.time()
        with self.store.tx() as db:
            job=self.owned(db,job_id,who,write=True)
            if job['status'] in TERMINAL:return {'id':job_id,'status':job['status']}
            busy=db.execute("SELECT 1 FROM work_calls WHERE job_id=? AND status IN ('preparing','running','reconciliation')",(job_id,)).fetchone()
            if action=='heartbeat':
                if job['implicit']:raise Fault('implicit_task_has_no_lease',409)
                if job['lease_until']<=now:raise Fault('task_lease_expired',409)
                db.execute('UPDATE work_jobs SET lease_until=?,updated=? WHERE id=?',(now+300,now,job_id))
            elif action=='finish':
                if busy or db.execute("SELECT 1 FROM work_calls WHERE job_id=? AND status='queued'",(job_id,)).fetchone():
                    raise Fault('task_has_pending_calls',409)
                db.execute("UPDATE work_jobs SET status='completed',updated=?,finished=? WHERE id=?",(now,now,job_id))
            elif action=='cancel':
                db.execute("UPDATE work_calls SET status='cancelled',error='cancelled_before_dispatch',body=NULL,updated=? WHERE job_id=? AND status='queued'",(now,job_id))
                db.execute('UPDATE work_jobs SET status=?,updated=?,finished=? WHERE id=?',('cancelling' if busy else 'cancelled',now,None if busy else now,job_id))
            self.store.audit(db,who['user']['id'],'task.'+action,job_id,{})
        if action=='cancel':
            for call_id,task in list(self.runners.items()):
                row=self.store.one('SELECT job_id FROM work_calls WHERE id=?',(call_id,))
                if row and row['job_id']==job_id and self.loop_ref:self.loop_ref.call_soon_threadsafe(task.cancel)
        return self.snapshot(who,job_id)

    def snapshot(self,who,job_id=None,limit=50):
        with self.store.connect() as db:
            user,_=current_identity(db,who,allow_pending=True)
            if job_id:self.owned(db,job_id,who)
            admin=user['role']=='admin'
            rows=db.execute('SELECT j.*,u.name AS owner_name FROM work_jobs j JOIN users u ON u.id=j.user_id '+
                 ('WHERE j.id=?' if job_id else '' if admin else 'WHERE j.user_id=?')+
                 ' ORDER BY j.created DESC LIMIT ?',((job_id,) if job_id else () if admin else (user['id'],))+(limit,)).fetchall()
            queued=db.execute("SELECT j.id,j.user_id FROM work_jobs j LEFT JOIN work_fairness f ON f.user_id=j.user_id WHERE j.status IN ('queued','waiting_model') ORDER BY COALESCE(f.last_started,0),j.created,j.id").fetchall()
            counts={r[0]:r[1] for r in db.execute('SELECT status,COUNT(*) FROM work_jobs GROUP BY status')}
            result=[]
            for job in rows:
                ahead=[]
                if job['status'] in ('queued','waiting_model'):
                    for row in queued:
                        if row['id']==job['id']:break
                        ahead.append(row['user_id'])
                fields=('id','model','kind','label','status','created','updated','started','finished','error','lease_until')
                item={k:job[k] for k in fields}
                item['people_ahead']=len(set(ahead));item['tasks_ahead']=len(ahead)
                if admin:item.update(user_id=job['user_id'],owner_name=job['owner_name'])
                item['calls']=list(map(dict,db.execute('SELECT id,status,request_id,error,created,updated FROM work_calls WHERE job_id=? ORDER BY created DESC LIMIT 30',(job['id'],))))
                result.append(item)
            running=db.execute("SELECT COUNT(*) FROM work_calls WHERE status IN ('preparing','running','reconciliation')").fetchone()[0]
            return {'jobs':result,'active_tasks':sum(counts.get(s,0) for s in ACTIVE),'task_limit':3,
                    'waiting':counts.get('queued',0)+counts.get('waiting_model',0),
                    'inferences':running,'inference_limit':self.slots,'source':self.provider.source}

    def call(self,call_id,who):
        row=self.store.one('SELECT * FROM work_calls WHERE id=?',(call_id,))
        if not row:raise Fault('call_not_found',404)
        with self.store.connect() as db:self.owned(db,row['job_id'],who)
        return {k:json.loads(row[k]) if k=='result' and row[k] else row[k]
                for k in ('id','job_id','status','request_id','result','error','created','updated')}

    def recover(self):
        with self.store.tx() as db:
            # Crash between admission and receipt linkage: recover the immutable id.
            db.execute("UPDATE work_calls SET request_id=(SELECT r.id FROM requests r WHERE r.key_id=work_calls.key_id AND r.idem='work:'||work_calls.id) WHERE request_id IS NULL AND status IN ('preparing','running','reconciliation')")
            db.execute("UPDATE work_calls SET status='reconciliation',error='process_restart_unknown',updated=? WHERE status IN ('preparing','running')",(time.time(),))
            db.execute("UPDATE work_jobs SET status='reconciliation' WHERE id IN (SELECT job_id FROM work_calls WHERE status='reconciliation')")

    async def start(self):
        if self.owner is not None:return False  # Nested test clients share the owning app's dispatcher.
        fd=os.open(self.store.path.parent/'.task-queue.lock',os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        self.owner=os.fdopen(fd,'r+b')
        entry=os.fstat(fd)
        if not stat.S_ISREG(entry.st_mode) or entry.st_nlink!=1:
            self.owner.close();self.owner=None;raise ValueError('invalid task queue lock')
        try:
            if entry.st_size==0:self.owner.write(b'0');self.owner.flush()
            self.owner.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.owner.close();self.owner=None
            raise ValueError('task queue already owned; use one gateway worker') from None
        self.recover();self.stopped=False;self.loop_ref=asyncio.get_running_loop()
        self.task=asyncio.create_task(self.loop())
        return True

    async def close(self):
        self.stopped=True
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):await self.task
        for task in list(self.runners.values()):task.cancel()
        await asyncio.gather(*self.runners.values(),return_exceptions=True)
        if self.owner:self.owner.close();self.owner=None

    async def loop(self):
        while not self.stopped:
            try:await self.tick()
            except Exception:pass  # Fail closed; no private bodies or credential logs.
            await asyncio.sleep(.08)

    async def tick(self):
        now=time.time()
        # Reconciliation is resolved only by the existing request-level operator path.
        with self.store.tx() as db:
            for row in db.execute("SELECT c.id,c.job_id,r.status,r.response,j.implicit FROM work_calls c JOIN requests r ON r.id=c.request_id JOIN work_jobs j ON j.id=c.job_id WHERE c.status='reconciliation' AND r.status IN ('succeeded','failed','cancelled')").fetchall():
                known=row['status']=='succeeded' and bool(row['response'])
                db.execute('UPDATE work_calls SET status=?,result=?,body=NULL,error=?,updated=? WHERE id=?',
                           ('completed' if known else 'failed',row['response'] if known else None,None if known else 'operator_reconciled',now,row['id']))
                status=('completed' if row['implicit'] else 'running') if known else 'failed'
                db.execute('UPDATE work_jobs SET status=?,error=?,finished=?,updated=? WHERE id=?',
                           (status,None if known else 'operator_reconciled',None if status=='running' else now,now,row['job_id']))
            expired=db.execute("SELECT id FROM work_jobs WHERE status IN ('queued','waiting_model','running') AND lease_until<?",(now,)).fetchall()
            for row in expired:
                if db.execute("SELECT 1 FROM work_calls WHERE job_id=? AND status IN ('preparing','running','reconciliation')",(row['id'],)).fetchone():continue
                db.execute("UPDATE work_jobs SET status='expired',finished=?,updated=? WHERE id=?",(now,now,row['id']))
                db.execute("UPDATE work_calls SET status='cancelled',error='task_lease_expired',body=NULL,updated=? WHERE job_id=? AND status='queued'",(now,row['id']))
            # Payload/result retention is separate from permanent usage accounting.
            db.execute("UPDATE work_calls SET body=NULL,result=NULL WHERE updated<? AND status IN ('completed','failed','cancelled')",(now-7*86400,))
        candidates=self.store.rows("SELECT j.* FROM work_jobs j LEFT JOIN work_fairness f ON f.user_id=j.user_id WHERE j.status IN ('queued','waiting_model') ORDER BY COALESCE(f.last_started,0),j.created,j.id LIMIT 100")
        admitted_users=set()
        for job in candidates:
            if job['user_id'] in admitted_users:continue
            try:
                with self.store.connect() as db:self.authorize(db,self.who(job['key_id']),job['model'])
                await self.resolve(job['model'])
            except Fault as error:
                with self.store.tx() as db:
                    if error.status in (401,402,403):
                        db.execute("UPDATE work_jobs SET status='failed',error=?,updated=?,finished=? WHERE id=?",(error.code,now,now,job['id']))
                        db.execute("UPDATE work_calls SET status='failed',error=?,body=NULL,updated=? WHERE job_id=? AND status='queued'",(error.code,now,job['id']))
                    else:db.execute("UPDATE work_jobs SET status='waiting_model',updated=? WHERE id=? AND status IN ('queued','waiting_model')",(now,job['id']))
                continue
            with self.store.tx() as db:
                if db.execute("SELECT COUNT(*) FROM work_jobs WHERE status IN ('running','reconciliation','cancelling')").fetchone()[0]>=3:break
                changed=db.execute("UPDATE work_jobs SET status='running',started=?,updated=? WHERE id=? AND status IN ('queued','waiting_model')",(now,now,job['id'])).rowcount
                if changed:
                    admitted_users.add(job['user_id'])
                    db.execute('INSERT INTO work_fairness VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_started=excluded.last_started',(job['user_id'],now))
        while len(self.runners)<self.slots:
            with self.store.tx() as db:
                active=db.execute("SELECT COUNT(*) FROM work_calls WHERE status IN ('preparing','running','reconciliation')").fetchone()[0]
                legacy=db.execute("SELECT COUNT(*) FROM requests WHERE kind='chat' AND status='reconciliation' AND id NOT IN (SELECT request_id FROM work_calls WHERE request_id IS NOT NULL)").fetchone()[0]
                if active+legacy>=self.slots:break
                row=db.execute("SELECT c.* FROM work_calls c JOIN work_jobs j ON j.id=c.job_id JOIN keys k ON k.id=c.key_id LEFT JOIN work_fairness f ON f.user_id=j.user_id WHERE c.status='queued' AND j.status='running' AND NOT EXISTS(SELECT 1 FROM work_calls x WHERE x.job_id=c.job_id AND x.status IN ('preparing','running','reconciliation')) AND (SELECT COUNT(*) FROM requests r WHERE r.key_id=k.id AND r.status IN ('reserved','dispatched','reconciliation'))<k.concurrency ORDER BY COALESCE(f.last_started,0),c.created,c.id LIMIT 1").fetchone()
                if not row:break
                call=dict(row)
                db.execute("UPDATE work_calls SET status='preparing',updated=? WHERE id=? AND status='queued'",(now,call['id']))
                db.execute("UPDATE work_fairness SET last_started=? WHERE user_id=(SELECT user_id FROM work_jobs WHERE id=?)",(now,call['job_id']))
            self.events[call['id']]=[]
            task=asyncio.create_task(self.execute(call));self.runners[call['id']]=task
            task.add_done_callback(lambda task,call_id=call['id']:self.runner_done(call_id,task))

    def runner_done(self,call_id,task):
        self.runners.pop(call_id,None)
        if task.cancelled():
            with self.store.tx() as db:
                row=db.execute('SELECT * FROM work_calls WHERE id=?',(call_id,)).fetchone()
                if row and row['status']=='preparing' and row['request_id'] is None:
                    db.execute("UPDATE work_calls SET status='cancelled',body=NULL,error='cancelled_before_dispatch',updated=? WHERE id=?",(time.time(),call_id))
                    db.execute("UPDATE work_jobs SET status='cancelled',finished=?,updated=? WHERE id=? AND status='cancelling'",(time.time(),time.time(),row['job_id']))

    async def execute(self,call):
        request=None;dispatched=False;error=None
        try:
            job=self.store.one('SELECT * FROM work_jobs WHERE id=?',(call['job_id'],))
            if job['status']!='running':raise Fault('task_cancelled',409)
            body=json.loads(call['body']);who=self.who(call['key_id']);route=await self.resolve(job['model'])
            if job['status']!='running':raise Fault('task_cancelled',409)
            points=len(canonical({'messages':body['messages'],'tools':body.get('tools',[])}).encode())+body['max_tokens']+512
            request,fresh=self.store.admit(who,'work:'+call['id'],body,'chat',job['model'],points,
                route['profile'],route['profile_hash'],runtime_required=route['runtime_required'],
                global_concurrency=self.slots,required_role='caller',source=self.provider.source)
            if not fresh:raise Fault('request_already_admitted',409)
            with self.store.tx() as db:
                current=db.execute('SELECT status FROM work_jobs WHERE id=?',(job['id'],)).fetchone()[0]
                if current!='running':raise Fault('task_cancelled',409)
                db.execute("UPDATE work_calls SET request_id=?,status='running',updated=? WHERE id=?",(request['id'],time.time(),call['id']))
            if hasattr(self.provider,'probe') and hasattr(self.provider,'ready') and not self.provider.ready:
                await self.provider.probe()
            self.store.mark_dispatched(request['id']);dispatched=True
            if body.get('stream'):
                accumulator=StreamAccumulator(body['model'])
                async for chunk in self.provider.stream(body):
                    accumulator.add(chunk);self.events[call['id']].append(chunk)
                result=accumulator.result(body['max_tokens'])
            else:
                result=validate_completion(await self.provider.complete(body),body['model'],body['max_tokens'])
            result['gateway']={'request_id':request['id'],'source':self.provider.source,'profile_hash':route['profile_hash']}
            usage=result['usage']
            if not self.store.settle(request['id'],usage['total_tokens'],usage['prompt_tokens'],usage['completion_tokens'],result):
                raise Fault('usage_settlement_conflict',502)
            with self.store.tx() as db:
                db.execute("UPDATE work_calls SET status='completed',result=?,body=NULL,updated=? WHERE id=?",(canonical(result),time.time(),call['id']))
                if job['implicit']:db.execute("UPDATE work_jobs SET status='completed',finished=?,updated=? WHERE id=?",(time.time(),time.time(),job['id']))
        except BaseException as exc:
            error=exc.code if isinstance(exc,Fault) else 'task_cancelled_outcome_unknown' if isinstance(exc,asyncio.CancelledError) else 'upstream_outcome_unknown'
            unknown=dispatched
            if request:
                if unknown:self.store.unknown(request['id'],error)
                else:self.store.settle(request['id'],0,status='failed',reason='not_dispatched:'+error)
            with self.store.tx() as db:
                db.execute('UPDATE work_calls SET status=?,error=?,body=NULL,updated=? WHERE id=?',('reconciliation' if unknown else 'failed',error,time.time(),call['id']))
                db.execute('UPDATE work_jobs SET status=?,error=?,updated=?,finished=? WHERE id=?',('reconciliation' if unknown else 'failed',error,time.time(),None if unknown else time.time(),call['job_id']))
        finally:
            with self.store.tx() as db:
                if not db.execute("SELECT 1 FROM work_calls WHERE job_id=? AND status IN ('preparing','running','reconciliation')",(call['job_id'],)).fetchone():
                    db.execute("UPDATE work_jobs SET status='cancelled',finished=?,updated=? WHERE id=? AND status='cancelling'",(time.time(),time.time(),call['job_id']))
            # Bounded terminal buffers: subscribers can replay the stored completion.
            if len(self.events)>30:
                for key in list(self.events):
                    if key not in self.runners and not self.subscribers.get(key):self.events.pop(key,None)

    async def wait(self,call,who,*,stream=False):
        deadline=time.monotonic()+self.queue_wait
        while True:
            row=self.call(call['id'],who)
            if row['status'] not in ('queued','preparing'):return row
            if time.monotonic()>=deadline:
                with self.store.tx() as db:
                    changed=db.execute("UPDATE work_calls SET status='cancelled',error='queue_wait_timeout',body=NULL,updated=? WHERE id=? AND status='queued'",(time.time(),call['id'])).rowcount
                    if changed:db.execute("UPDATE work_jobs SET status='cancelled',finished=?,updated=? WHERE id=? AND implicit=1",(time.time(),time.time(),call['job_id']))
                if changed:raise Fault('queue_wait_timeout',429)
            await asyncio.sleep(.04)

    async def chat(self,body,who,idem,job_id=None):
        previous=self.store.one('SELECT status FROM work_calls WHERE key_id=? AND idem=?',(who['key']['id'],idem)) if idem else None
        if previous and previous['status']=='reconciliation':raise Fault('request_reconciliation',409)
        if not previous or previous['status']!='completed':await self.resolve(body.model)
        unknown=self.store.one("SELECT COUNT(*) n FROM requests WHERE kind='chat' AND status='reconciliation'")['n']
        if unknown>=self.slots and not previous:raise Fault('gateway_capacity_or_reconciliation_limit',429)
        call=self.enqueue(who,body,idem,job_id)
        row=await self.wait(call,who,stream=body.stream)
        headers={'X-Task-ID':call['job_id'],'X-Call-ID':call['id']}
        if call['status']=='completed':headers['X-Idempotent-Replay']='true'
        if row.get('request_id'):headers['X-Request-ID']=row['request_id']
        if body.stream:
            async def events():
                if sum(self.subscribers.values())>=30:
                    yield sse({'error':{'code':'stream_subscriber_limit'}});return
                self.subscribers[call['id']]=self.subscribers.get(call['id'],0)+1
                try:
                    cursor=0
                    while True:
                        chunks=self.events.get(call['id'],[])
                        while cursor<len(chunks):
                            chunk=chunks[cursor];cursor+=1
                            if not (chunk.get('choices')==[] and not (body.stream_options or {}).get('include_usage',False)):
                                yield sse(chunk)
                        current=self.call(call['id'],who)
                        if current['status']=='completed':
                            if cursor==0 and current['result']:
                                for chunk in completion_chunks(current['result']):
                                    if chunk.get('choices')!=[] or (body.stream_options or {}).get('include_usage',False):yield sse(chunk)
                            yield 'data: [DONE]\n\n';break
                        if current['status'] in ('failed','cancelled','reconciliation'):
                            yield sse({'error':{'code':current['error'] or 'task_failed'}});break
                        yield ': keep-alive\n\n'
                        await asyncio.sleep(.1)
                finally:
                    count=self.subscribers.get(call['id'],1)-1
                    if count:self.subscribers[call['id']]=count
                    else:self.subscribers.pop(call['id'],None)
            return StreamingResponse(events(),media_type='text/event-stream',headers={**headers,'X-Accel-Buffering':'no'})
        while row['status'] in ('queued','preparing','running'):
            await asyncio.sleep(.04);row=self.call(call['id'],who)
        if row.get('request_id'):headers['X-Request-ID']=row['request_id']
        if row['status']=='completed' and row['result']:return JSONResponse(row['result'],headers=headers)
        status=502 if row['status']=='reconciliation' else {'rate_or_concurrency_limit':429,'insufficient_quota':402}.get(row['error'],503)
        if status==429:headers['Retry-After']='10'
        if row['status']=='reconciliation':headers['X-Should-Retry']='false'
        return JSONResponse({'error':{'code':row['error'] or 'task_failed'},'request_id':row.get('request_id'),
                             'submitted_to_model':row['status']=='reconciliation'},status_code=status,headers=headers)


def mount_work_queue(app,queue,identity,api_identity):
    def caller(who=Depends(api_identity)):
        if who['user']['role']!='caller':raise Fault('member_calling_key_required',403)
        return who

    @app.post('/v1/jobs',status_code=202)
    def create(body:JobInput,who=Depends(caller),idempotency_key:str=Header(default='',max_length=120)):
        job=queue.create(who,body.model_dump(),idempotency_key)
        return queue.snapshot(who,job['id'])['jobs'][0]

    @app.get('/v1/jobs/{job_id}')
    def get(job_id:str,who=Depends(caller)):return queue.snapshot(who,job_id)['jobs'][0]

    @app.post('/v1/jobs/{job_id}/calls',status_code=202)
    def submit(job_id:str,body:Chat,who=Depends(caller),idempotency_key:str=Header(default='',max_length=120)):
        row=queue.enqueue(who,body,idempotency_key,job_id)
        return queue.call(row['id'],who)

    @app.get('/v1/calls/{call_id}')
    def result(call_id:str,who=Depends(caller)):return queue.call(call_id,who)

    @app.get('/api/jobs')
    def listing(limit:int=Query(default=50,ge=1,le=100),who=Depends(identity)):return queue.snapshot(who,limit=limit)

    @app.post('/api/jobs/{job_id}/cancel')
    def cancel(job_id:str,who=Depends(identity)):return queue.change(job_id,who,'cancel')

    @app.post('/v1/jobs/{job_id}/{action}')
    def manage(job_id:str,action:str,who=Depends(caller)):
        if action not in ('heartbeat','finish','cancel'):raise Fault('not_found',404)
        return queue.change(job_id,who,action)
