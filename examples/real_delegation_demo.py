"""真实模型验证只读子任务与主会话保存恢复；凭据只来自环境。"""
import json
import os
import tempfile
from pathlib import Path
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.tools import ToolContext, build_default_registry
from fast_agent.delegation import delegate
from fast_agent.delegation_reports import DelegationReports
from fast_agent.sessions import SessionStore


def main():
    model = OpenAIModel(Config.from_env(os.environ))
    try:
        with tempfile.TemporaryDirectory(prefix='fast-agent-delegate-') as directory:
            root = Path(directory)
            (root/'evidence.txt').write_text('验收代号 DELEGATE-38\n')
            parent = Agent(model, build_default_registry(), ToolContext(root, lambda _: False, False))
            before = list(parent.history)
            first = delegate(parent, '必须用 read_file 读取 evidence.txt，报告其中验收代号。')
            SessionStore(root).save('parent', parent.history)
            parent.restore_history(SessionStore(root).load('parent'))
            second = delegate(parent, '重新用 read_file 读取 evidence.txt，报告验收代号。')
            reports = DelegationReports(root)
            summary = reports.summary([first['id'],second['id']])
            checks = {'summary_sources': [r['id'] for r in summary['reports']] == [first['id'],second['id']],
                      'summary_reference_only': summary['reference_only'] is True,
                      'summary_completed': summary['completed_count'] == 2,
                      'stored_report_matches': reports.load(first['id']) == first,
                      'listed_both_reports': len(reports.list()['reports']) == 2,
                      'first_completed': first['completed'], 'second_completed': second['completed'],
                      'first_read': {'name':'read_file','ok':True} in first['tools'],
                      'second_read': {'name':'read_file','ok':True} in second['tools'],
                      'answers': all('DELEGATE-38' in r['text'] for r in [first,second]),
                      'parent_history_isolated': len(parent.history)==len(before)==1,
                      'parent_plan_after_restore': parent.context.mode=='plan',
                      'unchanged_file': (root/'evidence.txt').read_text()=='验收代号 DELEGATE-38\n'}
            print(json.dumps({'verified':all(checks.values()),'checks':checks,'usage':model.usage.snapshot()},ensure_ascii=False,indent=2))
            if not all(checks.values()): raise SystemExit(1)
    finally:
        model.client.close()


if __name__ == '__main__': main()
