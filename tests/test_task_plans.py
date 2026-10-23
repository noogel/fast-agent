import pytest
from fast_agent.task_plans import PlanStore, update_plan
from fast_agent.tools import ToolContext


def request(revision=0):
    return dict(name='repair', expected_revision=revision, title='修复计算',
                steps=[dict(id='read',title='读取源码',status='in_progress',note=''),
                       dict(id='test',title='验证测试',status='pending',note='')])


def test_create_restore_revision_and_stale_write(tmp_path):
    context=ToolContext(tmp_path,lambda _:True,True)
    first=update_plan(request(),context)
    assert first.ok and first.data['revision']==1
    assert PlanStore(tmp_path).load('repair')['steps'][0]['status']=='in_progress'
    with pytest.raises(ValueError,match='版本'): update_plan(request(),context)
    second=request(1);second['steps'][0]['status']='completed'
    assert update_plan(second,context).data['revision']==2
    assert PlanStore(tmp_path).list()[0]['name']=='repair'


def test_plan_update_respects_approval_and_read_only(tmp_path):
    context=ToolContext(tmp_path,lambda _:False,True)
    assert not update_plan(request(),context).ok
    assert PlanStore(tmp_path).load('repair')['revision']==0
    context.confirm=lambda _:True;context.mode='plan'
    assert not update_plan(request(),context).ok
    assert PlanStore(tmp_path).list()==[]


@pytest.mark.parametrize('change', ['duplicate','status','parallel','blank','oversize','traversal'])
def test_invalid_plan_no_write(tmp_path,change):
    args=request()
    if change=='duplicate': args['steps'][1]['id']='read'
    if change=='status': args['steps'][0]['status']='maybe'
    if change=='parallel': args['steps'][1]['status']='in_progress'
    if change=='blank': args['title']=' '
    if change=='oversize': args['steps'][0]['note']='x'*2001
    if change=='traversal': args['name']='../outside'
    with pytest.raises(ValueError): update_plan(args,ToolContext(tmp_path,lambda _:True,True))
    assert PlanStore(tmp_path).list()==[]


def test_confirmation_cannot_overwrite_new_plan(tmp_path):
    store=PlanStore(tmp_path)
    context=ToolContext(tmp_path,lambda _:True,True)
    update_plan(request(),context)
    def changed(_):
        newer=store.load('repair');newer['title']='另一个用户的计划'
        store.store.write('repair.json',newer)
        return True
    context.confirm=changed
    with pytest.raises(ValueError,match='变化'): update_plan(request(1),context)
    assert store.load('repair')['title']=='另一个用户的计划'
