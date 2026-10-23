"""真实模型验证：持久计划、仓库修复、保存加载后重新查询计划。"""
import json
import os
from dataclasses import replace
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.task_plans import PlanStore
from fast_agent.sessions import SessionStore, validate_history
from fast_agent.tools import ToolContext, build_default_registry


def main():
    config = Config.from_env(os.environ)
    sandbox = os.environ.get('FAST_AGENT_DEMO_SANDBOX', 'none')
    with tempfile.TemporaryDirectory(prefix='fast-agent-usage-') as directory:
        root = Path(directory).resolve()
        source = root / 'math_utils.py'
        source.write_text('def add(a, b):\n    return a - b\n')
        test = root / 'test_math.py'
        test.write_text('import unittest\nfrom math_utils import add\nclass Tests(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\n')
        original = test.read_bytes()
        command = [sys.executable, '-m', 'unittest', '-v']
        (root/'AGENTS.md').write_text('只能修改 math_utils.py，禁止修改测试。测试命令：'+shlex.join(command))
        events = []
        approve = lambda prompt: prompt == f'执行命令 {shlex.join(command)}？' or prompt.startswith(f'修改文件 {source}\n') or prompt.startswith('更新任务计划 repair\n')
        first = OpenAIModel(replace(config, max_requests=25))
        agent = Agent(first, build_default_registry(), ToolContext(root, approve, True, sandbox=sandbox), stream=True, checkpoints=True, max_steps=20, on_event=lambda e: events.append({'kind':e.kind,'data':e.data}))
        result = agent.run('先 get_plan 查询 repair，然后 update_plan 创建一个持久计划（读取、修复、测试三个步骤）。读取源码测试并修复 add，运行 AGENTS.md 中测试；成功后 update_plan 按最新版本更新 repair，将三个步骤标为 completed，并在 note 写明实际测试结果。禁止修改测试。')
        SessionStore(root).save('budget-paused', agent.history)
        first_usage = first.usage.snapshot()
        first_plan = PlanStore(root).load('repair')
        first.client.close()
        second = OpenAIModel(replace(config, max_requests=15))
        restored = Agent(second, build_default_registry(), ToolContext(root, approve, True, sandbox=sandbox), stream=True,
                         checkpoints=True, max_steps=12, on_event=lambda e: events.append({'kind':e.kind,'data':e.data}))
        restored.restore_history(SessionStore(root).load('budget-paused'))
        was_plan = restored.context.mode == 'plan'
        restored.set_mode('execute')
        final = restored.continue_task('会话已恢复。必须通过 get_plan 重新读取 repair 持久计划，读取源码，并用 run_command 重跑规定测试核实完成状态。不要修改文件或计划；用中文说明结果。')
        SessionStore(root).save('finished', restored.history)
        validate_history(SessionStore(root).load('finished'))
        check = subprocess.run(command, cwd=root, capture_output=True, text=True)
        checks = dict(first_completed=result.completed,
                      plan_saved=first_plan['revision']>=2 and all(step['status']=='completed' for step in first_plan['steps']),
                      plan_preserved=PlanStore(root).load('repair')==first_plan,
                      get_plan_called=sum(e['kind']=='tool_end' and e['data']['name']=='get_plan' and e['data']['result']['ok'] for e in events)>=2,
                      restored_plan=was_plan, completed=final.completed,
                      tool_loop=any(e['kind']=='tool_end' and e['data']['name']=='run_command' for e in events),
                      tests_pass=check.returncode==0, tests_unchanged=test.read_bytes()==original,
                      saved_history=True)
        report = dict(sandbox=sandbox, verified=all(checks.values()), checks=checks, first_usage=first_usage,
                      resumed_usage=second.usage.snapshot(), events=events, final=final.text,
                      test_output=check.stdout+check.stderr, plan=first_plan, first_text=result.text)
        second.client.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report['verified']: raise SystemExit(1)


if __name__ == '__main__': main()
