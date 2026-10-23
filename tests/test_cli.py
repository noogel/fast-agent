import os
import subprocess
import sys
from fast_agent.cli import main, run_demo

def test_demo():
    for _ in range(2):
        events=[]
        result=run_demo(events.append)
        assert result.completed and 'Fast Agent' in result.text
        assert [e.data['name'] for e in events if e.kind=='tool_start']==['read_file','edit_file','read_file']

def test_cli_modes(monkeypatch,capsys):
    monkeypatch.delenv('FAST_AGENT_API_KEY',raising=False)
    monkeypatch.delenv('FAST_AGENT_MODEL',raising=False)
    assert main(['--prompt','hi'])==1
    assert main(['--mock','--prompt','hi'])==0
    assert '模拟' in capsys.readouterr().out

def test_help_and_demo_process():
    env={k:v for k,v in os.environ.items() if not k.startswith('FAST_AGENT_')}
    for args in [['--help'],['--demo']]:
        result=subprocess.run([sys.executable,'-m','fast_agent',*args],capture_output=True,text=True,env=env,timeout=10)
        assert result.returncode==0, result.stderr

def test_reset_exit(monkeypatch):
    inputs=iter(['hi','/reset','/exit'])
    monkeypatch.setattr('builtins.input',lambda _:next(inputs))
    assert main(['--mock'])==0

def test_eof(monkeypatch):
    def stop(_): raise EOFError
    monkeypatch.setattr('builtins.input',stop)
    assert main(['--mock'])==0

def test_confirm(monkeypatch):
    from fast_agent.cli import confirm
    for answer,expected in [('y',True),('yes',True),('',False),('no',False),('YES',True)]:
        monkeypatch.setattr('builtins.input',lambda _,v=answer:v)
        assert confirm('执行？') is expected

def test_keyboard_interrupt(monkeypatch):
    def stop(_): raise KeyboardInterrupt
    monkeypatch.setattr('builtins.input',stop)
    assert main(['--mock'])==0

def test_session_commands(tmp_path,monkeypatch,capsys):
    inputs=iter(['你好','/save 测试','/reset','/load 测试','/context','/history','/trim 0','/context','/exit'])
    monkeypatch.setattr('builtins.input',lambda _:next(inputs))
    assert main(['--mock','--workspace',str(tmp_path)])==0
    output=capsys.readouterr().out
    assert '会话已保存' in output and '会话已加载' in output
    assert '完整回复轮数：1' in output and '消息数：1' in output
    assert 'user' in output and '历史已裁剪' in output


def test_bad_load_keeps_history(tmp_path,monkeypatch,capsys):
    inputs=iter(['你好','/load 不存在','/context','/trim -1','/context','/exit'])
    monkeypatch.setattr('builtins.input',lambda _:next(inputs))
    assert main(['--mock','--workspace',str(tmp_path)])==0
    output=capsys.readouterr().out
    assert output.count('消息数：3')==2
    assert '会话文件不存在' in output

def test_session_across_processes(tmp_path):
    command=[sys.executable,'-m','fast_agent','--mock','--workspace',str(tmp_path)]
    first=subprocess.run(command,input='你好\n/save demo\n/exit\n',capture_output=True,text=True,timeout=10)
    assert first.returncode==0 and '会话已保存' in first.stdout
    second=subprocess.run(command,input='/load demo\n/context\n/exit\n',capture_output=True,text=True,timeout=10)
    assert second.returncode==0 and '消息数：3' in second.stdout

def test_stream_prints_once(monkeypatch,capsys):
    from types import SimpleNamespace
    from fast_agent.models import ModelReply
    import fast_agent.cli as cli
    closed=[]
    class Fake:
        def __init__(self,config): self.client=SimpleNamespace(close=lambda:closed.append(True))
        def complete_stream(self,messages,tools,on_text):
            on_text('唯一回复');return ModelReply('唯一回复')
    monkeypatch.setattr(cli,'OpenAIModel',Fake)
    monkeypatch.setenv('FAST_AGENT_API_KEY','fake');monkeypatch.setenv('FAST_AGENT_MODEL','fake')
    assert main(['--prompt','hi'])==0
    assert capsys.readouterr().out.count('唯一回复')==1 and closed==[True]


def test_invalid_compaction_options(capsys):
    assert main(['--mock','--context-chars','-1','--prompt','hi'])==1
    assert main(['--mock','--keep-rounds','0','--prompt','hi'])==1
