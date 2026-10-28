"""Real loopback HTTP + official OpenAI SDK, with synthetic upstream only."""
import asyncio
import json
import socket
import threading
import time

import httpx
from openai import OpenAI
import uvicorn

from gateway.app import create_app
from gateway.protocols import completion_chunks,sse
from gateway.store import Store
from gateway.upstream import CloudText


def test_sdk_receives_tool_delta_before_upstream_finishes(tmp_path):
    store=Store(tmp_path/'gateway.sqlite',seed_demo=False)
    model='synthetic-text';secret='fixture_upstream_credential_only'
    catalog=tmp_path/'model-catalog.local.json'
    catalog.write_text(json.dumps({'models':[{'id':model,'display_name':'Synthetic','kind':'text','deployment_status':'offline'}]}))
    user=store.create_user('Synthetic SDK member','caller',[model],[],-1)
    key=store.issue_key(user,[model],-1)['key']
    release=threading.Event()
    result={'id':'chatcmpl-fixture','model':'engine-fixture','created':1,'choices':[{'index':0,'message':{
        'role':'assistant','content':None,'tool_calls':[{'id':'echo_call','type':'function','function':{'name':'echo','arguments':'{"text":"hello"}'}}]},
        'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':7,'completion_tokens':3,'total_tokens':10}}
    class LiveStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            chunks=list(completion_chunks(result))
            yield sse(chunks[0]).encode()
            deadline=time.monotonic()+4
            while not release.is_set() and time.monotonic()<deadline:await asyncio.sleep(.01)
            for chunk in chunks[1:]:yield sse(chunk).encode()
            yield b'data: [DONE]\n\n'
    async def upstream(request):
        if request.method=='GET':return httpx.Response(200,json={'data':[{'id':'engine-fixture'}]})
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=LiveStream())
    provider=CloudText({'alias':model,'served_model':'engine-fixture','base_url':'http://127.0.0.1:18080/v1'},tmp_path,transport=httpx.MockTransport(upstream))
    provider.key_path.write_text(secret);provider.key_path.chmod(0o600)
    app=create_app(store,provider,background=False,model_catalog_path=catalog)
    # Fixed CloudText transport can also be exercised on the nonpublic loopback test server.
    app.state.work_queue.resolve=lambda _:asyncio.sleep(0,result={'profile':provider.profile,'profile_hash':provider.profile_hash,'runtime_required':False})
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    client=None
    try:
        deadline=time.monotonic()+5
        while not server.started and time.monotonic()<deadline:time.sleep(.02)
        assert server.started
        client=OpenAI(base_url=f'http://127.0.0.1:{port}/v1',api_key=key,max_retries=0,timeout=8)
        with client.chat.completions.create(model=model,messages=[{'role':'user','content':'synthetic'}],
            tools=[{'type':'function','function':{'name':'echo','parameters':{'type':'object'}}}],
            stream=True,stream_options={'include_usage':True},max_tokens=128) as stream:
            first=next(stream)
            assert first.choices[0].delta.tool_calls[0].function.name=='echo'
            assert not release.is_set()  # A buffered implementation would wait for release.
            assert store.one("SELECT COUNT(*) n FROM requests WHERE status='dispatched'")['n']==1
            release.set();rest=list(stream)
            assert rest[-1].usage.total_tokens==10
        deadline=time.monotonic()+2
        while not store.one("SELECT 1 FROM requests WHERE status='succeeded'") and time.monotonic()<deadline:time.sleep(.02)
        assert store.one("SELECT SUM(points) n FROM ledger WHERE event='settle'")['n']==10
    finally:
        release.set()
        if client:client.close()
        server.should_exit=True;thread.join(timeout=8);sock.close()
