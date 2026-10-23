import json
import sys
import time
import pytest
from fast_agent.agent import Agent
from fast_agent.models import ModelReply, ToolCall
from fast_agent.tools import ToolContext, Tool, ToolResult, build_default_registry, object_schema


class Sequence:
    def __init__(self, replies): self.replies = iter(replies)
    def complete(self, messages, tools): return next(self.replies)


def test_readonly_blocks_unclassified_and_command(tmp_path):
    from fast_agent.permissions import PermissionPolicy
    ctx = ToolContext(tmp_path, lambda _: True, True, policy=PermissionPolicy('read-only'))
    registry = build_default_registry()
    registry.register(Tool('custom', '', object_schema({}, []), lambda a,c: ToolResult(True)))
    for name,args in [('custom',{}), ('run_command',{'argv':[sys.executable,'-c','print(1)']}), ('write_file',{'path':'x','content':'x'})]:
        assert not registry.execute(ToolCall('x',name,json.dumps(args)),ctx).ok
    assert not (tmp_path/'x').exists()
    (tmp_path/'x').write_text('ok')
    assert registry.execute(ToolCall('r','read_file','{"path":"x"}'),ctx).ok


def test_restricted_exact_commands_and_audit(tmp_path):
    from fast_agent.permissions import PermissionPolicy
    argv=[sys.executable,'-c','print("allowed")']
    ctx=ToolContext(tmp_path,lambda _:False,False,policy=PermissionPolicy('restricted',[argv]))
    registry=build_default_registry()
    assert registry.execute(ToolCall('a','run_command',json.dumps({'argv':argv})),ctx).ok
    assert not registry.execute(ToolCall('b','run_command',json.dumps({'argv':argv+['extra']})),ctx).ok
    assert [e['allowed'] for e in ctx.audit]==[True,False]


def test_checkpoint_unknown_blocks_replay_until_human_resolution(tmp_path):
    from fast_agent.tasks import TaskStore
    registry=build_default_registry()
    def interrupted(args,ctx):
        (tmp_path/'count').write_text('1')
        raise KeyboardInterrupt
    registry.register(Tool('effect','',object_schema({},[]),interrupted))
    agent=Agent(Sequence([ModelReply(tool_calls=[ToolCall('one','effect','{}'),ToolCall('two','effect','{}')])]),registry,ToolContext(tmp_path,lambda _:True,True),checkpoints=True)
    assert not agent.run('do once').completed
    task=agent.last_task_id
    state=TaskStore(tmp_path).load(task)
    assert state['uncertain']==['one']
    assert '未执行' in state['messages'][-1]['content']
    restored=Agent(Sequence([ModelReply(tool_calls=[ToolCall('again','effect','{}')]),ModelReply('blocked')]),registry,ToolContext(tmp_path,lambda _:True,True),checkpoints=True)
    restored.resume(task)
    assert restored.run('continue').completed
    assert (tmp_path/'count').read_text()=='1'
    assert '未核实' in restored.history[-2].content
    TaskStore(tmp_path).resolve(task,'已检查 count 为 1，不重放')
    assert not TaskStore(tmp_path).blockers()


def test_completed_tools_restored_without_replay(tmp_path):
    from fast_agent.tasks import TaskStore
    a=Agent(Sequence([ModelReply(tool_calls=[ToolCall('x','write_file','{"path":"x","content":"once"}')]),ModelReply('done')]),build_default_registry(),ToolContext(tmp_path,lambda _:True,True),checkpoints=True)
    assert a.run('write').completed
    data=TaskStore(tmp_path).load(a.last_task_id)
    assert data['status']=='completed' and not data['uncertain']
    b=Agent(Sequence([ModelReply('remembered')]),build_default_registry(),ToolContext(tmp_path,lambda _:False,False),checkpoints=True)
    b.resume(a.last_task_id)
    assert b.history[-1].content=='done' and (tmp_path/'x').read_text()=='once'


def test_background_survives_manager_and_cancel(tmp_path):
    from fast_agent.jobs import JobStore
    jobs=JobStore(tmp_path)
    job=jobs.start([sys.executable,'-u','-c','import time; print("ready"); time.sleep(30)'],60)
    try:
        fresh=JobStore(tmp_path)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            state=fresh.poll(job)
            if 'ready' in state['output']: break
            time.sleep(.05)
        assert 'ready' in state['output'] and state['status']=='running'
        fresh.cancel(job)
        while time.monotonic()<deadline:
            state=fresh.poll(job)
            if state['status'] not in {'queued','running'}: break
            time.sleep(.05)
        assert state['status']=='cancelled'
    finally: jobs.cancel(job)


def test_background_timeout_and_bounded_output(tmp_path):
    from fast_agent.jobs import JobStore
    jobs=JobStore(tmp_path)
    job=jobs.start([sys.executable,'-u','-c','import time; print("x"*1200000); time.sleep(10)'],.5)
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        state=jobs.poll(job)
        if state['status'] not in {'queued','running'}: break
        time.sleep(.05)
    assert state['status']=='timed_out' and state['truncated']
    assert len(state['output'].encode())<=65536
    assert jobs.store.path(job+'.log').stat().st_size<=1048576


def test_process_exit_preserves_unknown_checkpoint(tmp_path):
    import subprocess
    from fast_agent.tasks import TaskStore
    script = '''
import os,sys
from pathlib import Path
from fast_agent.agent import Agent
from fast_agent.tools import *
from fast_agent.models import *
class Model:
 def complete(self,messages,tools): return ModelReply(tool_calls=[ToolCall('crash','crash','{}')])
def crash(args,ctx):
 (ctx.root/'once').write_text('effect happened')
 os._exit(17)
r=build_default_registry()
r.register(Tool('crash','',object_schema({},[]),crash))
Agent(Model(),r,ToolContext(Path(sys.argv[1]),lambda _:True,True),checkpoints=True).run('once')
'''
    child=subprocess.run([sys.executable,'-c',script,str(tmp_path)])
    assert child.returncode==17 and (tmp_path/'once').read_text()=='effect happened'
    records=TaskStore(tmp_path).list()
    assert len(records)==1 and records[0]['uncertain']==['crash']


