"""Bounded text/tool contracts. A tool definition never grants host execution."""
import json
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .store import Fault, canonical
from .providers import verified_usage


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class FunctionCall(Input):
    name: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    arguments: str = Field(max_length=32000)


class ToolCall(Input):
    id: str = Field(min_length=1, max_length=160)
    type: Literal['function'] = 'function'
    function: FunctionCall


class Message(Input):
    role: Literal['system','developer','user','assistant','tool']
    content: str | list[dict] | None = Field(default=None, max_length=64000)
    tool_calls: list[ToolCall] | None = Field(default=None, max_length=16)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=160)
    reasoning_content: str | None = Field(default=None, max_length=64000)
    name: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,64}$')

    @model_validator(mode='after')
    def shape(self):
        if isinstance(self.content,list):
            if len(self.content)>32 or any(not isinstance(block,dict) or set(block)!={'type','text'} or block['type']!='text' or not isinstance(block['text'],str) for block in self.content):
                raise ValueError('only text content blocks are supported')
            self.content='\n'.join(block['text'] for block in self.content)
            if len(self.content)>64000:raise ValueError('text content too large')
        if self.role=='tool' and (not self.tool_call_id or self.content is None):
            raise ValueError('tool result requires an id and text')
        if self.tool_calls and self.role!='assistant' or self.tool_call_id and self.role!='tool':
            raise ValueError('invalid tool message')
        if self.content is None and not self.tool_calls:
            raise ValueError('message requires text or tool calls')
        return self


class Function(Input):
    name: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    description: str | None = Field(default=None, max_length=4000)
    parameters: dict = Field(default_factory=lambda:{'type':'object','properties':{}})
    strict: bool | None = None


class Tool(Input):
    type: Literal['function'] = 'function'
    function: Function


