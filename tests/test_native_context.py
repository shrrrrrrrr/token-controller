"""Native token preflight is opt-in and runs before inference admission."""
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.store import Store
from gateway.upstream import CloudText

MODEL='research-text'
SECRET='fixture_upstream_credential_only'


@pytest.fixture
def native(tmp_path):
    store=Store(tmp_path/'gateway.sqlite',seed_demo=False)
    member=store.create_user('Synthetic member','caller',[MODEL],[],-1)
    headers={'Authorization':'Bearer '+store.issue_key(member,[MODEL],-1,concurrency=3)['key']}
    catalog=tmp_path/'model-catalog.local.json'
    catalog.write_text(json.dumps({'models':[{'id':MODEL,'display_name':'Synthetic','kind':'text','deployment_status':'offline'}]}))
    state={'tokens':4012,'calls':[],'metadata_status':200,'malformed':False}
    async def upstream(request):
        state['calls'].append(request)
        assert request.url.host=='127.0.0.1' and request.url.port==18080
        assert request.headers['authorization']=='Bearer '+SECRET
        if request.method=='GET':return httpx.Response(200,json={'data':[{'id':'engine-fixture'}]})
        if request.url.path in ('/apply-template','/tokenize'):
            if state['metadata_status']!=200:return httpx.Response(state['metadata_status'],json={'private':'must not echo'})
            if request.url.path=='/apply-template':return httpx.Response(200,json={'prompt':'synthetic rendered template'})
            return httpx.Response(200,json={'tokens':[True] if state['malformed'] else [17]*state['tokens']})
        assert request.url.path=='/v1/chat/completions'
        body=json.loads(request.content)
        result={'id':'chatcmpl-fixture','object':'chat.completion','created':1,'model':'engine-fixture','choices':[{'message':{'role':'assistant','content':'synthetic response'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':state['tokens'],'completion_tokens':9,'total_tokens':state['tokens']+9}}
        if body.get('stream'):
            from gateway.protocols import completion_chunks,sse
            return httpx.Response(200,text=''.join(sse(c) for c in completion_chunks(result))+'data: [DONE]\n\n',headers={'content-type':'text/event-stream'})
        return httpx.Response(200,json=result)
    provider=CloudText({'alias':MODEL,'served_model':'engine-fixture','base_url':'http://127.0.0.1:18080/v1'},tmp_path,
                       transport=httpx.MockTransport(upstream),native_context=True)
    provider.key_path.write_text(SECRET);provider.key_path.chmod(0o600)
    app=create_app(store,provider,cloud_api=True,public_origin='https://models.example.test',model_catalog_path=catalog,inference_slots=3,queue_wait=.3)
    with TestClient(app,base_url='https://models.example.test') as client:yield store,client,headers,state,provider,app


def chat(native,**changes):
    body={'model':MODEL,'messages':[{'role':'user','content':'这是用于上下文容量检查的合成材料。'*400}],'max_tokens':16}
    body.update(changes)
    return native[1].post('/v1/chat/completions',headers={**native[2],'Idempotency-Key':'synthetic-context'},json=body)


def test_long_chinese_uses_native_tokens_without_truncating_and_settles_once(native):
    store,client,headers,state,provider,app=native
    result=chat(native)
    assert result.status_code==200,result.text
    assert result.json()['usage']['prompt_tokens']==4012
    paths=[r.url.path for r in state['calls']]
    assert paths.count('/apply-template')==paths.count('/tokenize')==paths.count('/v1/chat/completions')==1
    render=next(json.loads(r.content) for r in state['calls'] if r.url.path=='/apply-template')
    generation=next(json.loads(r.content) for r in state['calls'] if r.url.path=='/v1/chat/completions')
    assert render['messages']==generation['messages'] and len(generation['messages'][0]['content'])==6800
    before=len(state['calls']);again=chat(native)
    assert again.status_code==200 and again.headers['x-idempotent-replay']=='true'
    assert len(state['calls'])==before
    assert store.one("SELECT COUNT(*) n FROM ledger WHERE event='settle'")['n']==1
    assert store.one('SELECT SUM(reserved) n FROM users')['n']==0


def test_native_over_context_is_refused_before_any_generation_or_admission(native):
    store,client,headers,state,provider,app=native;state['tokens']=16384
    result=chat(native)
    assert result.status_code==400 and result.json()['error']['code']=='context_length_exceeded'
    assert not any(r.url.path.endswith('completions') for r in state['calls'])
    assert not store.rows('SELECT * FROM requests') and not store.rows('SELECT * FROM ledger')
    assert store.one('SELECT status FROM work_calls')['status']=='failed'


@pytest.mark.parametrize('status',[401,404,429,500])
def test_unavailable_tokenizer_never_falls_back_or_generates(native,status):
    store,client,headers,state,provider,app=native;state['metadata_status']=status
    result=chat(native)
    assert result.status_code==503 and result.json()['error']['code']=='context_budget_unavailable'
    assert SECRET not in result.text and 'must not echo' not in result.text
    assert not any(r.url.path.endswith('completions') for r in state['calls'])
    assert not store.rows('SELECT * FROM requests')


def test_invalid_tokenizer_values_are_not_treated_as_token_counts(native):
    native[3]['malformed']=True
    response=chat(native)
    assert response.status_code==503 and response.json()['error']['code']=='context_budget_unavailable'
    assert not native[0].rows('SELECT * FROM requests')


def test_native_mode_still_rejects_large_payload_without_metadata_request(native):
    response=chat(native,messages=[{'role':'user','content':'中'*60000},{'role':'assistant','content':'中'*60000}])
    assert response.status_code==413
    assert all(r.method=='GET' for r in native[3]['calls']) and not native[0].rows('SELECT * FROM work_jobs')


def test_tool_schema_and_generation_options_share_the_same_render_payload(native):
    tools=[{'type':'function','function':{'name':'synthetic_echo','parameters':{'type':'object','properties':{'text':{'type':'string'}}}}}]
    response=chat(native,tools=tools,tool_choice='none',reasoning_effort='low',stop=['END'])
    assert response.status_code==200,response.text
    bodies={r.url.path:json.loads(r.content) for r in native[3]['calls'] if r.method=='POST'}
    assert bodies['/apply-template']==bodies['/v1/chat/completions']
    assert bodies['/tokenize']=={'content':'synthetic rendered template','add_special':False,'parse_special':True}


def test_streaming_also_preflights_once_and_keeps_usage(native):
    response=chat(native,stream=True,stream_options={'include_usage':True})
    assert response.status_code==200 and 'data: [DONE]' in response.text
    assert '"prompt_tokens":4012' in response.text
    assert sum(r.url.path=='/apply-template' for r in native[3]['calls'])==1


@pytest.mark.parametrize('tokens,metadata_status,expected',[(16384,200,400),(4012,404,503)])
def test_stream_preflight_errors_return_http_status_before_stream_headers(native,tokens,metadata_status,expected):
    native[3].update(tokens=tokens,metadata_status=metadata_status)
    response=chat(native,stream=True,stream_options={'include_usage':True})
    assert response.status_code==expected,response.text
    assert response.headers['content-type'].startswith('application/json')
    assert response.json()['submitted_to_model'] is False
    assert not any(r.url.path.endswith('completions') for r in native[3]['calls'])


def test_native_context_flag_is_off_by_default_and_changes_profile_when_enabled(tmp_path):
    config={'alias':MODEL,'served_model':'engine-fixture','base_url':'http://127.0.0.1:18080/v1'}
    standard=CloudText(config,tmp_path)
    assert standard.native_context is False
    native=CloudText(config,tmp_path,native_context=True)
    assert native.native_context is True and native.profile_hash!=standard.profile_hash


def test_native_context_cli_flag_rejects_non_cloud_mode_before_state_creation(monkeypatch,tmp_path):
    from gateway.__main__ import main
    monkeypatch.setattr('sys.argv',['gateway','serve','--native-context','--state',str(tmp_path/'must-not-exist')])
    with pytest.raises(SystemExit,match='requires --cloud-api'):main()
    assert not (tmp_path/'must-not-exist').exists()
