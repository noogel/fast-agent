"""真实超限→分段摘要→工具调用；只发送合成历史，可能产生长输入费用。"""
import json,os,tempfile
from pathlib import Path
from dataclasses import replace
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.models import Message
from fast_agent.compaction import ContextPolicy
from fast_agent.tools import ToolContext,ToolRegistry,build_default_registry
from fast_agent.sessions import SessionStore,validate_history


def main():
    model=OpenAIModel(replace(Config.from_env(os.environ),max_requests=24,max_retries=0,max_output_tokens=4096))
    try:
        with tempfile.TemporaryDirectory(prefix='fast-agent-overflow-') as directory:
            root=Path(directory).resolve();(root/'answer.txt').write_text('OVERFLOW-RECOVERED')
            registry=ToolRegistry();registry.register(build_default_registry().tools['read_file'])
            events=[]
            agent=Agent(model,registry,ToolContext(root,lambda _:False,False),max_steps=6,
                context_policy=ContextPolicy(0,1),on_event=events.append)
            agent.history += [Message('user','这是已完成的合成压力材料，不含任务信息：'+'龘 '*600000),Message('assistant','合成材料已记录。')]
            result=agent.run('实际读取 answer.txt，报告其中的代号。不要修改文件。')
            validate_history(agent.history)
            starts=[e for e in events if e.kind=='compaction_start' and e.data.get('reason')=='context_window']
            summaries=[e.data for e in events if e.kind=='compaction_end']
            backups=list((root/'.fast-agent/sessions').glob('overflow-*.json'))
            checks={'completed':result.completed,'actual_service_rejection':len(starts)==1,
                'chunked':bool(summaries) and summaries[0].get('summary_chunks',0)>1,
                'tool_called':any(e.kind=='tool_end' and e.data['name']=='read_file' and e.data['result']['ok'] for e in events),
                'code':'OVERFLOW-RECOVERED' in result.text,'backup':len(backups)==1,
                'source_unchanged':(root/'answer.txt').read_text()=='OVERFLOW-RECOVERED'}
            if backups:
                original=SessionStore(root).load(backups[0].stem)
                checks['backup_preserved_long_history']=len(original[1].content)>1200000
            print(json.dumps({'verified':all(checks.values()),'checks':checks,'summaries':summaries,
                'usage':model.usage.snapshot(),'answer':result.text},ensure_ascii=False,indent=2))
            return 0 if all(checks.values()) else 1
    finally:model.client.close()


if __name__=='__main__':raise SystemExit(main())
