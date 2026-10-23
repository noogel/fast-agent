import os
from pathlib import Path
import pytest
from fast_agent.mcp_tls import tls_context


def test_ca_growth_after_stat_is_bounded(tmp_path, monkeypatch):
    path=tmp_path/'ca.pem'
    path.write_bytes(b'x'*(1024*1024+1))
    # 路径元数据不可靠时，仍必须按实际读取量拒绝，而不是交给SSL解析。
    original=os.fstat
    def small_stat(fd):
        result=original(fd)
        values=list(result);values[6]=1
        return os.stat_result(values)
    monkeypatch.setattr(os,'fstat',small_stat)
    with pytest.raises(ValueError,match='1MiB'):
        tls_context({'ca_file':str(path)})


def test_ca_symlink_rejected(tmp_path):
    target=tmp_path/'target';target.write_text('invalid')
    link=tmp_path/'ca.pem';link.symlink_to(target)
    with pytest.raises(ValueError,match='普通文件'):
        tls_context({'ca_file':str(link)})


def test_no_custom_ca_uses_default():
    assert tls_context({}) is None
