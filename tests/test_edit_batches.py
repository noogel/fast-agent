"""多文件修改先预检整批，逐文件原子写入，显式恢复不覆盖外部变更。"""
import pytest
from fast_agent.tools import ToolContext
from fast_agent.file_tools import read_file
from fast_agent.edit_batches import edit_files, EditBatches


def setup(root, approve=lambda _: True):
    context = ToolContext(root, approve, True)
    for name in ('a.txt', 'b.txt'):
        (root/name).write_text('old\n')
        read_file({'path':name}, context)
    return context


def edits():
    return {'files':[{'path':name,'old':'old','new':'new'} for name in ('a.txt','b.txt')]}


def test_batch_one_approval_and_restore(tmp_path):
    approvals=[]
    context=setup(tmp_path,lambda p: approvals.append(p) or True)
    result=edit_files(edits(),context)
    assert result.ok and len(approvals)==1
    assert all(name in approvals[0] for name in ('a.txt','b.txt'))
    store=EditBatches(tmp_path)
    assert store.load(result.data['batch_id'])['status']=='completed'
    restored=store.undo(result.data['batch_id'], context)
    assert restored.ok and len(approvals)==2
    assert all((tmp_path/name).read_text()=='old\n' for name in ('a.txt','b.txt'))


def test_preflight_failure_writes_nothing(tmp_path):
    context=setup(tmp_path)
    (tmp_path/'b.txt').write_text('external')
    with pytest.raises(ValueError): edit_files(edits(),context)
    assert (tmp_path/'a.txt').read_text()=='old\n'
    assert EditBatches(tmp_path).list()==[]


def test_reject_confirmation_and_changed_during_confirmation(tmp_path):
    context=setup(tmp_path,lambda _: False)
    assert not edit_files(edits(),context).ok
    assert (tmp_path/'a.txt').read_text()=='old\n'
    def changed(_):
        (tmp_path/'b.txt').write_text('external')
        return True
    context.confirm=changed
    with pytest.raises(ValueError): edit_files(edits(),context)
    assert (tmp_path/'a.txt').read_text()=='old\n'


def test_partial_failure_receipt_can_restore(tmp_path,monkeypatch):
    import fast_agent.edit_batches as batches
    context=setup(tmp_path)
    real=batches.commit_text
    def broken(path,*args,**kwargs):
        if path.name=='b.txt': raise OSError('fixture disk error')
        return real(path,*args,**kwargs)
    monkeypatch.setattr(batches,'commit_text',broken)
    result=edit_files(edits(),context)
    assert not result.ok and result.data['uncertain']
    assert (tmp_path/'a.txt').read_text()=='new\n'
    assert (tmp_path/'b.txt').read_text()=='old\n'
    monkeypatch.setattr(batches,'commit_text',real)
    assert EditBatches(tmp_path).undo(result.data['batch_id'],context).ok
    assert (tmp_path/'a.txt').read_text()=='old\n'


def test_restore_preflights_every_file_and_preserves_external(tmp_path):
    context=setup(tmp_path)
    result=edit_files(edits(),context)
    (tmp_path/'b.txt').write_text('external')
    with pytest.raises(ValueError): EditBatches(tmp_path).undo(result.data['batch_id'],context)
    assert (tmp_path/'a.txt').read_text()=='new\n'
    assert (tmp_path/'b.txt').read_text()=='external'


def test_duplicate_and_plan_rejected(tmp_path):
    context=setup(tmp_path)
    args=edits();args['files'][1]['path']='./a.txt'
    with pytest.raises(ValueError): edit_files(args,context)
    context.mode='plan'
    assert not edit_files(edits(),context).ok


def test_pending_batch_blocks_new_tasks_but_can_be_restored(tmp_path,monkeypatch):
    import fast_agent.edit_batches as batches
    from fast_agent.tasks import TaskStore
    context=setup(tmp_path)
    real=batches.commit_text
    def broken(*args,**kwargs): raise OSError('disk')
    monkeypatch.setattr(batches,'commit_text',broken)
    result=edit_files(edits(),context)
    ident=result.data['batch_id']
    assert 'batch:'+ident in TaskStore(tmp_path).blockers()
    monkeypatch.setattr(batches,'commit_text',real)
    assert EditBatches(tmp_path).undo(ident,context).ok
    assert TaskStore(tmp_path).blockers()==[]


def test_rule_files_written_after_dependents_in_apply_and_restore(tmp_path):
    (tmp_path/'AGENTS.md').write_text('old rule\n')
    (tmp_path/'sub').mkdir()
    (tmp_path/'sub/AGENTS.md').write_text('old local rule\n')
    (tmp_path/'sub/a.txt').write_text('old\n')
    context=ToolContext(tmp_path,lambda _:True,True)
    names=['AGENTS.md','sub/AGENTS.md','sub/a.txt']
    for name in names: read_file({'path':name},context)
    result=edit_files({'files':[{'path':name,'old':'old','new':'new'} for name in names]},context)
    assert result.ok
    assert result.data['paths']==['sub/a.txt','sub/AGENTS.md','AGENTS.md']
    assert EditBatches(tmp_path).undo(result.data['batch_id'],context).ok
    assert (tmp_path/'AGENTS.md').read_text()=='old rule\n'
    assert (tmp_path/'sub/a.txt').read_text()=='old\n'
