"""真实模型验证显式 Skill、资源读取和恢复重读；凭据只来自环境。"""
import contextlib
import io
import json
import os
import tempfile
from pathlib import Path
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.tools import ToolContext,build_default_registry
from fast_agent.skills import skill_tools
from fast_agent.sessions import SessionStore,validate_history
from fast_agent.session_commands import handle_session_command


def main():
    model=OpenAIModel(Config.from_env(os.environ))
    try:
        with tempfile.TemporaryDirectory(prefix='fast-agent-skill-') as directory:
            root=Path(directory);skill=root/'skills'/'review';skill.mkdir(parents=True)
            (skill/'SKILL.md').write_text('---\nname: review\ndescription: 读取验收资源\n---\n使用 list_skill_resources 列出资源，再用 read_skill_resource 读取 evidence.txt，报告验收代号。')
            (skill/'evidence.txt').write_text('验收代号 SKILL-36\n')
            events=[]
            def make():
                registry=build_default_registry()
                for tool in skill_tools(root,'skills'):registry.register(tool)
                return Agent(model,registry,ToolContext(root,lambda _:False,False),plan=True,on_event=events.append)
            agent=make()
            with contextlib.redirect_stdout(io.StringIO()):
                handle_session_command('/skill review 按说明读取资源并报告代号',agent)
            first=list(events)
            SessionStore(root).save('skill',agent.history)
            restored=make();restored.restore_history(SessionStore(root).load('skill'))
            empty=not restored.context.skill_reads
            events.clear()
            result=restored.run('已恢复。请用 read_skill 重新读取 review，再用 read_skill_resource 重新读取 evidence.txt，报告代号。')
            validate_history(restored.history)
            def used(records,name):return any(e.kind=='tool_end' and e.data['name']==name and e.data['result']['ok'] for e in records)
            checks={'explicit_read':bool(agent.context.skill_reads),'first_resource':used(first,'read_skill_resource'),
                    'restored_markers_empty':empty,'restored_plan':restored.context.mode=='plan',
                    'fresh_skill':used(events,'read_skill'),'fresh_resource':used(events,'read_skill_resource'),
                    'completed':result.completed,'answer': 'SKILL-36' in result.text}
            print(json.dumps({'verified':all(checks.values()),'checks':checks,'usage':model.usage.snapshot()},ensure_ascii=False,indent=2))
            if not all(checks.values()):raise SystemExit(1)
    finally:model.client.close()


if __name__=='__main__':main()
