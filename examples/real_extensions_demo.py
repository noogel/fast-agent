"""真实模型验证：外部分析工具、仓库修复、质量检查及恢复后再调用扩展。"""
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
from fast_agent.extensions import load_extensions
from fast_agent.quality_checks import CheckRuns, load_checks
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
        command = [sys.executable, '-B', '-m', 'unittest', '-v']
        checks_path = root/'checks.json'
        checks_path.write_text(json.dumps({'tests':{'argv':command,'timeout':30}}))
        checks = load_checks(checks_path)
        inspector=root/'inspect_source.py'
        inspector.write_text("import json,sys\nfrom pathlib import Path\na=json.loads(sys.argv[-1])\nassert a['filename']=='math_utils.py'\ntext=Path(a['filename']).read_text()\nprint(json.dumps({'ok':True,'data':{'contains_subtraction':'return a - b' in text},'error':None}))\n")
        extension_path=root/'extensions.json'
        extension_path.write_text(json.dumps({'tools':[{'name':'inspect_source','description':'检查 math_utils.py 的 add 是否仍包含减法实现',
            'argv':[sys.executable,str(inspector)],'parameters':{'type':'object','properties':{'filename':{'type':'string'}},'required':['filename']}}]}))
        def registry():
            result=build_default_registry()
            for tool in load_extensions(extension_path):result.register(tool)
            return result
        (root/'AGENTS.md').write_text('只能修改 math_utils.py，禁止修改测试。测试命令：'+shlex.join(command))
        events = []
        approve = lambda prompt: prompt == f'执行命令 {shlex.join(command)}？' or prompt.startswith(f'修改文件 {source}\n') or prompt.startswith('执行命令 '+shlex.join([sys.executable,str(inspector)])+' ')
        first = OpenAIModel(replace(config, max_requests=25))
        agent = Agent(first, registry(), ToolContext(root, approve, True, sandbox=sandbox, quality_checks=checks), stream=True, checkpoints=True, max_steps=20, on_event=lambda e: events.append({'kind':e.kind,'data':e.data}))
        result = agent.run('先使用 ext_inspect_source 检查 math_utils.py（filename参数），再 list_checks 查看配置，用 run_checks 运行 tests 复现失败。读取源码测试并修复 add，再调用 ext_inspect_source 确认减法已消失，并 run_checks tests 验证成功。禁止修改测试或检查配置。必须使用配置的质量检查工具，不用 run_command 代替。')
        SessionStore(root).save('budget-paused', agent.history)
        first_usage = first.usage.snapshot()
        first_runs = CheckRuns(root).list()
        first.client.close()
        second = OpenAIModel(replace(config, max_requests=15))
        restored = Agent(second, registry(), ToolContext(root, approve, True, sandbox=sandbox, quality_checks=checks), stream=True,
                         checkpoints=True, max_steps=12, on_event=lambda e: events.append({'kind':e.kind,'data':e.data}))
        restored.restore_history(SessionStore(root).load('budget-paused'))
        was_plan = restored.context.mode == 'plan'
        restored.set_mode('execute')
        final = restored.continue_task('会话已恢复。再次调用 ext_inspect_source 检查 math_utils.py，再读取 list_checks 配置，再用 run_checks tests 重跑质量检查。不要修改文件；用中文说明实际退出码。')
        SessionStore(root).save('finished', restored.history)
        validate_history(SessionStore(root).load('finished'))
        check = subprocess.run(command, cwd=root, capture_output=True, text=True)
        checks = dict(first_completed=result.completed,
                      failed_then_passed=any(r['status']=='failed' for r in first_runs) and any(r['status']=='passed' for r in first_runs),
                      report_persisted=all(CheckRuns(root).load(r['id']) for r in first_runs),
                      restored_run=len(CheckRuns(root).list())>len(first_runs),
                      restored_plan=was_plan, completed=final.completed,
                      tool_loop=any(e['kind']=='tool_end' and e['data']['name']=='run_checks' for e in events),
                      tests_pass=check.returncode==0,
                      extension_before=any(e['kind']=='tool_end' and e['data']['name']=='ext_inspect_source' and e['data']['result']['ok'] and e['data']['result']['data']['result']['contains_subtraction'] for e in events),
                      extension_after=sum(e['kind']=='tool_end' and e['data']['name']=='ext_inspect_source' and e['data']['result']['ok'] and not e['data']['result']['data']['result']['contains_subtraction'] for e in events)>=2, tests_unchanged=test.read_bytes()==original,
                      saved_history=True)
        report = dict(sandbox=sandbox, verified=all(checks.values()), checks=checks, first_usage=first_usage,
                      resumed_usage=second.usage.snapshot(), events=events, final=final.text,
                      test_output=check.stdout+check.stderr, runs=CheckRuns(root).list(), first_text=result.text)
        second.client.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report['verified']: raise SystemExit(1)


if __name__ == '__main__': main()
