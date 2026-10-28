import json

import httpx

from integrations.task_client import TaskClient
from gateway.store import canonical


def test_workflow_lease_reuses_one_task_id_across_simcourt_steps():
    calls=[]
    def server(request):
        calls.append(request)
        if request.url.path=='/v1/jobs':return httpx.Response(202,json={'id':'work_synthetic'})
        if request.method=='GET':return httpx.Response(200,json={'id':'work_synthetic','status':'running'})
        if request.url.path=='/v1/chat/completions':
            assert request.headers['x-task-id']=='work_synthetic'
            return httpx.Response(200,json={'choices':[{'message':{'content':'synthetic'}}]})
        return httpx.Response(200,json={'status':'completed'})
    client=TaskClient('https://models.example.test/v1','fixture_member_key_only',transport=httpx.MockTransport(server))
    with client.workflow('research-text',kind='simcourt') as task:
        task.chat([{'role':'user','content':'first stage'}])
        task.chat([{'role':'user','content':'second stage'}])
    client.close()
    assert len([r for r in calls if r.url.path=='/v1/jobs'])==1
    assert any(r.url.path.endswith('/finish') for r in calls)
    assert json.loads(calls[0].content)['kind']=='simcourt'


def test_official_openai_sdk_parses_streamed_tool_calls_and_usage(work):
    # Reuse the real ASGI gateway and upstream simulator, not a hand-written API reply.
    from openai import OpenAI
    store,test_client,headers,*_=work
    def gateway(request):
        response=test_client.request(request.method,request.url.path,
            headers={**dict(request.headers),'host':'models.example.test'},content=request.content)
        return httpx.Response(response.status_code,content=response.content,headers=dict(response.headers))
    client=OpenAI(api_key=headers[0]['Authorization'][7:],base_url='https://models.example.test/v1',max_retries=0,
                  http_client=httpx.Client(transport=httpx.MockTransport(gateway)))
    stream=client.chat.completions.create(model='research-text',messages=[{'role':'user','content':'synthetic'}],
        tools=[{'type':'function','function':{'name':'echo','parameters':{'type':'object','properties':{}}}}],
        max_completion_tokens=128,stream=True,stream_options={'include_usage':True})
    chunks=list(stream)
    assert chunks[0].choices[0].delta.tool_calls[0].function.name=='echo'
    assert chunks[-1].usage.total_tokens==10
    client.close()


# pytest discovers fixture symbols imported by a module.
from test_work_queue import work


def test_simcourt_identity_mapping_rejects_unlinked_principal(tmp_path):
    import pytest
    from integrations.simcourt_queue import linked_court_task
    from integrations.task_client import TaskError
    mapping=tmp_path/'mapping.json';mapping.write_text('{"principals":{}}');mapping.chmod(0o600)
    with pytest.raises(TaskError,match='identity_not_linked'):
        with linked_court_task(mapping,'unlinked-authenticated-user','research-text'):
            raise AssertionError('must not start a task')


def test_sdk_does_not_retry_an_unknown_submission_even_with_default_retries(work):
    import pytest
    from openai import OpenAI, APIStatusError
    store,test_client,headers,users,state,*_=work
    state['fail']=True
    def gateway(request):
        response=test_client.request(request.method,request.url.path,
            headers={**dict(request.headers),'host':'models.example.test'},content=request.content)
        return httpx.Response(response.status_code,content=response.content,headers=dict(response.headers))
    client=OpenAI(api_key=headers[0]['Authorization'][7:],base_url='https://models.example.test/v1',max_retries=2,
                  http_client=httpx.Client(transport=httpx.MockTransport(gateway)))
    with pytest.raises(APIStatusError):
        client.chat.completions.create(model='research-text',messages=[{'role':'user','content':'synthetic'}],max_tokens=128)
    assert state['posts']==1
    # A caller that ignores the advisory cannot create a new implicit retry task.
    response=test_client.post('/v1/chat/completions',headers=headers[0],json={'model':'research-text','messages':[{'role':'user','content':'synthetic'}]})
    assert response.status_code==409 and response.headers['x-should-retry']=='false'
    assert state['posts']==1
    client.close()
