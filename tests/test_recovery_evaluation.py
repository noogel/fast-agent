from fast_agent.evaluation import recovery_checks


def test_recovery_requires_actual_failure_and_success():
    failed=[{'name':'read_file','result':{'ok':False}}]
    succeeded=[{'name':'read_file','result':{'ok':True,'content':'RECOVERED'}}]
    checks=recovery_checks(failed,succeeded,'RECOVERED')
    assert all(checks.values())
    assert not all(recovery_checks([],succeeded,'RECOVERED').values())
    assert not all(recovery_checks(failed,[],'RECOVERED').values())
    assert not all(recovery_checks(failed,[{'name':'read_file','result':{'ok':False,'content':'RECOVERED'}}],'RECOVERED').values())
    assert not all(recovery_checks(failed,succeeded,'WRONG').values())
