from fast_agent.evaluation import evaluate_suite
import pytest


@pytest.mark.parametrize('source',[
    '@transform\ndef add(a,b):\n return a+b',
    'def add(a,b=1):\n return a+b',
    'def add(a,b,**kwargs):\n return a+b',
    'def add(a:int,b):\n return a+b',
    'def add(a,b):\n return a-b',
    'not valid python',
])
def test_repair_oracle_rejects_extra_behavior(source):
    from fast_agent.evaluation import addition_checks
    assert not all(addition_checks(source).values())


def test_repair_oracle_accepts_comment_and_rejects_without_execution():
    from fast_agent.evaluation import addition_checks
    assert all(addition_checks('def add(a,b):\n # 中文注释\n return a+b\n').values())
    assert not all(addition_checks("raise RuntimeError('must not execute')").values())


def test_failed_case_preserves_known_usage():
    from fast_agent.evaluation import EvaluationFailure
    usage={'requests':1,'unknown_requests':1,'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}
    def fail():raise EvaluationFailure('provider_failure',usage)
    report=evaluate_suite('fixture',[('failure',fail)],model='fixture')
    assert report['usage']==usage and report['cases'][0]['status']=='error'


def test_quality_not_model_claim_and_unknown_cost():
    report=evaluate_suite('fixture-v1',[
        ('pretends',lambda:{'completed':True,'checks':{'test_passed':False},'usage':{'requests':1,'unknown_requests':1,'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}}),
        ('verified',lambda:{'completed':True,'checks':{'test_passed':True},'usage':{'requests':1,'unknown_requests':0,'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}),
    ],model='fixture')
    assert report['passed']==1 and report['total']==2
    assert report['cases'][0]['passed'] is False
    assert report['cases'][1]['cost'] is None
    assert report['usage']['unknown_requests']==1


def test_failure_continues_and_redacts_exception():
    def fail():raise RuntimeError('secret-token')
    report=evaluate_suite('fixture-v1',[('fails',fail),('next',lambda:{
        'completed':True,'checks':{'ok':True},'usage':{'requests':0,'unknown_requests':0,'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}})],model='fixture')
    assert report['passed']==1 and report['cases'][0]['status']=='error'
    assert 'secret-token' not in str(report)
    assert report['usage'] is None  # failed runner cannot establish total usage


def test_empty_or_nonboolean_checks_cannot_pass():
    report=evaluate_suite('fixture-v1',[
        ('empty',lambda:{'completed':True,'checks':{},'usage':{}}),
        ('string',lambda:{'completed':True,'checks':{'ok':'true'},'usage':{}}),
    ],model='fixture')
    assert report['passed']==0
