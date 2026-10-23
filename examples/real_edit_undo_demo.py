"""真实模型修复与跨进程恢复，再通过本地命令安全撤销。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from contextlib import redirect_stdout
from fast_agent.edit_history import EditHistory
from fast_agent.tools import ToolContext
from fast_agent.session_commands import handle_session_command
from real_multi_edit_demo import worker, git


def main():
    if len(sys.argv) > 1:
        root = Path(sys.argv[2])
        if sys.argv[1] == 'undo':
            history = EditHistory(root)
            ctx = ToolContext(root, lambda _: True, True)
            with redirect_stdout(sys.stderr):
                handle_session_command('/changes', SimpleNamespace(context=ctx))
                for record in history.list():
                    handle_session_command('/undo ' + record['id'], SimpleNamespace(context=ctx))
            print(json.dumps({'pid': os.getpid(), 'records': history.list()}))
        else:
            print(json.dumps(worker(sys.argv[1], root, sys.argv[3] if len(sys.argv)>3 else ''), ensure_ascii=False))
        return
    with tempfile.TemporaryDirectory(prefix='fast-agent-undo-') as directory:
        root = Path(directory).resolve()
        (root/'src').mkdir(); (root/'tests').mkdir()
        source = root/'src/math_utils.py'
        original = 'def add(a, b):\n    return a - b\n\ndef multiply(a, b):\n    return a + b\n'
        source.write_text(original)
        test = root/'tests/test_math.py'
        test.write_text('import unittest\nfrom src.math_utils import add, multiply\nclass Tests(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2,3),5)\n    def test_multiply(self): self.assertEqual(multiply(2,3),6)\n')
        tests_before = test.read_bytes()
        command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']
        import shlex
        (root/'AGENTS.md').write_text('仅修改 src/math_utils.py，保留函数签名，添加中文注释，禁止修改测试。测试命令：'+shlex.join(command))
        (root/'.gitignore').write_text('.fast-agent/\n__pycache__/\n')
        git(root,'init','-q'); git(root,'add','.')
        git(root,'-c','user.name=Demo','-c','user.email=demo@example.invalid','commit','-qm','fixture')
        def tests():
            return subprocess.run(command,cwd=root,capture_output=True,text=True).returncode
        baseline = tests()
        phases=[]
        for phase in ('plan','execute','restore'):
            args = [phases[0]['task_id']] if phase=='execute' else []
            process = subprocess.run([sys.executable,__file__,phase,str(root),*args],capture_output=True,text=True,timeout=600)
            if process.returncode:
                raise RuntimeError(process.stderr[-3000:])
            phases.append(json.loads(process.stdout))
            print('完成阶段 '+phase, file=sys.stderr, flush=True)
        fixed = tests()
        repaired = source.read_bytes()
        history=EditHistory(root)
        ident=history.list()[0]['id']
        source.write_text('外部修改\n')
        conflict=False
        try:
            history.undo(ident,ToolContext(root,lambda _:True,True))
        except ValueError:
            conflict=source.read_text()=='外部修改\n'
        source.write_bytes(repaired)
        process=subprocess.run([sys.executable,__file__,'undo',str(root)],capture_output=True,text=True,timeout=30,check=True)
        undo=json.loads(process.stdout)
        reverted=tests()
        checks={'baseline_failed':baseline!=0,'fixed_passed':fixed==0,'undo_tests_failed':reverted!=0,
                'restored_plan':phases[2]['mode']=='plan','restored_read': 'read_file' in phases[2]['calls'],
                'tool_edit': 'multi_edit_file' in phases[1]['calls'], 'tool_tests':'run_command' in phases[1]['calls'],
                'distinct_processes':len({p['pid'] for p in phases} | {undo['pid']})==4,
                'conflict_preserved':conflict,'exact_original':source.read_text()==original,
                'tests_unchanged':test.read_bytes()==tests_before,'git_clean':not git(root,'diff'),
                'records_undone':all(r['status']=='undone' for r in undo['records'])}
        report={'verified':all(checks.values()),'checks':checks,'phases':phases,'undo':undo}
        destination=Path(__file__).resolve().parents[1]/'.fast-agent/validation/edit-undo-demo.json'
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({'verified':report['verified'],'checks':checks},ensure_ascii=False,indent=2))
        if not report['verified']: raise SystemExit(1)


if __name__=='__main__':
    main()
