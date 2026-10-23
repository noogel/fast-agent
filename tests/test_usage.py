"""请求预算是本进程累计上限，失败和摘要也消耗请求额度。"""
from types import SimpleNamespace as NS
import pytest
from fast_agent.config import Config
from fast_agent.providers import ProviderError
from test_request_retries import make, response, failure


def test_request_budget_counts_retries(monkeypatch):
    monkeypatch.setattr('fast_agent.providers.time.sleep', lambda _: None)
    model, calls = make([failure(503), response()], max_requests=1)
    with pytest.raises(ProviderError, match='请求预算'):
        model.complete([], [])
    assert len(calls.requests) == 1
    assert model.usage.snapshot()['requests'] == 1
    assert model.usage.snapshot()['unknown_requests'] == 1


def test_usage_is_observed_and_missing_is_unknown():
    reply = response()
    reply.usage = NS(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    model, _ = make([reply, response()], max_requests=2)
    model.complete([], [])
    model.complete([], [])
    assert model.usage.snapshot() == dict(requests=2, max_requests=2,
        prompt_tokens=10, completion_tokens=5, total_tokens=15, unknown_requests=1)
    with pytest.raises(ProviderError, match='请求预算'):
        model.complete([], [])


@pytest.mark.parametrize('value', [-1, True, 1.5])
def test_invalid_budget(value):
    with pytest.raises(ValueError):
        Config('key', 'm', max_requests=value)


def test_budget_environment():
    assert Config.from_env(dict(FAST_AGENT_API_KEY='key', FAST_AGENT_MODEL='m',
        FAST_AGENT_MAX_REQUESTS='5')).max_requests == 5


def test_stream_usage_duplicate_trailer_not_double_counted():
    from test_streaming import model, chunk
    trailer = NS(choices=[], usage=NS(prompt_tokens=12, completion_tokens=3, total_tokens=15))
    m, stream, sent = model([chunk('完成'), chunk(finish='stop'), trailer, trailer])
    m.complete_stream([], [], lambda _: None)
    assert m.usage.snapshot()['total_tokens'] == 15
    assert m.usage.snapshot()['unknown_requests'] == 0
    assert sent['stream_options'] == {'include_usage': True}
    assert stream.closed


def test_summary_and_restore_share_process_budget(tmp_path, capsys):
    from test_manual_compaction import setup
    from fast_agent.session_commands import handle_session_command
    m, calls = make([response('保留任务目标')], max_requests=1)
    agent, _ = setup(tmp_path, m)
    assert agent.compact() is not None
    saved = list(agent.history)
    agent.restore_history(saved)
    result = agent.continue_task('继续')
    assert not result.completed and '请求预算' in result.text
    assert len(calls.requests) == 1
    handle_session_command('/usage', agent)
    assert 'unknown_requests' in capsys.readouterr().out
    agent.reset()
    assert m.usage.snapshot()['requests'] == 1


def test_shared_budget_is_atomic_across_workers():
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from fast_agent.usage import UsageTracker
    from fast_agent.providers import ProviderError
    tracker=UsageTracker(7)
    def attempt(_):
        try: request=tracker.begin()
        except ProviderError:return None
        tracker.observe(request,SimpleNamespace(prompt_tokens=2,completion_tokens=1,total_tokens=3))
        tracker.snapshot()
        return request
    with ThreadPoolExecutor(max_workers=16) as executor:
        ids=list(executor.map(attempt,range(200)))
    assert sorted(i for i in ids if i is not None)==list(range(1,8))
    assert tracker.snapshot()==dict(requests=7,max_requests=7,prompt_tokens=14,completion_tokens=7,total_tokens=21,unknown_requests=0)
