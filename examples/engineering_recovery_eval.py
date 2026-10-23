"""真实模型失败→保存→恢复评测；宿主只修复临时夹具，不授权模型写入。"""
import json
import os
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.evaluation import evaluate_suite, recovery_checks, EvaluationFailure
from fast_agent.providers import OpenAIModel
from fast_agent.sessions import SessionStore
from fast_agent.tools import ToolContext, build_default_registry


def run_case(config):
    model = OpenAIModel(replace(config, max_requests=8, max_retries=0))
    try:
        with tempfile.TemporaryDirectory(prefix='fast-agent-recovery-eval-') as directory:
            root = Path(directory).resolve()
            events = []
            def make_agent():
                registry = build_default_registry()
                registry.tools = {name: tool for name, tool in registry.tools.items() if name == 'read_file'}
                return Agent(model, registry, ToolContext(root, lambda _: False, True), max_steps=4,
                             on_event=lambda event: events.append(event))
            agent = make_agent()
            first = agent.run('实际调用 read_file 读取 answer.txt；如果不存在，报告失败原因后停止，不要猜测文件内容。')
            before = [event.data for event in events if event.kind == 'tool_end']
            store = SessionStore(root)
            store.save('recovery', agent.history)
            loaded = store.load('recovery')
            expected = 'RECOVERED-' + uuid.uuid4().hex
            (root / 'answer.txt').write_text(expected)
            events.clear()
            resumed = make_agent()
            resumed.restore_history(loaded)
            restored_exact = resumed.history[1:] == agent.history[1:]
            plan = resumed.context.mode == 'plan'
            result = resumed.run('外部已补齐缺失文件，请继续上一轮目标，实际重新读取并报告文件内容。')
            after = [event.data for event in events if event.kind == 'tool_end']
            checks = recovery_checks(before, after, expected)
            checks.update(first_turn_finished=first.completed, history_restored=restored_exact,
                          restored_plan_mode=plan, source_unchanged=(root / 'answer.txt').read_text()==expected,
                          no_extra_files={path.name for path in root.iterdir()}=={'answer.txt','.fast-agent'})
            return {'completed':result.completed, 'checks':checks, 'usage':model.usage.snapshot()}
    except Exception:
        raise EvaluationFailure('recovery_failure', model.usage.snapshot()) from None
    finally:
        model.client.close()


if __name__ == '__main__':
    config = Config.from_env(os.environ)
    report = evaluate_suite('engineering-recovery-v1', [('missing_file_resume', lambda: run_case(config))], model=config.model)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['passed']==report['total'] else 1)
