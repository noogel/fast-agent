import subprocess
from pathlib import Path
import pytest
from fast_agent.worktrees import create_worktree
from fast_agent.worktree_lifecycle import archive_worktree,restore_worktree
from fast_agent.tools import ToolContext
from fast_agent.runtime_store import RuntimeStore
from test_worktrees import repo


def setup(root):
    repo(root);ctx=ToolContext(root,lambda _:True,True)
    return ctx,Path(create_worktree('task',ctx)['path'])


def test_archive_restore_preserves_dirty_ignored_evidence_and_commit(tmp_path):
    ctx,target=setup(tmp_path)
    (target/'file.txt').write_text('child commit\n')
    subprocess.run(['git','commit','-qam','child'],cwd=target,check=True)
    commit=subprocess.run(['git','rev-parse','HEAD'],cwd=target,capture_output=True,text=True,check=True).stdout
    (target/'file.txt').write_text('unsubmitted\n')
    (target/'.gitignore').write_text('ignored.bin\n.fast-agent/\n')
    (target/'ignored.bin').write_bytes(b'\x00private\xff')
    RuntimeStore(target,'evidence').write('test.json',{'result':'keep'})
    original={p.relative_to(target):p.read_bytes() for p in target.rglob('*') if p.is_file() and p.name!='.git'}
    result=archive_worktree('task',ctx);archived=Path(result['path'])
    assert not target.exists() and archived.is_dir()
    for path,data in original.items():assert (archived/path).read_bytes()==data
    with pytest.raises(ValueError):create_worktree('task',ctx)
    restored=Path(restore_worktree('task',ctx)['path'])
    assert restored==target and not archived.exists()
    for path,data in original.items():assert (restored/path).read_bytes()==data
    assert subprocess.run(['git','rev-parse','HEAD'],cwd=target,capture_output=True,text=True,check=True).stdout==commit
    assert (tmp_path/'file.txt').read_text()=='committed\n'


@pytest.mark.parametrize('category,state',[('jobs',{'status':'running'}),('jobs',{'status':'unknown'}),('tasks',{'status':'running','uncertain':[]}),('tasks',{'status':'paused','uncertain':['x']}),('batches',{'status':'pending'})])
def test_active_or_uncertain_records_refuse(tmp_path,category,state):
    ctx,target=setup(tmp_path)
    RuntimeStore(target,category).write('a'*32+'.json',state)
    with pytest.raises(ValueError):archive_worktree('task',ctx)
    assert target.exists()


def test_denied_and_restore_collision(tmp_path):
    ctx,target=setup(tmp_path);ctx.mode='plan'
    with pytest.raises(ValueError):archive_worktree('task',ctx)
    ctx.mode='execute';archive_worktree('task',ctx)
    target.mkdir();(target/'keep').write_text('do not overwrite')
    with pytest.raises(ValueError):restore_worktree('task',ctx)
    assert (target/'keep').read_text()=='do not overwrite'


def test_task_lock_blocks_move(tmp_path):
    ctx,target=setup(tmp_path)
    with RuntimeStore(target,'tasks').lock():
        with pytest.raises(ValueError):archive_worktree('task',ctx)
    assert target.exists()


def test_uncertain_move_preserves_journal_and_files(tmp_path,monkeypatch):
    import fast_agent.worktree_lifecycle as module
    ctx,target=setup(tmp_path)
    (target/'untracked').write_text('preserve me')
    real=module.git
    def interrupted(root,args):
        result=real(root,args)
        if args[:2]==['worktree','move']:raise KeyboardInterrupt
        return result
    monkeypatch.setattr(module,'git',interrupted)
    with pytest.raises(ValueError,match='未确认'):archive_worktree('task',ctx)
    journal=RuntimeStore(tmp_path,'worktree-archives').read('task.json')
    assert journal['status']=='moving_archive'
    assert (tmp_path/'.fast-agent/archived-worktrees'/journal['archive_name']/'untracked').read_text()=='preserve me'
    with pytest.raises(ValueError):restore_worktree('task',ctx)


def test_reconcile_before_and_after_move(tmp_path):
    from fast_agent.worktree_lifecycle import reconcile_worktree
    ctx,target=setup(tmp_path)
    archive_worktree('task',ctx)
    store=RuntimeStore(tmp_path,'worktree-archives')
    record=store.read('task.json');record['status']='moving_archive';store.write('task.json',record)
    assert reconcile_worktree('task',ctx)['status']=='archived'
    restore_worktree('task',ctx)
    record=store.read('task.json');record['status']='moving_archive';store.write('task.json',record)
    assert reconcile_worktree('task',ctx)['status']=='restored'


def test_reconcile_refuses_changed_head(tmp_path):
    from fast_agent.worktree_lifecycle import reconcile_worktree
    ctx,target=setup(tmp_path)
    archive_worktree('task',ctx)
    store=RuntimeStore(tmp_path,'worktree-archives')
    record=store.read('task.json');record.update(status='moving_archive',head_commit='0'*40);store.write('task.json',record)
    with pytest.raises(ValueError,match='HEAD'):reconcile_worktree('task',ctx)
    assert store.read('task.json')['status']=='moving_archive'


@pytest.mark.parametrize('condition',['collision','active','denied','changed_during_confirmation'])
def test_reconcile_refuses_unsafe_state(tmp_path,condition):
    from fast_agent.worktree_lifecycle import reconcile_worktree
    ctx,target=setup(tmp_path)
    archived=Path(archive_worktree('task',ctx)['path'])
    store=RuntimeStore(tmp_path,'worktree-archives')
    record=store.read('task.json');record['status']='moving_archive';store.write('task.json',record)
    if condition=='collision':target.mkdir()
    if condition=='active':RuntimeStore(archived,'jobs').write('a'*32+'.json',{'status':'running'})
    if condition=='denied':ctx.confirm=lambda _:False
    if condition=='changed_during_confirmation':
        def approve(_):
            target.mkdir()
            return True
        ctx.confirm=approve
    with pytest.raises(ValueError):reconcile_worktree('task',ctx)
    assert store.read('task.json')['status']=='moving_archive'
    assert archived.exists()
