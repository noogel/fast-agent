"""真实模型读取资源前后版本，验证失效和会话恢复；不输出凭据。"""
import json
import os
from pathlib import Path
import tempfile
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.tools import ToolContext,build_default_registry
from fast_agent.skills import skill_tools
from fast_agent.models import ToolCall
from fast_agent.sessions import SessionStore


def main():
    model=OpenAIModel(Config.from_env(os.environ))
    try:
        with tempfile.TemporaryDirectory(prefix='fast-agent-skill-versions-') as directory:
            root=Path(directory);skill=root/'skills/review';skill.mkdir(parents=True)
            (skill/'SKILL.md').write_text('使用 read_skill_resource 读取 evidence.txt，报告其中版本代号。')
            evidence=skill/'evidence.txt';evidence.write_text('VERSION-OLD-40\n')
            registry=build_default_registry()
            for tool in skill_tools(root,'skills'):registry.register(tool)
            events=[]
            agent=Agent(model,registry,ToolContext(root,lambda _:False,False),plan=True,on_event=events.append,max_steps=7)
            prompt='必须先用 inspect_skill_tree 检查 review 的资源树，然后用 read_skill 读取 review，再用 read_skill_resource 读取 review 的 evidence.txt，报告版本代号。'
            first=agent.run(prompt)
            old=agent.context.skill_resource_reads.get('review',{}).get('evidence.txt')
            old_tree=agent.context.skill_trees.get('review')
            (skill/'new-unread.bin').write_bytes(b'new resource')
            evidence.write_text('VERSION-NEW-40\n')
            discovered=registry.execute(ToolCall('discover','list_skills','{}'),agent.context)
            invalidated='review' not in agent.context.skill_reads
            pinned=registry.execute(ToolCall('pin','read_skill_resource',json.dumps({'name':'review','path':'evidence.txt','expected_sha256':old})),agent.context) if old else None
            events.clear()
            second=agent.run('资源已由用户更新，旧内容不可作为当前依据。'+prompt)
            fresh=any(e.kind=='tool_end' and e.data['name']=='read_skill_resource' and e.data['result']['ok'] for e in events)
            SessionStore(root).save('versions',agent.history)
            agent.restore_history(SessionStore(root).load('versions'))
            cleared=not agent.context.skill_resource_reads and not agent.context.skill_reads and not agent.context.skill_trees
            events.clear()
            third=agent.run('会话已恢复，请重新读取。'+prompt)
            checks={'tree_pinned':bool(old_tree),'tree_refreshed':bool(agent.context.skill_trees.get('review')) and agent.context.skill_trees.get('review')!=old_tree,'old_completed':first.completed,'old_value':'VERSION-OLD-40' in first.text,
                    'change_discovered':discovered.data['skills'][0]['resource_changes']==['evidence.txt'],
                    'invalidated':invalidated,'stale_pin_rejected':pinned is not None and not pinned.ok,
                    'fresh_read':fresh,'new_completed':second.completed,'new_value':'VERSION-NEW-40' in second.text,
                    'restore_cleared':cleared,'restored_completed':third.completed,'restored_value':'VERSION-NEW-40' in third.text,
                    'restored_tool':any(e.kind=='tool_end' and e.data['name']=='read_skill_resource' and e.data['result']['ok'] for e in events)}
            print(json.dumps({'verified':all(checks.values()),'checks':checks,'usage':model.usage.snapshot(),'answers':[first.text,second.text,third.text]},ensure_ascii=False,indent=2))
            return 0 if all(checks.values()) else 1
    finally:model.client.close()


if __name__=='__main__':raise SystemExit(main())
