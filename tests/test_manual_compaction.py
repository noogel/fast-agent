import pytest
from fast_agent.agent import Agent
from fast_agent.models import Message, ModelReply
from fast_agent.tools import ToolContext, build_default_registry
from fast_agent.compaction import ContextPolicy, SUMMARY_MARKER
from fast_agent.sessions import SessionStore, validate_history


class Summarizer:
    def __init__(self): self.requests=[]
    def complete(self,messages,tools):
        self.requests.append((messages,tools))
        return ModelReply('任务编号 TARGET-19；禁止修改测试；已读取资料，尚未实施修复。')


def setup(root, model=None):
    model=model or Summarizer()
    agent=Agent(model,build_default_registry(),ToolContext(root,lambda _:False,False),context_policy=ContextPolicy(0,1),plan=True)
    agent.history += [Message('user','任务编号 TARGET-19；禁止修改测试。'+'资料 '*2000),Message('assistant','已读取'),Message('user','接下来修复'),Message('assistant','等待执行')]
    return agent,model


def test_manual_below_threshold_focus_backup_restore(tmp_path):
    agent,model=setup(tmp_path)
    old=list(agent.history)
    report=agent.compact('重点保留编号和测试限制')
    assert report['after']<report['before']
    assert agent.history[-2:]==old[-2:] and agent.context.mode=='plan'
    assert any(m.content==SUMMARY_MARKER for m in agent.history)
    assert '重点保留编号和测试限制' in model.requests[0][0][0].content
    assert model.requests[0][1]==[]
    from pathlib import Path
    assert SessionStore(tmp_path).load(Path(report['backup']).stem)==old
    validate_history(agent.history)


def test_manual_failure_preserves_history_and_backup(tmp_path):
    class Bad:
        def complete(self,messages,tools): raise ValueError('failure')
    agent,_=setup(tmp_path,Bad()); old=list(agent.history)
    with pytest.raises(ValueError): agent.compact()
    assert agent.history==old
    assert list((tmp_path/'.fast-agent/sessions').glob('*.json'))


def test_local_compact_and_empty_noop(tmp_path,capsys):
    from fast_agent.session_commands import handle_session_command
    agent,model=setup(tmp_path)
    assert handle_session_command('/compact 保留编号',agent)
    assert '压缩' in capsys.readouterr().out
    agent.reset()
    assert agent.compact() is None
    assert len(model.requests)==1


def test_long_focus_rejected_before_model(tmp_path):
    agent,model=setup(tmp_path)
    with pytest.raises(ValueError): agent.compact('x'*1001)
    assert not model.requests


def test_backup_failure_never_calls_model(tmp_path,monkeypatch):
    agent,model=setup(tmp_path);old=list(agent.history)
    def fail(*args): raise OSError('full')
    monkeypatch.setattr(SessionStore,'save',fail)
    with pytest.raises(OSError): agent.compact()
    assert agent.history==old and not model.requests


@pytest.mark.parametrize('kind',['tool','interrupt','oversize'])
def test_invalid_summary_or_interrupt_never_adopted(tmp_path,kind):
    from fast_agent.models import ToolCall
    class Bad:
        def complete(self,messages,tools):
            if kind=='interrupt': raise KeyboardInterrupt
            if kind=='tool': return ModelReply(tool_calls=[ToolCall('bad','write_file','{}')])
            return ModelReply('x'*10000)
    agent,_=setup(tmp_path,Bad());old=list(agent.history)
    with pytest.raises(ValueError): agent.compact()
    assert agent.history==old
    assert len(list((tmp_path/'.fast-agent/sessions').glob('manual-*.json')))==1


def test_enabled_but_below_threshold_still_compacts(tmp_path):
    agent,model=setup(tmp_path)
    agent.context_policy=ContextPolicy(60000,1)
    report=agent.compact()
    assert report['before']<60000 and report['after']<report['before']


def test_manual_preserves_latest_tool_batch(tmp_path):
    from fast_agent.models import ToolCall
    agent,model=setup(tmp_path)
    agent.history=agent.history[:1]+[Message('user','当前目标'),
        Message('assistant',tool_calls=[ToolCall('first','read_file','{}')]),Message('tool','早期资料'*1000,tool_call_id='first'),
        Message('assistant',tool_calls=[ToolCall('last','read_file','{}')]),Message('tool','最近结果',tool_call_id='last')]
    latest=agent.history[-2:]
    report=agent.compact()
    assert report['scope']=='current_task'
    assert agent.history[-2:]==latest and any(m.content=='当前目标' for m in agent.history)
    validate_history(agent.history)
