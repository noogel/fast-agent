from types import SimpleNamespace as NS
import pytest
from fast_agent.config import Config
from fast_agent.models import Message, ToolCall
from fast_agent.providers import OpenAIModel, ProviderError

def test_config():
    with pytest.raises(ValueError): Config.from_env({})
    c=Config.from_env({'FAST_AGENT_API_KEY':'secret','FAST_AGENT_MODEL':'test'})
    assert c.base_url=='https://api.openai.com/v1'
    assert 'secret' not in repr(c)

def test_conversion():
    seen={}
    def create(**kw):
        seen.update(kw)
        return NS(choices=[NS(message=NS(content='完成',tool_calls=None))])
    client=NS(chat=NS(completions=NS(create=create)))
    model=OpenAIModel(Config('key','model'),client)
    result=model.complete([Message('assistant',tool_calls=[ToolCall('a','read_file','{}')]),Message('tool','{}',tool_call_id='a')],[])
    assert result.content=='完成'
    assert seen['messages'][1]['tool_call_id']=='a'
    assert seen['messages'][0]['tool_calls'][0]['function']['name']=='read_file'

@pytest.mark.parametrize('reply',[None,NS(content=None,tool_calls=None),NS(content=None,tool_calls=[NS(id='',function=NS(name='x',arguments='{}'))])])
def test_bad_reply(reply):
    client=NS(chat=NS(completions=NS(create=lambda **kw:NS(choices=[] if reply is None else [NS(message=reply)]))))
    with pytest.raises(ProviderError): OpenAIModel(Config('secret','m'),client).complete([],[])

def test_sanitized_error():
    def fail(**kw): raise RuntimeError('secret')
    client=NS(chat=NS(completions=NS(create=fail)))
    with pytest.raises(ProviderError) as e: OpenAIModel(Config('secret','m'),client).complete([],[])
    assert 'secret' not in str(e.value)

def test_duplicate_ids():
    from fast_agent.models import ModelReply
    from fast_agent.providers import validate_reply
    with pytest.raises(ProviderError):
        validate_reply(ModelReply(tool_calls=[ToolCall('same','x','{}'),ToolCall('same','y','{}')]))

def test_sdk_client_configuration():
    model=OpenAIModel(Config('test-not-real','demo','http://127.0.0.1:1/v1'))
    try:
        assert model.client.max_retries==0
        assert model.client.timeout.connect==10
        assert model.client.timeout.read==60
        assert str(model.client.base_url)=='http://127.0.0.1:1/v1/'
    finally:
        model.client.close()

def test_mock_failure_and_reset():
    import json
    from fast_agent.providers import MockModel
    mock=MockModel()
    user=Message('user','演示：将 note.txt 中的旧标题改为 Fast Agent')
    failed=Message('tool',json.dumps({'ok':False,'error':'不存在'}),tool_call_id='demo-read')
    assert '失败' in mock.complete([user,failed],[]).content
    assert mock.complete([user,failed,user],[]).tool_calls[0].name=='read_file'

@pytest.mark.parametrize('reason',['length','content_filter','unexpected'])
def test_incomplete_finish_rejected(reason):
    choice=NS(finish_reason=reason,message=NS(content='尚未输出完整',tool_calls=None))
    client=NS(chat=NS(completions=NS(create=lambda **kw:NS(choices=[choice]))))
    with pytest.raises(ProviderError,match='未正常完成'):
        OpenAIModel(Config('key','m'),client).complete([],[])


@pytest.mark.parametrize('status,hint',[(401,'认证失败'),(429,'请求受限'),(500,'模型调用失败')])
def test_stream_error_classification_is_safe(status,hint):
    class Failure(Exception): status_code=status
    def fail(**kwargs): raise Failure('secret-key-and-private-request')
    client=NS(chat=NS(completions=NS(create=fail)))
    with pytest.raises(ProviderError) as caught:
        OpenAIModel(Config('secret','m'),client).complete_stream([],[],lambda _:None)
    assert hint in str(caught.value) and 'secret' not in str(caught.value)

@pytest.mark.parametrize('code,status,expected', [('context_length_exceeded',400,True), ('context_window_exceeded',422,True), ('invalid_request',400,False), ('context_length_exceeded',500,False)])
def test_explicit_context_window_error(code,status,expected):
    from fast_agent.providers import ContextWindowError
    class Rejected(Exception):
        status_code=status
        body={'error':{'code':code,'message':'private prompt'}}
    calls=[]
    def create(**kwargs):
        calls.append(kwargs)
        raise Rejected()
    model=OpenAIModel(Config('key','model',max_retries=0),NS(chat=NS(completions=NS(create=create))))
    with pytest.raises(ProviderError) as caught: model.complete([],[])
    assert isinstance(caught.value,ContextWindowError)==expected
    assert 'private prompt' not in str(caught.value)
    assert len(calls)==1

def test_output_reservation_sent_to_provider():
    seen={}
    def create(**kw):
        seen.update(kw)
        return NS(choices=[NS(message=NS(content='done',tool_calls=None))])
    model=OpenAIModel(Config('k','m',max_output_tokens=256),NS(chat=NS(completions=NS(create=create))))
    model.complete([],[])
    assert seen['max_tokens']==256
@pytest.mark.parametrize('message,expected',[
    ('invalid params, context window exceeds limit (2013)',True),
    ('invalid params, something else (2013)',False),
    ('context window exceeds limit',False),
])
def test_minimax_context_rejection_exact(message,expected):
    from types import SimpleNamespace
    from fast_agent.providers import context_window_rejection
    error=SimpleNamespace(status_code=400,body={'error':{'type':'bad_request_error','message':message}})
    assert context_window_rejection(error) is expected
