import base64
import hashlib
import json
import subprocess
from pathlib import Path
import pytest
from fast_agent.tools import ToolContext
from fast_agent.worktrees import create_worktree, export_worktree
from test_worktrees import repo


def setup(root):
    repo(root)
    ctx=ToolContext(root,lambda _:True,True)
    info=create_worktree('fix',ctx)
    return ctx,Path(info['path'])


def test_export_includes_commits_staged_and_unstaged_preserves_parent(tmp_path):
    ctx,target=setup(tmp_path)
    (tmp_path/'file.txt').write_text('parent dirty\n')
    (target/'file.txt').write_text('committed child\n')
    subprocess.run(['git','commit','-qam','child'],cwd=target,check=True)
    (target/'new.txt').write_text('new\n')
    subprocess.run(['git','add','new.txt'],cwd=target,check=True)
    (target/'file.txt').write_text('final child\n')
    result=export_worktree('fix',ctx)
    record=json.loads(Path(result['path']).read_text())
    patch=base64.b64decode(record['patch_base64'])
    assert hashlib.sha256(patch).hexdigest()==record['sha256']
    assert b'+final child' in patch and b'+new' in patch
    assert (tmp_path/'file.txt').read_text()=='parent dirty\n'
    # 可在精确 base 的另一检出上检查并应用，不能只以补丁文本当作验收。
    clean=tmp_path/'clean'
    subprocess.run(['git','worktree','add','--detach',str(clean),record['base_commit']],cwd=tmp_path,check=True,capture_output=True)
    subprocess.run(['git','apply','--check','-'],input=patch,cwd=clean,check=True)
    subprocess.run(['git','apply','-'],input=patch,cwd=clean,check=True)
    assert (clean/'file.txt').read_text()=='final child\n'
    assert (clean/'new.txt').read_text()=='new\n'


def test_untracked_and_missing_provenance_refused(tmp_path):
    ctx,target=setup(tmp_path)
    (target/'forgotten.txt').write_text('not staged')
    with pytest.raises(ValueError,match='未跟踪'):export_worktree('fix',ctx)
    (target/'forgotten.txt').unlink()
    (tmp_path/'.fast-agent/worktree-records/fix.json').unlink()
    with pytest.raises((ValueError,OSError)):export_worktree('fix',ctx)


def test_denied_export_does_not_publish(tmp_path):
    ctx,target=setup(tmp_path)
    ctx.mode='plan'
    with pytest.raises(ValueError,match='授权'):export_worktree('fix',ctx)
    assert not (tmp_path/'.fast-agent/worktree-exports').exists()


def test_changed_during_capture_refused(tmp_path,monkeypatch):
    import fast_agent.worktrees as module
    ctx,target=setup(tmp_path)
    original=module.patch_bytes
    calls=[]
    def changing(*args):
        result=original(*args)
        calls.append(1)
        if len(calls)==1:(target/'file.txt').write_text('raced\n')
        return result
    monkeypatch.setattr(module,'patch_bytes',changing)
    with pytest.raises(ValueError,match='变化'):export_worktree('fix',ctx)


def test_binary_patch_is_lossless(tmp_path):
    ctx,target=setup(tmp_path)
    data=bytes(range(256))*4
    (target/'binary.dat').write_bytes(data)
    subprocess.run(['git','add','binary.dat'],cwd=target,check=True)
    record=json.loads(Path(export_worktree('fix',ctx)['path']).read_text())
    patch=base64.b64decode(record['patch_base64'])
    clean=tmp_path/'clean'
    subprocess.run(['git','worktree','add','--detach',str(clean),record['base_commit']],cwd=tmp_path,check=True,capture_output=True)
    subprocess.run(['git','apply','-'],input=patch,cwd=clean,check=True)
    assert (clean/'binary.dat').read_bytes()==data


def test_forged_foreign_target_rejected(tmp_path):
    ctx,target=setup(tmp_path)
    record_path=tmp_path/'.fast-agent/worktree-records/fix.json'
    record=json.loads(record_path.read_text());record['path']=str(tmp_path)
    record_path.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='记录'):export_worktree('fix',ctx)


def test_conflicted_index_rejected(tmp_path):
    ctx,target=setup(tmp_path)
    blob=subprocess.run(['git','rev-parse','HEAD:file.txt'],cwd=target,capture_output=True,text=True,check=True).stdout.strip()
    subprocess.run(['git','update-index','--index-info'],cwd=target,input=f'100644 {blob} 1\tfile.txt\n100644 {blob} 2\tfile.txt\n',text=True,check=True)
    with pytest.raises(ValueError,match='冲突'):export_worktree('fix',ctx)
