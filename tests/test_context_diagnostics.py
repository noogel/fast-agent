import copy
from fast_agent.compaction import ContextPolicy, request_characters, context_diagnostics
from fast_agent.models import Message


def test_breakdown_is_exact_and_private():
    history = [Message('system', '私密规则'), Message('user', '中文任务'), Message('assistant', '完成')]
    tools = [{'type':'function','function':{'name':'lookup','description':'secret-description','parameters':{}}}]
    before = copy.deepcopy((history, tools))
    report = context_diagnostics(history, tools, ContextPolicy(10))
    assert report['total_chars'] == request_characters(history, tools)
    assert sum(report['components'].values()) == report['total_chars']
    assert report['over_budget_chars'] == report['total_chars'] - 10
    assert report['remaining_chars'] == 0 and report['compression_due']
    assert report['largest_tools'][0]['name'] == 'lookup'
    assert 'secret-description' not in str(report) and '私密规则' not in str(report)
    assert (history, tools) == before


def test_disabled_budget_and_exact_threshold():
    history = [Message('system', 'rules')]
    total = request_characters(history, [])
    report = context_diagnostics(history, [], ContextPolicy(total))
    assert report['remaining_chars'] == 0 and not report['compression_due']
    report = context_diagnostics(history, [], ContextPolicy(0))
    assert report['remaining_chars'] is None and not report['compression_due']
    assert report['largest_tools'] == []


def test_cli_diagnostics_preserves_history_and_makes_no_requests(tmp_path, capsys):
    from fast_agent.agent import Agent
    from fast_agent.tools import ToolContext, build_default_registry
    from fast_agent.session_commands import handle_session_command
    class NoCalls:
        def complete(self, *args):
            raise AssertionError('diagnostics must not call model')
    agent = Agent(NoCalls(), build_default_registry(), ToolContext(tmp_path, lambda _: False, False))
    before = copy.deepcopy(agent.history)
    assert handle_session_command('/context', agent)
    output = capsys.readouterr().out
    assert 'largest_tools' in output and '不是 token' in output
    assert agent.history == before
    assert not list(tmp_path.iterdir())

def test_window_diagnostic_is_explicit_estimate():
    history=[Message('system','规则'),Message('user','很长'*1000)]
    report=context_diagnostics(history,[],ContextPolicy(60000,1,8192,1024))
    assert report['compression_due'] and report['over_budget_chars']==0
    assert report['effective_budget_chars']==1536
    assert report['output_reserved_tokens']==1024
    assert 'not tokenizer' in report['token_estimator']
