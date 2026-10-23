"""MiniMax/OpenAI 兼容服务：只读规划、跨进程恢复、显式执行与真实测试修复。"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout

from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.session_commands import handle_session_command
from fast_agent.sessions import SessionStore
from fast_agent.tools import ToolContext, build_default_registry


def worker(phase, root, task_id=''):
    command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']
    source = root/'src/math_utils.py'
    approvals = []
    events = []
    def approve(prompt):
        allowed = prompt == f'执行命令 {shlex.join(command)}？' or prompt.startswith(f'修改文件 {source}\n')
        approvals.append({'allowed': allowed, 'prompt': prompt})
        return allowed
    def observe(event):
        events.append({'kind': event.kind, 'data': event.data})
        if event.kind in {'tool_start', 'tool_end', 'model_retry'}:
            print(json.dumps(events[-1], ensure_ascii=False)[:1600], file=sys.stderr, flush=True)
    model = OpenAIModel(Config.from_env(os.environ))
    agent = Agent(model, build_default_registry(), ToolContext(root, approve, True),
                  max_steps=15, stream=True, checkpoints=True, plan=phase=='plan', on_event=observe)
    restored_modes = []
    try:
        if phase == 'plan':
            prompt = (f'当前唯一工作目录为 {root}，文件工具使用相对路径。请分析加法函数为何可能导致测试失败，并给出中文实施计划。'
                      '先 list_files，再 search_code 搜索 add，read_file 读取 src/math_utils.py 和 tests/test_math.py，遵循沿途 AGENTS.md。'
                      '只规划，不执行测试或修改文件。最终说明目标、依据、拟修改文件、步骤、验证方法及待确认事项，列出精确测试命令。')
        else:
            # 两种恢复入口都只能恢复内容，不能从磁盘取得执行授权。
            with redirect_stdout(sys.stderr):
                handle_session_command('/load planned', agent)
            restored_modes.append(agent.context.mode)
            agent.resume(task_id)
            restored_modes.append(agent.context.mode)
            if restored_modes != ['plan', 'plan']:
                raise RuntimeError('恢复历史错误地保留了执行授权')
            with redirect_stdout(sys.stderr):
                handle_session_command('/execute', agent)
            if agent.context.mode != 'execute':
                raise RuntimeError('本地执行切换失败')
            prompt = (f'当前唯一工作目录为 {root}。我已通过本地 /execute 切换执行模式，请按照恢复的计划实施。'
                      '先重新 read_file 读取源码和测试；用 run_command 执行规则给定的精确 argv 复现失败，'
                      '再 edit_file 修复 src/math_utils.py，保留签名并添加中文注释；重跑相同测试，最后 git_status 和 git_diff 核实范围。'
                      '禁止修改测试或执行其他命令；必须实际完成以上步骤后中文总结。')
        result = agent.run(prompt)
        if not result.completed:
            raise RuntimeError(result.text)
        SessionStore(root).save('planned' if phase=='plan' else 'executed', agent.history)
        calls = [e['data']['name'] for e in events if e['kind']=='tool_start']
        results = [e['data']['result'] for e in events if e['kind']=='tool_end' and e['data']['name']=='run_command']
        return {'phase': phase, 'pid': os.getpid(), 'task_id': agent.last_task_id,
                'mode': agent.context.mode, 'restored_modes': restored_modes, 'steps': result.steps,
                'calls': calls, 'command_results': results, 'approvals': approvals,
                'retries': [e['data'] for e in events if e['kind']=='model_retry'],
                'delta_count': sum(e['kind']=='text_delta' for e in events), 'answer': result.text}
    finally:
        model.client.close()


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=True).stdout


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--worker':
        print(json.dumps(worker(sys.argv[2], Path(sys.argv[3]), sys.argv[4] if len(sys.argv)>4 else ''), ensure_ascii=False))
        return 0
    config = Config.from_env(os.environ)
    destination = Path(__file__).resolve().parents[1]/'.fast-agent/validation'
    destination.mkdir(parents=True, exist_ok=True)
    report_path = destination/'plan-demo.json'
    report_path.unlink(missing_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='fast-agent-plan-') as directory:
        root = Path(directory).resolve()
        (root/'src').mkdir(); (root/'tests').mkdir()
        source, test = root/'src/math_utils.py', root/'tests/test_math.py'
        source.write_text('def add(a, b):\n    return a - b\n')
        test.write_text('import unittest\nfrom src.math_utils import add\nclass TestAdd(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n    def test_negative(self):\n        self.assertEqual(add(-1, 1), 0)\n')
        command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']
        (root/'AGENTS.md').write_text('只允许修改 src/math_utils.py，禁止修改 tests。计划阶段只读分析；执行阶段先复现失败，再修改，再重跑测试和检查 Git 差异。\n精确测试命令：'+shlex.join(command)+'\n')
        (root/'src/AGENTS.md').write_text('保留 add(a, b) 签名，修复时加一句中文注释说明加法行为。\n')
        (root/'.gitignore').write_text('__pycache__/\n.fast-agent/\n')
        git(root, 'init', '-q'); git(root, 'add', '.')
        git(root, '-c', 'user.name=Fast Agent Demo', '-c', 'user.email=demo@example.invalid', 'commit', '-qm', 'fixture')
        baseline = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
        if baseline.returncode == 0:
            raise RuntimeError('样本没有失败测试')
        before = {str(p.relative_to(root)): digest(p) for p in [source, test, root/'AGENTS.md', root/'src/AGENTS.md', root/'.gitignore']}
        reports = []
        plan_unchanged = False
        for phase in ['plan', 'execute']:
            extra = [reports[0]['task_id']] if phase=='execute' else []
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', phase, str(root), *extra],
                                     stdout=subprocess.PIPE, text=True, timeout=600)
            if process.returncode:
                raise RuntimeError(f'{phase} 阶段失败')
            report = json.loads(process.stdout); reports.append(report)
            print(json.dumps({k:v for k,v in report.items() if k not in {'answer','approvals','command_results'}}, ensure_ascii=False), flush=True)
            if phase == 'plan':
                plan_unchanged = not git(root, 'status', '--porcelain') and all(digest(root/p)==h for p,h in before.items())
                if not plan_unchanged or report['approvals']:
                    raise RuntimeError('计划阶段修改了项目或触发了操作确认')
        after = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
        changed = git(root, 'diff', '--name-only').splitlines()
        diff = git(root, '--no-pager', 'diff', '--no-ext-diff', '--no-textconv')
        plan, execute = reports
        read_names = {t.name for t in build_default_registry().tools.values() if t.effect=='read'}
        runs = execute['command_results']
        verified = (plan_unchanged and set(plan['calls']) <= read_names
                    and {'list_files','read_file'}.issubset(plan['calls'])
                    and 'src/math_utils.py' in plan['answer'] and 'unittest' in plan['answer']
                    and execute['restored_modes']==['plan','plan'] and execute['mode']=='execute'
                    and plan['pid'] != execute['pid'] and plan['delta_count'] > 0 and execute['delta_count'] > 0
                    and {'read_file','edit_file','run_command','git_status','git_diff'}.issubset(execute['calls'])
                    and len(runs)>=2 and runs[0]['data']['returncode']!=0 and runs[-1]['data']['returncode']==0
                    and after.returncode==0 and changed==['src/math_utils.py'] and digest(test)==before['tests/test_math.py']
                    and '#' in source.read_text() and all(a['allowed'] for a in execute['approvals']))
        report = {'verified': bool(verified), 'seconds': round(time.monotonic()-started, 2),
                  'plan_workspace_unchanged': plan_unchanged, 'baseline_exit': baseline.returncode, 'final_exit': after.returncode,
                  'tests_unchanged': digest(test)==before['tests/test_math.py'], 'changed_files': changed, 'diff': diff,
                  'request_config': {'connect_timeout':config.connect_timeout,'read_timeout':config.read_timeout,'max_retries':config.max_retries},
                  'phases': reports, 'test_output': after.stdout+after.stderr}
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({k:v for k,v in report.items() if k!='phases'}, ensure_ascii=False, indent=2))
        return 0 if verified else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f'验证失败：{exc}', file=sys.stderr)
        raise SystemExit(1)
