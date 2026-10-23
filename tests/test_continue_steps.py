import pytest
from fast_agent.agent import Agent
from fast_agent.models import ModelReply, ToolCall
from fast_agent.tools import ToolContext, build_default_registry
from fast_agent.sessions import validate_history
from fast_agent.tasks import TaskStore
from fast_agent.session_commands import handle_session_command


class Model:
    def __init__(self):self.requests=[]
    def complete(self,messages,tools):
        self.requests.append(list(messages))
        if len(self.requests)==1:return ModelReply(tool_calls=[ToolCall('read','read_file','{"path":"a"}')])
        return ModelReply('已完成')


def test_continue_keeps_results_without_replay(tmp_path):
    (tmp_path/'a').write_text('evidence')
    model=Model();events=[]
    agent=Agent(model,build_default_registry(),ToolContext(tmp_path,lambda _:False,False),max_steps=1,checkpoints=True,on_event=events.append)
    result=agent.run('读取资料')
    assert not result.completed and '/continue' in result.text and agent.last_task_id in result.text
    old=list(agent.history);task=agent.last_task_id
    assert TaskStore(tmp_path).load(task)['status']=='incomplete'
    agent.set_steps(3)
    assert agent.continue_task('只总结').completed
    assert model.requests[-1][:len(old)]==old
    assert sum(e.kind=='tool_start' for e in events)==1
    validate_history(agent.history)


def test_resume_continue_stays_plan(tmp_path):
    (tmp_path/'a').write_text('evidence')
    agent=Agent(Model(),build_default_registry(),ToolContext(tmp_path,lambda _:False,False),max_steps=1,checkpoints=True)
    agent.run('读取')
    agent.resume(agent.last_task_id)
    assert agent.continue_task().completed and agent.context.mode=='plan'


def test_steps_and_empty_continue(tmp_path,capsys):
    agent=Agent(Model(),build_default_registry(),ToolContext(tmp_path,lambda _:False,False))
    with pytest.raises(ValueError):agent.continue_task()
    for value in [0,-1,True,1.5,1001]:
        with pytest.raises(ValueError):agent.set_steps(value)
    handle_session_command('/steps 7',agent)
    assert agent.max_steps==7
    handle_session_command('/steps',agent)
    assert '7' in capsys.readouterr().out


def test_continue_cannot_bypass_unknown_results(tmp_path):
    from fast_agent.models import Message
    (tmp_path/'a').write_text('unchanged')
    call=ToolCall('unknown','write_file','{"path":"a","content":"bad"}')
    TaskStore(tmp_path).save('blocked',[Message('system','s'),Message('user','task'),Message('assistant',tool_calls=[call])], [call],'unknown','interrupted',[])
    class Writer:
        def complete(self,messages,tools):
            if messages[-1].role=='tool':return ModelReply('操作被阻止')
            return ModelReply(tool_calls=[ToolCall('new','write_file','{"path":"a","content":"bad"}')])
    agent=Agent(Writer(),build_default_registry(),ToolContext(tmp_path,lambda _:True,True),checkpoints=True)
    agent.resume('blocked');agent.set_mode('execute');agent.set_steps(5)
    agent.continue_task()
    assert (tmp_path/'a').read_text()=='unchanged'
    assert agent.context.blockers
    assert any(m.role=='tool' and '权限拒绝' in m.content for m in agent.history)


@pytest.mark.parametrize('note',["don't repeat completed work", '执行 python -c "print(1 + 2)"  保留空格'])
def test_continue_preserves_verbatim_note(note):
    class Capture:
        def continue_task(self,text):
            self.note=text
            from fast_agent.models import AgentResult
            return AgentResult('done',True,1)
    agent=Capture()
    handle_session_command('/continue '+note,agent)
    assert agent.note==note
