"""MiniMax + 官方 SDK stdio 服务：真实修复、检查、保存和新连接恢复。"""
import json
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.mcp_tools import MCPManager
from fast_agent.providers import OpenAIModel
from fast_agent.quality_checks import load_checks, CheckRuns
from fast_agent.sessions import SessionStore, validate_history
from fast_agent.tools import ToolContext, build_default_registry


def main():
    config = replace(Config.from_env(os.environ), max_requests=25)
    server = Path(__file__).with_name('mcp_inspector_server.py').resolve()
    with tempfile.TemporaryDirectory(prefix='fast-agent-mcp-') as directory:
        root = Path(directory).resolve()
        source = root/'math_utils.py'
        source.write_text('def add(a, b):\n    return a - b\n')
        test = root/'test_math.py'
        test.write_text('import unittest\nfrom math_utils import add\nclass Tests(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2,3),5)\n')
        original_test = test.read_bytes()
        command = [sys.executable, '-B', '-m', 'unittest', '-v']
        (root/'AGENTS.md').write_text('只能修改 math_utils.py，禁止修改测试和配置。必须使用 run_checks 执行 tests。')
        (root/'.gitignore').write_text('.fast-agent/\n__pycache__/\n')
        checks_path = root/'checks.json'
        checks_path.write_text(json.dumps({'tests':{'argv':command, 'timeout':30}}))
        mcp_path = root/'mcp.json'
        mcp_path.write_text(json.dumps({'servers':[{'name':'repo', 'argv':[sys.executable,str(server)], 'timeout':15}]}))
        for argv in (['git','init','-q'],['git','add','.'],['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture']):
            subprocess.run(argv,cwd=root,check=True,capture_output=True)
        approvals = []
        def approve(prompt):
            allowed = (prompt.startswith('启动 MCP 服务 ') or prompt.startswith('调用 MCP 工具 ')
                       or prompt == f'执行命令 {shlex.join(command)}？' or prompt.startswith(f'修改文件 {source}\n'))
            approvals.append({'prompt':prompt, 'allowed':allowed})
            return allowed
        models, managers = [], []
        def make(events):
            model = OpenAIModel(config)
            models.append(model)
            registry = build_default_registry()
            context = ToolContext(root, approve, True, sandbox='macos', quality_checks=load_checks(checks_path))
            agent = Agent(model, registry, context, max_steps=20, stream=True, checkpoints=True,
                          on_event=lambda e:events.append({'kind':e.kind,'data':e.data}))
            return agent
        def connect(agent):
            manager = MCPManager(mcp_path,agent.context)
            managers.append(manager)
            for tool in manager.start():agent.registry.register(tool)
            return manager
        first_events, second_events = [], []
        try:
            agent = make(first_events)
            manager = connect(agent)
            first = agent.run('先调用 mcp_repo_inspect_source（空参数）诊断源码，再 list_checks 并 run_checks tests 复现失败。读取规则、源码、测试后修复 add，用 MCP 再检查，并 run_checks tests 确认通过。禁止修改测试配置，不用 run_command 替代质量检查。')
            first_runs = CheckRuns(root).list()
            SessionStore(root).save('mcp-first',agent.history)
            manager.close()
            first_closed = all(c.process.poll() is not None for c in manager.clients)
            restored = make(second_events)
            restored.restore_history(SessionStore(root).load('mcp-first'))
            restored_plan = restored.context.mode == 'plan'
            no_replay = not second_events and not any(n.startswith('mcp_') for n in restored.registry.tools)
            restored.set_mode('execute')
            connect(restored)
            final = restored.continue_task('已恢复会话并重新授权连接 MCP。必须重新调用 mcp_repo_inspect_source 获取当前诊断，再用 run_checks tests 重跑检查。不要修改文件，用中文说明当前结果。')
            SessionStore(root).save('mcp-finished',restored.history)
            validate_history(SessionStore(root).load('mcp-finished'))
            independent = subprocess.run(command,cwd=root,capture_output=True,text=True)
            def values(events):
                return [json.loads(e['data']['result']['data']['content'][0]['text'])['contains_subtraction']
                        for e in events if e['kind']=='tool_end' and e['data']['name']=='mcp_repo_inspect_source' and e['data']['result']['ok']]
            first_values, second_values = values(first_events), values(second_events)
            test_results = [e['data']['result']['ok'] for e in first_events if e['kind']=='tool_end' and e['data']['name']=='run_checks']
            new_runs = [r for r in CheckRuns(root).list() if r['id'] not in {x['id'] for x in first_runs}]
            checks = dict(first_completed=first.completed, first_diagnosis=bool(first_values) and first_values[0] is True,
                          repair_diagnosis=False in first_values,
                          failed_then_passed=bool(test_results) and test_results[0] is False and test_results[-1] is True,
                          old_process_closed=first_closed, restored_plan=restored_plan, restore_no_replay=no_replay,
                          resumed_completed=final.completed, fresh_diagnosis=bool(second_values) and second_values[-1] is False,
                          fresh_test_passed=any(r['status']=='passed' for r in new_runs), independent_test_passed=independent.returncode==0,
                          tests_unchanged=test.read_bytes()==original_test,
                          git_scope=subprocess.run(['git','diff','--name-only'],cwd=root,capture_output=True,text=True,check=True).stdout.strip()=='math_utils.py',
                          authorized_reconnect=sum(a['allowed'] and a['prompt'].startswith('启动 MCP 服务 ') for a in approvals)==2,
                          sandbox_checks=all(CheckRuns(root).load(r['id'])['sandbox']=='macos' for r in CheckRuns(root).list()))
            for manager in managers:manager.close()
            checks['all_processes_closed'] = all(c.process.poll() is not None for m in managers for c in m.clients)
            report = dict(verified=all(checks.values()), checks=checks, sdk_version=version('mcp'),
                          model=config.model, first_text=first.text, final_text=final.text,
                          first_events=first_events, second_events=second_events,
                          usage=[m.usage.snapshot() for m in models], test_output=independent.stdout+independent.stderr)
            print(json.dumps(report,ensure_ascii=False,indent=2))
            if not report['verified']:raise SystemExit(1)
        finally:
            for manager in managers:manager.close()
            for model in models:model.client.close()


if __name__ == '__main__':
    main()
