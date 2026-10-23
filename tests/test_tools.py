import json
import sys
import pytest
from fast_agent.models import ToolCall
from fast_agent.tools import ToolContext, build_default_registry

def invoke(root,name,args,allow=True,interactive=True):
    registry=build_default_registry()
    context=ToolContext(root,lambda _:allow,interactive)
    if name in {'edit_file','write_file'} and (root/args['path']).is_file():
        registry.execute(ToolCall('read','read_file',json.dumps({'path':args['path']})),context)
    return registry.execute(ToolCall('1',name,json.dumps(args)),context)

@pytest.mark.parametrize('name,args',[('missing',{}),('read_file',{}),('read_file',{'path':1}),('read_file',{'path':'a','extra':1}),('run_command',{'argv':'ls'})])
def test_bad_arguments(tmp_path,name,args):
    assert not invoke(tmp_path,name,args).ok

def test_invalid_json(tmp_path):
    r=build_default_registry().execute(ToolCall('1','read_file','{'),ToolContext(tmp_path,lambda _:False,False))
    assert not r.ok

def test_file_roundtrip(tmp_path):
    assert invoke(tmp_path,'write_file',{'path':'a','content':'旧标题'}).ok
    assert not invoke(tmp_path,'write_file',{'path':'a','content':'覆盖'},False).ok
    assert invoke(tmp_path,'edit_file',{'path':'a','old':'旧','new':'新'}).ok
    assert invoke(tmp_path,'read_file',{'path':'a'}).data['content']=='新标题'
    assert not invoke(tmp_path,'edit_file',{'path':'a','old':'','new':'x'}).ok
    assert not invoke(tmp_path,'edit_file',{'path':'a','old':'missing','new':'x'}).ok

def test_boundaries(tmp_path):
    (tmp_path/'link').symlink_to(tmp_path.parent)
    for path in ['../outside','link/outside','.']:
        assert not invoke(tmp_path,'read_file',{'path':path}).ok
    (tmp_path/'bad').write_bytes(b'\xff')
    assert not invoke(tmp_path,'read_file',{'path':'bad'}).ok
    (tmp_path/'large').write_bytes(b'x'*65537)
    assert not invoke(tmp_path,'read_file',{'path':'large'}).ok
    assert not invoke(tmp_path,'write_file',{'path':'x','content':'x'*1048577}).ok

@pytest.mark.parametrize('allow,interactive',[(False,True),(True,False)])
def test_command_refused(tmp_path,allow,interactive):
    result=invoke(tmp_path,'run_command',{'argv':[sys.executable,'-c',"open('oops','w').write('x')"]},allow,interactive)
    assert not result.ok and not (tmp_path/'oops').exists()

def test_command(tmp_path):
    r=invoke(tmp_path,'run_command',{'argv':[sys.executable,'-c','import sys;print(sys.argv[1]);sys.exit(3)','$(echo bad)']})
    assert r.data['returncode']==3 and '$(echo bad)' in r.data['output']
    assert not r.ok

def test_command_limit(tmp_path):
    r=invoke(tmp_path,'run_command',{'argv':[sys.executable,'-c',"print('a'*200000)"]})
    assert r.ok and r.data['truncated'] and len(r.data['output'])<=65536

def test_command_timeout(tmp_path):
    r=invoke(tmp_path,'run_command',{'argv':[sys.executable,'-c','import time;time.sleep(10)'],'timeout':0.1})
    assert not r.ok and '超时' in r.error

def test_child_pipe_timeout(tmp_path):
    r=invoke(tmp_path,'run_command',{'argv':[sys.executable,'-c',"import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'])"],'timeout':0.2})
    assert not r.ok and '超时' in r.error

def test_duplicate_registration():
    registry=build_default_registry()
    with pytest.raises(ValueError): registry.register(registry.tools['read_file'])

def test_ambiguous_edit(tmp_path):
    (tmp_path/'a').write_text('repeat repeat')
    assert not invoke(tmp_path,'edit_file',{'path':'a','old':'repeat','new':'x'}).ok
    assert (tmp_path/'a').read_text()=='repeat repeat'

def test_write_boundary(tmp_path):
    (tmp_path/'link').symlink_to(tmp_path.parent)
    assert not invoke(tmp_path,'write_file',{'path':'link/outside','content':'x'}).ok

def test_command_interrupt(tmp_path,monkeypatch):
    import os
    import selectors
    import subprocess
    captured=[]
    original=subprocess.Popen
    def capture(*a,**kw):
        p=original(*a,**kw);captured.append(p);return p
    monkeypatch.setattr(subprocess,'Popen',capture)
    def interrupt(*a,**kw): raise KeyboardInterrupt
    monkeypatch.setattr(selectors.DefaultSelector,'select',interrupt)
    with pytest.raises(KeyboardInterrupt):
        invoke(tmp_path,'run_command',{'argv':[sys.executable,'-c','import time;time.sleep(10)']})
    assert captured[0].poll() is not None
    with pytest.raises(ProcessLookupError): os.killpg(captured[0].pid,0)
