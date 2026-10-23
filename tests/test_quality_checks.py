import json
import sys
import pytest
from fast_agent.quality_checks import load_checks, run_checks, CheckRuns
from fast_agent.tools import ToolContext


def configured(root,code='print("ok")'):
    path=root/'checks.json'
    path.write_text(json.dumps({'tests':{'argv':[sys.executable,'-c',code],'timeout':5}}))
    return load_checks(path)


def test_runs_persist_exact_command_and_result(tmp_path):
    context=ToolContext(tmp_path,lambda _:True,True,quality_checks=configured(tmp_path))
    result=run_checks({'names':['tests']},context)
    assert result.ok
    stored=CheckRuns(tmp_path).load(result.data['run_id'])
    assert stored['status']=='passed' and stored['results'][0]['result']['data']['returncode']==0
    assert stored['results'][0]['argv'][0]==sys.executable
    assert CheckRuns(tmp_path).list()[0]['id']==result.data['run_id']


def test_failure_and_denial_are_not_passed(tmp_path):
    context=ToolContext(tmp_path,lambda _:True,True,quality_checks=configured(tmp_path,'raise SystemExit(3)'))
    result=run_checks({'names':['tests']},context)
    assert not result.ok and result.data['status']=='failed'
    context.confirm=lambda _:False
    result=run_checks({'names':['tests']},context)
    assert not result.ok and result.data['results'][0]['result']['error']


def test_invalid_selection_never_runs(tmp_path):
    context=ToolContext(tmp_path,lambda _:pytest.fail('must not approve'),True,quality_checks=configured(tmp_path))
    for names in ([],['missing'],['tests','tests']):
        with pytest.raises(ValueError):run_checks({'names':names},context)
    assert CheckRuns(tmp_path).list()==[]


@pytest.mark.parametrize('config',[{}, {'x':{'argv':[]}}, {'x':{'argv':['echo'], 'timeout':61}},
    {'x':{'argv':'echo x'}}, {'x':{'argv':['echo'],'shell':True}}])
def test_invalid_configuration(tmp_path,config):
    path=tmp_path/'checks.json';path.write_text(json.dumps(config))
    with pytest.raises(ValueError):load_checks(path)


def test_start_error_preserves_unknown_without_fake_exit_code(tmp_path):
    context=ToolContext(tmp_path,lambda _:True,True,quality_checks={'missing':{'argv':['/no/such/check-program'],'timeout':5}})
    with pytest.raises(OSError):run_checks({'names':['missing']},context)
    record=CheckRuns(tmp_path).load(CheckRuns(tmp_path).list()[0]['id'])
    assert record['status']=='unknown'
    assert record['results'][0]['name']=='missing'
    assert 'returncode' not in record['results'][0]['result']['data']


def test_interrupt_records_terminal_status_and_reraises(tmp_path,monkeypatch):
    def interrupt(*args):raise KeyboardInterrupt
    monkeypatch.setattr('fast_agent.quality_checks.run_command',interrupt)
    context=ToolContext(tmp_path,lambda _:True,True,quality_checks=configured(tmp_path))
    with pytest.raises(KeyboardInterrupt):run_checks({'names':['tests']},context)
    assert CheckRuns(tmp_path).list()[0]['status']=='interrupted'
