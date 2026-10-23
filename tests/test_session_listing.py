import os
import pytest
from fast_agent.sessions import SessionStore
from fast_agent.models import Message
from fast_agent.cli import main


def test_listing_pages_order_and_backups(tmp_path):
    store=SessionStore(tmp_path)
    for i in range(23):
        path=store.save(f'session-{i:02}',[Message('system','s')])
        os.utime(path,(100+i,100+i))
    store.save('auto-backup',[Message('system','s')])
    store.save('manual-backup',[Message('system','s')])
    first=store.list()
    assert first['total']==23 and len(first['sessions'])==20
    assert first['sessions'][0]['name']=='session-22'
    assert len(store.list(page=2)['sessions'])==3
    assert store.list(include_backups=True)['total']==25
    with pytest.raises(ValueError): store.list(page=0)


def test_listing_does_not_parse_contents_or_follow_links(tmp_path):
    store=SessionStore(tmp_path)
    path=store.path('broken',create=True);path.write_text('{broken')
    store.path('link').symlink_to(path)
    result=store.list()
    assert [r['name'] for r in result['sessions']]==['broken']
    with pytest.raises(ValueError):store.load('broken')


def test_cli_load_plan_and_commands(tmp_path,monkeypatch,capsys):
    SessionStore(tmp_path).save('named',[Message('system','old'),Message('user','MARK-20'),Message('assistant','记住了')])
    inputs=iter(['/sessions','/history','/exit'])
    monkeypatch.setattr('builtins.input',lambda _:next(inputs))
    assert main(['--mock','--workspace',str(tmp_path),'--load','named'])==0
    output=capsys.readouterr().out
    assert '计划模式' in output and 'MARK-20' in output and 'named' in output


def test_load_resume_exclusive_and_missing(tmp_path):
    with pytest.raises(SystemExit):main(['--mock','--load','a','--resume','b'])
    assert main(['--mock','--workspace',str(tmp_path),'--load','missing','--prompt','hi'])==1


def test_index_symlink_does_not_block_other_sessions(tmp_path):
    store=SessionStore(tmp_path)
    path=store.save('valid',[Message('system','s')])
    store.path('index').symlink_to(path)
    assert [r['name'] for r in store.list()['sessions']]==['valid']


def test_corrupt_startup_load_reports_error(tmp_path,capsys):
    store=SessionStore(tmp_path)
    store.path('bad',create=True).write_text('{broken')
    assert main(['--mock','--workspace',str(tmp_path),'--load','bad','--prompt','hi'])==1
    assert '错误' in capsys.readouterr().err


def test_startup_load_does_not_call_model_or_replay_tools(tmp_path,monkeypatch):
    from fast_agent.models import ToolCall
    from fast_agent.tools import ToolRegistry
    from fast_agent.providers import MockModel
    SessionStore(tmp_path).save('history',[Message('system','old'),Message('user','task'),
        Message('assistant',tool_calls=[ToolCall('old','read_file','{}')]),
        Message('tool','previous result',tool_call_id='old'),Message('assistant','done')])
    def forbidden(*args,**kwargs):raise AssertionError('加载不应执行模型或工具')
    monkeypatch.setattr(MockModel,'complete',forbidden)
    monkeypatch.setattr(ToolRegistry,'execute',forbidden)
    monkeypatch.setattr('builtins.input',lambda _: '/exit')
    assert main(['--mock','--workspace',str(tmp_path),'--load','history'])==0
