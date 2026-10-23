"""MiniMax 工程核心组合验证：预算、沙箱、批次恢复、计划、质量检查和扩展。"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.edit_batches import EditBatches
from fast_agent.providers import OpenAIModel, ProviderError
from fast_agent.extensions import load_extensions
from fast_agent.quality_checks import load_checks, CheckRuns
from fast_agent.task_plans import PlanStore
from fast_agent.sessions import SessionStore, validate_history
from fast_agent.tools import ToolContext, build_default_registry


def main():
    with tempfile.TemporaryDirectory(prefix='fast-agent-batches-') as directory:
        root=Path(directory).resolve()
        originals={'add.py':'def add(a,b):\n    return a-b\n', 'multiply.py':'def multiply(a,b):\n    return a+b\n'}
        for name,text in originals.items(): (root/name).write_text(text)
        test=root/'test_math.py'
        test.write_text('import unittest\nfrom add import add\nfrom multiply import multiply\nclass Tests(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2,3),5)\n    def test_multiply(self): self.assertEqual(multiply(2,3),6)\n')
        initial_test=test.read_bytes()
        command=[sys.executable,'-B','-m','unittest','-v']
        (root/'AGENTS.md').write_text('只允许修改 add.py 和 multiply.py，禁止修改测试。必须使用 edit_files 一次批次修改两个文件；测试命令：'+shlex.join(command))
        (root/'.gitignore').write_text('.fast-agent/\n__pycache__/\n')
        inspector=root/'inspect_source.py'
        inspector.write_text("import json,sys\nfrom pathlib import Path\njson.loads(sys.argv[-1])\nprint(json.dumps({'ok':True,'data':{'broken':[p for p,bad in [('add.py','return a-b'),('multiply.py','return a+b')] if bad in Path(p).read_text()]},'error':None}))\n")
        extpath=root/'extensions.json'
        extpath.write_text(json.dumps({'tools':[{'name':'inspect_source','description':'分析两个运算源码，返回仍含错误实现的文件列表',
            'argv':[sys.executable,str(inspector)],'parameters':{'type':'object','properties':{}}}]}))
        checkpath=root/'checks.json'
        checkpath.write_text(json.dumps({'tests':{'argv':command,'timeout':30},'diff':{'argv':['git','diff','--check'],'timeout':10}}))
        checks=load_checks(checkpath)
        for argv in (['git','init','-q'],['git','add','.'],['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture']):
            subprocess.run(argv,cwd=root,check=True,capture_output=True)
        events=[]
        def approve(prompt):
            if prompt in (f'执行命令 {shlex.join(command)}？','执行命令 git diff --check？'): return True
            if prompt.startswith(('更新任务计划 engineering\n','执行命令 '+shlex.join([sys.executable,str(inspector)])+' ')):return True
            if prompt.startswith(('修改多文件批次\n','恢复多文件批次 ')):
                headers=[line[4:] for line in prompt.splitlines() if line.startswith(('--- ','+++ '))]
                return set(headers)=={str(root/name) for name in originals}
            return False
        def make():
            model=OpenAIModel(Config.from_env(os.environ))
            registry=build_default_registry()
            for tool in load_extensions(extpath):registry.register(tool)
            agent=Agent(model,registry,ToolContext(root,approve,True,sandbox='macos',quality_checks=checks),
                max_steps=25,stream=True,checkpoints=True,on_event=lambda e:events.append({'kind':e.kind,'data':e.data}))
            return model,agent
        model,agent=make()
        first=agent.run('完成工程任务：先 get_plan engineering 并 update_plan 建立读取/修复/验证步骤；调用 ext_inspect_source 分析（空参数），list_checks 后 run_checks tests 复现失败。读取 AGENTS.md、源码和测试，用 edit_files 一个批次修复两个文件。再调用扩展验证没有错误，用 run_checks 执行 tests 和 diff，通过后更新计划completed。不要修改测试或配置。')
        SessionStore(root).save('first',agent.history)
        first_check_runs=CheckRuns(root).list()
        def passed_names(records):
            return {entry['name'] for record in records for entry in CheckRuns(root).load(record['id'])['results'] if entry['result']['ok']}
        first_quality_passed=set(checks)<=passed_names(first_check_runs)
        first_usage=model.usage.snapshot()
        model.usage.max_requests=model.usage.requests
        budget_stopped=False
        try:model.complete([],[])
        except ProviderError as exc:budget_stopped='请求预算' in str(exc)
        model.client.close()
        records=EditBatches(root).list()
        model,restored=make()
        restored.restore_history(SessionStore(root).load('first'))
        plan=restored.context.mode=='plan'
        restored.set_mode('execute')
        undone=False
        if first.completed and len(records)==1 and records[0]['status']=='completed':
            undone=EditBatches(root).undo(records[0]['id'],restored.context).ok
        exact_restore=all((root/name).read_text()==text for name,text in originals.items())
        after_undo=subprocess.run(command,cwd=root,capture_output=True,text=True)
        final=restored.continue_task('用户已撤销批次修改。先 get_plan engineering 查询持久计划，但旧完成状态不能替代磁盘检查；调用 ext_inspect_source 重新分析，读取源码并用 edit_files 再修复两个文件，用 run_checks tests和diff再次验证。最后更新计划说明此次恢复后验证结果。不要修改测试或配置。')
        SessionStore(root).save('finished',restored.history)
        validate_history(SessionStore(root).load('finished'))
        after=subprocess.run(command,cwd=root,capture_output=True,text=True)
        checks=dict(first_completed=first.completed, first_single_batch=len(records)==1,
            loaded_plan=plan, undo_ok=undone, undo_exact=exact_restore,
            undone_tests_fail=after_undo.returncode!=0, resumed_completed=final.completed,
            final_tests_pass=after.returncode==0, tests_unchanged=test.read_bytes()==initial_test,
            completed_batches=sum(r['status']=='completed' for r in EditBatches(root).list())==1,
            tool_test_pass=any(e['kind']=='tool_end' and e['data']['name']=='run_checks' and e['data']['result']['ok'] for e in events),
            budget_preflight=budget_stopped,
            first_all_checks_passed=first_quality_passed,
            restored_all_checks_passed=set(checks)<=passed_names([r for r in CheckRuns(root).list() if r['id'] not in {x['id'] for x in first_check_runs}]),
            plan_completed=PlanStore(root).load('engineering')['revision']>=2 and all(s['status']=='completed' for s in PlanStore(root).load('engineering')['steps']),
            external_tool_used=sum(e['kind']=='tool_end' and e['data']['name']=='ext_inspect_source' and e['data']['result']['ok'] for e in events)>=2,
            sandbox_checks=all(CheckRuns(root).load(r['id'])['sandbox']=='macos' for r in CheckRuns(root).list()),
            git_scope=set(subprocess.run(['git','diff','--name-only'],cwd=root,capture_output=True,text=True,check=True).stdout.splitlines())==set(originals))
        report=dict(verified=all(checks.values()),checks=checks,first_text=first.text,final_text=final.text,
                    first_usage=first_usage,second_usage=model.usage.snapshot(),events=events,test_output=after.stdout+after.stderr)
        model.client.close()
        print(json.dumps(report,ensure_ascii=False,indent=2))
        if not report['verified']: raise SystemExit(1)


if __name__=='__main__': main()
