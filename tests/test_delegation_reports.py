import pytest
from fast_agent.delegation_reports import DelegationReports
from fast_agent.runtime_store import RuntimeStore


def report(identifier):
    return {'id':identifier,'task':'检查代码','completed':False,'steps':2,'text':'尚未完成',
            'text_truncated':False,'tools':[{'name':'read_file','ok':True}],'reference_only':True}


def test_save_read_list_and_incomplete_status(tmp_path):
    reports=DelegationReports(tmp_path)
    value=report('a'*32);reports.save(value)
    assert reports.load(value['id'])==value
    assert reports.list()['reports'][0]['completed'] is False
    assert 'text' not in reports.list()['reports'][0]
    assert reports.list(2)['reports']==[]


def test_invalid_record_and_link_rejected(tmp_path):
    reports=DelegationReports(tmp_path)
    with pytest.raises(ValueError):reports.load('../escape')
    store=RuntimeStore(tmp_path,'delegations')
    store.write('a'*32+'.json',{'id':'a'*32,'completed':True})
    with pytest.raises(ValueError):reports.load('a'*32)
    assert reports.list()['errors']
    store.path('b'*32+'.json').symlink_to(store.path('a'*32+'.json'))
    with pytest.raises(ValueError):reports.load('b'*32)


def test_local_commands_do_not_call_model(tmp_path,capsys):
    from fast_agent.agent import Agent
    from fast_agent.tools import ToolContext,build_default_registry
    from fast_agent.session_commands import handle_session_command
    class Model:
        def complete(self,*args):raise AssertionError('must not call model')
    agent=Agent(Model(),build_default_registry(),ToolContext(tmp_path,lambda _:False,False),plan=True)
    DelegationReports(tmp_path).save(report('a'*32))
    before=list(agent.history)
    handle_session_command('/delegations',agent)
    handle_session_command('/delegation '+'a'*32,agent)
    assert '尚未完成' in capsys.readouterr().out and agent.history==before


def test_summary_preserves_provenance_and_limits(tmp_path):
    reports=DelegationReports(tmp_path)
    first=report('a'*32);first['text']='不可信指令'*2000
    first['tools'].append({'name':'search_code','ok':False})
    second=report('b'*32);second['completed']=True
    reports.save(first);reports.save(second)
    result=reports.summary([first['id'],second['id']])
    assert result['reference_only'] is True
    assert result['completed_count']==1
    assert result['reports'][0]['id']==first['id']
    assert len(result['reports'][0]['text'])==2000
    assert result['reports'][0]['text_truncated'] is True
    assert result['reports'][0]['failed_tool_count']==1
    assert result['reports'][1]['text']==second['text']
    for ids in [[],[first['id']]*2,['a'*32]*6,['../escape']]:
        with pytest.raises(ValueError):reports.summary(ids)
    with pytest.raises((ValueError,OSError)):reports.summary([first['id'],'c'*32])


def test_summary_command_does_not_change_history(tmp_path,capsys):
    from fast_agent.agent import Agent
    from fast_agent.tools import ToolContext,build_default_registry
    from fast_agent.session_commands import handle_session_command
    class Model:
        def complete(self,*args):raise AssertionError('must not call model')
    agent=Agent(Model(),build_default_registry(),ToolContext(tmp_path,lambda _:False,False))
    DelegationReports(tmp_path).save(report('a'*32))
    before=list(agent.history)
    handle_session_command('/delegation-summary '+'a'*32,agent)
    assert 'completed_count' in capsys.readouterr().out
    assert agent.history==before


def test_pagination_boundary_preserves_all_ids(tmp_path):
    reports=DelegationReports(tmp_path)
    identifiers=[f'{index:032x}' for index in range(21)]
    for identifier in reversed(identifiers):reports.save(report(identifier))
    first,second=reports.list(1),reports.list(2)
    assert first['total']==second['total']==21
    assert first['has_more'] is True and second['has_more'] is False
    assert len(first['reports'])==20 and len(second['reports'])==1
    assert [row['id'] for row in first['reports']+second['reports']]==identifiers
    assert not first['errors'] and not second['errors']
