"""Anthropic Messages text/function-tool adapter; no hosted tools or vision."""
import json

from fastapi import Depends, Header
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field

from .protocols import Chat, Input, sse
from .store import Fault, canonical, uid


class Messages(Input):
    model: str = Field(min_length=1,max_length=100)
    max_tokens: int = Field(ge=1,le=8192)
    messages: list[dict] = Field(min_length=1,max_length=128)
    system: str | list[dict] | None = None
    stream: bool = False
    temperature: float = Field(default=0.2,ge=0,le=1)
    top_p: float | None = Field(default=None,gt=0,le=1)
    tools: list[dict] | None = Field(default=None,max_length=32)
    tool_choice: dict | None = None
    stop_sequences: list[str] | None = Field(default=None,max_length=4)

    def chat(self):
        def text(value):
            if isinstance(value,str):return value
            if not isinstance(value,list):raise ValueError('invalid text')
            parts=[]
            for block in value:
                if not isinstance(block,dict) or set(block)-{'type','text'} or block.get('type')!='text' or not isinstance(block.get('text'),str):
                    raise ValueError('unsupported content block')
                parts.append(block['text'])
            return '\n'.join(parts)
        messages=[]
        try:
            if self.system:messages.append({'role':'system','content':text(self.system)})
            for message in self.messages:
                if set(message)!={'role','content'} or message['role'] not in ('user','assistant'):raise ValueError()
                role=message['role'];blocks=message['content']
                if isinstance(blocks,str):messages.append({'role':role,'content':blocks});continue
                if not isinstance(blocks,list):raise ValueError()
                texts=[];calls=[];results=[]
                for block in blocks:
                    if not isinstance(block,dict):raise ValueError()
                    kind=block.get('type')
                    if kind=='text':texts.append(text([block]))
                    elif kind=='tool_use' and role=='assistant' and set(block)=={'type','id','name','input'} and isinstance(block['input'],dict):
                        calls.append({'type':'function','id':block['id'],'function':{'name':block['name'],'arguments':canonical(block['input'])}})
                    elif kind=='tool_result' and role=='user' and not set(block)-{'type','tool_use_id','content','is_error'}:
                        value=text(block.get('content',''))
                        if block.get('is_error'):value='Tool error: '+value
                        results.append({'role':'tool','tool_call_id':block['tool_use_id'],'content':value})
                    else:raise ValueError()
                messages.extend(results)
                if texts or calls:messages.append({'role':role,'content':'\n'.join(texts) if texts else None,**({'tool_calls':calls} if calls else {})})
            data={'model':self.model,'messages':messages,'max_tokens':self.max_tokens,'temperature':self.temperature,'stream':self.stream}
            if self.stream:data['stream_options']={'include_usage':True}
            if self.top_p is not None:data['top_p']=self.top_p
            if self.stop_sequences:data['stop']=self.stop_sequences
            if self.tools:
                tools=[]
                for tool in self.tools:
                    if set(tool)-{'name','description','input_schema'} or not isinstance(tool.get('input_schema'),dict):raise ValueError()
                    tools.append({'type':'function','function':{'name':tool['name'],'parameters':tool['input_schema'],**({'description':tool['description']} if 'description' in tool else {})}})
                data['tools']=tools
            if self.tool_choice:
                choice=self.tool_choice;kind=choice.get('type')
                if set(choice)-{'type','name','disable_parallel_tool_use'}:raise ValueError()
                if 'disable_parallel_tool_use' in choice and type(choice['disable_parallel_tool_use']) is not bool:raise ValueError()
                if kind in ('auto','none','any'):data['tool_choice']={'any':'required'}.get(kind,kind)
                elif kind=='tool':data['tool_choice']={'type':'function','function':{'name':choice['name']}}
                else:raise ValueError()
                if 'disable_parallel_tool_use' in choice:data['parallel_tool_calls']=not choice['disable_parallel_tool_use']
            return Chat.model_validate(data)
        except (ValueError,TypeError,KeyError):raise Fault('unsupported_messages_content',400) from None


