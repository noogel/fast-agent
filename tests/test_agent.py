import json
import pytest
from fast_agent.agent import Agent
from fast_agent.models import ModelReply, ToolCall
from fast_agent.providers import ProviderError
from fast_agent.tools import ToolContext, build_default_registry

class Sequence:
    def __init__(self,replies): self.replies=iter(replies); self.seen=[]
    def complete(self,messages,tools):
        self.seen.append(list(messages))
        reply=next(self.replies)
        if isinstance(reply,BaseException): raise reply
        return reply

def make(tmp_path,model,**kw):
    return Agent(model,build_default_registry(),ToolContext(tmp_path,lambda _:False,False),**kw)

def test_direct_and_reset(tmp_path):
    m=Sequence([ModelReply('你好'),ModelReply('再见')]); a=make(tmp_path,m)
    assert a.run('hi').completed
    assert a.run('bye').steps==1
    assert len(m.seen[1])==4
    a.reset(); assert len(a.history)==1

def test_tools(tmp_path):
    calls=[ToolCall('a','missing','{}'),ToolCall('b','read_file','{"path":"absent"}')]
    m=Sequence([ModelReply(tool_calls=calls),ModelReply('失败原因已收到')])
    assert make(tmp_path,m).run('test').completed
    results=m.seen[1][-2:]
    assert [r.tool_call_id for r in results]==['a','b']
    assert all(not json.loads(r.content)['ok'] for r in results)

def test_step_limit(tmp_path):
    m=Sequence([ModelReply(tool_calls=[ToolCall(str(i),'missing',json.dumps({'attempt':i}))]) for i in range(10)])
    a=make(tmp_path,m); r=a.run('循环')
    assert not r.completed and r.steps==10 and len(m.seen)==10
    assert a.history[-1].role=='tool'

@pytest.mark.parametrize('reply',[ModelReply(),ProviderError('服务失败')])
def test_failure(tmp_path,reply):
    assert not make(tmp_path,Sequence([reply])).run('x').completed

def test_invalid_steps(tmp_path):
    with pytest.raises(ValueError): make(tmp_path,Sequence([]),max_steps=0)

def test_interrupt(tmp_path):
    a=make(tmp_path,Sequence([ModelReply(tool_calls=[ToolCall('a','x','{}'),ToolCall('b','x','{}')])]))
    def interrupt(*args): raise KeyboardInterrupt
    a.registry.execute=interrupt
    assert not a.run('x').completed
    assert [m.tool_call_id for m in a.history if m.role=='tool']==['a','b']

def test_stream_never_executes_partial_tools(tmp_path):
    class Broken:
        def complete_stream(self,messages,tools,on_text):
            on_text('未完成')
            raise ProviderError('断流')
    events=[];a=make(tmp_path,Broken(),stream=True,on_event=events.append)
    r=a.run('x')
    assert not r.completed and len(a.history)==2
    assert not any(e.kind=='tool_start' for e in events)
    assert any(e.kind=='text_delta' for e in events)


def test_streamed_final_result(tmp_path):
    class Streaming:
        def complete_stream(self,messages,tools,on_text):
            on_text('你好');on_text('世界');return ModelReply('你好世界')
    a=make(tmp_path,Streaming(),stream=True)
    r=a.run('x')
    assert r.completed and r.streamed and r.text=='你好世界'


def test_auto_compaction_and_backup(tmp_path):
    from fast_agent.compaction import ContextPolicy
    from fast_agent.models import Message
    from fast_agent.sessions import SessionStore
    class Model:
        def complete(self,messages,tools):
            if not tools:return ModelReply('目标为 ALPHA')
            assert any('目标为 ALPHA'==m.content for m in messages)
            return ModelReply('已恢复目标 ALPHA')
    a=make(tmp_path,Model(),context_policy=ContextPolicy(12000,1))
    a.history += [Message('user','目标ALPHA '+ '背景'*7000),Message('assistant','收到')]
    r=a.run('继续')
    assert r.completed
    backups=list((tmp_path/'.fast-agent/sessions').glob('auto-*.json'))
    assert len(backups)==1
    original=SessionStore(tmp_path).load(backups[0].stem)
    assert len(original[1].content)>10000

def test_window_recovery_once_and_backup(tmp_path):
    from fast_agent.providers import ContextWindowError
    from fast_agent.compaction import ContextPolicy
    from fast_agent.models import Message
    from fast_agent.sessions import validate_history
    m=Sequence([ContextWindowError('window'),ModelReply('旧目标与事实'),ModelReply('完成')])
    a=make(tmp_path,m,context_policy=ContextPolicy(0,1))
    a.history.extend([Message('user','旧目标'+ '背景'*2000),Message('assistant','收到')])
    assert a.run('继续').completed
    assert len(m.seen)==3 and m.seen[1][0].content.startswith('你是会话记录整理器')
    assert len(str(m.seen[2])) < len(str(m.seen[0]))
    validate_history(a.history)
    assert list((tmp_path/'.fast-agent'/'sessions').glob('overflow-*.json'))


def test_window_second_rejection_stops(tmp_path):
    from fast_agent.providers import ContextWindowError
    from fast_agent.compaction import ContextPolicy
    from fast_agent.models import Message
    m=Sequence([ContextWindowError('window'),ModelReply('摘要'),ContextWindowError('still too large')])
    a=make(tmp_path,m,context_policy=ContextPolicy(0,1))
    a.history.extend([Message('user','背景'*2000),Message('assistant','收到')])
    assert not a.run('继续').completed
    assert len(m.seen)==3


def test_window_no_history_does_not_retry(tmp_path):
    from fast_agent.providers import ContextWindowError
    m=Sequence([ContextWindowError('window')])
    assert not make(tmp_path,m).run('单次过大输入').completed
    assert len(m.seen)==1
