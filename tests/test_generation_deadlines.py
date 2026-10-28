"""Exercise actual transports without waiting minutes in the test suite."""
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from gateway.protocols import completion_chunks, sse
from gateway.store import Fault
from gateway.timeouts import CLIENT_SECONDS, GENERATION_SECONDS
from gateway.upstream import CloudText
from integrations.task_client import GENERATION_CLIENT_SECONDS, TaskClient, TaskError


def completion():
    return {'id':'synthetic','model':'synthetic-engine','created':1,
            'choices':[{'index':0,'message':{'role':'assistant','content':'done'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':14583,'completion_tokens':9,'total_tokens':14592}}


@pytest.mark.parametrize('stream',[False,True])
def test_long_generation_has_bounded_deadline_and_same_read_allowance(tmp_path,monkeypatch,stream):
    deadlines=[];requests=[]
    real_timeout=asyncio.timeout
    @asynccontextmanager
    async def observed(seconds):
        deadlines.append(seconds)
        async with real_timeout(seconds):yield
    monkeypatch.setattr('gateway.upstream.asyncio.timeout',observed)
    def upstream(request):
        requests.append(request)
        assert request.extensions['timeout']['read']==600
        if stream:
            return httpx.Response(200,text=''.join(sse(c) for c in completion_chunks(completion()))+'data: [DONE]\n\n',
                                  headers={'content-type':'text/event-stream'})
        return httpx.Response(200,json=completion())
    path=tmp_path/'upstream-api.key';path.write_text('synthetic_credential_for_test_only');path.chmod(0o600)
    provider=CloudText({'alias':'synthetic-model','served_model':'synthetic-engine',
                        'base_url':'http://127.0.0.1:18080/v1'},tmp_path,transport=httpx.MockTransport(upstream))
    body={'model':'synthetic-model','messages':[{'role':'user','content':'synthetic'}],'max_tokens':512}
    async def run():
        if stream:return [item async for item in provider.stream(body)]
        return await provider.complete(body)
    assert asyncio.run(run())
    assert deadlines==[600] and len(requests)==1
    assert CLIENT_SECONDS==GENERATION_CLIENT_SECONDS>=GENERATION_SECONDS+25+30


def test_health_probe_stays_short_and_unknown_generation_never_retries(tmp_path):
    attempts=[]
    def upstream(request):
        attempts.append(request.method)
        if request.method=='GET':
            assert request.extensions['timeout']['read']==8
            return httpx.Response(200,json={'data':[{'id':'synthetic-engine'}]})
        raise httpx.ReadTimeout('private diagnostic')
    provider=CloudText({'alias':'synthetic','served_model':'synthetic-engine',
                       'base_url':'http://127.0.0.1:18080/v1'},tmp_path,transport=httpx.MockTransport(upstream))
    asyncio.run(provider.probe('synthetic_credential_for_test_only'))
    with pytest.raises(Fault,match='upstream_outcome_unknown'):
        asyncio.run(provider._request('POST','/chat/completions','synthetic_credential_for_test_only',{}))
    assert attempts==['GET','POST']


def test_workflow_client_keeps_heartbeat_short_and_generation_long_without_retry():
    paths=[]
    def upstream(request):
        paths.append(request.url.path)
        if request.url.path.endswith('/heartbeat'):
            assert request.extensions['timeout']['read']==10
            return httpx.Response(200,json={'status':'running'})
        assert request.extensions['timeout']['read']==660
        raise httpx.ReadTimeout('private diagnostic')
    client=TaskClient('https://models.example.test/v1','synthetic-key',transport=httpx.MockTransport(upstream))
    try:
        client.request('POST','/jobs/synthetic/heartbeat',json={})
        with pytest.raises(TaskError,match='outcome_unknown'):
            client.request('POST','/chat/completions',json={})
    finally:client.close()
    assert len(paths)==2
