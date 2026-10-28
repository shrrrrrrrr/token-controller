import asyncio
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.protocols import Chat, completion_chunks, sse
from gateway.store import Store
from gateway.upstream import CloudText

MODEL='research-text';SECRET='fixture_upstream_credential_only'
ORIGIN='https://models.example.test'


@pytest.fixture
def work(tmp_path):
    store=Store(tmp_path/'gateway.sqlite',seed_demo=False)
    catalog=tmp_path/'model-catalog.local.json'
    catalog.write_text(json.dumps({'models':[{'id':MODEL,'display_name':'Synthetic text','kind':'text','deployment_status':'offline'}]}))
    headers={};users=[]
    for i in range(5):
        user=store.create_user('Synthetic member '+str(i),'caller',[MODEL],[],-1);users.append(user)
        headers[i]={'Authorization':'Bearer '+store.issue_key(user,[MODEL],-1,concurrency=3)['key']}
    admin=store.create_user('Synthetic admin','admin',[MODEL],[],-1)
    headers['admin']={'Authorization':'Bearer '+store.issue_key(admin,[MODEL],-1)['key']}
    state={'posts':0,'active':0,'peak':0,'hold':False,'fail':False};gate=threading.Event();gate.set()
    async def upstream(request):
        assert request.headers['authorization']=='Bearer '+SECRET
        if request.method=='GET':return httpx.Response(200,json={'data':[{'id':'engine-fixture'}]})
        state['posts']+=1;state['active']+=1;state['peak']=max(state['peak'],state['active'])
        try:
            while state['hold'] and not gate.is_set():await asyncio.sleep(.01)
            if state['fail']:raise httpx.ReadTimeout('private upstream details')
            body=json.loads(request.content)
            use_tool=bool(body.get('tools') and body['messages'][-1]['role']!='tool')
            message={'role':'assistant','content':None if use_tool else 'synthetic response'}
            if use_tool:message['tool_calls']=[{'id':'tool_fixture','type':'function','function':{'name':'echo','arguments':'{"text":"hello"}'}}]
            result={'id':'chatcmpl-fixture','object':'chat.completion','created':1,'model':'engine-fixture',
                    'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if use_tool else 'stop'}],
                    'usage':{'prompt_tokens':7,'completion_tokens':3,'total_tokens':10}}
            if body.get('stream'):
                raw=''.join(sse(chunk) for chunk in completion_chunks(result))+'data: [DONE]\n\n'
                return httpx.Response(200,text=raw,headers={'content-type':'text/event-stream'})
            return httpx.Response(200,json=result)
        finally:state['active']-=1
    provider=CloudText({'alias':MODEL,'served_model':'engine-fixture','base_url':'http://127.0.0.1:18080/v1'},tmp_path,transport=httpx.MockTransport(upstream))
    provider.key_path.write_text(SECRET);provider.key_path.chmod(0o600)
    app=create_app(store,provider,cloud_api=True,public_origin=ORIGIN,model_catalog_path=catalog,inference_slots=3,queue_wait=.3)
    with TestClient(app,base_url=ORIGIN) as client:
        yield store,client,headers,users,state,gate,app
        gate.set()


def job(work,index=0,kind='harness',idem=None):
    r=work[1].post('/v1/jobs',headers={**work[2][index],**({'Idempotency-Key':idem} if idem else {})},json={'model':MODEL,'kind':kind})
    assert r.status_code==202,r.text
    return r.json()['id']


def chat_body(**extra):
    return {'model':MODEL,'messages':[{'role':'user','content':'synthetic'}],'max_tokens':128,**extra}


def wait_status(work,job_id,index=0,status='running'):
    end=time.monotonic()+3
    while time.monotonic()<end:
        result=work[1].get('/v1/jobs/'+job_id,headers=work[2][index]).json()
        if result.get('status')==status:return result
        time.sleep(.02)
    raise AssertionError(result)


