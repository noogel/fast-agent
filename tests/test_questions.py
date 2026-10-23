import pytest
from fast_agent.questions import Questions


def test_persistent_answer_not_authorization(tmp_path):
    q=Questions(tmp_path);record=q.create('修改哪些文件？',['A','B'])
    answer=Questions(tmp_path).answer(record['id'],'A')
    assert answer['authorization'] is False
    assert Questions(tmp_path).load(record['id'])['answer']=='A'
    with pytest.raises(ValueError):q.answer(record['id'],'B')


def test_expired_or_invalid_cannot_answer(tmp_path,monkeypatch):
    import fast_agent.questions as module
    monkeypatch.setattr(module.time,'time',lambda:100)
    q=Questions(tmp_path);record=q.create('选择？',ttl=1)
    monkeypatch.setattr(module.time,'time',lambda:102)
    with pytest.raises(ValueError,match='过期'):q.answer(record['id'],'yes')
    assert q.load(record['id'])['status']=='pending'
    with pytest.raises(ValueError):q.load('../escape')


def test_pause_save_restore_answer_and_no_duplicate(tmp_path):
    import json
    from fast_agent.agent import Agent
    from fast_agent.models import ModelReply,ToolCall
    from fast_agent.tools import ToolContext,build_default_registry
    from fast_agent.sessions import SessionStore
    class Model:
        def __init__(self):self.calls=0
        def complete(self,*args):
            self.calls+=1
            if self.calls==1:return ModelReply(tool_calls=[ToolCall('q','ask_user','{"question":"修复 A 还是 B？"}'),ToolCall('r','read_file','{"path":"missing"}')])
            return ModelReply('已收到 A，保持计划模式')
    ctx=ToolContext(tmp_path,lambda _:False,False)
    model=Model();agent=Agent(model,build_default_registry(),ctx)
    result=agent.run('修复问题')
    assert not result.completed and model.calls==1
    payload=json.loads(agent.history[-2].content)
    identifier=payload['data']['pending_question']
    assert '尚未执行' in agent.history[-1].content
    store=SessionStore(tmp_path);store.save('waiting',agent.history)
    Questions(tmp_path).answer(identifier,'A')
    agent.restore_history(store.load('waiting'))
    assert agent.continue_question(identifier).completed
    assert ctx.mode=='plan'
    with pytest.raises(ValueError,match='已经接收'):agent.continue_question(identifier)


def test_listing_preserves_invalid_and_expired(tmp_path,monkeypatch):
    import fast_agent.questions as module
    monkeypatch.setattr(module.time,'time',lambda:100)
    store=Questions(tmp_path);record=store.create('旧问题',ttl=1)
    store.store.write('a'*32+'.json',{'id':'a'*32})
    monkeypatch.setattr(module.time,'time',lambda:102)
    result=store.list()
    assert result['questions'][0]['id']==record['id']
    assert result['questions'][0]['expired'] is True
    assert result['invalid']==['a'*32]
