"""MiniMax 修复隔离工作区，并在精确基点的干净目录验收交付补丁。"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from fast_agent.agent import Agent
from fast_agent.config import Config
from fast_agent.providers import OpenAIModel
from fast_agent.tools import ToolContext, build_default_registry
from fast_agent.worktrees import create_worktree, export_worktree
from fast_agent.worktree_lifecycle import archive_worktree,restore_worktree,reconcile_worktree


def main():
    model=OpenAIModel(Config.from_env(os.environ))
    try:
        with tempfile.TemporaryDirectory(prefix='fast-agent-delivery-') as directory:
            root=Path(directory).resolve()
            command=[sys.executable,'-m','unittest','discover','-s','tests','-v']
            (root/'tests').mkdir()
            (root/'calc.py').write_text('def add(a, b):\n    return a - b\n')
            tests='import unittest\nfrom calc import add\nclass TestAdd(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2,3),5)\n        self.assertEqual(add(-1,1),0)\n'
            (root/'tests/test_calc.py').write_text(tests)
            (root/'.gitignore').write_text('.fast-agent/\n__pycache__/\n')
            (root/'AGENTS.md').write_text('只修改 calc.py，禁止修改测试。先运行测试复现，再修复，再运行同一测试命令，最后 git_status 和 git_diff。\n命令：'+shlex.join(command))
            for args in [['init','-q'],['add','.'],['-c','user.name=Demo','-c','user.email=demo@example.invalid','commit','-qm','fixture']]:
                subprocess.run(['git',*args],cwd=root,check=True,capture_output=True)
            parent=ToolContext(root,lambda _:True,True)
            info=create_worktree('repair',parent)
            target=Path(info['path'])
            (root/'calc.py').write_text('# parent dirty, must remain\n')
            baseline=subprocess.run(command,cwd=target,capture_output=True,text=True,timeout=30)
            events=[]
            def approve(prompt):
                return prompt==f'执行命令 {shlex.join(command)}？' or prompt.startswith(f'修改文件 {target/"calc.py"}\n')
            agent=Agent(model,build_default_registry(),ToolContext(target,approve,True),max_steps=12,on_event=events.append)
            answer=agent.run('修复当前仓库加法测试失败。必须 read_file 读取 calc.py 和 tests/test_calc.py，先 run_command 执行 AGENTS.md 精确命令复现失败，再 edit_file 修复 calc.py，再运行同一命令通过测试，最后 git_status 和 git_diff。不要修改测试或执行其他命令。')
            after=subprocess.run(command,cwd=target,capture_output=True,text=True,timeout=30)
            calls=[e.data['name'] for e in events if e.kind=='tool_end' and e.data['result']['ok']]
            runs=[e.data['result']['data'].get('returncode') for e in events if e.kind=='tool_end' and e.data['name']=='run_command']
            checks={'completed':answer.completed,'baseline_fails':baseline.returncode!=0,'fixed_tests_pass':after.returncode==0,
                    'tools_used':{'read_file','edit_file','git_status','git_diff'}.issubset(calls),
                    'model_ran_failure_and_success':len(runs)>=2 and runs[0]!=0 and runs[-1]==0,
                    'tests_unchanged':(target/'tests/test_calc.py').read_text()==tests,
                    'parent_dirty_preserved':(root/'calc.py').read_text()=='# parent dirty, must remain\n'}
            # 即使模型失败也输出诚实报告；只有实际修复才进入补丁验收。
            if checks['fixed_tests_pass']:
                delivery=export_worktree('repair',parent)
                package=json.loads(Path(delivery['path']).read_text())
                patch=base64.b64decode(package['patch_base64'])
                checks['checksum']=hashlib.sha256(patch).hexdigest()==package['sha256']
                clean=root/'acceptance'
                subprocess.run(['git','worktree','add','--detach',str(clean),package['base_commit']],cwd=root,check=True,capture_output=True)
                checked=subprocess.run(['git','apply','--check','-'],cwd=clean,input=patch,capture_output=True)
                checks['patch_check']=checked.returncode==0
                applied=subprocess.run(['git','apply','-'],cwd=clean,input=patch,capture_output=True)
                checks['patch_apply']=applied.returncode==0
                validation=subprocess.run(command,cwd=clean,capture_output=True,text=True,timeout=30)
                checks['delivered_tests_pass']=validation.returncode==0
                changed=subprocess.run(['git','diff','--name-only'],cwd=clean,capture_output=True,text=True,check=True).stdout.splitlines()
                checks['only_source_changed']=changed==['calc.py']
            # 交付后再制造尚未交付的文件和运行证据，验证归档不会以已有补丁为由丢弃它们。
            (target/'pending-note.txt').write_text('not delivered yet\n')
            (target/'__pycache__').mkdir(exist_ok=True)
            (target/'__pycache__/retain.bin').write_bytes(b'ignored evidence')
            proof=target/'.fast-agent/manual-evidence.json'
            proof.parent.mkdir(exist_ok=True)
            proof.write_text('{"status":"keep"}')
            preserved={p.relative_to(target):p.read_bytes() for p in target.rglob('*') if p.is_file() and p.name!='.git'}
            archived=Path(archive_worktree('repair',parent)['path'])
            checks['archived_preserves_all']=all((archived/p).read_bytes()==data for p,data in preserved.items())
            checks['active_path_removed']=not target.exists()
            # 模拟移动已完成、最终日志写回前退出；核实不能重放移动。
            from fast_agent.runtime_store import RuntimeStore
            journals=RuntimeStore(parent.root,'worktree-archives')
            record=journals.read('repair.json')
            record['status']='moving_archive'
            journals.write('repair.json',record)
            checks['reconciled_archive']=reconcile_worktree('repair',parent)['status']=='archived'
            restored=Path(restore_worktree('repair',parent)['path'])
            checks['restored_preserves_all']=all((restored/p).read_bytes()==data for p,data in preserved.items())
            checks['restored_tests_pass']=subprocess.run(command,cwd=restored,capture_output=True,timeout=30).returncode==0
            if checks['fixed_tests_pass'] and checks.get('delivered_tests_pass'):
                # 临时验收仓库把已经独立测试的交付提交到主 HEAD；保留主目录脏文件字节。
                subprocess.run(['git','-c','user.name=Demo','-c','user.email=demo@example.invalid','commit','-am','accepted repair'],cwd=clean,check=True,capture_output=True)
                accepted=subprocess.run(['git','rev-parse','HEAD'],cwd=clean,check=True,capture_output=True,text=True).stdout.strip()
                subprocess.run(['git','update-ref','HEAD',accepted],cwd=root,check=True,capture_output=True)
                from fast_agent.worktree_cleanup import cleanup_worktree,restore_evidence
                cleanup_target=Path(create_worktree('cleanup-proof',parent)['path'])
                (cleanup_target/'evidence.bin').write_bytes(b'accepted evidence\0\xff')
                cleanup_archive=Path(archive_worktree('cleanup-proof',parent)['path'])
                receipt=cleanup_worktree('cleanup-proof',parent)
                recovered=Path(restore_evidence(receipt['id'],parent)['path'])
                checks['cleanup_removed']=not cleanup_archive.exists()
                checks['cleanup_evidence_restored']=(recovered/'evidence.bin').read_bytes()==b'accepted evidence\0\xff'
                checks['cleanup_restored_tests']=subprocess.run(command,cwd=recovered,capture_output=True,timeout=30).returncode==0
                checks['cleanup_parent_preserved']=(root/'calc.py').read_text()=='# parent dirty, must remain\n'
            report={'verified':all(checks.values()),'checks':checks,'usage':model.usage.snapshot(),
                    'tools':calls,'test_runs':runs,'answer':answer.text,'test_output':after.stdout+after.stderr}
            print(json.dumps(report,ensure_ascii=False,indent=2))
            return 0 if report['verified'] else 1
    finally:model.client.close()


if __name__=='__main__':raise SystemExit(main())