def test_three_workflows_and_fourth_waits_with_private_ownership(work):
    ids=[job(work,i,'simcourt' if i==0 else 'harness') for i in range(4)]
    for i in range(3):wait_status(work,ids[i],i)
    fourth=work[1].get('/v1/jobs/'+ids[3],headers=work[2][3]).json()
    assert fourth['status']=='queued'
    # Add a fifth queued user, so the previous queued user's identity is private.
    fifth=job(work,4);view=work[1].get('/v1/jobs/'+fifth,headers=work[2][4]).json()
    assert view['people_ahead']==1 and 'user_id' not in view and 'owner_name' not in view
    assert work[1].get('/v1/jobs/'+ids[0],headers=work[2][4]).status_code==404
    assert work[1].post('/v1/jobs/'+ids[0]+'/cancel',headers=work[2][4]).status_code==404
    admin=work[1].get('/api/jobs',headers=work[2]['admin']).json()
    assert admin['active_tasks']==3 and all('owner_name' in item for item in admin['jobs'])
    assert work[1].post('/v1/jobs/'+ids[0]+'/finish',headers=work[2][0]).status_code==200
    wait_status(work,ids[3],3)
    assert work[0].one('SELECT SUM(reserved) n FROM users')['n']==0


def test_three_actual_inference_calls_overlap_and_simcourt_is_not_special(work):
    store,client,headers,users,state,gate,app=work
    state['hold']=True;gate.clear()
    ids=[job(work,i,'simcourt' if i==0 else 'harness') for i in range(3)]
    calls=[]
    for i in range(3):
        response=client.post('/v1/jobs/'+ids[i]+'/calls',headers=headers[i],json=chat_body())
        assert response.status_code==202;calls.append(response.json()['id'])
    end=time.monotonic()+3
    while state['active']<3 and time.monotonic()<end:time.sleep(.02)
    assert state['peak']==3
    assert store.one('SELECT SUM(reserved) n FROM users')['n']==0
    gate.set()
    for i,call_id in enumerate(calls):
        end=time.monotonic()+3
        while time.monotonic()<end:
            result=client.get('/v1/calls/'+call_id,headers=headers[i]).json()
            if result['status']=='completed':break
            time.sleep(.02)
        assert result['status']=='completed' and result['result']['usage']['total_tokens']==10
    assert store.one("SELECT COUNT(*) n FROM ledger WHERE event='settle'")['n']==3


def test_openai_real_sse_tool_round_trip_and_exactly_once_usage(work):
    store,client,headers,*_=work
    tools=[{'type':'function','function':{'name':'echo','parameters':{'type':'object','properties':{'text':{'type':'string'}}}}}]
    body=chat_body(stream=True,stream_options={'include_usage':True},tools=tools)
    response=client.post('/v1/chat/completions',headers={**headers[0],'Idempotency-Key':'stream-tools'},json=body)
    assert response.status_code==200 and response.headers['content-type'].startswith('text/event-stream')
    chunks=[json.loads(x[6:]) for x in response.text.splitlines() if x.startswith('data: ') and x!='data: [DONE]']
    assert chunks[0]['choices'][0]['delta']['tool_calls'][0]['function']['name']=='echo'
    assert chunks[-1]['usage']['total_tokens']==10
    messages=[*body['messages'],{'role':'assistant','content':None,'tool_calls':[{'id':'tool_fixture','type':'function','function':{'name':'echo','arguments':'{"text":"hello"}'}}]},
              {'role':'tool','tool_call_id':'tool_fixture','content':'hello'}]
    result=client.post('/v1/chat/completions',headers=headers[0],json=chat_body(messages=messages)).json()
    assert result['choices'][0]['message']['content']=='synthetic response'
    assert store.one("SELECT SUM(points) n FROM ledger WHERE event='settle'")['n']==20
    replay=client.post('/v1/chat/completions',headers={**headers[0],'Idempotency-Key':'stream-tools'},json=body)
    assert replay.headers['x-idempotent-replay']=='true'
    assert store.one("SELECT COUNT(*) n FROM ledger WHERE event='settle'")['n']==2