def message_result(result):
    choice=result['choices'][0];message=choice['message'];content=[]
    if message.get('content'):content.append({'type':'text','text':message['content']})
    for call in message.get('tool_calls',[]):
        content.append({'type':'tool_use','id':call['id'],'name':call['function']['name'],'input':json.loads(call['function']['arguments'])})
    return {'id':result['id'] or uid('msg_'),'type':'message','role':'assistant','model':result['model'],'content':content,
            'stop_reason':{'stop':'end_turn','length':'max_tokens','tool_calls':'tool_use'}[choice['finish_reason']],
            'stop_sequence':None,'usage':{'input_tokens':result['usage']['prompt_tokens'],'output_tokens':result['usage']['completion_tokens']}}


def event(kind,**data):
    return 'event: '+kind+'\ndata: '+canonical({'type':kind,**data})+'\n\n'


def mount_messages(app,queue,api_identity):
    @app.post('/v1/messages')
    async def messages(body:Messages,who=Depends(api_identity),idempotency_key:str=Header(default='',max_length=120),
                       x_task_id:str|None=Header(default=None,max_length=100)):
        chat=body.chat()
        with queue.store.connect() as db:queue.authorize(db,who,chat.model,budget=False)
        response=await queue.chat(chat,who,idempotency_key,x_task_id)
        if response.status_code!=200:return response
        if not body.stream:
            return JSONResponse(message_result(json.loads(response.body)),headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-type','content-length')})

        async def convert():
            started=False;index=0;opened={};buffer='';usage={'input_tokens':0,'output_tokens':0};reason=None
            async for raw in response.body_iterator:
                buffer+=raw.decode() if isinstance(raw,bytes) else raw
                while '\n\n' in buffer:
                    frame,buffer=buffer.split('\n\n',1)
                    lines=[line[6:] for line in frame.splitlines() if line.startswith('data: ')]
                    if not lines:yield event('ping');continue
                    data='\n'.join(lines)
                    if data=='[DONE]':continue
                    chunk=json.loads(data)
                    if 'error' in chunk:
                        yield event('error',error={'type':'api_error','message':chunk['error']['code']});return
                    if not started:
                        started=True
                        yield event('message_start',message={'id':chunk.get('id') or uid('msg_'),'type':'message','role':'assistant',
                            'model':body.model,'content':[],'stop_reason':None,'stop_sequence':None,'usage':dict(usage)})
                    if chunk.get('usage'):
                        usage={'input_tokens':chunk['usage']['prompt_tokens'],'output_tokens':chunk['usage']['completion_tokens']}
                    for choice in chunk.get('choices',[]):
                        delta=choice.get('delta') or {}
                        if delta.get('content'):
                            if 'text' not in opened:
                                opened['text']=index;index+=1
                                yield event('content_block_start',index=opened['text'],content_block={'type':'text','text':''})
                            yield event('content_block_delta',index=opened['text'],delta={'type':'text_delta','text':delta['content']})
                        for call in delta.get('tool_calls') or []:
                            key=('tool',call.get('index',0))
                            if key not in opened:
                                if not call.get('id') or not (call.get('function') or {}).get('name'):
                                    yield event('error',error={'type':'api_error','message':'incomplete_tool_stream_header'});return
                                opened[key]=index;index+=1
                                yield event('content_block_start',index=opened[key],content_block={'type':'tool_use','id':call['id'],'name':call['function']['name'],'input':{}})
                            arguments=(call.get('function') or {}).get('arguments')
                            if arguments:yield event('content_block_delta',index=opened[key],delta={'type':'input_json_delta','partial_json':arguments})
                        if choice.get('finish_reason'):reason={'stop':'end_turn','length':'max_tokens','tool_calls':'tool_use'}.get(choice['finish_reason'])
            if started and reason:
                for i in opened.values():yield event('content_block_stop',index=i)
                yield event('message_delta',delta={'stop_reason':reason,'stop_sequence':None},usage=usage)
                yield event('message_stop')
            else:yield event('error',error={'type':'api_error','message':'incomplete_stream'})
        headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-type','content-length')}
        return StreamingResponse(convert(),media_type='text/event-stream',headers=headers)
