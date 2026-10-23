import copy
import pytest
from fast_agent.worktree_snapshot import capture,restore,validate


def test_roundtrip_includes_hidden_binary_empty_directory(tmp_path):
    source=tmp_path/'source';source.mkdir()
    (source/'.git').write_text('gitdir: private')
    (source/'.fast-agent').mkdir();(source/'.fast-agent/evidence').write_bytes(b'\0\xff')
    (source/'empty').mkdir();(source/'run').write_text('test');(source/'run').chmod(0o755)
    snapshot=capture(source);validate(snapshot)
    target=restore(snapshot,tmp_path/'restored')
    assert capture(target)==snapshot
    assert not (target/'.git').exists()
    with pytest.raises(ValueError):restore(snapshot,target)


def test_link_and_corrupt_data_rejected(tmp_path):
    (tmp_path/'link').symlink_to('/etc/passwd')
    with pytest.raises(ValueError):capture(tmp_path)
    (tmp_path/'link').unlink();(tmp_path/'file').write_text('abc')
    snapshot=capture(tmp_path)
    broken=copy.deepcopy(snapshot);broken['entries'][0]['sha256']='0'*64
    with pytest.raises(ValueError):restore(broken,tmp_path/'out')
    assert not (tmp_path/'out').exists()
    for name in ['../escape','/absolute','.git','a/../escape','a//b']:
        broken=copy.deepcopy(snapshot);broken['entries'][0]['path']=name
        with pytest.raises(ValueError):validate(broken)
