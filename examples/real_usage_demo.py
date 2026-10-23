"""真实模型验证：耗尽请求预算、保存、加载、继续工具调用。凭据只读环境。"""
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
        approve = lambda prompt: prompt == f'执行命令 {shlex.join(command)}？' or prompt.startswith(f'修改文件 {source}\n')
        first = OpenAIModel(replace(config, max_requests=1))
        agent = Agent(first, build_default_registry(), ToolContext(root, approve, True, sandbox=sandbox), stream=True, checkpoints=True)
        result = agent.run('请先用 read_file 读取 math_utils.py 和 test_math.py，修复 add，运行 AGENTS.md 中的测试命令验证。必须实际调用工具。')
        SessionStore(root).save('budget-paused', agent.history)
        first_usage = first.usage.snapshot()
        first.client.close()
        second = OpenAIModel(replace(config, max_requests=15))
        restored = Agent(second, build_default_registry(), ToolContext(root, approve, True, sandbox=sandbox), stream=True,
                         checkpoints=True, max_steps=12, on_event=lambda e: events.append({'kind':e.kind,'data':e.data}))
        restored.restore_history(SessionStore(root).load('budget-paused'))
        was_plan = restored.context.mode == 'plan'
        restored.set_mode('execute')
        final = restored.continue_task('继续修复 add，先读取文件确认现状，只改 math_utils.py，使用 run_command 执行指定测试，完成后说明结果。')
        SessionStore(root).save('finished', restored.history)
        validate_history(SessionStore(root).load('finished'))
        check = subprocess.run(command, cwd=root, capture_output=True, text=True)
        checks = dict(budget_paused=not result.completed and '请求预算' in result.text,
                      first_request_count=first_usage['requests']==1,
                      restored_plan=was_plan, completed=final.completed,
                      tool_loop=any(e['kind']=='tool_end' and e['data']['name']=='run_command' for e in events),
                      tests_pass=check.returncode==0, tests_unchanged=test.read_bytes()==original,
                      saved_history=True)
        report = dict(sandbox=sandbox, verified=all(checks.values()), checks=checks, first_usage=first_usage,
                      resumed_usage=second.usage.snapshot(), events=events, final=final.text,
                      test_output=check.stdout+check.stderr)
        second.client.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report['verified']: raise SystemExit(1)


if __name__ == '__main__': main()
