import json
from fast_agent.agent import Agent
from fast_agent.models import ModelReply, ToolCall
from fast_agent.tools import ToolContext, ToolRegistry, Tool, ToolResult, object_schema
from fast_agent.sessions import validate_history
from fast_agent.tasks import TaskStore


class Sequence:
    def __init__(self,replies):self.replies=iter(replies);self.calls=0
    def complete(self,messages,tools):self.calls+=1;return next(self.replies)


def setup(root,replies,handler):
    registry=ToolRegistry();registry.register(Tool('probe','probe',object_schema({'x':{'type':'integer'}},[]),handler,'read'))
    model=Sequence(replies)
    return Agent(model,registry,ToolContext(root,lambda _:False,False),max_steps=10,checkpoints=True),model


def reply(i,args='{}'):
    return ModelReply(tool_calls=[ToolCall(str(i),'probe',args)])


def test_three_identical_failures_pause_and_continue(tmp_path):
    attempts=[]
    agent,model=setup(tmp_path,[reply(i) for i in range(6)]+[ModelReply('done')],lambda args,ctx:attempts.append(args) or ToolResult(False,error='missing'))
    result=agent.run('task')
    assert not result.completed and model.calls==3 and len(attempts)==3
    assert '重复失败' in result.text and agent.last_task_id in result.text
    assert TaskStore(tmp_path).load(agent.last_task_id)['status']=='paused_repeated_failure'
    assert not agent.context.blockers
    validate_history(agent.history)
    agent.model=Sequence([ModelReply('已确认并停止尝试')])
    assert agent.continue_task('无需再试').completed


def test_batch_remaining_calls_marked_not_executed(tmp_path):
    attempts=[]
    batch=ModelReply(tool_calls=[ToolCall(str(i),'probe','{}') for i in range(5)])
    agent,_=setup(tmp_path,[batch,ModelReply('done')],lambda args,ctx:attempts.append(args) or ToolResult(False,error='missing'))
    assert not agent.run('task').completed
    assert len(attempts)==3
    assert len([m for m in agent.history if m.role=='tool'])==5
    assert '尚未执行' in agent.history[-1].content
    from fast_agent.sessions import decode_history
    validate_history(decode_history(TaskStore(tmp_path).load(agent.last_task_id)['messages']))


def test_changed_args_result_or_success_resets(tmp_path):
    results=iter([ToolResult(False,error='a'),ToolResult(False,error='a'),ToolResult(True),
                  ToolResult(False,error='a'),ToolResult(False,error='b'),ToolResult(False,error='b'),
                  ToolResult(False,{'output':'new'},error='b')])
    agent,model=setup(tmp_path,[reply(i,json.dumps({'x':i//5})) for i in range(7)]+[ModelReply('done')],lambda args,ctx:next(results))
    assert agent.run('task').completed and model.calls==8


def test_json_whitespace_does_not_evade_detection(tmp_path):
    agent,model=setup(tmp_path,[reply(1,'{"x": 1}'),reply(2,'{ "x":1 }'),reply(3,'{"x":1}'),ModelReply('done')],lambda args,ctx:ToolResult(False,error='same'))
    assert not agent.run('task').completed and model.calls==3


def test_command_log_ids_do_not_count_as_progress():
    from fast_agent.agent import failure_signature
    call=ToolCall('id','run_command','{"argv":["false"]}')
    first=failure_signature(call,ToolResult(False,{'log_id':'one','returncode':1,'output':'same'},'exit 1'))
    assert first==failure_signature(call,ToolResult(False,{'log_id':'two','returncode':1,'output':'same'},'exit 1'))
    assert first!=failure_signature(call,ToolResult(False,{'log_id':'two','returncode':1,'output':'different'},'exit 1'))


def test_uncertain_write_still_stops_immediately(tmp_path):
    registry=ToolRegistry()
    registry.register(Tool('write','write',object_schema({},[]),lambda args,ctx:ToolResult(False,{'uncertain':True},'unknown')))
    model=Sequence([ModelReply(tool_calls=[ToolCall('w','write','{}')])])
    agent=Agent(model,registry,ToolContext(tmp_path,lambda _:True,True),checkpoints=True)
    assert not agent.run('task').completed
    assert model.calls==1 and agent.context.blockers
    assert TaskStore(tmp_path).load(agent.last_task_id)['status']=='interrupted'


def test_continue_resets_streak_for_same_failure(tmp_path):
    agent,model=setup(tmp_path,[reply(i) for i in range(6)],lambda args,ctx:ToolResult(False,error='same'))
    assert not agent.run('task').completed and model.calls==3
    assert not agent.continue_task('重试').completed and model.calls==6


def test_argument_change_alone_resets_streak(tmp_path):
    agent,model=setup(tmp_path,[reply(i,json.dumps({'x':i//2})) for i in range(6)]+[ModelReply('done')],lambda args,ctx:ToolResult(False,error='same'))
    assert agent.run('task').completed and model.calls==7


def test_result_change_alone_resets_streak(tmp_path):
    results=iter([ToolResult(False,{'value':i//2},error='same') for i in range(6)])
    agent,model=setup(tmp_path,[reply(i) for i in range(6)]+[ModelReply('done')],lambda args,ctx:next(results))
    assert agent.run('task').completed and model.calls==7
