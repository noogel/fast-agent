"""命令隔离策略不能静默退回普通执行。"""
import os
import sys
import pytest
from fast_agent.command_sandbox import command_launch


def test_clean_environment_even_without_os_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv('FAST_AGENT_API_KEY', 'fixture-secret')
    argv, env = command_launch([sys.executable, '-V'], tmp_path, 'none')
    assert argv == [sys.executable, '-V']
    assert 'FAST_AGENT_API_KEY' not in env


def test_macos_profile_and_unknown_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    from pathlib import Path
    original = Path.is_file
    monkeypatch.setattr(Path, 'is_file', lambda p: True if str(p) == '/usr/bin/sandbox-exec' else original(p))
    argv, env = command_launch(['/usr/bin/true'], tmp_path, 'macos')
    assert argv[0] == '/usr/bin/sandbox-exec'
    assert '(deny network*)' in argv[2]
    assert '(deny file-write*' in argv[2]
    assert str(tmp_path.resolve()) in argv[2]
    with pytest.raises(ValueError): command_launch(['true'], tmp_path, 'unknown')


def test_unsupported_platform_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    with pytest.raises(ValueError, match='macOS'):
        command_launch(['true'], tmp_path, 'macos')


@pytest.mark.skipif(os.environ.get('FAST_AGENT_TEST_SANDBOX') != '1', reason='需要允许启动 macOS 子沙箱的本机执行环境')
def test_real_os_enforcement(tmp_path):
    import subprocess
    import json
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('original')
    (root/'escape').symlink_to(outside)
    (root/'.fast-agent').mkdir()
    program = '''
import pathlib, socket, json
p=pathlib.Path('.')
checks={}
(p/'inside.txt').write_text('ok')
checks['inside']=True
for name in ['../outside.txt','escape','.env','.fast-agent/state.json']:
    try: (p/name).write_text('changed')
    except PermissionError: checks[name]=True
    else: checks[name]=False
try:
    s=socket.socket();s.bind(('127.0.0.1',0))
except PermissionError: checks['network']=True
else: checks['network']=False
print(json.dumps(checks))
'''
    argv, env = command_launch([sys.executable, '-c', program], root, 'macos')
    process = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    assert all(json.loads(process.stdout).values()), process.stdout
    assert outside.read_text() == 'original'


@pytest.mark.skipif(os.environ.get('FAST_AGENT_TEST_SANDBOX') != '1', reason='需要本机 macOS 沙箱权限')
def test_background_uses_same_sandbox(tmp_path):
    import time
    from fast_agent.jobs import JobStore
    jobs = JobStore(tmp_path)
    job = jobs.start([sys.executable, '-c', "open('../outside-forbidden.txt','w').write('x')"], sandbox='macos')
    deadline = time.monotonic()+10
    while time.monotonic()<deadline:
        state = jobs.poll(job)
        if state['status'] not in {'queued','running'}: break
        time.sleep(.05)
    assert state['status'] == 'failed'
    assert not (tmp_path.parent/'outside-forbidden.txt').exists()


def test_missing_executable_fails_closed(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(Path, 'is_file', lambda _: False)
    with pytest.raises(ValueError, match='不会退回'):
        command_launch(['true'], tmp_path, 'macos')


def test_sync_tool_does_not_pass_model_credential(tmp_path, monkeypatch):
    from fast_agent.command_tool import run_command
    from fast_agent.tools import ToolContext
    monkeypatch.setenv('FAST_AGENT_API_KEY', 'fixture-only-value')
    context = ToolContext(tmp_path, lambda _: True, True)
    result = run_command({'argv': [sys.executable, '-c',
        "import os; print('absent' if 'FAST_AGENT_API_KEY' not in os.environ else 'present')"]}, context)
    assert result.ok and result.data['output'].strip() == 'absent'


@pytest.mark.skipif(os.environ.get('FAST_AGENT_TEST_SANDBOX') != '1', reason='需要本机 macOS 沙箱权限')
def test_git_diff_check_and_null_device_work_in_sandbox(tmp_path):
    import subprocess
    from fast_agent.command_tool import run_command
    from fast_agent.tools import ToolContext
    subprocess.run(['git','init','-q'],cwd=tmp_path,check=True)
    context=ToolContext(tmp_path,lambda _:True,True,sandbox='macos')
    result=run_command({'argv':['git','diff','--check']},context)
    assert result.ok,result.data
