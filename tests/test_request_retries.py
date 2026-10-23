"""故障注入检验重试边界；真实模型链路由 examples 独立验证。"""
from types import SimpleNamespace as NS
import httpx2 as httpx
import pytest
from openai import APIConnectionError, APIStatusError
from fast_agent.config import Config
from fast_agent.models import Message
from fast_agent.providers import OpenAIModel, ProviderError


def response(text='完成'):
    return NS(choices=[NS(finish_reason='stop',message=NS(content=text,tool_calls=[]))])


def failure(status,headers=None):
    request=httpx.Request('POST','https://test.invalid/v1/chat/completions')
    return APIStatusError('private secret',response=httpx.Response(status,request=request,headers=headers),body=None)


class Calls:
    def __init__(self,items): self.items=iter(items);self.requests=[]
    def create(self,**kwargs):
        self.requests.append(kwargs)
        item=next(self.items)
        if isinstance(item,BaseException): raise item
        return item


def make(items,**kwargs):
    calls=Calls(items)
    return OpenAIModel(Config('secret','test',**kwargs),NS(chat=NS(completions=calls))),calls


def test_timeout_configuration_reaches_sdk():
    c=Config.from_env({'FAST_AGENT_API_KEY':'secret','FAST_AGENT_MODEL':'m','FAST_AGENT_CONNECT_TIMEOUT':'4.5','FAST_AGENT_READ_TIMEOUT':'91','FAST_AGENT_MAX_RETRIES':'1'})
    model=OpenAIModel(c)
    try:
        assert model.client.timeout.connect==4.5 and model.client.timeout.read==91
        assert c.max_retries==1 and model.client.max_retries==0
    finally: model.client.close()


@pytest.mark.parametrize('key,value',[('FAST_AGENT_CONNECT_TIMEOUT','nan'),('FAST_AGENT_READ_TIMEOUT','0'),('FAST_AGENT_READ_TIMEOUT','601'),('FAST_AGENT_MAX_RETRIES','-1'),('FAST_AGENT_MAX_RETRIES','1.5'),('FAST_AGENT_MAX_RETRIES','6')])
def test_bad_retry_settings_rejected(key,value):
    with pytest.raises(ValueError):
        Config.from_env({'FAST_AGENT_API_KEY':'key','FAST_AGENT_MODEL':'m',key:value})


def test_transient_retry_keeps_request_and_reports_attempt(monkeypatch):
    import fast_agent.providers as providers
    sleeps=[];monkeypatch.setattr(providers.time,'sleep',sleeps.append)
    m,calls=make([failure(429,{'retry-after':'2'}),failure(503),response()])
    events=[];m.set_event_handler(events.append)
    assert m.complete([Message('user','hi')],[]).content=='完成'
    assert len(calls.requests)==3 and all(r==calls.requests[0] for r in calls.requests)
    assert sleeps[0]==2 and len(sleeps)==2
    assert [e.data['attempt'] for e in events]==[2,3]
    assert all('secret' not in str(e) for e in events)


@pytest.mark.parametrize('status',[400,401,403,404,422,501,505])
def test_permanent_errors_never_retry(status,monkeypatch):
    m,calls=make([failure(status),response()])
    with pytest.raises(ProviderError):m.complete([],[])
    assert len(calls.requests)==1


def test_retry_exhaustion_and_interrupt(monkeypatch):
    import fast_agent.providers as providers
    monkeypatch.setattr(providers.time,'sleep',lambda _:None)
    m,calls=make([failure(500)]*4,max_retries=2)
    with pytest.raises(ProviderError,match='3 次'):m.complete([],[])
    assert len(calls.requests)==3
    def interrupt(_):raise KeyboardInterrupt
    monkeypatch.setattr(providers.time,'sleep',interrupt)
    m,calls=make([failure(503),response()])
    with pytest.raises(KeyboardInterrupt):m.complete([],[])
    assert len(calls.requests)==1


def test_large_retry_after_stops_instead_of_retrying_early():
    m,calls=make([failure(429,{'retry-after':'3600'}),response()])
    with pytest.raises(ProviderError):m.complete([],[])
    assert len(calls.requests)==1


class Stream:
    def __init__(self,items):self.items=items;self.closed=False
    def __iter__(self):
        for x in self.items:
            if isinstance(x,BaseException):raise x
            yield x
    def close(self):self.closed=True


def chunk(content=None,calls=None,finish=None):
    return NS(choices=[NS(index=0,delta=NS(content=content,tool_calls=calls),finish_reason=finish)])


