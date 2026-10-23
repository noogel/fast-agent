import subprocess
import pytest
from fast_agent.worktrees import create_worktree
from fast_agent.tools import ToolContext


def repo(root):
    for args in (['init','-q'],['config','user.email','test@example.invalid'],['config','user.name','Test']):
        subprocess.run(['git',*args],cwd=root,check=True)
    (root/'file.txt').write_text('committed\n')
    subprocess.run(['git','add','.'],cwd=root,check=True)
    subprocess.run(['git','commit','-qm','base'],cwd=root,check=True)


def test_independent_checkout_preserves_dirty_source(tmp_path):
    repo(tmp_path)
    (tmp_path/'file.txt').write_text('dirty\n')
    ctx=ToolContext(tmp_path,lambda _:True,True)
    result=create_worktree('task-one',ctx)
    from pathlib import Path
    target=Path(result['path'])
    assert (target/'file.txt').read_text()=='committed\n'
    (target/'file.txt').write_text('isolated\n')
    assert (tmp_path/'file.txt').read_text()=='dirty\n'
    with pytest.raises(ValueError):create_worktree('task-one',ctx)


@pytest.mark.parametrize('name',['../escape','--option','a/b',''])
def test_bad_name(tmp_path,name):
    with pytest.raises(ValueError):create_worktree(name,ToolContext(tmp_path,lambda _:True,True))


def test_denied_and_sandbox_do_not_create(tmp_path):
    repo(tmp_path)
    for ctx in (ToolContext(tmp_path,lambda _:False,True),ToolContext(tmp_path,lambda _:True,True,mode='plan'),ToolContext(tmp_path,lambda _:True,True,sandbox='macos')):
        with pytest.raises(ValueError):create_worktree('task',ctx)
    assert not (tmp_path/'.fast-agent'/'worktrees'/'task').exists()


def test_agent_tools_operate_in_new_workspace(tmp_path):
    import json
    from pathlib import Path
    from fast_agent.models import ToolCall
    from fast_agent.tools import build_default_registry
    repo(tmp_path)
    info=create_worktree('isolated',ToolContext(tmp_path,lambda _:True,True))
    context=ToolContext(Path(info['path']),lambda _:True,True)
    tools=build_default_registry()
    def call(name,args):return tools.execute(ToolCall('x',name,json.dumps(args)),context)
    assert call('read_file',{'path':'file.txt'}).ok
    assert call('edit_file',{'path':'file.txt','old':'committed','new':'isolated'}).ok
    assert (tmp_path/'file.txt').read_text()=='committed\n'
    assert 'isolated' in subprocess.run(['git','diff'],cwd=info['path'],capture_output=True,text=True,check=True).stdout


def test_symlink_target_rejected(tmp_path):
    repo(tmp_path)
    base=tmp_path/'.fast-agent';base.mkdir()
    (base/'worktrees').symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(ValueError):create_worktree('unsafe',ToolContext(tmp_path,lambda _:True,True))
