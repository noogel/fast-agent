"""真实 MiniMax/OpenAI 兼容服务：长历史自动摘要、流式工具与跨进程恢复。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

from fast_agent.agent import Agent
from fast_agent.compaction import ContextPolicy, SUMMARY_MARKER
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.sessions import SessionStore
from fast_agent.tools import ToolContext, build_default_registry


def worker(phase, root, title=''):
    events = []
    deltas = []
    def observe(event):
        if event.kind == 'text_delta':
            deltas.append(event.data['text'])
            print(event.data['text'], end='', file=sys.stderr, flush=True)
        elif event.kind == 'stream_end' and event.data['displayed']:
            print(file=sys.stderr, flush=True)
        elif event.kind in {'compaction_start', 'compaction_end', 'tool_start', 'tool_end', 'model_retry'}:
            print(json.dumps({'kind': event.kind, 'data': event.data}, ensure_ascii=False)[:1200], file=sys.stderr, flush=True)
        events.append({'kind': event.kind, 'data': event.data})
    config = Config.from_env(os.environ)
    model = OpenAIModel(config)
    context = ToolContext(root, lambda prompt: prompt.startswith(f'修改文件 {root / "note.txt"}\n'), True)
    agent = Agent(model, build_default_registry(), context, stream=True,
                  context_policy=ContextPolicy(0 if phase == 'remember' else 12000, 1), on_event=observe)
    store = SessionStore(root)
    try:
        if phase == 'remember':
            padding = 'Historical background: the sample is a teaching exercise; no extra action is required.\n' * 180
            prompt = f'请记住后续任务的目标标题是“{title}”。后续将把 note.txt 里的旧标题改成这个目标，正文保留。本轮只简短确认，不要使用工具。以下重复背景无需逐条回复：\n{padding}\n重要目标标题仍是“{title}”。'
        elif phase == 'execute':
            agent.restore_history(store.load('remembered'))
            # 演示宿主明确授权执行；恢复的历史本身不携带授权。
            agent.set_mode('execute')
            prompt = '继续之前的任务。请先 read_file 读取 note.txt，按之前记住的目标标题，用 edit_file 替换唯一的“旧标题”，然后 read_file 复读确认，最后用中文总结。不要读取会话文件，不要使用命令或 write_file。'
        else:
            agent.restore_history(store.load('compressed'))
            prompt = '只回答之前任务的目标标题。不要使用任何工具。'
        result = agent.run(prompt)
        if not result.completed:
            raise RuntimeError(result.text)
        store.save('remembered' if phase == 'remember' else 'compressed', agent.history)
        tools = [event['data']['name'] for event in events if event['kind'] == 'tool_start']
        compactions = [event['data'] for event in events if event['kind'] == 'compaction_end']
        return {'phase': phase, 'pid': os.getpid(), 'steps': result.steps, 'tools': tools,
                'retries': [e['data'] for e in events if e['kind'] == 'model_retry'],
                'delta_count': len(deltas), 'streamed': result.streamed, 'compactions': compactions,
                'has_summary': any(m.content == SUMMARY_MARKER for m in agent.history), 'answer': result.text}
    finally:
        model.client.close()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        report = worker(sys.argv[2], Path(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else '')
        print(json.dumps(report, ensure_ascii=False))
        return 0
    config = Config.from_env(os.environ)
    destination = Path(__file__).resolve().parents[1]/'.fast-agent/validation'
    destination.mkdir(parents=True, exist_ok=True)
    (destination/'context-stream-demo.json').unlink(missing_ok=True)
    started = time.monotonic()
    title = 'Fast Agent-' + uuid.uuid4().hex[:12]
    reports = []
    with tempfile.TemporaryDirectory(prefix='fast-agent-stream-') as directory:
        root = Path(directory).resolve()
        note = root / 'note.txt'
        note.write_text('旧标题\n正文保持不变。\n', encoding='utf-8')
        for phase, extra in [('remember', [title]), ('execute', []), ('restore', [])]:
            # 只有第一进程直接收到随机标题，其后只能依赖会话和压缩摘要。
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', phase, str(root), *extra],
                                     stdout=subprocess.PIPE, text=True, timeout=240)
            if process.returncode:
                raise RuntimeError(f'{phase} 阶段失败')
            report = json.loads(process.stdout)
            reports.append(report)
            print(json.dumps({k:v for k,v in report.items() if k != 'answer'}, ensure_ascii=False), flush=True)
        verified = (note.read_text(encoding='utf-8') == title+'\n正文保持不变。\n'
                    and not reports[0]['tools'] and reports[1]['tools'] == ['read_file', 'edit_file', 'read_file']
                    and reports[1]['compactions'] and reports[2]['has_summary'] and not reports[2]['tools']
                    and title in reports[2]['answer'] and all(r['delta_count'] > 1 and r['streamed'] for r in reports)
                    and len({r['pid'] for r in reports}) == 3)
        backup_paths = list((root/'.fast-agent/sessions').glob('auto-*.json'))
        verified = bool(verified and backup_paths)
        result = {'verified': verified, 'request_config': {'connect_timeout': config.connect_timeout, 'read_timeout': config.read_timeout, 'max_retries': config.max_retries}, 'seconds': round(time.monotonic()-started, 2), 'title': title,
                  'after': note.read_text(encoding='utf-8'), 'backup_count': len(backup_paths), 'phases': reports}
        destination = Path(__file__).resolve().parents[1]/'.fast-agent/validation'
        destination.mkdir(parents=True, exist_ok=True)
        (destination/'context-stream-demo.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k != 'phases'}, ensure_ascii=False, indent=2))
        return 0 if verified else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f'验证失败：{exc}', file=sys.stderr)
        raise SystemExit(1)
