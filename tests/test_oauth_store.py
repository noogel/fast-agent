from dataclasses import asdict
import json,os,time
import pytest
from fast_agent.oauth_store import CredentialStore
from fast_agent.oauth_tokens import Tokens


def token():return Tokens('private-access','private-refresh',time.time()+300,'read','https://mcp.example/mcp','https://auth.example','client')


def test_private_roundtrip_binding_and_lock(tmp_path):
    store=CredentialStore(tmp_path/'credentials');value=token();identity=(value.resource,value.issuer,value.client_id)
    with store.lock(*identity):
        store.save(value)
        with pytest.raises(ValueError,match='刷新'):
            with store.lock(*identity):pass
    assert asdict(store.load(*identity))==asdict(value)
    assert store.path(*identity).stat().st_mode&0o777==0o600
    assert store.directory.stat().st_mode&0o777==0o700
    assert store.load(value.resource,value.issuer,'other') is None
    store.delete(*identity);assert store.load(*identity) is None


def test_permissions_link_corruption_and_binding(tmp_path):
    store=CredentialStore(tmp_path/'credentials');value=token();identity=(value.resource,value.issuer,value.client_id)
    store.save(value);path=store.path(*identity)
    os.chmod(path,0o644)
    with pytest.raises(ValueError,match='权限'):store.load(*identity)
    os.chmod(path,0o600)
    data=asdict(value);data['resource']='https://other.example/mcp';path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='绑定'):store.load(*identity)
    path.write_text('{private-secret')
    with pytest.raises(ValueError) as caught:store.load(*identity)
    assert 'private-secret' not in str(caught.value)
    path.unlink();path.symlink_to(tmp_path/'elsewhere')
    with pytest.raises(ValueError,match='链接'):store.load(*identity)