def test_background_survives_starting_process_exit(tmp_path):
    import subprocess
    from fast_agent.jobs import JobStore
    script='from pathlib import Path; import sys; from fast_agent.jobs import JobStore; print(JobStore(Path(sys.argv[1])).start([sys.executable,"-c","import time; time.sleep(.2); print(123)"],10))'
    child=subprocess.run([sys.executable,'-c',script,str(tmp_path)],capture_output=True,text=True,check=True)
    job=child.stdout.strip()
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        state=JobStore(tmp_path).poll(job)
        if state['status'] not in {'queued','running'}: break
        time.sleep(.05)
    assert state['status']=='succeeded' and state['returncode']==0 and state['output']=='123\n'


def test_cli_checkpoint_resume_and_readonly(tmp_path,capsys):
    from fast_agent.cli import main
    from fast_agent.tasks import TaskStore
    assert main(['--mock','--workspace',str(tmp_path),'--permissions','read-only','--prompt','hello'])==0
    task=TaskStore(tmp_path).list()[0]['id']
    assert main(['--mock','--workspace',str(tmp_path),'--resume',task,'--prompt','continue'])==0
    assert len(TaskStore(tmp_path).list())==2


def test_uncertain_tool_exception_stops_further_mutations(tmp_path):
    from fast_agent.tasks import TaskStore
    registry=build_default_registry()
    def broken(args,ctx):
        (tmp_path/'first').write_text('changed')
        raise RuntimeError('post-write error')
    registry.register(Tool('broken','',object_schema({},[]),broken))
    a=Agent(Sequence([ModelReply(tool_calls=[ToolCall('x','broken','{}'),ToolCall('y','write_file','{"path":"second","content":"bad"}')])]),registry,ToolContext(tmp_path,lambda _:True,True),checkpoints=True)
    assert not a.run('do').completed
    assert not (tmp_path/'second').exists()
    assert TaskStore(tmp_path).blockers()==[a.last_task_id]


def test_task_lock_and_symlink_fail_closed(tmp_path):
    from fast_agent.tasks import TaskStore
    store=TaskStore(tmp_path)
    with store.store.lock():
        with pytest.raises(ValueError):
            with store.store.lock(): pass
    outside=tmp_path/'outside';outside.write_text('{}')
    store.store.path('bad.json').symlink_to(outside)
    with pytest.raises(ValueError): store.load('bad')


@pytest.mark.parametrize('failure',[OSError('disk'),ValueError('after effect')])
def test_handler_errors_preserve_uncertainty(tmp_path,failure):
    from fast_agent.tasks import TaskStore
    registry=build_default_registry()
    def broken(args,ctx):
        (tmp_path/'first').write_text('changed')
        raise failure
    registry.register(Tool('broken','',object_schema({},[]),broken))
    a=Agent(Sequence([ModelReply(tool_calls=[ToolCall('x','broken','{}')]),ModelReply('done')]),registry,ToolContext(tmp_path,lambda _:True,True),checkpoints=True)
    assert not a.run('do').completed
    assert TaskStore(tmp_path).blockers()==[a.last_task_id]


def test_lost_job_blocks_new_mutations_until_resolved(tmp_path):
    from fast_agent.jobs import JobStore
    from fast_agent.tasks import TaskStore
    jobs=JobStore(tmp_path)
    job='a'*32
    jobs.store.write(job+'.json',{'id':job,'workspace':str(tmp_path.resolve()),'status':'running','updated':time.time()-20,'argv':['fake'],'returncode':None,'truncated':False})
    ctx=ToolContext(tmp_path,lambda _:True,True)
    r=build_default_registry().execute(ToolCall('x','write_file','{"path":"x","content":"bad"}'),ctx)
    assert not r.ok and not (tmp_path/'x').exists()
    assert TaskStore(tmp_path).blockers()==['job:'+job]
    jobs.resolve(job,'已核实进程结束且无需重放')
    assert not TaskStore(tmp_path).blockers()


def test_precancelled_job_does_not_spawn(tmp_path,monkeypatch):
    from fast_agent.jobs import JobStore
    from fast_agent import job_worker
    jobs=JobStore(tmp_path);job='b'*32
    jobs.store.write(job+'.json',{'id':job,'workspace':str(tmp_path.resolve()),'status':'queued','updated':time.time(),'argv':['never-execute'],'timeout':10,'returncode':None,'truncated':False})
    jobs.cancel(job)
    def must_not_spawn(*args,**kwargs): raise AssertionError('cancelled job was spawned')
    monkeypatch.setattr(job_worker.subprocess,'Popen',must_not_spawn)
    job_worker.run(tmp_path,job)
    assert jobs.poll(job)['status']=='cancelled'


@pytest.mark.parametrize('path',['../outside.txt','.fast-agent/tasks/x.json'])
def test_preflight_path_rejection_does_not_claim_unknown_effect(tmp_path,path):
    from fast_agent.tasks import TaskStore
    call=ToolCall('x','write_file',json.dumps({'path':path,'content':'bad'}))
    a=Agent(Sequence([ModelReply(tool_calls=[call]),ModelReply('路径已被拒绝')]),build_default_registry(),ToolContext(tmp_path,lambda _:True,True),checkpoints=True)
    assert a.run('do').completed
    assert not json.loads(a.history[-2].content)['ok']
    assert not TaskStore(tmp_path).blockers()
