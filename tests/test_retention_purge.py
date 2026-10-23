import os,time
import pytest
from fast_agent.runtime_store import RuntimeStore
from fast_agent.tools import ToolContext
from fast_agent.retention import preview_retention
from fast_agent.retention_actions import quarantine
from fast_agent.retention_purge import preview_purge,purge


def setup(root):
    context=ToolContext(root,lambda _:True,True)
    store=RuntimeStore(root,'tool-outputs');name='a'*32+'.json'
    store.write(name,{'text':'test evidence'})
    old=time.time()-40*86400;os.utime(store.path(name),(old,old))
    record=quarantine(context,preview_retention(root)['fingerprint'])
    return context,record


def mature(root,record):
    record['quarantined_at']=time.time()-40*86400
    RuntimeStore(root,'retention').write(record['id']+'.json',record)


def test_age_references_and_purge(tmp_path):
    context,record=setup(tmp_path);identifier=record['id']
    with pytest.raises(ValueError,match='保留期'):preview_purge(tmp_path,identifier)
    mature(tmp_path,record)
    sessions=RuntimeStore(tmp_path,'sessions')
    sessions.write('refer.json',{'reference':'a'*32})
    with pytest.raises(ValueError,match='引用'):preview_purge(tmp_path,identifier)
    sessions.path('refer.json').unlink()
    preview=preview_purge(tmp_path,identifier)
    result=purge(context,identifier,preview['fingerprint'])
    assert result['status']=='purged' and result['entries'][0]['purge_state']=='deleted'
    assert not RuntimeStore(tmp_path,'retention-'+identifier).path('0.record').exists()
    assert RuntimeStore(tmp_path,'retention').read(identifier+'.json')['status']=='purged'


def test_confirmation_new_reference_refuses(tmp_path):
    context,record=setup(tmp_path);mature(tmp_path,record)
    preview=preview_purge(tmp_path,record['id'])
    def confirm(_):
        RuntimeStore(tmp_path,'sessions').write('new.json',{'reference':'a'*32});return True
    context.confirm=confirm
    with pytest.raises(ValueError,match='引用'):purge(context,record['id'],preview['fingerprint'])
    assert RuntimeStore(tmp_path,'retention-'+record['id']).path('0.record').exists()


def test_interruption_reconfirmation(tmp_path,monkeypatch):
    context,record=setup(tmp_path);mature(tmp_path,record);identifier=record['id']
    preview=preview_purge(tmp_path,identifier)
    original=RuntimeStore.write
    def fail(self,name,value):
        if self.category=='retention' and value.get('status')=='purged':raise OSError('crash after deleting')
        return original(self,name,value)
    with monkeypatch.context() as m:
        m.setattr(RuntimeStore,'write',fail)
        with pytest.raises(OSError):purge(context,identifier,preview['fingerprint'])
    retry=preview_purge(tmp_path,identifier)
    context.confirm=lambda _:False
    with pytest.raises(ValueError,match='授权'):purge(context,identifier,retry['fingerprint'])
    context.confirm=lambda _:True
    assert purge(context,identifier,retry['fingerprint'])['status']=='purged'


def test_plan_mode_and_archive_reference_block_purge(tmp_path):
    context,record=setup(tmp_path);mature(tmp_path,record)
    preview=preview_purge(tmp_path,record['id'])
    context.mode='plan'
    with pytest.raises(ValueError,match='授权'):purge(context,record['id'],preview['fingerprint'])
    child=tmp_path/'.fast-agent/archived-worktrees/child'
    child.mkdir(parents=True)
    RuntimeStore(child,'sessions').write('reference.json',{'reference':'a'*32})
    with pytest.raises(ValueError,match='引用'):preview_purge(tmp_path,record['id'])


def test_unexplained_missing_is_not_treated_as_deleted(tmp_path):
    context,record=setup(tmp_path);mature(tmp_path,record)
    RuntimeStore(tmp_path,'retention-'+record['id']).path('0.record').unlink()
    with pytest.raises(ValueError,match='缺失'):preview_purge(tmp_path,record['id'])
