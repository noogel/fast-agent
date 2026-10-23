"""真实模型提问、离线答复、加载历史后继续；不授予写入权限。"""
import json
import os
from pathlib import Path
import tempfile
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.tools import ToolContext,build_default_registry
from fast_agent.questions import Questions
from fast_agent.sessions import SessionStore


def main():
    model=OpenAIModel(Config.from_env(os.environ))
    try:
        with tempfile.TemporaryDirectory(prefix='fast-agent-question-') as directory:
            root=Path(directory);ctx=ToolContext(root,lambda _:False,False)
            agent=Agent(model,build_default_registry(),ctx,max_steps=4,plan=True)
            first=agent.run('这是需求澄清任务。必须调用 ask_user 询问报告应使用中文还是英文，在获得回答前不要猜测，不要访问文件。')
            ids=Questions(root).store.names()
            checks={'paused':not first.completed,'one_question':len(ids)==1}
            if len(ids)==1:
                identifier=ids[0];sessions=SessionStore(root);sessions.save('pending',agent.history)
                Questions(root).answer(identifier,'使用中文，并在最终回复包含验收标记 DURABLE-45。')
                fresh=Agent(model,build_default_registry(),ToolContext(root,lambda _:False,False),max_steps=4)
                fresh.restore_history(sessions.load('pending'))
                final=fresh.continue_question(identifier)
                checks.update(completed=final.completed,answer_used='DURABLE-45' in final.text,
                              still_plan=fresh.context.mode=='plan',not_authorized=Questions(root).load(identifier)['authorization'] is False)
                try:fresh.continue_question(identifier)
                except ValueError:checks['duplicate_refused']=True
                else:checks['duplicate_refused']=False
                sessions.save('answered',fresh.history)
                checks['saved_after_resume']=len(sessions.load('answered'))==len(fresh.history)
            print(json.dumps({'verified':all(checks.values()),'checks':checks,'usage':model.usage.snapshot(),'first_text':first.text},ensure_ascii=False,indent=2))
            return 0 if all(checks.values()) else 1
    finally:model.client.close()


if __name__=='__main__':raise SystemExit(main())
