"""MiniMax 双客户端只读并发与持久报告验收。"""
import json,os,tempfile
from pathlib import Path
from dataclasses import replace
from fast_agent.providers import OpenAIModel
from fast_agent.config import Config
from fast_agent.agent import Agent
from fast_agent.tools import ToolContext,build_default_registry
from fast_agent.delegation_scheduler import delegate_parallel
from fast_agent.delegation_batches import DelegationBatches

model=OpenAIModel(replace(Config.from_env(os.environ),max_requests=12))
try:
    with tempfile.TemporaryDirectory(prefix='fast-agent-parallel-') as directory:
        root=Path(directory)
        (root/'a.txt').write_text('PARALLEL-A\n')
        (root/'b.txt').write_text('PARALLEL-B\n')
        parent=Agent(model,build_default_registry(),ToolContext(root,lambda _:False,False))
        history=list(parent.history)
        reports=delegate_parallel(parent,['用 read_file 读取 a.txt 并报告代号。','用 read_file 读取 b.txt 并报告代号。'])
        batches=DelegationBatches(root)
        batch=batches.reconcile(batches.store.names()[0])
        checks={'completed':all(r['completed'] for r in reports),'codes':all(code in r['text'] for code,r in zip(['PARALLEL-A','PARALLEL-B'],reports)),
            'tools':all(any(t['name']=='read_file' and t['ok'] for t in r['tools']) for r in reports),
            'parent_unchanged':parent.history==history,'batch_completed':batch['status']=='completed',
            'unique_reports':len({r['id'] for r in reports})==2,'budget_shared':0<model.usage.snapshot()['requests']<=12,
            'source_unchanged':(root/'a.txt').read_text()=='PARALLEL-A\n' and (root/'b.txt').read_text()=='PARALLEL-B\n'}
        print(json.dumps({'verified':all(checks.values()),'checks':checks,'usage':model.usage.snapshot()},ensure_ascii=False,indent=2))
        if not all(checks.values()):raise SystemExit(1)
finally:model.client.close()