class Chat(Input):
    model: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')
    messages: list[Message] = Field(min_length=1, max_length=128)
    max_tokens: int = Field(default=256, ge=1, le=8192)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=8192)
    temperature: float = Field(default=0.2, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    stream: bool = False
    stream_options: dict | None = None
    tools: list[Tool] | None = Field(default=None, max_length=32)
    tool_choice: str | dict | None = None
    parallel_tool_calls: bool | None = None
    stop: str | list[str] | None = None
    reasoning_effort: Literal['none','low','medium','high','max'] | None = None
    n: Literal[1] = 1

    @model_validator(mode='after')
    def contract(self):
        if self.max_completion_tokens is not None:
            if 'max_tokens' in self.model_fields_set and self.max_tokens!=self.max_completion_tokens:
                raise ValueError('conflicting token caps')
            self.max_tokens=self.max_completion_tokens
        if self.stream_options is not None and (set(self.stream_options)-{'include_usage'} or
                type(self.stream_options.get('include_usage',True)) is not bool):
            raise ValueError('invalid stream options')
        names={tool.function.name for tool in self.tools or []}
        if len(names)!=len(self.tools or []):raise ValueError('duplicate tool names')
        if self.tool_choice is not None:
            if isinstance(self.tool_choice,str):
                if self.tool_choice not in ('auto','none','required'):raise ValueError('invalid tool choice')
                if self.tool_choice=='required' and not names:raise ValueError('no tools')
            elif (set(self.tool_choice)!={'type','function'} or self.tool_choice['type']!='function'
                  or not isinstance(self.tool_choice['function'],dict)
                  or set(self.tool_choice['function'])!={'name'}
                  or self.tool_choice['function']['name'] not in names):raise ValueError('unknown tool')
        if self.stop is not None and (isinstance(self.stop,list) and (len(self.stop)>4 or any(not isinstance(x,str) or len(x)>256 for x in self.stop))
                                     or isinstance(self.stop,str) and len(self.stop)>256):raise ValueError('invalid stop')
        if len(canonical([t.model_dump() for t in self.tools or []]).encode())>65536:
            raise ValueError('tool schemas too large')
        # No remote schema resolution or non-JSON numbers reach the engine.
        schema_nodes=[(tool.function.parameters,0) for tool in self.tools or []];count=0
        while schema_nodes:
            node,depth=schema_nodes.pop();count+=1
            if depth>24 or count>4096:raise ValueError('tool schema complexity limit')
            if isinstance(node,dict):
                if '$ref' in node and (not isinstance(node['$ref'],str) or not node['$ref'].startswith('#/')):
                    raise ValueError('only local schema references are supported')
                schema_nodes.extend((value,depth+1) for value in node.values())
            elif isinstance(node,list):schema_nodes.extend((value,depth+1) for value in node)
        json.dumps(self.model_dump(),allow_nan=False)
        pending=set()
        for message in self.messages:
            if message.role=='tool':
                if message.tool_call_id not in pending:raise ValueError('orphan tool result')
                pending.remove(message.tool_call_id)
            elif pending:raise ValueError('missing tool result')
            for call in message.tool_calls or []:
                if call.id in pending:raise ValueError('duplicate tool id')
                pending.add(call.id)
        if pending:raise ValueError('missing tool result')
        return self

    def payload(self):
        data=self.model_dump(exclude_none=True)
        data.pop('max_completion_tokens',None);data.pop('n',None)
        # Developer is mapped for the self-hosted text engines, not ignored.
        for message in data['messages']:
            if message['role']=='developer':message['role']='system'
        return data


def validate_completion(result, model, cap, *, context=16384):
    if not isinstance(result,dict) or result.get('model')!=model:
        raise Fault('upstream_response_model_mismatch',502)
    choices=result.get('choices')
    if not isinstance(choices,list) or len(choices)!=1:raise Fault('upstream_response_invalid',502)
    choice=choices[0]; message=choice.get('message') if isinstance(choice,dict) else None
    if not isinstance(message,dict) or message.get('role')!='assistant':raise Fault('upstream_response_invalid',502)
    calls=message.get('tool_calls') or []
    try:
        for call in calls:
            parsed=ToolCall.model_validate(call)
            if not isinstance(json.loads(parsed.function.arguments),dict):raise ValueError()
    except (ValueError,TypeError):raise Fault('upstream_tool_call_invalid',502) from None
    if (not isinstance(message.get('content'),str) and not (message.get('content') is None and calls)
            or choice.get('finish_reason') not in ('stop','length','tool_calls')
            or (choice.get('finish_reason')=='tool_calls')!=bool(calls)):
        raise Fault('upstream_response_invalid',502)
    prompt,completion,total=verified_usage(result)
    if completion>cap or total>context:raise Fault('usage_invalid_reconciliation',502)
    clean={'role':'assistant','content':message.get('content')}
    if calls:clean['tool_calls']=calls
    if isinstance(message.get('reasoning_content'),str):clean['reasoning_content']=message['reasoning_content']
    return {'id':str(result.get('id',''))[:160], 'object':'chat.completion','created':int(time.time()),'model':model,
            'choices':[{'index':0,'message':clean,'finish_reason':choice['finish_reason']}],
            'usage':{'prompt_tokens':prompt,'completion_tokens':completion,'total_tokens':total}}


class StreamAccumulator:
    def __init__(self, model):
        self.model=model;self.content='';self.reasoning='';self.calls={};self.usage=None;self.finish=None;self.id=''
        self.bytes=0

    def add(self, chunk):
        self.bytes+=len(canonical(chunk).encode())
        if self.bytes>2097152:raise Fault('upstream_response_too_large',502)
        if not isinstance(chunk,dict) or chunk.get('model')!=self.model:raise Fault('upstream_response_model_mismatch',502)
        self.id=str(chunk.get('id',''))[:160]
        if chunk.get('usage') is not None:self.usage=chunk['usage']
        for choice in chunk.get('choices',[]):
            if choice.get('index',0)!=0:raise Fault('upstream_response_invalid',502)
            delta=choice.get('delta') or {}
            for field in ('content','reasoning_content'):
                if delta.get(field) is not None:
                    if not isinstance(delta[field],str):raise Fault('upstream_response_invalid',502)
                    if field=='content':self.content+=delta[field]
                    else:self.reasoning+=delta[field]
            for call in delta.get('tool_calls') or []:
                index=call.get('index')
                if type(index) is not int or not 0<=index<16:raise Fault('upstream_tool_call_invalid',502)
                target=self.calls.setdefault(index,{'id':'','type':'function','function':{'name':'','arguments':''}})
                if call.get('id'):target['id']+=call['id']
                for key in ('name','arguments'):
                    value=(call.get('function') or {}).get(key,'')
                    if not isinstance(value,str):raise Fault('upstream_tool_call_invalid',502)
                    target['function'][key]+=value
            if choice.get('finish_reason') is not None:self.finish=choice['finish_reason']

    def result(self,cap):
        message={'role':'assistant','content':self.content or (None if self.calls else '')}
        if self.calls:message['tool_calls']=[self.calls[k] for k in sorted(self.calls)]
        if self.reasoning:message['reasoning_content']=self.reasoning
        return validate_completion({'id':self.id,'model':self.model,'choices':[{'message':message,'finish_reason':self.finish}],
                                    'usage':self.usage},self.model,cap)


def sse(data):
    return 'data: '+canonical(data)+'\n\n'


def completion_chunks(result):
    base={key:result[key] for key in ('id','created','model')};base['object']='chat.completion.chunk'
    delta=dict(result['choices'][0]['message'])
    if delta.get('tool_calls'):delta['tool_calls']=[dict(call,index=i) for i,call in enumerate(delta['tool_calls'])]
    yield {**base,'choices':[{'index':0,'delta':delta,'finish_reason':None}]}
    yield {**base,'choices':[{'index':0,'delta':{},'finish_reason':result['choices'][0]['finish_reason']}]}
    yield {**base,'choices':[],'usage':result['usage']}
