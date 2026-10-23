from fast_agent.agent import Agent
from fast_agent.models import ModelReply
from fast_agent.tools import ToolContext,ToolRegistry
from fast_agent.skills import skill_tools
from fast_agent.session_commands import handle_session_command


def setup(root):
    directory=root/'skills'/'review';directory.mkdir(parents=True)
    path=directory/'SKILL.md';path.write_text('读取代码，核实测试。')
    class Model:
        def __init__(self):self.calls=[]
        def complete(self,messages,tools):self.calls.append(messages[-1].content);return ModelReply('完成只读分析')
    registry=ToolRegistry()
    for tool in skill_tools(root,'skills'):registry.register(tool)
    agent=Agent(Model(),registry,ToolContext(root,lambda _:False,False),plan=True)
    return path,agent


def test_explicit_skill_and_changed_status(tmp_path,capsys):
    path,agent=setup(tmp_path)
    assert handle_session_command('/skill review 检查当前代码',agent)
    assert len(agent.model.calls)==1 and '读取代码' in agent.model.calls[0]
    assert agent.context.mode=='plan'
    path.write_text('新的说明')
    handle_session_command('/skills',agent)
    assert 'changed' in capsys.readouterr().out
    assert len(agent.model.calls)==1
    agent.restore_history(agent.history)
    assert agent.context.skill_reads=={}


def test_missing_or_invalid_request_does_not_call_model(tmp_path):
    _,agent=setup(tmp_path)
    handle_session_command('/skill absent task',agent)
    handle_session_command('/skill review',agent)
    assert agent.model.calls==[]


def test_compaction_and_reset_clear_loaded_state(tmp_path,monkeypatch):
    _,agent=setup(tmp_path)
    handle_session_command('/skill review 分析',agent)
    assert agent.context.skill_reads
    monkeypatch.setattr('fast_agent.agent.compact_history',lambda history,*a,**k:(history,{'before':100,'after':90}))
    agent.compact()
    assert agent.context.skill_reads=={}
    handle_session_command('/skill review 分析',agent)
    agent.reset()
    assert agent.context.skill_reads=={}
