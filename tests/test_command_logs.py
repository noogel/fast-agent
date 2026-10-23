import sys
import pytest
from fast_agent.tools import ToolContext, build_default_registry
from fast_agent.models import ToolCall
import json


def test_long_output_retained_and_read_after_new_context(tmp_path):
    registry = build_default_registry()
    context = ToolContext(tmp_path, lambda _: True, True)
    result = registry.execute(ToolCall('run','run_command',json.dumps({'argv':[sys.executable,'-c','print("x"*80000);print("TAIL_MARKER")']})),context)
    assert result.ok and result.data['truncated']
    assert not result.data['storage_truncated']
    restored = ToolContext(tmp_path, lambda _: False, False, mode='plan')
    page = registry.execute(ToolCall('read','read_command_log',json.dumps({'log_id':result.data['log_id'],'tail':True,'limit':100})),restored)
    assert page.ok and 'TAIL_MARKER' in page.data['output']
    assert page.data['next_offset'] == result.data['stored_bytes']


def test_storage_cap_and_pagination(tmp_path):
    from fast_agent.command_logs import CommandLog, read_command_log, STORAGE_LIMIT
    with CommandLog(tmp_path) as log:
        log.append(b'a' * (STORAGE_LIMIT + 50))
        assert log.truncated and log.stored == STORAGE_LIMIT
    context = ToolContext(tmp_path, lambda _: False, False)
    page = read_command_log({'log_id':log.id, 'offset':7, 'limit':10},context)
    assert page.data['output'] == 'a'*10 and page.data['next_offset']==17
    assert page.data['storage_truncated']


@pytest.mark.parametrize('args',[{'log_id':'../bad'},{'log_id':'a'*32,'offset':-1},{'log_id':'a'*32,'limit':65537},{'log_id':'a'*32,'tail':True,'offset':1}])
def test_invalid_reads(tmp_path,args):
    registry=build_default_registry()
    assert 'read_command_log' in registry.tools
    assert not registry.execute(ToolCall('read','read_command_log',json.dumps(args)),ToolContext(tmp_path,lambda _:True,True)).ok


def test_symlink_log_rejected(tmp_path):
    from fast_agent.command_logs import read_command_log
    folder=tmp_path/'.fast-agent'/'command-logs';folder.mkdir(parents=True)
    (folder/('a'*32+'.log')).symlink_to(tmp_path/'outside')
    with pytest.raises(ValueError):
        read_command_log({'log_id':'a'*32},ToolContext(tmp_path,lambda _:False,False))


def test_failed_command_log_and_private_mode(tmp_path):
    import stat
    registry=build_default_registry()
    result=registry.execute(ToolCall('run','run_command',json.dumps({'argv':[sys.executable,'-c','print("failure");exit(2)']})),ToolContext(tmp_path,lambda _:True,True))
    assert not result.ok and result.data['returncode']==2
    path=tmp_path/'.fast-agent'/'command-logs'/(result.data['log_id']+'.log')
    assert stat.S_IMODE(path.stat().st_mode)==0o600
    assert path.read_text()=='failure\n'


def test_missing_metadata_is_not_claimed_complete(tmp_path):
    from fast_agent.command_logs import read_command_log
    folder=tmp_path/'.fast-agent'/'command-logs';folder.mkdir(parents=True)
    (folder/('b'*32+'.log')).write_bytes(b'partial')
    result=read_command_log({'log_id':'b'*32},ToolContext(tmp_path,lambda _:False,False))
    assert not result.data['finalized'] and result.data['storage_truncated'] is None


def test_model_preview_is_smaller_than_live_output(tmp_path):
    from fast_agent.command_tool import run_command
    context=ToolContext(tmp_path,lambda _:True,True)
    chunks=[]
    context.on_command_event=lambda kind,data: chunks.append(data['text']) if kind=='command_output' else None
    result=run_command({'argv':[sys.executable,'-c','print("x"*20000)']},context)
    assert result.ok and result.data['truncated']
    assert len(result.data['output'].encode()) <= 4096
    assert len(''.join(chunks)) > 8192
