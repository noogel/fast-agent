"""MiniMax 多文件修复、保存恢复、人工撤销与再次修复；仅使用临时夹具。"""
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
from fast_agent.providers import OpenAIModel
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
        events=[]
        def approve(prompt):
            if prompt==f'执行命令 {shlex.join(command)}？': return True
            if prompt.startswith(('修改多文件批次\n','恢复多文件批次 ')):
                headers=[line[4:] for line in prompt.splitlines() if line.startswith(('--- ','+++ '))]
                return set(headers)=={str(root/name) for name in originals}
            return False
        def make():
            model=OpenAIModel(Config.from_env(os.environ))
            agent=Agent(model,build_default_registry(),ToolContext(root,approve,True),
                max_steps=15,stream=True,checkpoints=True,on_event=lambda e:events.append({'kind':e.kind,'data':e.data}))
            return model,agent
        model,agent=make()
        first=agent.run('读取 AGENTS.md、两个源码和测试，用 edit_files 一个批次修复 add.py 与 multiply.py 的运算错误，然后 run_command 执行规定测试。不要修改测试。')
        SessionStore(root).save('first',agent.history)
        first_usage=model.usage.snapshot();model.client.close()
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
        final=restored.continue_task('用户已经显式撤销了上次整批修改。请重新读取两个源码确认磁盘现状，用 edit_files 再修复两个文件，再调用规定测试命令验证；不要依赖历史中的旧文件内容。')
        SessionStore(root).save('finished',restored.history)
        validate_history(SessionStore(root).load('finished'))
        after=subprocess.run(command,cwd=root,capture_output=True,text=True)
        checks=dict(first_completed=first.completed, first_single_batch=len(records)==1,
            loaded_plan=plan, undo_ok=undone, undo_exact=exact_restore,
            undone_tests_fail=after_undo.returncode!=0, resumed_completed=final.completed,
            final_tests_pass=after.returncode==0, tests_unchanged=test.read_bytes()==initial_test,
            completed_batches=sum(r['status']=='completed' for r in EditBatches(root).list())==1,
            tool_test_pass=any(e['kind']=='tool_end' and e['data']['name']=='run_command' and e['data']['result']['ok'] for e in events))
        report=dict(verified=all(checks.values()),checks=checks,first_text=first.text,final_text=final.text,
                    first_usage=first_usage,second_usage=model.usage.snapshot(),events=events,test_output=after.stdout+after.stderr)
        model.client.close()
        print(json.dumps(report,ensure_ascii=False,indent=2))
        if not report['verified']: raise SystemExit(1)


if __name__=='__main__': main()
