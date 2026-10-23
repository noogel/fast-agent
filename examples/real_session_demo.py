"""需要真实模型配置：两个独立 Python 进程保存/恢复会话，再核对真实文件。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.sessions import SessionStore, context_stats
from fast_agent.tools import ToolContext, build_default_registry


def worker(phase: str, root: Path, title: str = '') -> dict:
    model = OpenAIModel(Config.from_env(os.environ))
    events = []
    agent = Agent(model, build_default_registry(), ToolContext(root, lambda prompt: prompt.startswith(f'修改文件 {root / "note.txt"}'), True),
                  on_event=lambda event: events.append({'kind': event.kind, 'data': event.data}))
    store = SessionStore(root)
    try:
        if phase == 'save':
            prompt = f'请记住本项目的目标标题是“{title}”，下一轮需要用它替换旧标题。现在只调用 read_file 读取 note.txt 并确认理解；本轮不要修改任何文件。'
        else:
            agent.restore_history(store.load('demo'))
            # 演示宿主明确授权执行；恢复的历史本身不携带授权。
            agent.set_mode('execute')
            prompt = '请先 read_file 重新读取 note.txt，以获得新进程的修改前检查记录。然后继续上一轮任务：按之前记住的目标标题，使用 edit_file 替换 note.txt 中唯一的“旧标题”，正文保持不变；然后 read_file 复读验证。不要使用命令或 write_file，也不要把会话文件作为工具输入读取。'
        result = agent.run(prompt)
        if not result.completed:
            raise RuntimeError(result.text)
        store.save('demo', agent.history)
        tools = [event['data'] for event in events if event['kind'] == 'tool_end']
        if any(not item['result']['ok'] for item in tools):
            raise RuntimeError('存在失败的工具调用')
        return {'phase': phase, 'steps': result.steps, 'tools': [item['name'] for item in tools],
                'context': context_stats(agent.history), 'pid': os.getpid()}
    finally:
        model.client.close()


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        report = worker(sys.argv[2], Path(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else '')
        print(json.dumps(report, ensure_ascii=False))
        return 0
    Config.from_env(os.environ)
    title = 'Fast Agent-' + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix='fast-agent-session-') as directory:
        root = Path(directory).resolve()
        note = root / 'note.txt'
        note.write_text('旧标题\n正文必须保留。\n', encoding='utf-8')
        reports = []
        for phase, extra in [('save', [title]), ('load', [])]:
            # 第二个进程不接收 title，只能从恢复后的历史中获得它。
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', phase, str(root), *extra],
                                     text=True, capture_output=True, timeout=180)
            if process.returncode:
                raise RuntimeError(f'{phase} 阶段失败：{process.stderr[-1500:]}')
            report = json.loads(process.stdout)
            reports.append(report)
            print(json.dumps(report, ensure_ascii=False), flush=True)
            if phase == 'save' and (note.read_text(encoding='utf-8') != '旧标题\n正文必须保留。\n' or report['tools'] != ['read_file']):
                raise RuntimeError('第一阶段未遵循只读要求')
        expected = title + '\n正文必须保留。\n'
        if note.read_text(encoding='utf-8') != expected or reports[1]['tools'] != ['read_file', 'edit_file', 'read_file']:
            raise RuntimeError('恢复后的工具顺序或磁盘内容验证失败')
        if reports[0]['pid'] == reports[1]['pid']:
            raise RuntimeError('没有创建独立进程')
        print(json.dumps({'verified': True, 'after': expected, 'independent_processes': True}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'验证失败：{exc}', file=sys.stderr)
        raise SystemExit(1)