def test_cancel_queued_task_never_calls_model_and_lease_expiry_releases(work):
    ids=[job(work,i) for i in range(4)]
    response=work[1].post('/v1/jobs/'+ids[3]+'/calls',headers=work[2][3],json=chat_body())
    assert response.status_code==202
    assert work[1].post('/v1/jobs/'+ids[3]+'/cancel',headers=work[2][3]).status_code==200
    assert work[4]['posts']==0
    with work[0].tx() as db:db.execute('UPDATE work_jobs SET lease_until=0 WHERE id=?',(ids[0],))
    wait_status(work,ids[0],0,'expired')


def test_explicit_task_id_is_bound_to_owner_model_and_key(work):
    task=job(work,0,idem='same-job');assert job(work,0,idem='same-job')==task
    result=work[1].post('/v1/chat/completions',headers={**work[2][0],'X-Task-ID':task},json=chat_body())
    assert result.status_code==200 and result.headers['x-task-id']==task
    assert work[1].post('/v1/chat/completions',headers={**work[2][1],'X-Task-ID':task},json=chat_body()).status_code==404


def test_developer_role_and_token_alias_are_normalized(work):
    result=work[1].post('/v1/chat/completions',headers=work[2][0],json={'model':MODEL,'messages':[{'role':'developer','content':'system instruction'},{'role':'user','content':'hello'}],'max_completion_tokens':16})
    assert result.status_code==200
    assert work[1].post('/v1/chat/completions',headers=work[2][0],json=chat_body(max_completion_tokens=64)).status_code==422


def test_anthropic_messages_tool_and_stream_adapter(work):
    client,headers=work[1],work[2]
    key=headers[0]['Authorization'][7:]
    body={'model':MODEL,'max_tokens':128,'messages':[{'role':'user','content':'synthetic'}],
          'tools':[{'name':'echo','input_schema':{'type':'object','properties':{}}}]}
    response=client.post('/v1/messages',headers={'x-api-key':key,'anthropic-version':'2023-06-01'},json=body)
    assert response.status_code==200,response.text
    result=response.json();assert result['stop_reason']=='tool_use' and result['content'][0]['type']=='tool_use'
    response=client.post('/v1/messages',headers={'x-api-key':key},json={**body,'stream':True})
    assert 'event: content_block_start' in response.text and 'input_json_delta' in response.text
    assert 'event: message_stop' in response.text
    assert '"input_tokens":7' in response.text


def test_restart_keeps_queued_calls_but_never_replays_dispatched(work):
    store,client,headers,*_=work
    task=job(work)
    with store.tx() as db:
        db.execute("INSERT INTO work_calls VALUES('call_uncertain',?,?,?,?,'{}','running',NULL,NULL,NULL,?,?)",(task,work[0].one('SELECT key_id FROM work_jobs WHERE id=?',(task,))['key_id'],'uncertain','hash',time.time(),time.time()))
    work[-1].state.work_queue.recover()
    assert store.one("SELECT status FROM work_calls WHERE id='call_uncertain'")['status']=='reconciliation'
    assert work[4]['posts']==0


def test_invalid_protocol_blocks_never_reach_upstream(work):
    body=chat_body(messages=[{'role':'tool','content':'orphan','tool_call_id':'missing'}])
    assert work[1].post('/v1/chat/completions',headers=work[2][0],json=body).status_code==422
    assert work[1].post('/v1/messages',headers=work[2][0],json={'model':MODEL,'max_tokens':16,'messages':[{'role':'user','content':[{'type':'image','source':{}}]}]}).status_code==400
    assert work[4]['posts']==0
    external=chat_body(tools=[{'type':'function','function':{'name':'echo','parameters':{'$ref':'https://other.example.test/schema'}}}])
    assert work[1].post('/v1/chat/completions',headers=work[2][0],json=external).status_code==422
    assert work[4]['posts']==0


def test_second_gateway_cannot_take_queue_ownership(work):
    from gateway.work_queue import WorkQueue
    original=work[-1].state.work_queue
    second=WorkQueue(work[0],original.provider,original.resolve,original.registered)
    with pytest.raises(ValueError,match='already owned'):
        asyncio.run(second.start())
    assert original.owner is not None


