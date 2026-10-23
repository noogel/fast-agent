"""MiniMax：读取与规划 → /compact → 保存 → 新进程恢复并完成修复。"""
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from fast_agent.agent import Agent
from fast_agent.compaction import ContextPolicy, SUMMARY_MARKER
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.sessions import SessionStore
from fast_agent.session_commands import handle_session_command
from fast_agent.tools import ToolContext, build_default_registry


def worker(phase, root):
    command=[sys.executable,'-m','unittest','discover','-s','tests','-v']
    events=[]
    def observe(event):
        events.append({'kind':event.kind,'data':event.data})
        if event.kind in {'tool_start','model_retry'}:
            print(json.dumps({'kind':event.kind,'data':event.data},ensure_ascii=False),file=sys.stderr,flush=True)
    def approve(prompt):
        return prompt == '执行命令 '+shlex.join(command)+'？' or prompt.startswith(f'修改文件 {root}/calc.py\n')
    model=OpenAIModel(Config.from_env(os.environ))
    agent=Agent(model,build_default_registry(),ToolContext(root,approve,True),max_steps=15,
                stream=True,checkpoints=True,plan=True,context_policy=ContextPolicy(0,1),
                on_event=observe)
    try:
        report={'pid':os.getpid(),'phase':phase}
        if phase=='prepare':
            result=agent.run('任务编号 TASK-C19。目标修复 calc.py 的 add，禁止修改测试；最终总结注明任务编号。请 read_file 读取 background.txt、calc.py、tests/test_calc.py，然后仅给出中文计划，不修改或运行命令。')
            if not result.completed: raise RuntimeError(result.text)
            result=agent.run('确认收到计划。本轮不要调用工具，简短回复等待本地切换执行。')
            if not result.completed: raise RuntimeError(result.text)
            original=list(agent.history)
            captured=io.StringIO()
            with redirect_stdout(captured):
                handle_session_command('/compact 重点保留任务编号、禁止修改测试、待修复函数和验证方式',agent)
            summary=next((agent.history[i+1].content for i,m in enumerate(agent.history[:-1]) if m.content==SUMMARY_MARKER),'')
            backup_files=list((root/'.fast-agent/sessions').glob('manual-*.json'))
            report.update(compact_output=captured.getvalue(),summary=summary,
                          backup_exact=bool(backup_files) and SessionStore(root).load(backup_files[0].stem)==original,
                          mode=agent.context.mode)
            SessionStore(root).save('compressed',agent.history)
        else:
            with redirect_stdout(sys.stderr): handle_session_command('/load compressed',agent)
            report['restored_mode']=agent.context.mode
            report['restored_summary']=any(m.content==SUMMARY_MARKER for m in agent.history)
            with redirect_stdout(sys.stderr): handle_session_command('/execute',agent)
            result=agent.run('请继续完成恢复的任务。先重新读取源码和测试，再 run_command 用规则中的精确命令复现失败，edit_file 修复函数，重跑测试，git_diff 核对修改范围。最后中文总结任务编号和结果。')
            if not result.completed: raise RuntimeError(result.text)
            report['answer']=result.text
        report['calls']=[e['data']['name'] for e in events if e['kind']=='tool_start']
        report['runs']=[e['data']['result'] for e in events if e['kind']=='tool_end' and e['data']['name']=='run_command']
        return report
    finally:
        model.client.close()


def main():
    if len(sys.argv)>1:
        print(json.dumps(worker(sys.argv[1],Path(sys.argv[2])),ensure_ascii=False));return
    with tempfile.TemporaryDirectory(prefix='fast-agent-manual-compact-') as directory:
        root=Path(directory).resolve();(root/'tests').mkdir()
        (root/'calc.py').write_text('def add(a, b):\n    return a - b\n')
        test=root/'tests/test_calc.py'
        test.write_text('import unittest\nfrom calc import add\nclass TestAdd(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2,3),5)\n')
        original_test=test.read_bytes()
        (root/'background.txt').write_text('历史资料：函数必须保留 add(a, b) 签名，测试不得修改。\n'+'Reference notes: preserve the public API and verify behavior with unit tests.\n'*250)
        command=[sys.executable,'-m','unittest','discover','-s','tests','-v']
        (root/'AGENTS.md').write_text('只修改 calc.py，保留签名；测试命令：'+shlex.join(command))
        (root/'.gitignore').write_text('.fast-agent/\n__pycache__/\n')
        def git(*args): return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True,text=True).stdout
        git('init','-q');git('add','.');git('-c','user.name=Demo','-c','user.email=demo@example.invalid','commit','-qm','fixture')
        phases=[]
        for phase in ['prepare','continue']:
            process=subprocess.run([sys.executable,__file__,phase,str(root)],capture_output=True,text=True,timeout=600)
            if process.returncode: raise RuntimeError(process.stderr[-4000:])
            phases.append(json.loads(process.stdout));print('完成 '+phase,file=sys.stderr,flush=True)
            progress=Path(__file__).resolve().parents[1]/'.fast-agent/validation/manual-compaction-progress.json'
            progress.write_text(json.dumps(phases,ensure_ascii=False,indent=2))
            if phase=='prepare' and git('diff'): raise RuntimeError('规划改变了文件')
        prepared,continued=phases
        final=subprocess.run(command,cwd=root,capture_output=True,text=True)
        checks={'summary_created':bool(prepared['summary']),'constraint_retained':'禁止修改测试' in prepared['summary'] or '测试不得修改' in prepared['summary'],
                'identifier_retained':'TASK-C19' in prepared['summary'],'backup_exact':prepared['backup_exact'],
                'plan_preserved':prepared['mode']=='plan','restored_plan':continued['restored_mode']=='plan',
                'restored_summary':continued['restored_summary'],'different_process':prepared['pid']!=continued['pid'],
                'continued_identifier':'TASK-C19' in continued['answer'],
                'tools_completed':{'read_file','run_command','edit_file','git_diff'}<=set(continued['calls']),
                'failure_then_success':len(continued['runs'])>=2 and continued['runs'][0]['data']['returncode']!=0 and continued['runs'][-1]['data']['returncode']==0,
                'tests_pass':final.returncode==0,'tests_unchanged':test.read_bytes()==original_test,'only_source_changed':git('diff','--name-only').splitlines()==['calc.py']}
        report={'verified':all(checks.values()),'checks':checks,'phases':phases,'test_output':final.stdout+final.stderr}
        dest=Path(__file__).resolve().parents[1]/'.fast-agent/validation/manual-compaction-demo.json'
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({'verified':report['verified'],'checks':checks},ensure_ascii=False,indent=2))
        if not report['verified']: raise SystemExit(1)


if __name__=='__main__': main()
