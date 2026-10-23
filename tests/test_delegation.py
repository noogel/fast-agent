from copy import deepcopy
from fast_agent.agent import Agent
from fast_agent.models import ModelReply, ToolCall
from fast_agent.tools import ToolContext, build_default_registry
from fast_agent.delegation import delegate


def test_isolated_read_and_report(tmp_path):
    (tmp_path/'answer.txt').write_text('evidence-38')
    class Model:
        def __init__(self): self.calls=0; self.handler=None
        def set_event_handler(self,handler): self.handler=handler
        def complete(self,messages,tools):
            self.calls+=1
            assert all(t['function']['name'] not in {'write_file','run_command','ask_user'} for t in tools)
            if self.calls==1:return ModelReply(tool_calls=[ToolCall('r','read_file','{"path":"answer.txt"}')])
            return ModelReply('evidence-38')
    model=Model(); events=[]
    parent=Agent(model,build_default_registry(),ToolContext(tmp_path,lambda _:True,True),on_event=events.append)
    original=deepcopy(parent.history)
    report=delegate(parent,'读取 answer.txt')
    assert report['completed'] and report['text']=='evidence-38'
    assert report['tools']==[{'name':'read_file','ok':True}]
    assert parent.history==original and parent.context.read_versions=={}
    assert model.handler==events.append
    assert (tmp_path/'.fast-agent/delegations'/f"{report['id']}.json").exists()


def test_forbidden_write_and_step_limit(tmp_path):
    class Model:
        def complete(self,messages,tools):return ModelReply(tool_calls=[ToolCall('w','write_file','{"path":"bad","content":"bad"}')])
    parent=Agent(Model(),build_default_registry(),ToolContext(tmp_path,lambda _:True,True),max_steps=1)
    report=delegate(parent,'尝试写入')
    assert not report['completed'] and report['steps']==1
    assert not (tmp_path/'bad').exists()
    assert report['tools']==[{'name':'write_file','ok':False}]


def test_cancel(tmp_path):
    class Model:
        def complete(self,*args):raise KeyboardInterrupt
    parent=Agent(Model(),build_default_registry(),ToolContext(tmp_path,lambda _:False,False))
    assert not delegate(parent,'读取')['completed']


def test_shared_request_budget_is_not_reset(tmp_path):
    from fast_agent.usage import UsageTracker
    class Model:
        def __init__(self): self.usage=UsageTracker(max_requests=1)
        def complete(self,*args):
            self.usage.begin()
            return ModelReply('首次完成')
    parent=Agent(Model(),build_default_registry(),ToolContext(tmp_path,lambda _:False,False))
    assert delegate(parent,'第一次')['completed']
    assert not delegate(parent,'第二次')['completed']
    assert parent.model.usage.snapshot()['requests']==1
