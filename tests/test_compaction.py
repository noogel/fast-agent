import pytest
from fast_agent.models import Message, ModelReply, ToolCall
from fast_agent.providers import ProviderError
from fast_agent.compaction import ContextPolicy, compact_history, request_characters
from fast_agent.sessions import validate_history, SessionStore


def history():
    return [Message('system','rules'),Message('user','目标标题 SECRET-TITLE。'+('background '*400)),
            Message('assistant','收到'),Message('user','查看文件'),
            Message('assistant',tool_calls=[ToolCall('a','read_file','{}')]),Message('tool','file',tool_call_id='a'),
            Message('assistant','已读'),Message('user','现在修改')]


class SummaryModel:
    def __init__(self,text='目标标题 SECRET-TITLE；已读取文件，尚未修改。'):self.text=text;self.requests=[]
    def complete(self,messages,tools):
        self.requests.append((messages,tools));return ModelReply(self.text)


def test_large_history_chunked_then_combined():
    old=[Message('system','rules'),Message('user','background '*22000),Message('assistant','done'),Message('user','continue')]
    model=SummaryModel();backups=[]
    new,info=compact_history(old,[],model,ContextPolicy(5000,1),lambda h:backups.append(list(h)))
    assert len(model.requests)==4
    assert all(not tools for _,tools in model.requests)
    assert backups==[old] and new[-1]==old[-1]
    assert info['summary_requests']==4 and info['summary_chunks']==3
    validate_history(new)


def test_failed_middle_chunk_preserves_original():
    old=[Message('system','rules'),Message('user','x'*220000),Message('assistant','done'),Message('user','continue')]
    before=list(old);backups=[]
    class Failing(SummaryModel):
        def complete(self,messages,tools):
            if self.requests:raise ProviderError('budget exhausted')
            return super().complete(messages,tools)
    with pytest.raises(ProviderError):
        compact_history(old,[],Failing(),ContextPolicy(5000,1),lambda h:backups.append(h))
    assert old==before and not backups


def test_compact_rounds_and_restore(tmp_path):
    old=history();m=SummaryModel();backups=[]
    new,info=compact_history(old,[],m,ContextPolicy(2000,2),lambda h:backups.append(list(h)))
    assert len(m.requests)==1 and not m.requests[0][1]
    assert new[-5:]==old[-5:] and new[0]==old[0]
    assert 'SECRET-TITLE' in new[2].content
    assert backups==[old] and len(old)==8 and info['after']<info['before']
    validate_history(new)
    store=SessionStore(tmp_path);store.save('compressed',new)
    assert store.load('compressed')==new


def test_noop_and_disabled():
    old=history();m=SummaryModel()
    assert compact_history(old,[],m,ContextPolicy(0,2))[0] is old
    assert compact_history(old,[],m,ContextPolicy(100000,2))[0] is old
    assert not m.requests


def test_failed_summary_keeps_original():
    old=history();before=list(old)
    with pytest.raises(ProviderError):compact_history(old,[],SummaryModel('x'*10000),ContextPolicy(2000,2))
    assert old==before


def test_no_room_or_backup_failure():
    old=[Message('system','s'),Message('user','x'*3000)]
    with pytest.raises(ProviderError,match='当前'):compact_history(old,[],SummaryModel(),ContextPolicy(2000,2))
    def fail(_):raise OSError('disk full')
    old=history()
    with pytest.raises(ProviderError):compact_history(old,[],SummaryModel(),ContextPolicy(2000,2),fail)
    assert len(old)==8


def test_schema_count_and_bad_policy():
    assert request_characters([Message('system','s')],[{'large':'x'*200}])>200
    with pytest.raises(ValueError):ContextPolicy(-1,2)
    with pytest.raises(ValueError):ContextPolicy(1000,0)

def test_repeated_compaction_replaces_old_summary():
    old=history();m=SummaryModel()
    compacted,_=compact_history(old,[],m,ContextPolicy(2000,2))
    compacted += [Message('assistant','done'),Message('user','background '*400),Message('assistant','done'),Message('user','next')]
    new,_=compact_history(compacted,[],m,ContextPolicy(2000,1))
    from fast_agent.compaction import SUMMARY_MARKER
    assert sum(message.content==SUMMARY_MARKER for message in new)==1
    validate_history(new)

def test_summary_tool_call_refused():
    class ToolModel:
        def complete(self,messages,tools):return ModelReply(tool_calls=[ToolCall('x','edit_file','{}')])
    backups=[]
    with pytest.raises(ProviderError):compact_history(history(),[],ToolModel(),ContextPolicy(2000,2),backups.append)
    assert not backups


