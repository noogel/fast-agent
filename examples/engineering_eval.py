"""版本化双任务评测：检索定位与代码修复；仅使用临时工作区。"""
import json,os,tempfile
from pathlib import Path
from dataclasses import replace
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.tools import ToolContext,build_default_registry
from fast_agent.evaluation import evaluate_suite,addition_checks,EvaluationFailure


def run_case(kind,config):
    model=OpenAIModel(replace(config,max_requests=8))
    try:
        with tempfile.TemporaryDirectory(prefix='fast-agent-eval-') as directory:
            root=Path(directory).resolve()
            source=root/'maths.py'
            before='def add(a, b):\n    return a - b\n'
            source.write_text(before)
            events=[]
            context=ToolContext(root,lambda prompt:prompt.startswith(f'修改文件 {source}\n'),True)
            registry=build_default_registry()
            # 任务只需要检索/读写；不允许模型自行替换评测断言或执行命令。
            allowed={'read_file','search_code','list_files','edit_file','write_file'}
            registry.tools={name:tool for name,tool in registry.tools.items() if name in allowed}
            agent=Agent(model,registry,context,max_steps=8,on_event=lambda event:events.append(event))
            prompt=('查找 add 函数，实际读取 maths.py，说明缺陷，不修改任何文件。' if kind=='locate'
                    else '实际读取 maths.py，将 add 修复为返回参数 a 与 b 的和，保留签名；只修改该文件。')
            result=agent.run(prompt)
            tools=[event.data for event in events if event.kind=='tool_end']
            checks={'read_actual_file':any(event['name']=='read_file' and event['result']['ok'] for event in tools)}
            if kind=='locate':
                checks['unchanged']=source.read_text()==before
                checks['search_used']=any(event['name']=='search_code' and event['result']['ok'] for event in tools)
            else:
                # 在宿主解析AST，避免把模型生成的Python当评测器执行。
                checks.update(addition_checks(source.read_text()))
                checks['edit_executed']=any(event['name'] in {'edit_file','write_file'} and event['result']['ok'] for event in tools)
            checks['no_extra_files']=set(path.name for path in root.iterdir())<={'maths.py','.fast-agent'}
            return {'completed':result.completed,'checks':checks,'usage':model.usage.snapshot()}
    except Exception:
        raise EvaluationFailure('task_failure',model.usage.snapshot()) from None
    finally:model.client.close()


def main():
    config=Config.from_env(os.environ)
    report=evaluate_suite('engineering-v2',[(kind,lambda kind=kind:run_case(kind,config)) for kind in ('locate','repair')],model=config.model)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':raise SystemExit(main())
