from fast_agent.user_questions import ask_user
from fast_agent.tools import ToolContext


def test_answer_is_separate_from_permission(tmp_path):
    ctx=ToolContext(tmp_path,lambda _:False,False,mode='plan')
    ctx.ask_user=lambda question,choices:'只修复 A'
    result=ask_user({'question':'修复哪些文件？','choices':['只修复 A','全部']},ctx)
    assert result.ok and result.data['answer']=='只修复 A'
    assert ctx.mode=='plan' and not ctx.authorize('command',['touch','x'],'执行？')


def test_unavailable_and_declined(tmp_path):
    ctx=ToolContext(tmp_path,lambda _:False,False)
    assert not ask_user({'question':'选择？'},ctx).ok
    ctx.ask_user=lambda *_:None
    assert not ask_user({'question':'选择？'},ctx).ok


def test_agent_records_answer_and_restore_does_not_reask(tmp_path):
    import json
    from fast_agent.agent import Agent
    from fast_agent.models import ModelReply,ToolCall
    from fast_agent.tools import ToolRegistry
    from fast_agent.user_questions import question_tool
    from fast_agent.sessions import SessionStore
    calls=[]
    ctx=ToolContext(tmp_path,lambda _:False,False,mode='plan')
    ctx.ask_user=lambda q,c:calls.append(q) or 'A'
    registry=ToolRegistry();registry.register(question_tool())
    class Model:
        def __init__(self):self.i=0
        def complete(self,*args):
            self.i+=1
            return ModelReply(tool_calls=[ToolCall('q','ask_user',json.dumps({'question':'选 A 还是 B?'}))]) if self.i==1 else ModelReply('已收到 A')
    agent=Agent(Model(),registry,ctx)
    assert agent.run('澄清').completed and len(calls)==1
    SessionStore(tmp_path).save('question',agent.history)
    agent.restore_history(SessionStore(tmp_path).load('question'))
    assert len(calls)==1 and agent.context.mode=='plan'


def test_terminal_selection_and_eof(monkeypatch):
    from fast_agent.user_questions import terminal_question
    monkeypatch.setattr('builtins.input',lambda _:'2')
    assert terminal_question('选择',['A','B'])=='B'
    monkeypatch.setattr('builtins.input',lambda _:'')
    assert terminal_question('选择',[]) is None
