"""真实模型选择 LSP 工具；独立核对结果，不以模型自述作为成功依据。"""
import json,os,shutil,tempfile
from pathlib import Path
from dataclasses import replace
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.tools import ToolContext,ToolRegistry
from fast_agent.lsp_tools import lsp_tools
from fast_agent.sessions import SessionStore


def main():
    executable=shutil.which('clangd')
    if not executable:raise RuntimeError('真实验收需要 clangd')
    model=OpenAIModel(replace(Config.from_env(os.environ),max_requests=8))
    try:
        with tempfile.TemporaryDirectory(prefix='fast-agent-lsp-') as directory:
            root=Path(directory).resolve()
            source='int answer(){return 42;}\nint main(){return answer();}\n'
            (root/'main.cpp').write_text(source)
            broken='int broken(){return missing_name;}\n'
            (root/'broken.cpp').write_text(broken)
            config=root/'lsp.json'
            config.write_text(json.dumps({'argv':[executable,'--background-index=0'],'language_id':'cpp','extensions':['.cpp'],'timeout':10}))
            registry=ToolRegistry()
            for tool in lsp_tools(config):registry.register(tool)
            events=[]
            context=ToolContext(root,lambda prompt:prompt.startswith('启动语言服务'),True)
            agent=Agent(model,registry,context,max_steps=8,on_event=lambda event:events.append(event))
            result=agent.run('请实际调用三个 LSP 工具，不修改文件：查询 main.cpp 符号；用 line=2,column=19 定位 main.cpp 的 answer 调用定义（工具输入是1基，不要转换）；取得 broken.cpp 当前诊断。最后中文说明函数、定义位置和错误。')
            outputs={event.data['name']:event.data['result'] for event in events if event.kind=='tool_end'}
            symbols=outputs.get('lsp_symbols',{}).get('data',{}).get('result',[])
            definitions=outputs.get('lsp_definition',{}).get('data',{}).get('result',[])
            diagnostics=outputs.get('lsp_diagnostics',{}).get('data',{}).get('result',{})
            SessionStore(root).save('lsp-reviewed',agent.history)
            restored=Agent(model,registry,ToolContext(root,lambda _:False,False))
            restored.restore_history(SessionStore(root).load('lsp-reviewed'))
            checks={
                'completed':result.completed,
                'all_tools_succeeded':all(outputs.get(name,{}).get('ok') for name in ('lsp_symbols','lsp_definition','lsp_diagnostics')),
                'symbols':{'answer','main'}<={item.get('name') for item in symbols},
                'definition':any(item.get('uri')==(root/'main.cpp').as_uri() and item.get('range',{}).get('start',{}).get('line')==0 for item in definitions),
                'diagnostic':diagnostics.get('version')==1 and any(item.get('severity')==1 and 'missing_name' in item.get('message','') for item in diagnostics.get('items',[])),
                'source_unchanged':(root/'main.cpp').read_text()==source and (root/'broken.cpp').read_text()==broken,
                'authorized':len(context.audit)>=3 and all(item['effect']=='lsp_start' and item['allowed'] for item in context.audit),
                'restored_plan':restored.context.mode=='plan',
                'restored_results':restored.history[1:]==agent.history[1:] and bool(outputs),
            }
            print(json.dumps({'verified':all(checks.values()),'checks':checks,'tool_results':outputs,'usage':model.usage.snapshot(),'answer':result.text},ensure_ascii=False,indent=2))
            return 0 if all(checks.values()) else 1
    finally:model.client.close()


if __name__=='__main__':raise SystemExit(main())
