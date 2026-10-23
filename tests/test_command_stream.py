import sys
import pytest
from fast_agent.command_tool import execute_process, LIMIT


def test_output_arrives_before_process_finishes(tmp_path):
    release = tmp_path / 'release'
    chunks = []
    def receive(text):
        chunks.append(text)
        release.touch()
    code = 'import pathlib,time; print("ready",flush=True); p=pathlib.Path("release"); end=time.monotonic()+2\nwhile not p.exists() and time.monotonic()<end: time.sleep(.01)\nassert p.exists(); print("done",flush=True)'
    result = execute_process([sys.executable, '-c', code], tmp_path, on_output=receive)
    assert result.ok and ''.join(chunks) == result.data['output'] == 'ready\ndone\n'


def test_split_utf8_and_output_limit(tmp_path):
    chunks = []
    code = 'import os,time; b="中文".encode(); os.write(1,b[:1]); time.sleep(.1); os.write(1,b[1:]); os.write(1,b"x"*100000)'
    result = execute_process([sys.executable, '-c', code], tmp_path, on_output=chunks.append)
    assert result.ok and result.data['truncated']
    assert ''.join(chunks) == result.data['output']
    assert result.data['output'].startswith('中文')
    assert len(result.data['output'].encode()) == LIMIT


@pytest.mark.parametrize('code,timeout', [('print("failed",flush=True);exit(3)', 2), ('import time;print("waiting",flush=True);time.sleep(5)', .2)])
def test_failed_and_timeout_output_preserved(tmp_path, code, timeout):
    chunks = []
    result = execute_process([sys.executable, '-c', code], tmp_path, timeout, on_output=chunks.append)
    assert not result.ok and chunks
    assert ''.join(chunks) == result.data['output']


def test_interrupt_during_output_reaps_process(tmp_path):
    import os
    pids = []
    def interrupt(text):
        pids.append(int(text.strip()))
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        execute_process([sys.executable, '-c', 'import os,time;print(os.getpid(),flush=True);time.sleep(30)'], tmp_path, on_output=interrupt)
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)


def test_agent_events_and_history_have_one_final_tool_result(tmp_path, capsys):
    from fast_agent.agent import Agent
    from fast_agent.models import ModelReply, ToolCall
    from fast_agent.tools import ToolContext, build_default_registry
    from fast_agent.cli import show_event
    import json
    class Model:
        def complete(self, messages, tools):
            if messages[-1].role == 'user':
                return ModelReply(tool_calls=[ToolCall('cmd', 'run_command', json.dumps({'argv': [sys.executable, '-c', 'print("唯一日志")']}))])
            return ModelReply('完成')
    events = []
    def observe(event):
        events.append(event)
        show_event(event)
    agent = Agent(Model(), build_default_registry(), ToolContext(tmp_path, lambda _: True, True), on_event=observe)
    assert agent.run('运行测试').completed
    kinds = [e.kind for e in events]
    assert kinds.index('command_output') < kinds.index('command_output_end') < kinds.index('tool_end')
    results = [m for m in agent.history if m.role == 'tool']
    assert len(results) == 1 and json.loads(results[0].content)['data']['output'] == '唯一日志\n'
    # 工具开始事件含命令参数；结果事件不再重复正文。
    assert 'output' not in capsys.readouterr().out.split('[tool_end]')[1]


def test_denied_command_emits_no_output(tmp_path):
    from fast_agent.command_tool import run_command
    from fast_agent.tools import ToolContext
    context = ToolContext(tmp_path, lambda _: False, True)
    events = []
    context.on_command_event = lambda *args: events.append(args)
    assert not run_command({'argv': [sys.executable, '-c', 'print("bad")']}, context).ok
    assert not events
