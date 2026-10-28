"""Client-side workflow lease for harness and SimCourt.

The process running the workflow must stay online. Only the gateway's async
call endpoint runs accepted inference independently of this client process.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import secrets
import stat
import threading
import time
from urllib.parse import urlsplit

import httpx

# Keep this client independently deployable on Python 3.10 without the gateway.
GENERATION_CLIENT_SECONDS = 660


class TaskError(RuntimeError):
    pass


class TaskClient:
    def __init__(self,base_url,api_key,*,transport=None):
        parsed=urlsplit(base_url)
        if (parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path.rstrip('/')!='/v1'
                or parsed.scheme=='http' and parsed.hostname not in ('127.0.0.1','localhost','::1')):
            raise ValueError('Use verified HTTPS or a loopback /v1 base URL')
        self.http=httpx.Client(base_url=base_url.rstrip('/'),headers={'Authorization':'Bearer '+api_key},
            timeout=httpx.Timeout(GENERATION_CLIENT_SECONDS,connect=5),follow_redirects=False,trust_env=False,transport=transport)

    @classmethod
    def from_file(cls,path):
        path=Path(path);entry=path.lstat()
        if not stat.S_ISREG(entry.st_mode) or entry.st_nlink!=1 or entry.st_size>4096:
            raise ValueError('Invalid private task-client config')
        if __import__('os').name!='nt' and (entry.st_uid!=__import__('os').getuid() or stat.S_IMODE(entry.st_mode)!=0o600):
            raise ValueError('Task-client config must be owned and mode 0600')
        config=json.loads(path.read_text())
        if set(config)!={'base_url','api_key'}:raise ValueError('Invalid task-client config fields')
        return cls(config['base_url'],config['api_key'])

    def request(self,method,path,**kwargs):
        kwargs.setdefault('timeout',httpx.Timeout(GENERATION_CLIENT_SECONDS if path=='/chat/completions' else 10,connect=5))
        try:response=self.http.request(method,path,**kwargs)
        except httpx.HTTPError:raise TaskError('transport_failed_outcome_unknown; do not automatically resubmit') from None
        if not response.is_success:
            try:code=response.json()['error']['code']
            except (ValueError,KeyError,TypeError):code='gateway_request_failed'
            raise TaskError(str(code)[:100])
        return response.json()

    @contextmanager
    def workflow(self,model,*,kind='harness',label='模型任务',wait_seconds=1800):
        task=Workflow(self,model,kind,label)
        try:
            task.wait(wait_seconds)
            yield task
        except BaseException:
            # Explicit cancellation is idempotent. Never resubmit an unknown call.
            try:task.cancel()
            except TaskError:pass
            raise
        else:task.finish()
        finally:task.close()

    def close(self):self.http.close()


class Workflow:
    def __init__(self,client,model,kind,label):
        self.client,self.model=client,model
        job=client.request('POST','/jobs',json={'model':model,'kind':kind,'label':label},
                           headers={'Idempotency-Key':'workflow-'+secrets.token_hex(16)})
        self.id=job['id'];self.stop=threading.Event();self.failed=threading.Event()
        self.thread=threading.Thread(target=self.heartbeat,daemon=True);self.thread.start()

    def heartbeat(self):
        while not self.stop.wait(30):
            try:self.client.request('POST','/jobs/'+self.id+'/heartbeat',json={})
            except TaskError:self.failed.set();return

    def wait(self,seconds):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if self.failed.is_set():raise TaskError('workflow_heartbeat_lost')
            job=self.client.request('GET','/jobs/'+self.id)
            if job['status']=='running':return
            if job['status'] not in ('queued','waiting_model'):raise TaskError('workflow_'+job['status'])
            if self.stop.wait(.5):raise TaskError('workflow_closed')
        raise TaskError('workflow_queue_timeout')

    @property
    def headers(self):
        if self.failed.is_set():raise TaskError('workflow_heartbeat_lost')
        return {'X-Task-ID':self.id}

    def chat(self,messages,**options):
        return self.client.request('POST','/chat/completions',json={'model':self.model,'messages':messages,**options},
            headers={**self.headers,'Idempotency-Key':'step-'+secrets.token_hex(16)})

    def finish(self):return self.client.request('POST','/jobs/'+self.id+'/finish',json={})
    def cancel(self):return self.client.request('POST','/jobs/'+self.id+'/cancel',json={})
    def close(self):self.stop.set();self.thread.join(timeout=12)
