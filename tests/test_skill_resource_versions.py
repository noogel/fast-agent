from test_skills import setup
from fast_agent.agent import Agent
from fast_agent.models import ModelReply


def test_pagination_refuses_changed_version(tmp_path):
    skill,_,ctx,call=setup(tmp_path)
    call('read_skill',name='review')
    first=call('read_skill_resource',name='review',path='notes.txt',max_lines=1)
    digest=first.data['sha256']
    (skill/'notes.txt').write_text('新版\n第二段\n')
    result=call('read_skill_resource',name='review',path='notes.txt',start_line=2,expected_sha256=digest)
    assert not result.ok and '版本' in result.error
    assert 'review' not in ctx.skill_reads
    fresh=call('read_skill_resource',name='review',path='notes.txt')
    assert fresh.ok and fresh.data['sha256']!=digest


def test_discovery_invalidates_changed_and_missing_resources(tmp_path):
    skill,_,ctx,call=setup(tmp_path)
    call('read_skill',name='review')
    call('read_skill_resource',name='review',path='notes.txt')
    (skill/'notes.txt').unlink()
    row=call('list_skills').data['skills'][0]
    assert row['resource_changes']==['notes.txt']
    assert row['loaded_state']=='changed' and 'review' not in ctx.skill_reads


def test_restore_clears_resource_markers(tmp_path):
    _,registry,ctx,call=setup(tmp_path)
    class Model:
        def complete(self,*args):return ModelReply('ok')
    agent=Agent(Model(),registry,ctx)
    call('read_skill_resource',name='review',path='notes.txt')
    assert ctx.skill_resource_reads
    agent.restore_history(agent.history)
    assert ctx.skill_resource_reads=={}


def test_changed_read_reports_new_digest_and_invalidates(tmp_path):
    skill,_,ctx,call=setup(tmp_path)
    call('read_skill',name='review')
    old=call('read_skill_resource',name='review',path='notes.txt').data['sha256']
    (skill/'notes.txt').write_text('updated')
    result=call('read_skill_resource',name='review',path='./notes.txt')
    assert result.ok and result.data['changed'] and result.data['previous_sha256']==old
    assert 'review' not in ctx.skill_reads
    assert len(ctx.skill_resource_reads['review'])==1


def test_resource_tracking_is_bounded(tmp_path):
    skill,_,ctx,call=setup(tmp_path)
    for i in range(64):
        (skill/f'{i}.txt').write_text('ok')
        assert call('read_skill_resource',name='review',path=f'{i}.txt').ok
    (skill/'extra.txt').write_text('extra')
    assert not call('read_skill_resource',name='review',path='extra.txt').ok
    assert call('read_skill_resource',name='review',path='0.txt').ok


def test_unchanged_pagination_and_link_replacement(tmp_path):
    skill,_,ctx,call=setup(tmp_path)
    call('read_skill',name='review')
    digest=call('read_skill_resource',name='review',path='notes.txt').data['sha256']
    result=call('read_skill_resource',name='review',path='notes.txt',start_line=2,max_lines=1,expected_sha256=digest)
    assert result.ok and result.data['content']=='第二行\n' and not result.data['changed']
    (skill/'notes.txt').unlink();(skill/'notes.txt').symlink_to(skill/'SKILL.md')
    assert not call('read_skill_resource',name='review',path='notes.txt').ok
    assert 'review' not in ctx.skill_reads
