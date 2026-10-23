import json
import sys
import pytest
from fast_agent.extensions import load_extensions
from fast_agent.models import ToolCall
from fast_agent.tools import ToolContext, build_default_registry


def fixture(root,program=None):
    script=root/'extension.py'
    script.write_text(program or 'import sys,json\na=json.loads(sys.argv[-1])\nprint(json.dumps({"ok":True,"data":{"echo":a["text"]},"error":None}))\n')
    config=root/'extensions.json'
    config.write_text(json.dumps({'tools':[{'name':'echo','description':'回传文本','argv':[sys.executable,str(script)],
        'parameters':{'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False}}]}))
    registry=build_default_registry()
    for tool in load_extensions(config):registry.register(tool)
    return registry,config


def test_external_tool_contract_and_validation(tmp_path):
    registry,_=fixture(tmp_path)
    approvals=[];ctx=ToolContext(tmp_path,lambda p:approvals.append(p) or True,True)
    result=registry.execute(ToolCall('x','ext_echo','{"text":"中文 ; $(false)"}'),ctx)
    assert result.ok and result.data['result']=={'echo':'中文 ; $(false)'}
    assert len(approvals)==1
    invalid=registry.execute(ToolCall('y','ext_echo','{"text":1}'),ctx)
    assert not invalid.ok and len(approvals)==1
    ctx.mode='plan'
    assert not registry.execute(ToolCall('z','ext_echo','{"text":"hi"}'),ctx).ok


def test_invalid_output_is_unknown_and_retains_log(tmp_path):
    registry,_=fixture(tmp_path,'print("not json")')
    result=registry.execute(ToolCall('x','ext_echo','{"text":"hi"}'),ToolContext(tmp_path,lambda _:True,True))
    assert not result.ok and result.data['uncertain'] and result.data['log_id']


@pytest.mark.parametrize('change',['readonly','schema','duplicate','shell'])
def test_unsupported_configuration_rejected(tmp_path,change):
    _,path=fixture(tmp_path);data=json.loads(path.read_text());tool=data['tools'][0]
    if change=='readonly':tool['effect']='read'
    if change=='schema':tool['parameters']['properties']['text']['pattern']='.*'
    if change=='duplicate':data['tools'].append(tool.copy())
    if change=='shell':tool['shell']=True
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):load_extensions(path)


@pytest.mark.parametrize('literal',['NaN','1e400'])
def test_nonfinite_output_is_rejected(tmp_path,literal):
    registry,_=fixture(tmp_path,'print('+repr('{"ok":true,"data":{"value":'+literal+'},"error":null}')+')')
    result=registry.execute(ToolCall('x','ext_echo','{"text":"hi"}'),ToolContext(tmp_path,lambda _:True,True))
    assert not result.ok and result.data['uncertain']


@pytest.mark.parametrize('timeout',[False,True])
def test_process_failure_after_side_effect_is_unknown(tmp_path,timeout):
    program='from pathlib import Path\nPath("effect.txt").write_text("changed")\n'
    program+='import time; time.sleep(3)\n' if timeout else 'raise SystemExit(2)\n'
    _,path=fixture(tmp_path,program)
    data=json.loads(path.read_text());data['tools'][0]['timeout']=.2 if timeout else 5
    path.write_text(json.dumps(data))
    registry=build_default_registry()
    for tool in load_extensions(path):registry.register(tool)
    result=registry.execute(ToolCall('x','ext_echo','{"text":"hi"}'),ToolContext(tmp_path,lambda _:True,True))
    assert (tmp_path/'effect.txt').read_text()=='changed'
    assert not result.ok and result.data.get('uncertain') and result.data['log_id']


def test_empty_object_schema_supported(tmp_path):
    _,path=fixture(tmp_path)
    data=json.loads(path.read_text());data['tools'][0]['parameters']={'type':'object'}
    path.write_text(json.dumps(data))
    tool=load_extensions(path)[0]
    from fast_agent.tools import validate
    validate({},tool.parameters)
    with pytest.raises(ValueError):validate({'unexpected':1},tool.parameters)


def test_cli_loads_extensions_and_checks_end_to_end(tmp_path,monkeypatch):
    from fast_agent.models import ModelReply
    from fast_agent.cli import main
    _,extpath=fixture(tmp_path)
    check_command=[sys.executable,'-c','print("check passed")']
    checks=tmp_path/'checks.json';checks.write_text(json.dumps({'tests':{'argv':check_command}}))
    allowed=tmp_path/'allowed.json'
    allowed.write_text(json.dumps({'commands':[[sys.executable,str(tmp_path/'extension.py'),json.dumps({'text':'hi'})],check_command]}))
    class Model:
        def complete(self,messages,tools):
            names={t['function']['name'] for t in tools}
            assert {'ext_echo','run_checks'}<=names
            results=[json.loads(m.content) for m in messages if m.role=='tool']
            assert all(r['ok'] for r in results)
            if not results:return ModelReply(tool_calls=[ToolCall('e','ext_echo','{"text":"hi"}')])
            if len(results)==1:return ModelReply(tool_calls=[ToolCall('c','run_checks','{"names":["tests"]}')])
            return ModelReply('已验证')
    monkeypatch.setattr('fast_agent.cli.MockModel',Model)
    assert main(['--mock','--no-stream','--workspace',str(tmp_path),'--extensions',str(extpath),
                 '--checks',str(checks),'--permissions','restricted','--allow-commands',str(allowed),'--prompt','验证'])==0
    from fast_agent.quality_checks import CheckRuns
    assert CheckRuns(tmp_path).list()[0]['status']=='passed'