def test_summary_interrupt_preserves_history():
    class Interrupted:
        def complete(self,messages,tools):raise KeyboardInterrupt
    old=history();before=list(old)
    with pytest.raises(KeyboardInterrupt):compact_history(old,[],Interrupted(),ContextPolicy(2000,2))
    assert old==before


def active_history():
    return [Message('system','rules'), Message('user','修复 add，禁止修改测试；编号 TASK-42'),
            Message('assistant', tool_calls=[ToolCall('a','read_file','{}'), ToolCall('b','read_file','{}')]),
            Message('tool','A'*3000,tool_call_id='a'), Message('tool','B'*3000,tool_call_id='b'),
            Message('assistant', tool_calls=[ToolCall('c','read_file','{}')]),
            Message('tool','latest evidence',tool_call_id='c')]


def test_compact_active_task_preserves_goal_and_last_batch(tmp_path):
    old=active_history(); backups=[]
    new,report=compact_history(old,[],SummaryModel('已读取早期文件，尚未修改。'),ContextPolicy(2000,2),lambda h:backups.append(list(h)))
    assert old[1] in new and new[-2:]==old[-2:]
    assert report['summarized_tool_batches']==1 and report['scope']=='current_task'
    assert backups==[old] and len(old)==7
    validate_history(new)
    store=SessionStore(tmp_path);store.save('active',new);assert store.load('active')==new


def test_active_repeated_summary_does_not_stack_markers():
    from fast_agent.compaction import SUMMARY_MARKER
    old=active_history()
    first,_=compact_history(old,[],SummaryModel(),ContextPolicy(2000,2))
    first[-1]=Message('tool','C'*4000,tool_call_id='c')
    first += [Message('assistant',tool_calls=[ToolCall('d','read_file','{}')]),Message('tool','new',tool_call_id='d')]
    new,_=compact_history(first,[],SummaryModel(),ContextPolicy(2000,2))
    assert sum(m.content==SUMMARY_MARKER for m in new)==1
    assert old[1] in new and new[-2:]==first[-2:]
    validate_history(new)


def test_uncompressible_latest_batch_does_not_call_model():
    old=active_history();old[-1]=Message('tool','Z'*5000,tool_call_id='c')
    m=SummaryModel()
    with pytest.raises(ProviderError):compact_history(old,[],m,ContextPolicy(2000,2))
    assert not m.requests


def test_active_failed_backup_preserves_history():
    old=active_history();snapshot=list(old)
    def fail(_):raise OSError('full')
    with pytest.raises(ProviderError):compact_history(old,[],SummaryModel(),ContextPolicy(2000,2),fail)
    assert old==snapshot


def test_active_incomplete_batch_is_rejected_before_summary():
    old=active_history()[:-1];model=SummaryModel()
    with pytest.raises(ValueError):compact_history(old,[],model,ContextPolicy(2000,2))
    assert not model.requests


def test_active_keeps_entire_latest_parallel_batch():
    old=active_history()
    old[-2]=Message('assistant',tool_calls=[ToolCall('c','read_file','{}'),ToolCall('d','read_file','{}')])
    old.append(Message('tool','second result',tool_call_id='d'))
    new,_=compact_history(old,[],SummaryModel(),ContextPolicy(2000,2))
    assert new[-3:]==old[-3:]
    validate_history(new)


def test_old_rounds_and_active_batches_compact_together():
    active=active_history()
    recent=[Message('user','近期约束不能丢失'),Message('assistant','收到')]
    old=[active[0],Message('user','历史背景'*1500),Message('assistant','已记录'),*recent,*active[1:]]
    new,report=compact_history(old,[],SummaryModel(),ContextPolicy(2000,2))
    assert report['summarized_rounds']==1 and report['summarized_tool_batches']==1
    assert new[-5:-3]==recent and active[1] in new and new[-2:]==active[-2:]
    validate_history(new)

def test_explicit_window_budget_and_validation():
    policy=ContextPolicy(60000,1,8192,1024,1024)
    assert policy.effective_chars==1536
    assert ContextPolicy(1000,1,8192,1024).effective_chars==1000
    assert ContextPolicy(0,1,8192,1024).effective_chars==1536
    for kwargs in ({'window_tokens':1000,'output_tokens':500}, {'window_tokens':8192}, {'window_tokens':True}):
        with pytest.raises(ValueError): ContextPolicy(**kwargs)
    new,report=compact_history(history(),[],SummaryModel(),policy)
    assert request_characters(new,[])<=1536 and report
