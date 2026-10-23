from types import SimpleNamespace as NS
import pytest
from fast_agent.config import Config
from fast_agent.models import Message
from fast_agent.providers import OpenAIModel, ProviderError


def chunk(text=None,calls=None,finish=None):
    return NS(choices=[NS(index=0,delta=NS(content=text,tool_calls=calls),finish_reason=finish)])


def call(index,id=None,name=None,args=None):
    return NS(index=index,id=id,function=NS(name=name,arguments=args))


class Stream:
    def __init__(self,items): self.items=items;self.closed=False
    def __iter__(self):
        for item in self.items:
            if isinstance(item,BaseException): raise item
            yield item
    def close(self): self.closed=True


def model(items):
    stream=Stream(items);seen={}
    def create(**kwargs):seen.update(kwargs);return stream
    client=NS(chat=NS(completions=NS(create=create)))
    return OpenAIModel(Config('key','m'),client),stream,seen


def test_text_chunks_and_close():
    m,s,seen=model([chunk('你好'),chunk('世界'),chunk(finish='stop')]);output=[]
    r=m.complete_stream([Message('user','hi')],[],output.append)
    assert r.content=='你好世界' and output==['你好','世界'] and s.closed and seen['stream']


def test_interleaved_tool_arguments():
    m,s,_=model([chunk(calls=[call(0,'a','read_file','{"pa'),call(1,'b','read_file','{"path":')]),
                 chunk(calls=[call(1,args='"b"}'),call(0,args='th":"a"}')]),chunk(finish='tool_calls')])
    r=m.complete_stream([],[],lambda _:None)
    assert [(c.id,c.arguments) for c in r.tool_calls]==[('a','{"path":"a"}'),('b','{"path":"b"}')]
    assert s.closed


@pytest.mark.parametrize('items',[[chunk('partial')],[chunk('partial'),chunk(finish='length')],
    [chunk(calls=[call(0,'a','read_file','{')]),chunk(finish='tool_calls')],
    [chunk('hello'),RuntimeError('secret')]])
def test_incomplete_stream_rejected(items):
    m,s,_=model(items)
    with pytest.raises(ProviderError) as exc:m.complete_stream([],[],lambda _:None)
    assert 'secret' not in str(exc.value) and s.closed


def test_interrupt_closes_stream():
    m,s,_=model([chunk('start'),KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):m.complete_stream([],[],lambda _:None)
    assert s.closed

def test_partial_tool_stream_not_executed_by_agent(tmp_path):
    from fast_agent.agent import Agent
    from fast_agent.tools import ToolContext, build_default_registry
    m,s,_=model([chunk(calls=[call(0,'a','write_file','{"path":"bad","content":"bad"}')])])
    events=[]
    a=Agent(m,build_default_registry(),ToolContext(tmp_path,lambda _:True,True),stream=True,on_event=events.append)
    result=a.run('test')
    assert not result.completed and not (tmp_path/'bad').exists()
    assert not any(e.kind=='tool_start' for e in events) and s.closed


def test_stream_size_guard(monkeypatch):
    import fast_agent.streaming as streaming
    monkeypatch.setattr(streaming,'MAX_OUTPUT',3)
    m,s,_=model([chunk('too large'),chunk(finish='stop')])
    with pytest.raises(ProviderError):m.complete_stream([],[],lambda _:None)
    assert s.closed

@pytest.mark.parametrize('started',[False,True])
def test_window_rejection_never_replays_partial_stream(started):
    from fast_agent.providers import ContextWindowError
    class Rejected(Exception):
        status_code=400
        body={'code':'context_length_exceeded'}
    m,s,_=model(([chunk('partial')] if started else [])+[Rejected()])
    with pytest.raises(ProviderError) as caught:
        m.complete_stream([],[],lambda _:None)
    assert isinstance(caught.value,ContextWindowError) is (not started)
    assert s.closed and m.usage.snapshot()['requests']==1