def test_cancel_running_request_requires_reconciliation_not_zero_usage(work):
    store,client,headers,users,state,gate,app=work
    state['hold']=True;gate.clear()
    task=job(work)
    result=client.post('/v1/jobs/'+task+'/calls',headers=headers[0],json=chat_body())
    assert result.status_code==202
    deadline=time.monotonic()+3
    while not state['active'] and time.monotonic()<deadline:time.sleep(.02)
    assert state['active']==1
    assert client.post('/v1/jobs/'+task+'/cancel',headers=headers[0]).status_code==200
    wait_status(work,task,0,'reconciliation')
    assert state['posts']==1 and not store.rows('SELECT * FROM ledger')
    gate.set()


def test_queued_call_survives_dispatcher_restart_without_double_submission(work):
    store,client,headers,users,state,gate,app=work
    ids=[job(work,i) for i in range(4)]
    for i in range(3):wait_status(work,ids[i],i)
    response=client.post('/v1/jobs/'+ids[3]+'/calls',headers={**headers[3],'Idempotency-Key':'survive-restart'},json=chat_body())
    assert response.status_code==202
    call_id=response.json()['id']
    client.portal.call(app.state.work_queue.close)
    clone=create_app(store,app.state.work_queue.provider,cloud_api=True,public_origin=ORIGIN,
                     model_catalog_path=store.path.parent/'model-catalog.local.json',inference_slots=3,queue_wait=.3)
    with TestClient(clone,base_url=ORIGIN) as restarted:
        assert restarted.post('/v1/jobs/'+ids[0]+'/finish',headers=headers[0]).status_code==200
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            final=restarted.get('/v1/calls/'+call_id,headers=headers[3]).json()
            if final['status']=='completed':break
            time.sleep(.02)
        assert final['status']=='completed' and state['posts']==1
    assert store.one("SELECT COUNT(*) n FROM ledger WHERE event='settle'")['n']==1


def test_restart_links_admission_phase_gap_without_resubmission(work):
    store,client,headers,users,state,gate,app=work
    task=job(work);queue=app.state.work_queue;who=store.identity(headers[0]['Authorization'][7:])
    request,_=store.admit(who,'work:call_gap',{},'chat',MODEL,100,queue.provider.profile,queue.provider.profile_hash,runtime_required=False)
    with store.tx() as db:
        db.execute("INSERT INTO work_calls VALUES('call_gap',?,?,?,?,'{}','preparing',NULL,NULL,NULL,?,?)",(task,who['key']['id'],'gap','hash',time.time(),time.time()))
    queue.recover();store.recover()
    assert store.one("SELECT request_id FROM work_calls WHERE id='call_gap'")['request_id']==request['id']
    assert state['posts']==0


def test_restart_recovers_already_settled_result_without_second_ledger_entry(work):
    store,client,headers,users,state,gate,app=work
    task=job(work);queue=app.state.work_queue;who=store.identity(headers[0]['Authorization'][7:])
    request,_=store.admit(who,'work:call_settled',{},'chat',MODEL,100,queue.provider.profile,queue.provider.profile_hash,runtime_required=False)
    result={'model':MODEL,'choices':[{'message':{'role':'assistant','content':'saved result'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':7,'completion_tokens':3,'total_tokens':10}}
    store.settle(request['id'],10,7,3,result)
    with store.tx() as db:
        db.execute("INSERT INTO work_calls VALUES('call_settled',?,?,?,?,'{}','running',?,NULL,NULL,?,?)",(task,who['key']['id'],'settled','hash',request['id'],time.time(),time.time()))
    queue.recover()
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        call=client.get('/v1/calls/call_settled',headers=headers[0]).json()
        if call['status']=='completed':break
        time.sleep(.02)
    assert call['status']=='completed' and call['result']['choices'][0]['message']['content']=='saved result'
    assert state['posts']==0 and store.one("SELECT COUNT(*) n FROM ledger WHERE event='settle'")['n']==1
