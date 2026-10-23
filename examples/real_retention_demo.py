"""临时记录：MiniMax读取、会话引用保护、隔离恢复、到期删除。"""
import json,os,tempfile,time
from pathlib import Path
from dataclasses import replace
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.tools import ToolContext,ToolRegistry,ToolResult
from fast_agent.tool_outputs import retain_result,output_tool,read_output
from fast_agent.sessions import SessionStore
from fast_agent.runtime_store import RuntimeStore
from fast_agent.retention import preview_retention
from fast_agent.retention_actions import quarantine,restore as restore_quarantine
from fast_agent.retention_purge import preview_purge,purge


def main():
    model=OpenAIModel(replace(Config.from_env(os.environ),max_requests=8))
    try:
        with tempfile.TemporaryDirectory(prefix='fast-agent-retention-') as directory:
            root=Path(directory).resolve();context=ToolContext(root,lambda _:True,True)
            retained=retain_result(root,ToolResult(True,{'code':'RETENTION-RECOVERED','padding':'x'*20000}))
            identifier=retained.data['output_ref']['id'];store=RuntimeStore(root,'tool-outputs')
            path=store.path(identifier+'.json');original=path.read_bytes()
            old=time.time()-40*86400;os.utime(path,(old,old))
            registry=ToolRegistry();registry.register(output_tool())
            events=[]
            agent=Agent(model,registry,context,on_event=lambda e:events.append(e))
            result=agent.run(f'实际用 read_tool_output 读取 {identifier} 的第一页，并报告其中code字段。不要读取其他内容。')
            SessionStore(root).save('retention-reference',agent.history)
            protected=preview_retention(root)
            checks={'model_completed':result.completed,'model_read':any(e.kind=='tool_end' and e.data['result']['ok'] for e in events),
                'code':'RETENTION-RECOVERED' in result.text,
                'session_protects':any(row['path']==f'tool-outputs/{identifier}.json' and row['references'] for row in protected['protected'])}
            # 只删除本验收创建的临时会话，模拟用户明确移除最后一个持久引用。
            RuntimeStore(root,'sessions').path('retention-reference.json').unlink()
            preview=preview_retention(root)
            batch=quarantine(context,preview['fingerprint'])
            checks['quarantined']=not path.exists()
            restore_quarantine(context,batch['id'])
            checks['restored_exact']=path.read_bytes()==original
            checks['read_after_restore']='RETENTION-RECOVERED' in read_output({'output_id':identifier},context).data['content']
            # 第二份无引用临时结果专门用于不可逆删除，不删除恢复验收的证据。
            second=retain_result(root,ToolResult(True,{'padding':'y'*20000})).data['output_ref']['id']
            secondpath=store.path(second+'.json');os.utime(secondpath,(old,old))
            batch2=quarantine(context,preview_retention(root)['fingerprint'])
            try:preview_purge(root,batch2['id']);checks['early_purge_denied']=False
            except ValueError:checks['early_purge_denied']=True
            batch2['quarantined_at']=old  # 仅夹具推进隔离时钟，真实用户不能照此绕过保留期。
            RuntimeStore(root,'retention').write(batch2['id']+'.json',batch2)
            deletion=preview_purge(root,batch2['id'])
            final=purge(context,batch2['id'],deletion['fingerprint'])
            checks['purged']=final['status']=='purged' and not secondpath.exists()
            checks['manifest_retained']=RuntimeStore(root,'retention').read(batch2['id']+'.json')['status']=='purged'
            checks['restored_evidence_survives']=path.read_bytes()==original
            print(json.dumps({'verified':all(checks.values()),'checks':checks,'usage':model.usage.snapshot()},ensure_ascii=False,indent=2))
            return 0 if all(checks.values()) else 1
    finally:model.client.close()


if __name__=='__main__':raise SystemExit(main())