def test_stream_retries_only_before_first_chunk(monkeypatch):
    import fast_agent.providers as providers
    monkeypatch.setattr(providers.time,'sleep',lambda _:None)
    first=Stream([httpx.ReadTimeout('private')])
    second=Stream([chunk('完成'),chunk(finish='stop')])
    m,calls=make([first,second]);text=[]
    assert m.complete_stream([],[],text.append).content=='完成'
    assert text==['完成'] and len(calls.requests)==2 and first.closed and second.closed


@pytest.mark.parametrize('prefix',[
    chunk('部分文本'),chunk(calls=[NS(index=0,id='x',function=NS(name='write_file',arguments='{'))]),chunk(),
])
def test_no_retry_after_any_stream_chunk(prefix,monkeypatch):
    first=Stream([prefix,httpx.ReadTimeout('secret')]);m,calls=make([first,response()])
    with pytest.raises(ProviderError):m.complete_stream([],[],lambda _:None)
    assert len(calls.requests)==1 and first.closed


def test_protocol_failure_not_retried():
    stream=Stream([chunk('missing finish')]);m,calls=make([stream,response()])
    with pytest.raises(ProviderError):m.complete_stream([],[],lambda _:None)
    assert len(calls.requests)==1 and stream.closed


def test_retry_after_tool_does_not_replay_side_effect(tmp_path,monkeypatch):
    import fast_agent.providers as providers
    from fast_agent.agent import Agent
    from fast_agent.tools import Tool,ToolContext,ToolResult,build_default_registry,object_schema
    monkeypatch.setattr(providers.time,'sleep',lambda _:None)
    decision=NS(choices=[NS(finish_reason='tool_calls',message=NS(content=None,tool_calls=[NS(id='once',function=NS(name='append_once',arguments='{}'))]))])
    m,calls=make([decision,failure(503),response()])
    registry=build_default_registry()
    def append(args,ctx):
        with (tmp_path/'count').open('a') as f:f.write('once\n')
        return ToolResult(True)
    registry.register(Tool('append_once','',object_schema({},[]),append))
    events=[]
    agent=Agent(m,registry,ToolContext(tmp_path,lambda _:True,True),on_event=events.append,checkpoints=True)
    assert agent.run('do').completed
    assert (tmp_path/'count').read_text()=='once\n'
    assert calls.requests[1]==calls.requests[2]
    assert any(e.kind=='model_retry' for e in events)


def test_real_sdk_http_503_then_stream_success(monkeypatch):
    import fast_agent.providers as providers
    from openai import OpenAI
    monkeypatch.setattr(providers.time,'sleep',lambda _:None)
    requests=[]
    def respond(request):
        requests.append(request.content)
        if len(requests)==1:return httpx.Response(503,json={'error':{'message':'private outage'}})
        body='data: {"id":"demo","object":"chat.completion.chunk","created":1,"model":"test","choices":[{"index":0,"delta":{"content":"成功"},"finish_reason":null}]}\n\ndata: {"id":"demo","object":"chat.completion.chunk","created":1,"model":"test","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=body.encode())
    transport=httpx.MockTransport(respond)
    client=OpenAI(api_key='fake',base_url='https://test.invalid/v1',max_retries=0,http_client=httpx.Client(transport=transport))
    try:
        m=OpenAIModel(Config('fake','test'),client);text=[]
        assert m.complete_stream([Message('user','hi')],[],text.append).content=='成功'
        assert requests[0]==requests[1] and len(requests)==2 and text==['成功']
    finally:client.close()


def test_retry_disabled_and_connection_retry(monkeypatch):
    import fast_agent.providers as providers
    monkeypatch.setattr(providers.time,'sleep',lambda _:None)
    error=APIConnectionError(request=httpx.Request('POST','https://test.invalid'))
    m,calls=make([error,response()],max_retries=0)
    with pytest.raises(ProviderError):m.complete([],[])
    assert len(calls.requests)==1
    m,calls=make([error,response()],max_retries=1)
    assert m.complete([],[]).content=='完成' and len(calls.requests)==2


def test_retry_after_date_and_backoff_bounds(monkeypatch):
    from datetime import datetime,timezone
    import fast_agent.request_policy as policy
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2026,10,19,12,0,0,tzinfo=timezone.utc)
    monkeypatch.setattr(policy,'datetime',Clock)
    assert policy.retry_delay(failure(429,{'retry-after':'Mon, 19 Oct 2026 12:00:07 GMT'}),1)==7
    assert policy.retry_delay(failure(429,{'retry-after':'Mon, 19 Oct 2026 12:01:00 GMT'}),1) is None
    for invalid in ['bad','nan','-2']:
        assert .8<=policy.retry_delay(failure(503,{'retry-after':invalid}),1)<=1.2
    assert policy.retry_delay(failure(503),10)<=30
