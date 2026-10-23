import json
from fast_agent.tool_outputs import retain_result, read_output
from fast_agent.tools import ToolResult, ToolContext


def test_small_unchanged_and_large_restorable(tmp_path):
    small=ToolResult(True,{'text':'ok'})
    assert retain_result(tmp_path,small) is small
    original=ToolResult(False,{'text':'中文'*20000,'returncode':2},'失败')
    result=retain_result(tmp_path,original)
    assert not result.ok and result.error=='失败'
    ref=result.data['output_ref']
    assert result.data['returncode']==2 and not ref['storage_truncated']
    assert len(json.dumps(result.data,ensure_ascii=False))<16000
    ctx=ToolContext(tmp_path,lambda _:False,False,mode='plan')
    chunks=[];offset=0
    while True:
        page=read_output({'output_id':ref['id'],'offset':offset,'limit':4000},ctx).data
        chunks.append(page['content']);offset=page['next_offset']
        if page['eof']:break
    assert json.loads(''.join(chunks))['data']==original.data


def test_storage_failure_preserves_operation_status(tmp_path):
    (tmp_path/'.fast-agent').write_text('blocked')
    result=retain_result(tmp_path,ToolResult(True,{'text':'x'*20000}))
    assert result.ok and result.data['storage_error'] and result.data['output_ref'] is None


def test_huge_is_explicitly_truncated(tmp_path):
    result=retain_result(tmp_path,ToolResult(True,{'text':'x'*1100000}))
    assert result.data['output_ref']['storage_truncated']


def test_agent_large_failure_still_pauses_and_history_restores(tmp_path):
    from fast_agent.agent import Agent
    from fast_agent.models import ModelReply,ToolCall
    from fast_agent.tools import Tool,ToolRegistry,object_schema
    from fast_agent.sessions import SessionStore,validate_history
    class Model:
        def __init__(self):self.i=0
        def complete(self,*args):
            self.i+=1
            return ModelReply(tool_calls=[ToolCall(str(self.i),'large','{}')])
    registry=ToolRegistry()
    registry.register(Tool('large','large',object_schema({},[]),lambda *_:ToolResult(False,{'text':'x'*20000},'failure'),'read'))
    agent=Agent(Model(),registry,ToolContext(tmp_path,lambda _:False,False))
    assert not agent.run('test').completed and agent.model.i==3
    assert all(len(m.content)<16000 for m in agent.history if m.role=='tool')
    SessionStore(tmp_path).save('large',agent.history)
    restored=SessionStore(tmp_path).load('large');validate_history(restored)
    ref=json.loads(restored[-1].content)['data']['output_ref']['id']
    assert read_output({'output_id':ref},agent.context).ok
