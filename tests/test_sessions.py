import json
import os
from pathlib import Path
import pytest
from fast_agent.models import Message, ToolCall
from fast_agent.sessions import SessionStore, validate_history, trim_history, context_stats, format_history


def conversation():
    return [Message('system','rules'),Message('user','read'),
            Message('assistant',tool_calls=[ToolCall('a','read_file','{"path":"a"}'),ToolCall('b','read_file','{"path":"b"}')]),
            Message('tool','{"ok":true}',tool_call_id='a'),Message('tool','{"ok":false}',tool_call_id='b'),
            Message('assistant','done'),Message('user','next'),Message('assistant','answer')]


def test_roundtrip(tmp_path):
    history=conversation()
    store=SessionStore(tmp_path)
    path=store.save('教学',history)
    assert store.load('教学')==history
    assert path.parent==tmp_path/'.fast-agent'/'sessions'
    assert path.stat().st_mode & 0o777 == 0o600
    data=json.loads(path.read_text())
    assert set(data)=={'version','workspace','messages'}
    store.save('教学',history[:6])
    assert store.load('教学')==history[:6]


@pytest.mark.parametrize('name',['../bad','/tmp/a','.','a/b','', 'x'*65])
def test_invalid_names(tmp_path,name):
    with pytest.raises(ValueError): SessionStore(tmp_path).save(name,conversation())


def test_wrong_workspace_and_version(tmp_path):
    store=SessionStore(tmp_path);path=store.save('x',conversation())
    data=json.loads(path.read_text());data['workspace']=str(tmp_path.parent)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='工作目录'): store.load('x')
    data['workspace']=str(tmp_path);data['version']=999
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='版本'): store.load('x')


def test_bad_json_and_symlink(tmp_path):
    store=SessionStore(tmp_path);path=store.save('x',conversation());path.write_text('{')
    with pytest.raises(ValueError): store.load('x')
    path.unlink();path.symlink_to(tmp_path/'external')
    with pytest.raises(ValueError): store.save('x',conversation())
    with pytest.raises(ValueError): store.load('x')


def test_symlink_directory(tmp_path):
    (tmp_path/'.fast-agent').symlink_to(tmp_path.parent,target_is_directory=True)
    with pytest.raises(ValueError): SessionStore(tmp_path).save('x',conversation())


def test_failed_save_keeps_previous(tmp_path,monkeypatch):
    store=SessionStore(tmp_path);path=store.save('x',conversation());old=path.read_bytes()
    def fail(*args): raise OSError('disk failure')
    monkeypatch.setattr(os,'replace',fail)
    with pytest.raises(OSError): store.save('x',conversation()[:6])
    assert path.read_bytes()==old
    assert sorted(p.name for p in path.parent.iterdir())==['x.json']


@pytest.mark.parametrize('history',[
    [],[Message('user','bad')],
    [Message('system','s'),Message('tool','x',tool_call_id='orphan')],
    [Message('system','s'),Message('user','u'),Message('assistant',tool_calls=[ToolCall('a','x','{}')])],
    [Message('system','s'),Message('user','u'),Message('assistant',tool_calls=[ToolCall('a','x','{}')]),Message('tool','x',tool_call_id='wrong')],
    [Message('system','s'),Message('system','s')],
    [Message('system','s'),Message('user','u'),Message('assistant',tool_calls=[ToolCall('a','x','{}'),ToolCall('a','y','{}')])],
])
def test_invalid_history(history):
    with pytest.raises(ValueError): validate_history(history)


def test_trim_and_stats():
    history=conversation();trimmed=trim_history(history,1)
    assert trimmed==[history[0],*history[-2:]]
    assert trim_history(history,0)==[history[0]]
    assert trim_history(history,9)==history
    validate_history(trim_history(history,2))
    stats=context_stats(history)
    assert stats['messages']==8 and stats['rounds']==2 and stats['completed_rounds']==2
    assert stats['characters']>0
    assert len(history)==8
    with pytest.raises(ValueError): trim_history(history,-1)


def test_unfinished_round_and_display():
    history=conversation()+[Message('user','pending')]
    validate_history(history)
    assert context_stats(history)['completed_rounds']==2
    assert trim_history(history,1)==[history[0],history[-1]]
    rendered=format_history(conversation(),80)
    assert 'read_file' in rendered and 'tool' in rendered and 'a' in rendered
    assert '\x1b' not in format_history([Message('system','\x1b[31m')])


def test_unknown_field_rejected(tmp_path):
    store=SessionStore(tmp_path);path=store.save('x',conversation())
    data=json.loads(path.read_text());data['messages'][0]['unexpected']=True
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError): store.load('x')

def test_size_guard(tmp_path,monkeypatch):
    import fast_agent.sessions as sessions
    store=SessionStore(tmp_path);path=store.save('x',conversation());old=path.read_bytes()
    monkeypatch.setattr(sessions,'MAX_SESSION_BYTES',10)
    with pytest.raises(ValueError,match='16 MiB'): store.load('x')
    with pytest.raises(ValueError,match='16 MiB'): store.save('x',conversation())
    assert path.read_bytes()==old


def test_duplicate_result_and_malformed_field(tmp_path):
    history=conversation()
    history[4]=Message('tool','{}',tool_call_id='a')
    with pytest.raises(ValueError): validate_history(history)
    store=SessionStore(tmp_path);path=store.save('x',conversation())
    data=json.loads(path.read_text());data['messages'][1]['role']={}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError): store.load('x')
