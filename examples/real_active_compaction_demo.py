"""MiniMax/OpenAI 兼容服务：批量精确编辑、规划、执行及修复后的跨进程恢复。"""
import ast
import hashlib
import io
import tokenize
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
from fast_agent.compaction import ContextPolicy, SUMMARY_MARKER
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
        if event.kind in {'tool_start', 'tool_end', 'model_retry', 'compaction_start', 'compaction_end'}:
            print(json.dumps(events[-1], ensure_ascii=False)[:1600], file=sys.stderr, flush=True)
    model = OpenAIModel(Config.from_env(os.environ))
    agent = Agent(model, build_default_registry(), ToolContext(root, approve, True),
                  max_steps=25, context_policy=ContextPolicy(40000 if phase=='execute' else 60000, 2), stream=True, checkpoints=True, plan=phase=='plan', on_event=observe)
    restored_modes = []
    try:
        if phase == 'plan':
            prompt = (f'当前唯一工作目录为 {root}，文件工具使用相对路径。请分析 add 和 multiply 两个函数为何可能导致测试失败，并给出中文实施计划。'
                      '先 list_files，再 search_code 搜索 add，read_file 读取 src/math_utils.py 和 tests/test_math.py，遵循沿途 AGENTS.md。'
                      '只规划，不执行测试或修改文件。本阶段禁止读取 docs 目录中的背景资料，这些资料仅供执行阶段的压缩演示使用。最终说明目标、依据、拟修改文件、步骤、验证方法及待确认事项，列出精确测试命令。')
        elif phase == 'restore':
            with redirect_stdout(sys.stderr):
                handle_session_command('/load executed', agent)
            restored_modes.append(agent.context.mode)
            prompt = '只读核验之前的修复：read_file 读取 src/math_utils.py，git_diff 检查差异，用中文指出之前修复的两个函数及测试结果，不运行命令、不修改文件。'
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
                      '先按顺序 read_file 读取 docs/one.txt、docs/two.txt，每次回复只能发起一个 read_file 调用，收到结果后再读下一个文件，不能同批读取这两个文件。这些资料与后续执行属于同一用户任务，历史压缩不改变任务目标；摘要记录已经读过的资料不要重复读取，继续尚未完成的步骤。然后重新 read_file 读取源码和测试；用 run_command 执行规则给定的精确 argv 复现失败，'
                      '再用一次 multi_edit_file 调用，在 edits 数组中至少列出两项，分别修复 src/math_utils.py 的 add 和 multiply，保留签名并添加中文注释；重跑相同测试，最后 git_status 和 git_diff 核实范围。'
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
                'compactions': [e['data'] for e in events if e['kind']=='compaction_end'],
                'has_summary': any(m.content==SUMMARY_MARKER for m in agent.history),
                'batch_sizes': [len(json.loads(e['data']['arguments'])['edits']) for e in events if e['kind']=='tool_start' and e['data']['name']=='multi_edit_file'],
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
    report_path = destination/'active-compaction-demo.json'
    report_path.unlink(missing_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='fast-agent-multi-edit-') as directory:
        root = Path(directory).resolve()
        (root/'src').mkdir(); (root/'tests').mkdir(); (root/'docs').mkdir()
        for name in ['one','two']:
            (root/'docs'/f'{name}.txt').write_text('只读背景资料，不能修改测试；最终需要修复 add 和 multiply。\n' + ('Background reference: preserve existing tests and implement correct arithmetic.\n' * 190))
        source, test = root/'src/math_utils.py', root/'tests/test_math.py'
        source.write_text('def add(a, b):\n    return a - b\n\ndef multiply(a, b):\n    return a + b\n')
        test.write_text('import unittest\nfrom src.math_utils import add, multiply\nclass TestAdd(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n    def test_negative(self):\n        self.assertEqual(add(-1, 1), 0)\n    def test_multiply(self):\n        self.assertEqual(multiply(2, 3), 6)\n')
        command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']
        (root/'AGENTS.md').write_text('只允许修改 src/math_utils.py，禁止修改 tests。计划阶段只读分析；执行阶段先复现失败，再修改，再重跑测试和检查 Git 差异。\n精确测试命令：'+shlex.join(command)+'\n')
        (root/'src/AGENTS.md').write_text('保留 add(a, b) 和 multiply(a, b) 签名，修复时加一句中文注释说明加法行为。\n')
        (root/'.gitignore').write_text('__pycache__/\n.fast-agent/\n')
        git(root, 'init', '-q'); git(root, 'add', '.')
        git(root, '-c', 'user.name=Fast Agent Demo', '-c', 'user.email=demo@example.invalid', 'commit', '-qm', 'fixture')
        baseline = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
        if baseline.returncode == 0:
            raise RuntimeError('样本没有失败测试')
        before = {str(p.relative_to(root)): digest(p) for p in [source, test, root/'AGENTS.md', root/'src/AGENTS.md', root/'.gitignore']}
        reports = []
        plan_unchanged = False
        for phase in ['plan', 'execute', 'restore']:
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
        functions = {node.name: ast.dump(node.args) for node in ast.parse(source.read_text()).body if isinstance(node, ast.FunctionDef)}
        expected_args = ast.dump(ast.parse('def example(a, b): pass').body[0].args)
        has_chinese_comment = any(token.type == tokenize.COMMENT and any('\u4e00' <= char <= '\u9fff' for char in token.string) for token in tokenize.generate_tokens(io.StringIO(source.read_text()).readline))
        plan, execute, restored = reports
        read_names = {t.name for t in build_default_registry().tools.values() if t.effect=='read'}
        runs = execute['command_results']
        verified = (plan_unchanged and set(plan['calls']) <= read_names
                    and {'list_files','read_file'}.issubset(plan['calls'])
                    and 'src/math_utils.py' in plan['answer'] and 'unittest' in plan['answer']
                    and execute['restored_modes']==['plan','plan'] and execute['mode']=='execute'
                    and plan['pid'] != execute['pid'] and plan['delta_count'] > 0 and execute['delta_count'] > 0
                    and {'read_file','multi_edit_file','run_command','git_status','git_diff'}.issubset(execute['calls'])
                    and len(execute['batch_sizes']) >= 1 and all(size >= 2 for size in execute['batch_sizes'])
                    and 'edit_file' not in execute['calls'] and 'write_file' not in execute['calls']
                    and restored['mode']=='plan' and set(restored['calls']) <= read_names
                    and {'read_file','git_diff'}.issubset(restored['calls'])
                    and 'add' in restored['answer'] and 'multiply' in restored['answer']
                    and len({p['pid'] for p in reports}) == 3 and not restored['approvals']
                    and any(c.get('scope')=='current_task' for c in execute['compactions'])
                    and execute['has_summary'] and restored['has_summary']
                    and len(runs)>=2 and runs[0]['data']['returncode']!=0 and runs[-1]['data']['returncode']==0
                    and after.returncode==0 and changed==['src/math_utils.py'] and digest(test)==before['tests/test_math.py']
                    and functions == {'add': expected_args, 'multiply': expected_args} and has_chinese_comment and all(a['allowed'] for a in execute['approvals']))
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
