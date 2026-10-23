from pathlib import Path
import pytest
from fast_agent.worktree_cleanup import preview_cleanup,cleanup_worktree,restore_evidence
from fast_agent.worktree_lifecycle import archive_worktree
from test_worktree_lifecycle import setup


def test_cleanup_and_restore_all_evidence(tmp_path):
    ctx,target=setup(tmp_path)
    (target/'note').write_bytes(b'\0\xff')
    archived=Path(archive_worktree('task',ctx)['path'])
    preview=preview_cleanup('task',ctx)
    assert preview['bytes']>0 and archived.exists()
    result=cleanup_worktree('task',ctx)
    assert not archived.exists()
    restored=Path(restore_evidence(result['id'],ctx)['path'])
    assert (restored/'note').read_bytes()==b'\0\xff'
    assert (restored/'file.txt').read_text()=='committed\n'
    with pytest.raises(ValueError):restore_evidence(result['id'],ctx)


def test_cleanup_dirty_refused(tmp_path):
    ctx,target=setup(tmp_path);(target/'file.txt').write_text('unsubmitted')
    archived=Path(archive_worktree('task',ctx)['path'])
    with pytest.raises(ValueError,match='未交付'):cleanup_worktree('task',ctx)
    assert (archived/'file.txt').read_text()=='unsubmitted'


def test_cleanup_change_during_confirm_refused(tmp_path):
    ctx,target=setup(tmp_path);archived=Path(archive_worktree('task',ctx)['path'])
    def approve(_):
        (archived/'new-evidence').write_text('preserve');return True
    ctx.confirm=approve
    with pytest.raises(ValueError,match='变化'):cleanup_worktree('task',ctx)
    assert (archived/'new-evidence').exists()


@pytest.mark.parametrize('after',[False,True])
def test_cleanup_interrupt_reconcile_never_replays(tmp_path,monkeypatch,after):
    import fast_agent.worktree_cleanup as module
    from fast_agent.runtime_store import RuntimeStore
    ctx,target=setup(tmp_path);archived=Path(archive_worktree('task',ctx)['path'])
    real=module.git
    def interrupted(root,args):
        if args[:2]==['worktree','remove']:
            if after:real(root,args)
            raise KeyboardInterrupt
        return real(root,args)
    monkeypatch.setattr(module,'git',interrupted)
    with pytest.raises(KeyboardInterrupt):cleanup_worktree('task',ctx)
    store=RuntimeStore(tmp_path,'worktree-cleanups');identifier=store.names()[0]
    with pytest.raises(ValueError):cleanup_worktree('task',ctx)
    assert module.reconcile_cleanup(identifier,ctx)['status']==('removed' if after else 'retained')
    assert archived.exists() is not after


def test_independent_commit_blocks_cleanup(tmp_path):
    import subprocess
    ctx,target=setup(tmp_path)
    (target/'file.txt').write_text('independent')
    subprocess.run(['git','commit','-qam','independent'],cwd=target,check=True)
    archived=Path(archive_worktree('task',ctx)['path'])
    with pytest.raises(ValueError):cleanup_worktree('task',ctx)
    assert archived.exists()
