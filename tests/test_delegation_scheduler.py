from types import SimpleNamespace
from threading import Barrier, Event
from fast_agent.agent import Agent
from fast_agent.models import ModelReply
from fast_agent.tools import ToolContext, build_default_registry
from fast_agent.delegation_scheduler import delegate_parallel
from fast_agent.usage import UsageTracker


def test_independent_clients_overlap_and_close(tmp_path):
    barrier=Barrier(2)
    children=[]
    usage=UsageTracker(2)
    class Child:
        def __init__(self):
            self.closed=False
            self.client=SimpleNamespace(close=self.close)
        def close(self):self.closed=True
        def set_event_handler(self,handler):self.handler=handler
        def complete(self,messages,tools):
            usage.begin()
            barrier.wait(timeout=5)
            return ModelReply(messages[-1].content)
    class Parent:
        def fork(self):
            child=Child();children.append(child);return child
    parent=Agent(Parent(),build_default_registry(),ToolContext(tmp_path,lambda _:False,False))
    old=list(parent.history)
    reports=delegate_parallel(parent,['任务甲','任务乙'])
    assert [r['text'] for r in reports]==['任务甲','任务乙']
    assert all(r['completed'] for r in reports)
    assert len(children)==2 and all(c.closed for c in children)
    assert usage.snapshot()['requests']==2 and parent.history==old
    assert len(list((tmp_path/'.fast-agent/delegations').glob('*.json')))==2


def test_cancel_before_request_still_records_incomplete(tmp_path):
    cancelled=Event();cancelled.set()
    class Child:
        client=SimpleNamespace(close=lambda:None)
        def set_event_handler(self,handler):pass
        def complete(self,*args):raise AssertionError('must not request')
    class Parent:
        def fork(self):return Child()
    parent=Agent(Parent(),build_default_registry(),ToolContext(tmp_path,lambda _:False,False))
    reports=delegate_parallel(parent,['甲','乙'],cancelled=cancelled)
    assert all(not r['completed'] and '取消' in r['text'] for r in reports)


def test_client_failure_is_sanitized_and_persisted(tmp_path):
    class Parent:
        def fork(self):raise RuntimeError('private credential')
    parent=Agent(Parent(),build_default_registry(),ToolContext(tmp_path,lambda _:False,False))
    reports=delegate_parallel(parent,['失败任务'])
    assert not reports[0]['completed'] and 'private' not in str(reports)
    from fast_agent.delegation_reports import DelegationReports
    assert DelegationReports(tmp_path).load(reports[0]['id'])==reports[0]


def test_durable_batch_reconcile_never_reexecutes(tmp_path):
    import pytest
    from fast_agent.delegation_batches import DelegationBatches
    from fast_agent.worktree_lifecycle import idle
    batches=DelegationBatches(tmp_path)
    record=batches.start(['尚未启动任务'])
    with pytest.raises(ValueError):idle(tmp_path)
    with batches.store.lock():
        with pytest.raises(ValueError):batches.reconcile(record['id'])
    result=batches.reconcile(record['id'])
    assert result['status']=='incomplete'
    assert result['evidence'][0]['state']=='missing_or_invalid'
    assert not list((tmp_path/'.fast-agent/delegations').glob('*.json'))
    idle(tmp_path)


def test_cancel_after_model_reply_discards_tools_and_closes(tmp_path, monkeypatch):
    from fast_agent.models import ToolCall
    from fast_agent.tools import ToolRegistry
    from fast_agent.delegation_reports import DelegationReports
    cancelled=Event()
    closed=[]
    def forbidden(*args,**kwargs):raise AssertionError('cancelled tool must not execute')
    monkeypatch.setattr(ToolRegistry,'execute',forbidden)
    class Child:
        def __init__(self):self.client=SimpleNamespace(close=lambda:closed.append(True))
        def set_event_handler(self,handler):pass
        def complete(self,*args):
            cancelled.set()
            return ModelReply(tool_calls=[ToolCall('cancelled-call','read_file','{"path":"missing.txt"}')])
    class Parent:
        def fork(self):return Child()
    parent=Agent(Parent(),build_default_registry(),ToolContext(tmp_path,lambda _:False,False))
    reports=delegate_parallel(parent,['读取文件'],cancelled=cancelled)
    assert closed==[True]
    assert len(reports)==1 and reports[0]['completed'] is False
    assert reports[0]['tools']==[] and '取消' in reports[0]['text']
    assert DelegationReports(tmp_path).load(reports[0]['id'])==reports[0]
