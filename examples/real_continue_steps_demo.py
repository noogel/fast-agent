"""MiniMax：低步数暂停、保存恢复、/steps 与 /continue 完成修复。"""
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from fast_agent.sessions import SessionStore
from real_multi_edit_demo import git
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.tools import ToolContext, build_default_registry
from fast_agent.tasks import TaskStore


def worker(phase, root):
    model=OpenAIModel(Config.from_env(os.environ))
    try:
        agent=Agent(model,build_default_registry(),ToolContext(root,lambda _:False,False),
                    max_steps=1,checkpoints=True,plan=True)
        result=agent.run('目标：修复 src/math_utils.py 的 add 和 multiply，保留签名，禁止修改测试。先调用 read_file 读取源码和 tests/test_math.py；本次必须先使用工具获取事实，不要凭空给出答案。')
        SessionStore(root).save('planned',agent.history)
        return {'pid':os.getpid(),'completed':result.completed,'text':result.text,'steps':result.steps,
                'task_id':agent.last_task_id,'status':TaskStore(root).load(agent.last_task_id)['status'],
                'tool_results':sum(m.role=='tool' for m in agent.history)}
    finally:
        model.client.close()


def cli_worker(root):
    import fast_agent.cli as cli
    command=[sys.executable,'-m','unittest','discover','-s','tests','-v']
    approvals=[];events=[]
    def approve(prompt):
        allowed=prompt==f'执行命令 {shlex.join(command)}？' or prompt.startswith(f'修改文件 {root}/src/math_utils.py\n')
        approvals.append({'allowed':allowed,'prompt':prompt});return allowed
    class Input(io.StringIO):
        def isatty(self):return True
    cli.confirm=approve
    cli.show_event=lambda event:events.append({'kind':event.kind,'data':event.data})
    sys.stdin=Input('/sessions\n/context\n/steps 15\n/execute\n/continue 请继续已恢复任务。先读取源码和测试，用 run_command 同步运行规则里的精确测试命令复现失败，multi_edit_file 修复两个函数并添加中文注释，重跑测试，git_diff 检查；禁止修改测试。\n/save finished\n/exit\n')
    output=io.StringIO()
    with redirect_stdout(output):
        code=cli.main(['--workspace',str(root),'--load','planned','--max-steps','1'])
    return {'pid':os.getpid(),'code':code,'output':output.getvalue(),'approvals':approvals,'events':events}


def main():
    if len(sys.argv)>1:
        root=Path(sys.argv[2])
        report=worker('plan',root) if sys.argv[1]=='prepare' else cli_worker(root)
        print(json.dumps(report,ensure_ascii=False));return
    with tempfile.TemporaryDirectory(prefix='fast-agent-session-load-') as directory:
        root=Path(directory).resolve();(root/'src').mkdir();(root/'tests').mkdir()
        (root/'src/math_utils.py').write_text('def add(a, b):\n    return a - b\n\ndef multiply(a, b):\n    return a + b\n')
        test=root/'tests/test_math.py'
        test.write_text('import unittest\nfrom src.math_utils import add, multiply\nclass Tests(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2,3),5)\n    def test_multiply(self): self.assertEqual(multiply(2,3),6)\n')
        before=test.read_bytes()
        command=[sys.executable,'-m','unittest','discover','-s','tests','-v']
        (root/'AGENTS.md').write_text('只允许修改 src/math_utils.py，禁止修改测试，保留两个函数签名，添加中文注释。测试命令：'+shlex.join(command))
        (root/'.gitignore').write_text('.fast-agent/\n__pycache__/\n')
        git(root,'init','-q');git(root,'add','.');git(root,'-c','user.name=Demo','-c','user.email=demo@example.invalid','commit','-qm','fixture')
        phases=[]
        for phase in ['prepare','continue']:
            process=subprocess.run([sys.executable,__file__,phase,str(root)],capture_output=True,text=True,timeout=600)
            if process.returncode:raise RuntimeError(process.stderr[-3000:])
            phases.append(json.loads(process.stdout));print('完成 '+phase,file=sys.stderr,flush=True)
            if phase=='prepare':
                listing=SessionStore(root).list()
                plan_unchanged=not git(root,'diff')
        prepared,continued=phases
        output=continued['output'];events=continued['events']
        calls=[e['data']['name'] for e in events if e['kind']=='tool_start']
        runs=[e['data']['result'] for e in events if e['kind']=='tool_end' and e['data']['name']=='run_command']
        final=subprocess.run(command,cwd=root,capture_output=True,text=True)
        saved=SessionStore(root).load('finished')
        checks={'paused_at_limit':not prepared['completed'] and prepared['steps']==1 and prepared['status']=='incomplete' and prepared['tool_results']>0,
                'resume_hint':prepared['task_id'] in prepared['text'] and '/continue' in prepared['text'],
                'steps_changed':'每轮模型调用上限：15' in output,
                'listed':any(r['name']=='planned' for r in listing['sessions']),
                'plan_unchanged':plan_unchanged,'different_process':prepared['pid']!=continued['pid'],
                'cli_success':continued['code']==0,'loaded_plan':'[模式] 计划模式' in output,
                'context_before_execute':output.index('当前模式：plan')<output.index('已切换执行模式'),
                'tools_used':{'read_file','run_command','multi_edit_file','git_diff'}<=set(calls),
                'failure_then_success':len(runs)>=2 and runs[0]['data']['returncode']!=0 and runs[-1]['data']['returncode']==0,
                'tests_pass':final.returncode==0,'tests_unchanged':test.read_bytes()==before,
                'only_source_changed':git(root,'diff','--name-only').splitlines()==['src/math_utils.py'],
                'saved_continuation':len(saved)>len(SessionStore(root).load('planned')),
                'approvals_scoped':any(a['allowed'] for a in continued['approvals']) and all(not a['allowed'] or a['prompt']==f'执行命令 {shlex.join(command)}？' or a['prompt'].startswith(f'修改文件 {root}/src/math_utils.py\n') for a in continued['approvals'])}
        report={'verified':all(checks.values()),'checks':checks,'listing':listing,'phases':phases,'test_output':final.stdout+final.stderr}
        dest=Path(__file__).resolve().parents[1]/'.fast-agent/validation/continue-steps-demo.json'
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({'verified':report['verified'],'checks':checks},ensure_ascii=False,indent=2))
        if not report['verified']:raise SystemExit(1)


if __name__=='__main__':main()
