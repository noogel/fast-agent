"""MiniMax 工具调用、保存恢复与只读上下文诊断验证。凭据来自环境。"""
import copy
import json
import os
import tempfile
from pathlib import Path
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.tools import ToolContext, build_default_registry
from fast_agent.sessions import SessionStore, validate_history
from fast_agent.compaction import context_diagnostics, request_characters


def main():
    model = OpenAIModel(Config.from_env(os.environ))
    try:
        with tempfile.TemporaryDirectory(prefix='fast-agent-context-') as directory:
            root = Path(directory)
            (root/'answer.txt').write_text('验收代号：CONTEXT-31\n', encoding='utf-8')
            events = []
            agent = Agent(model, build_default_registry(), ToolContext(root, lambda _: False, False),
                          max_steps=8, on_event=events.append)
            def inspect(current):
                before = copy.deepcopy(current.history)
                usage = model.usage.snapshot()
                report = context_diagnostics(current.history, current.tool_schemas(), current.context_policy)
                assert current.history == before and model.usage.snapshot() == usage
                assert sum(report['components'].values()) == report['total_chars']
                assert report['total_chars'] == request_characters(current.history, current.tool_schemas())
                return report
            initial = inspect(agent)
            first = agent.run('必须使用 read_file 读取 answer.txt，然后用中文报告文件中的验收代号。不要修改文件。')
            after = inspect(agent)
            SessionStore(root).save('diagnostics',agent.history)
            restored = Agent(model, build_default_registry(), ToolContext(root, lambda _: False, False),
                             max_steps=8, on_event=events.append)
            restored.restore_history(SessionStore(root).load('diagnostics'))
            recovered = inspect(restored)
            event_count = len(events)
            final = restored.run('再次使用 read_file 读取 answer.txt，核对代号是否和恢复的历史一致。')
            end = inspect(restored)
            validate_history(restored.history)
            checks = {'first_completed': first.completed, 'resumed_completed': final.completed,
                      'first_read': any(e.kind=='tool_end' and e.data['name']=='read_file' and e.data['result']['ok'] for e in events[:event_count]),
                      'fresh_read': any(e.kind=='tool_end' and e.data['name']=='read_file' and e.data['result']['ok'] for e in events[event_count:]),
                      'restored_plan': restored.context.mode=='plan', 'history_grew': after['total_chars'] > initial['total_chars'],
                      'tool_results_counted': end['components']['tool_results']>0,
                      'source_unchanged': (root/'answer.txt').read_text()=='验收代号：CONTEXT-31\n'}
            print(json.dumps({'verified':all(checks.values()),'checks':checks,'snapshots':[initial,after,recovered,end],
                              'usage':model.usage.snapshot(),'answer':final.text},ensure_ascii=False,indent=2))
            if not all(checks.values()): raise SystemExit(1)
    finally:
        model.client.close()


if __name__ == '__main__': main()
