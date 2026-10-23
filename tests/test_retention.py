import os
import time
import pytest
from fast_agent.runtime_store import RuntimeStore
from fast_agent.retention import preview_retention


def test_reference_protection_and_age(tmp_path):
    store=RuntimeStore(tmp_path,'tool-outputs');identifier='a'*32
    store.write(identifier+'.json',{'text':'large output'})
    path=store.path(identifier+'.json');old=time.time()-40*86400;os.utime(path,(old,old))
    assert len(preview_retention(tmp_path)['candidates'])==1
    RuntimeStore(tmp_path,'sessions').write('saved.json',{'messages':[{'content':'read '+identifier}]})
    result=preview_retention(tmp_path)
    assert not result['candidates'] and result['protected'][0]['references']==['sessions/saved.json']


def test_active_and_corrupt_records_block(tmp_path):
    store=RuntimeStore(tmp_path,'tasks');store.write('a.json',{'status':'running'})
    assert preview_retention(tmp_path)['blockers']==['tasks/a.json']
    store.path('bad.json').write_text('{')
    with pytest.raises(ValueError,match='损坏'):preview_retention(tmp_path)


def test_excluded_workspace_symlink_is_not_followed(tmp_path):
    base=tmp_path/'.fast-agent';base.mkdir()
    (base/'worktrees').symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(ValueError,match='链接'):preview_retention(tmp_path)


def test_quarantine_restore_preserves_bytes_and_conflicts(tmp_path):
    from fast_agent.retention_actions import quarantine,restore
    from fast_agent.tools import ToolContext
    context=ToolContext(tmp_path,lambda _:True,True)
    store=RuntimeStore(tmp_path,'tool-outputs');name='b'*32+'.json'
    store.write(name,{'text':'完整输出'})
    path=store.path(name);original=path.read_bytes();old=time.time()-40*86400;os.utime(path,(old,old))
    record=quarantine(context,preview_retention(tmp_path)['fingerprint'])
    assert not path.exists() and record['status']=='quarantined'
    path.write_text('冲突')
    with pytest.raises(ValueError):restore(context,record['id'])
    assert path.read_text()=='冲突'
    path.unlink()
    result=restore(context,record['id'])
    assert result['status']=='restored' and path.read_bytes()==original


def test_confirmation_change_refuses_quarantine(tmp_path):
    from fast_agent.retention_actions import quarantine
    from fast_agent.tools import ToolContext
    store=RuntimeStore(tmp_path,'tool-outputs');name='c'*32+'.json'
    store.write(name,{'text':'旧'})
    path=store.path(name);old=time.time()-40*86400;os.utime(path,(old,old))
    fingerprint=preview_retention(tmp_path)['fingerprint']
    def change(_):store.write(name,{'text':'新'});return True
    with pytest.raises(ValueError,match='变化'):
        quarantine(ToolContext(tmp_path,change,True),fingerprint)
    assert store.read(name)['text']=='新'


def test_isolation_interruption_preserves_evidence(tmp_path,monkeypatch):
    from fast_agent.retention_actions import quarantine,restore
    from fast_agent.tools import ToolContext
    from pathlib import Path
    context=ToolContext(tmp_path,lambda _:True,True)
    store=RuntimeStore(tmp_path,'tool-outputs');name='d'*32+'.json'
    store.write(name,{'text':'不能丢失'})
    path=store.path(name);data=path.read_bytes();old=time.time()-40*86400;os.utime(path,(old,old))
    original_unlink=Path.unlink
    def fail(self,*args,**kwargs):
        if self==path:raise OSError('simulated crash')
        return original_unlink(self,*args,**kwargs)
    with monkeypatch.context() as m:
        m.setattr(Path,'unlink',fail)
        with pytest.raises(OSError):quarantine(context,preview_retention(tmp_path)['fingerprint'])
    batch=RuntimeStore(tmp_path,'retention').names()[0]
    saved=RuntimeStore(tmp_path,'retention-'+batch).path('0.record')
    assert saved.read_bytes()==data and path.read_bytes()==data
    with pytest.raises(ValueError,match='冲突'):restore(context,batch)


def test_digest_refuses_symlink_and_special_file(tmp_path):
    from fast_agent.retention_actions import digest
    target=tmp_path/'target';target.write_text('private')
    link=tmp_path/'link';link.symlink_to(target)
    with pytest.raises(OSError):digest(link)
    fifo=tmp_path/'fifo';os.mkfifo(fifo)
    with pytest.raises(ValueError):digest(fifo)


def test_readonly_cannot_quarantine(tmp_path):
    from fast_agent.retention_actions import quarantine
    from fast_agent.tools import ToolContext
    store=RuntimeStore(tmp_path,'tool-outputs');name='e'*32+'.json'
    store.write(name,{'text':'keep'})
    path=store.path(name);old=time.time()-40*86400;os.utime(path,(old,old))
    context=ToolContext(tmp_path,lambda _:True,True,mode='plan')
    with pytest.raises(ValueError,match='授权'):quarantine(context,preview_retention(tmp_path)['fingerprint'])
    assert path.exists()


def test_cross_workspace_and_quarantine_references(tmp_path):
    store=RuntimeStore(tmp_path,'tool-outputs');identifier='f'*32
    store.write(identifier+'.json',{'text':'evidence'})
    old=time.time()-40*86400;os.utime(store.path(identifier+'.json'),(old,old))
    child=tmp_path/'.fast-agent/archived-worktrees/example'
    child.mkdir(parents=True)
    assert len(preview_retention(tmp_path)['candidates'])==1
    RuntimeStore(child,'sessions').write('saved.json',{'reference':identifier})
    preview=preview_retention(tmp_path)
    assert not preview['candidates'] and not preview['blockers']
    assert 'archived-worktrees/example' in preview['protected'][0]['references'][0]
    RuntimeStore(child,'tasks').write('job.json',{'status':'running'})
    assert preview_retention(tmp_path)['blockers']


def test_cleanup_snapshot_reference_is_decoded(tmp_path):
    import base64,hashlib
    identifier='a'*32;store=RuntimeStore(tmp_path,'tool-outputs')
    store.write(identifier+'.json',{'text':'evidence'})
    old=time.time()-40*86400;os.utime(store.path(identifier+'.json'),(old,old))
    data=identifier.encode()
    snapshot={'version':1,'bytes':len(data),'entries':[{'path':'proof.txt','kind':'file','mode':384,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'data':base64.b64encode(data).decode()}]}
    RuntimeStore(tmp_path,'worktree-cleanups').write('package.json',{'snapshot':snapshot})
    preview=preview_retention(tmp_path)
    assert not preview['candidates'] and preview['protected'][0]['references']==['worktree-cleanups/package.json']
def test_retention_reports_permanent_evidence_policy(tmp_path):
    from fast_agent.retention import preview_retention
    from fast_agent.runtime_store import RuntimeStore
    store=RuntimeStore(tmp_path,'retention')
    store.write('a'*32+'.json',{'status':'purged','entries':[]})
    result=preview_retention(tmp_path)
    assert result['retention_policy']['worktree-cleanups']['automatic_expiry'] is None
    assert result['retention_policy']['git_cleanup_refs']['automatic_expiry'] is None
    assert result['retained_evidence'][0]['path']=='retention/'+'a'*32+'.json'
    assert result['candidates']==[]
