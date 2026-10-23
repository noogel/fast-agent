"""真实模型验证：修改、后台测试、中断、跨进程恢复与取消。仅操作临时目录。"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.jobs import JobStore
from fast_agent.permissions import PermissionPolicy
from fast_agent.providers import OpenAIModel
from fast_agent.tasks import TaskStore
from fast_agent.tools import ToolContext, build_default_registry


def worker(phase, root, task=''):
    test_argv = [sys.executable, 'check.py']
    sleep_argv = [sys.executable, '-u', '-c', 'import time; print("started", flush=True); time.sleep(60)']
    events = []
    def observe(event):
        events.append({'kind':event.kind, 'data':event.data})
        if event.kind in {'tool_start','tool_end','task_start'}:
            print(json.dumps(events[-1],ensure_ascii=False)[:2000],file=sys.stderr,flush=True)
        if phase == 'start' and event.kind == 'tool_end' and event.data['name'] == 'start_command' and event.data['result']['ok']:
            raise KeyboardInterrupt  # 模拟启动命令后终端中断；该结果已保存
    def approve(prompt):
        if prompt.startswith(f'修改文件 {root / "note.txt"}\n'):
            return True
        if prompt.startswith('取消后台任务 '):
            return any(prompt == f"取消后台任务 {j['id']}？" for j in JobStore(root).list())
        return False
    context = ToolContext(root, approve, True, policy=PermissionPolicy('restricted', [test_argv, sleep_argv]))
    model = OpenAIModel(Config.from_env(os.environ))
    agent = Agent(model, build_default_registry(), context, max_steps=15, stream=True, checkpoints=True, on_event=observe)
    try:
        if phase == 'start':
            prompt = f'先 read_file 读取 note.txt，再用 edit_file 将唯一的旧标题替换成 Fast Agent，然后 start_command 后台执行精确 argv={json.dumps(test_argv)}，timeout=30。不要用 run_command，不要执行其他命令。'
        elif phase == 'resume':
            agent.resume(task)
            prompt = '恢复之前的任务。之前的 start_command 已经执行且结果已保存，绝对不要再次启动命令，也不要再次编辑。使用历史里的 job_id 调用 poll_command，wait_seconds=10，检查后台测试最终退出码和日志；read_file 复核 note.txt，最后中文总结。'
        else:
            prompt = f'用 start_command 启动精确 argv={json.dumps(sleep_argv)}，timeout=90；获取 job_id 后使用 cancel_command 取消该任务，再 poll_command（wait_seconds=10）核实 cancelled。只处理本轮创建的任务。'
        result = agent.run(prompt)
        if phase != 'start' and not result.completed:
            raise RuntimeError(result.text)
        tools = [e['data']['name'] for e in events if e['kind']=='tool_start']
        if phase == 'start' and (result.completed or 'start_command' not in tools):
            raise RuntimeError('没有触发启动后的中断')
        return {'phase':phase,'pid':os.getpid(),'task_id':agent.last_task_id,'completed':result.completed,
                'tools':tools,'steps':result.steps,'delta_count':sum(e['kind']=='text_delta' for e in events)}
    finally:
        model.client.close()


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--worker':
        print(json.dumps(worker(sys.argv[2],Path(sys.argv[3]),sys.argv[4] if len(sys.argv)>4 else ''),ensure_ascii=False))
        return 0
    Config.from_env(os.environ)
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='fast-agent-reliability-') as directory:
        root=Path(directory).resolve()
        (root/'note.txt').write_text('旧标题\n正文保留\n')
        (root/'check.py').write_text('''from pathlib import Path
import time, unittest
with Path('executions.txt').open('a') as f: f.write('once\\n')
time.sleep(3)
class Check(unittest.TestCase):
 def test_note(self): self.assertEqual(Path('note.txt').read_text(), 'Fast Agent\\n正文保留\\n')
unittest.main()
''')
        reports=[]
        try:
            for phase in ['start','resume','cancel']:
                extra=[reports[0]['task_id']] if phase=='resume' else []
                result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker',phase,str(root),*extra],capture_output=False,stdout=subprocess.PIPE,text=True,timeout=240)
                if result.returncode: raise RuntimeError(f'{phase} 子进程失败')
                report=json.loads(result.stdout);reports.append(report)
                print(json.dumps(report,ensure_ascii=False),flush=True)
            jobs=[JobStore(root).poll(j['id']) for j in JobStore(root).list()]
            tests=[j for j in jobs if j['argv']==[sys.executable,'check.py']]
            cancelled=[j for j in jobs if j['status']=='cancelled']
            verified=(len(tests)==1 and tests[0]['status']=='succeeded' and tests[0]['returncode']==0
                and 'OK' in tests[0]['output'] and len(cancelled)==1
                and (root/'executions.txt').read_text()=='once\n'
                and (root/'note.txt').read_text()=='Fast Agent\n正文保留\n'
                and 'start_command' not in reports[1]['tools'] and 'edit_file' not in reports[1]['tools']
                and len({r['pid'] for r in reports})==3 and not TaskStore(root).blockers())
            record={'verified':bool(verified),'seconds':round(time.monotonic()-started,2),'phases':reports,
                    'execution_count':len((root/'executions.txt').read_text().splitlines()),
                    'jobs':[{'status':j['status'],'returncode':j['returncode'],'output':j['output']} for j in jobs]}
            destination=Path(__file__).resolve().parents[1]/'.fast-agent/validation'
            destination.mkdir(parents=True,exist_ok=True)
            (destination/'reliability-demo.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
            print(json.dumps(record,ensure_ascii=False,indent=2))
            return 0 if verified else 1
        finally:
            for job in JobStore(root).list():
                if job['status'] in {'queued','running','unknown'}: JobStore(root).cancel(job['id'])
            deadline=time.monotonic()+5
            while any(j['status'] in {'queued','running'} for j in JobStore(root).list()) and time.monotonic()<deadline:
                time.sleep(.1)


if __name__=='__main__':
    raise SystemExit(main())
