"""真实模型修复临时仓库：仅批准指定源码修改和固定 unittest 命令。"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time

from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.tools import ToolContext, build_default_registry


def main():
    config = Config.from_env(os.environ)
    destination = Path(__file__).resolve().parents[1]/'.fast-agent/validation'
    destination.mkdir(parents=True, exist_ok=True)
    (destination/'repository-demo.json').unlink(missing_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='fast-agent-repo-') as directory:
        root = Path(directory).resolve()
        (root / 'src').mkdir()
        (root / 'tests').mkdir()
        source = root / 'src/math_utils.py'
        source.write_text('def add(a, b):\n    return a - b\n', encoding='utf-8')
        test = root / 'tests/test_math.py'
        test.write_text('import unittest\nfrom src.math_utils import add\n\nclass TestAdd(unittest.TestCase):\n    def test_positive(self):\n        self.assertEqual(add(2, 3), 5)\n    def test_mixed(self):\n        self.assertEqual(add(-1, 1), 0)\n', encoding='utf-8')
        test_before = test.read_bytes()
        command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']
        (root / 'AGENTS.md').write_text('只允许修改 src/math_utils.py；禁止修改 tests。先运行测试复现失败，再修复，最后重跑测试并检查 git_diff。\n测试命令：' + shlex.join(command) + '\n', encoding='utf-8')
        (root / 'src/AGENTS.md').write_text('保留 add(a, b) 签名。修复时添加简短中文注释说明加法行为。\n', encoding='utf-8')
        (root / '.gitignore').write_text('__pycache__/\n.fast-agent/\n', encoding='utf-8')
        for argv in [['git', 'init', '-q'], ['git', 'add', '.'], ['git', '-c', 'user.name=Fast Agent Demo', '-c', 'user.email=demo@example.invalid', 'commit', '-qm', 'fixture']]:
            subprocess.run(argv, cwd=root, check=True, capture_output=True)
        before = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
        if before.returncode == 0:
            raise RuntimeError('测试样本未能复现失败')
        approvals = []
        def approve(prompt):
            allowed = prompt == f'执行命令 {shlex.join(command)}？' or prompt.startswith(f'修改文件 {source}\n')
            approvals.append({'prompt': prompt, 'approved': allowed})
            print(f'[确认策略] {"批准" if allowed else "拒绝"}：{prompt}', flush=True)
            return allowed
        events = []
        def observe(event):
            events.append({'kind': event.kind, 'data': event.data})
            if event.kind in {'model_start', 'tool_start', 'tool_end', 'model_retry'}:
                print(json.dumps(events[-1], ensure_ascii=False)[:1600], flush=True)
        model = OpenAIModel(config)
        try:
            agent = Agent(model, build_default_registry(), ToolContext(root, approve, True), max_steps=20, on_event=observe)
            result = agent.run(f'当前唯一工作目录是 {root}，文件工具请使用工作目录内的相对路径。请修复这个仓库的测试失败。本次是工具覆盖验收，必须先实际调用 list_files，再实际调用 search_code（query 为 add）；这两步都不能跳过，即使已经知道文件路径。然后用 read_file 读取相关源码和测试，遵循所有 AGENTS.md。使用规则给出的精确测试命令，通过 run_command 先复现失败，再 edit_file 修复源码，重新运行相同测试，最后调用 git_status 和 git_diff 检查修改范围并总结。不要修改测试，不要执行其他命令；命令 argv 必须使用规则中的 Python 绝对路径。')
        finally:
            model.client.close()
        after = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
        diff = subprocess.run(['git', '--no-pager', 'diff', '--no-ext-diff', '--no-textconv'], cwd=root, capture_output=True, text=True, check=True).stdout
        changed = subprocess.run(['git', 'diff', '--name-only'], cwd=root, capture_output=True, text=True, check=True).stdout.splitlines()
        status = subprocess.run(['git', 'status', '--porcelain'], cwd=root, capture_output=True, text=True, check=True).stdout
        calls = [e['data']['name'] for e in events if e['kind'] == 'tool_start']
        runs = [e['data']['result'] for e in events if e['kind'] == 'tool_end' and e['data']['name'] == 'run_command']
        required = {'list_files', 'search_code', 'read_file', 'edit_file', 'run_command', 'git_status', 'git_diff'}
        verified = (result.completed and after.returncode == 0 and changed == ['src/math_utils.py']
                    and status.strip() == 'M src/math_utils.py' and test.read_bytes() == test_before
                    and required.issubset(calls) and len(runs) >= 2
                    and runs[0]['data'].get('returncode') != 0 and runs[-1]['data'].get('returncode') == 0
                    and '#' in source.read_text(encoding='utf-8'))
        report = {'verified': verified, 'model': config.model, 'seconds': round(time.monotonic()-started, 2),
                  'request_config': {'connect_timeout': config.connect_timeout, 'read_timeout': config.read_timeout, 'max_retries': config.max_retries},
                  'retries': [e['data'] for e in events if e['kind'] == 'model_retry'],
                  'steps': result.steps, 'calls': calls, 'baseline_exit': before.returncode,
                  'final_exit': after.returncode, 'tests_unchanged': test.read_bytes() == test_before,
                  'changed_files': changed, 'diff': diff, 'test_output': after.stdout + after.stderr,
                  'approvals': approvals, 'events': events}
        destination = Path(__file__).resolve().parents[1] / '.fast-agent/validation'
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'repository-demo.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k not in {'events','approvals'}}, ensure_ascii=False, indent=2))
        return 0 if verified else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f'验证失败：{exc}', file=sys.stderr)
        raise SystemExit(1)
