import json
import sys
import pytest
from fast_agent.mcp_tools import MCPManager
from fast_agent.models import ToolCall
from fast_agent.tools import ToolContext, build_default_registry

SERVER = '''import sys,json,time
from pathlib import Path
for line in sys.stdin:
 q=json.loads(line); method=q['method']
 if 'id' not in q: continue
 if method=='initialize': result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'test','version':'1'}}
 elif method=='tools/list': result={'tools':[{'name':'echo','description':'回传参数','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}]}
 else:
  Path('called').write_text('yes')
  text=q['params']['arguments']['text']
  if text=='timeout': time.sleep(3)
  if text=='crash': sys.exit(1)
  if text=='huge': print('x'*70000,flush=True); continue
  result={'content':[{'type':'text','text':text}],'isError':text=='error'}
 print(json.dumps({'jsonrpc':'2.0','id':q['id'],'result':result}),flush=True)
'''

def setup(tmp_path,approve=lambda _:True,program=SERVER):
 script=tmp_path/'server.py';script.write_text(program)
 path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':[{'name':'local','argv':[sys.executable,str(script)],'timeout':.5}]}))
 ctx=ToolContext(tmp_path,approve,True)
 manager=MCPManager(path,ctx)
 registry=build_default_registry()
 for tool in manager.start():registry.register(tool)
 return manager,registry,ctx

def call(registry,ctx,text):
 return registry.execute(ToolCall('1','mcp_local_echo',json.dumps({'text':text})),ctx)

def test_roundtrip_authorization_and_cleanup(tmp_path):
 prompts=[]
 manager,registry,ctx=setup(tmp_path,lambda p:prompts.append(p) or True)
 proc=manager.clients[0].process
 try:
  result=call(registry,ctx,'中文 ; $(false)')
  assert result.ok and result.data['content'][0]['text']=='中文 ; $(false)'
  assert len(prompts)==2
  ctx.mode='plan'
  assert not call(registry,ctx,'no').ok
  assert not any(s['function']['name'].startswith('mcp_') for s in registry.schemas(True))
 finally:manager.close()
 assert proc.poll() is not None

def test_start_denied_does_not_spawn(tmp_path):
 with pytest.raises(ValueError,match='授权'):setup(tmp_path,lambda _:False)
 assert not (tmp_path/'called').exists()

def test_call_denied_no_side_effect(tmp_path):
 manager,registry,ctx=setup(tmp_path)
 try:
  ctx.confirm=lambda _:False
  assert not call(registry,ctx,'no').ok
  assert not (tmp_path/'called').exists()
 finally:manager.close()

@pytest.mark.parametrize('text',['timeout','crash','huge'])
def test_transport_failure_uncertain_and_no_restart(tmp_path,text):
 manager,registry,ctx=setup(tmp_path)
 try:
  result=call(registry,ctx,text)
  assert not result.ok and result.data['uncertain']
  assert manager.clients[0].process.poll() is not None
  assert not call(registry,ctx,'again').ok
 finally:manager.close()

def test_tool_error_is_failure(tmp_path):
 manager,registry,ctx=setup(tmp_path)
 try:assert not call(registry,ctx,'error').ok
 finally:manager.close()

@pytest.mark.parametrize('replacement',["'9999-01-01'", "None", "[]", "{}"])
def test_bad_negotiation_rejected(tmp_path,replacement):
 with pytest.raises(ValueError):setup(tmp_path,program=SERVER.replace("'2025-11-25'",replacement))

def test_unsupported_schema_rejected(tmp_path):
 with pytest.raises(ValueError):setup(tmp_path,program=SERVER.replace("'type':'string'","'type':'string','pattern':'x'"))

def test_paginated_discovery(tmp_path):
 program=SERVER.replace("elif method=='tools/list': result=", "elif method=='tools/list':\n  result=")
 program=program.replace(" else:\n  Path", "  if not q.get('params',{}).get('cursor'): result['nextCursor']='next'\n  else: result['tools'][0]['name']='second'\n else:\n  Path")
 manager,registry,ctx=setup(tmp_path,program=program)
 try:assert {'mcp_local_echo','mcp_local_second'} <= registry.tools.keys()
 finally:manager.close()

@pytest.mark.parametrize('mutation',[
 lambda s:s.replace("'id':q['id'],'result':result", "'id':999,'result':result"),
 lambda s:s.replace("'content':[{'type':'text','text':text}]", "'content':[{'type':'image','data':'AA==','mimeType':'image/png'}]"),
 lambda s:s.replace("'text':text", "'text':float('nan')"),
])
def test_invalid_responses_fail_closed(tmp_path,mutation):
 try:manager,registry,ctx=setup(tmp_path,program=mutation(SERVER))
 except ValueError:return
 try:
  result=call(registry,ctx,'hi')
  assert not result.ok and result.data['uncertain']
 finally:manager.close()

def test_model_secret_not_in_server_environment(tmp_path,monkeypatch):
 monkeypatch.setenv('FAST_AGENT_API_KEY','test-secret')
 monkeypatch.setenv('UNRELATED_SECRET','test-secret')
 program=SERVER.replace('import sys,json,time','import sys,json,time,os').replace("text=q['params']['arguments']['text']", "text=str('FAST_AGENT_API_KEY' in os.environ or 'UNRELATED_SECRET' in os.environ)")
 manager,registry,ctx=setup(tmp_path,program=program)
 try:assert call(registry,ctx,'hi').data['content'][0]['text']=='False'
 finally:manager.close()

def test_cli_registers_mcp_and_closes(tmp_path,monkeypatch):
 from fast_agent.cli import main
 from fast_agent.models import ModelReply
 script=tmp_path/'server.py';script.write_text(SERVER)
 path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':[{'name':'local','argv':[sys.executable,str(script)]}]}))
 class Model:
  def complete(self,messages,tools):
   assert any(t['function']['name']=='mcp_local_echo' for t in tools)
   results=[json.loads(m.content) for m in messages if m.role=='tool']
   if not results:return ModelReply(tool_calls=[ToolCall('x','mcp_local_echo','{"text":"hello"}')])
   assert results[-1]['ok'] and results[-1]['data']['content'][0]['text']=='hello'
   return ModelReply('完成')
 entries=iter(['测试','/exit'])
 monkeypatch.setattr('fast_agent.cli.MockModel',Model)
 monkeypatch.setattr('fast_agent.cli.confirm',lambda _:True)
 monkeypatch.setattr('sys.stdin.isatty',lambda:True)
 monkeypatch.setattr('builtins.input',lambda _:next(entries))
 assert main(['--mock','--no-stream','--workspace',str(tmp_path),'--mcp',str(path)])==0
 assert (tmp_path/'called').exists()

def test_schema_title_annotation_from_sdk(tmp_path):
 manager,registry,ctx=setup(tmp_path,program=SERVER.replace("'type':'string'","'type':'string','title':'Text'"))
 try:assert call(registry,ctx,'hello').ok
 finally:manager.close()

def test_reverse_request_rejected_without_execution(tmp_path):
 program=SERVER.replace("print(json.dumps({'jsonrpc'", "if method=='tools/call':\n  print(json.dumps({'jsonrpc':'2.0','id':'reverse','method':'sampling/createMessage','params':{}}),flush=True)\n  rejection=json.loads(sys.stdin.readline())\n  assert rejection['error']['code']==-32601\n print(json.dumps({'jsonrpc'")
 manager,registry,ctx=setup(tmp_path,program=program)
 try:assert call(registry,ctx,'hello').ok
 finally:manager.close()

def test_cli_plan_defers_start_until_explicit_connect(tmp_path,monkeypatch):
 from fast_agent.cli import main
 from fast_agent.models import ModelReply
 script=tmp_path/'server.py';script.write_text(SERVER)
 path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':[{'name':'local','argv':[sys.executable,str(script)]}]}))
 prompts=[]
 entries=iter(['/execute','/mcp-connect','/exit'])
 monkeypatch.setattr('fast_agent.cli.confirm',lambda p:prompts.append(p) or True)
 monkeypatch.setattr('sys.stdin.isatty',lambda:True)
 monkeypatch.setattr('builtins.input',lambda _:next(entries))
 assert main(['--mock','--plan','--workspace',str(tmp_path),'--mcp',str(path)])==0
 assert len(prompts)==1 and prompts[0].startswith('启动 MCP 服务')
 assert not (tmp_path/'called').exists()

def test_official_sdk_interoperability(tmp_path):
 pytest.importorskip('mcp.server.fastmcp')
 from pathlib import Path
 script=Path(__file__).resolve().parents[1]/'examples/mcp_inspector_server.py'
 (tmp_path/'math_utils.py').write_text('def add(a, b):\n    return a - b\n')
 path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':[{'name':'repo','argv':[sys.executable,str(script)],'timeout':10}]}))
 ctx=ToolContext(tmp_path,lambda _:True,True)
 manager=MCPManager(path,ctx)
 registry=build_default_registry()
 try:
  for tool in manager.start():registry.register(tool)
  result=registry.execute(ToolCall('sdk','mcp_repo_inspect_source','{}'),ctx)
  assert result.ok and json.loads(result.data['content'][0]['text'])['contains_subtraction'] is True
 finally:manager.close()

def test_persisted_blocker_prevents_server_start(tmp_path,monkeypatch):
 monkeypatch.setattr('fast_agent.tasks.TaskStore.blockers',lambda _:['unknown-task'])
 with pytest.raises(ValueError,match='核实'):setup(tmp_path)

def test_cli_restore_closes_connection_and_reauthorizes(tmp_path,monkeypatch):
 from fast_agent.cli import main
 from fast_agent.mcp_tools import MCPManager
 script=tmp_path/'server.py';script.write_text(SERVER)
 path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':[{'name':'local','argv':[sys.executable,str(script)]}]}))
 prompts=[]; instances=[]
 class Tracking(MCPManager):
  def __init__(self,*a):super().__init__(*a);instances.append(self)
 entries=iter(['/save before','/load before','/execute','/mcp-connect','/exit'])
 monkeypatch.setattr('fast_agent.mcp_tools.MCPManager',Tracking)
 monkeypatch.setattr('fast_agent.cli.confirm',lambda p:prompts.append(p) or True)
 monkeypatch.setattr('sys.stdin.isatty',lambda:True)
 monkeypatch.setattr('builtins.input',lambda _:next(entries))
 assert main(['--mock','--workspace',str(tmp_path),'--mcp',str(path)])==0
 assert sum(p.startswith('启动 MCP 服务') for p in prompts)==2
 assert all(c.process.poll() is not None for m in instances for c in m.clients)

def test_cli_reconnects_partial_failure(tmp_path,monkeypatch):
 from fast_agent.cli import main
 from fast_agent.mcp_tools import MCPManager
 script=tmp_path/'server.py';script.write_text(SERVER)
 path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':[{'name':n,'argv':[sys.executable,str(script)]} for n in ['one','two']]}))
 instances=[];prompts=[];count=0
 class Tracking(MCPManager):
  def __init__(self,*a):super().__init__(*a);instances.append(self)
 def input_fn(_):
  nonlocal count
  count+=1
  if count==1:
   instances[-1].clients[0].close()
   return '/mcp-connect'
  return '/exit'
 monkeypatch.setattr('fast_agent.mcp_tools.MCPManager',Tracking)
 monkeypatch.setattr('fast_agent.cli.confirm',lambda p:prompts.append(p) or True)
 monkeypatch.setattr('sys.stdin.isatty',lambda:True)
 monkeypatch.setattr('builtins.input',input_fn)
 assert main(['--mock','--workspace',str(tmp_path),'--mcp',str(path)])==0
 assert sum(p.startswith('启动 MCP 服务') for p in prompts)==4
 assert all(c.process.poll() is not None for m in instances for c in m.clients)

def test_mcp_unknown_result_pauses_agent_checkpoint(tmp_path):
 from fast_agent.agent import Agent
 from fast_agent.models import ModelReply
 from fast_agent.tasks import TaskStore
 from fast_agent.sessions import validate_history
 manager,registry,ctx=setup(tmp_path)
 class Model:
  calls=0
  def complete(self,messages,tools):
   self.calls+=1
   assert self.calls==1  # 失败后不能再次向模型发请求来自动重试
   return ModelReply(tool_calls=[ToolCall('lost','mcp_local_echo','{"text":"crash"}')])
 model=Model()
 agent=Agent(model,registry,ctx,checkpoints=True)
 try:
  result=agent.run('执行检查')
  assert not result.completed and model.calls==1
  assert TaskStore(tmp_path).load(agent.last_task_id)['status']=='interrupted'
  assert TaskStore(tmp_path).blockers()
  validate_history(agent.history)
 finally:manager.close()


def test_refresh_invalidates_old_handles_and_disconnect_status(tmp_path):
 manager,registry,ctx=setup(tmp_path)
 try:
  assert manager.status()==[{'server':'local','transport':'stdio','connected':True}]
  refreshed=manager.refresh()
  assert not call(registry,ctx,'old').ok
  for tool in refreshed:registry.tools[tool.name]=tool
  assert call(registry,ctx,'new').ok
  manager.close()
  assert manager.status()[0]['connected'] is False
  assert not call(registry,ctx,'closed').ok
 finally:manager.close()


def test_denied_refresh_keeps_valid_tools(tmp_path):
 manager,registry,ctx=setup(tmp_path)
 try:
  ctx.confirm=lambda _:False
  with pytest.raises(ValueError,match='授权'):manager.refresh()
  ctx.confirm=lambda _:True
  assert call(registry,ctx,'still valid').ok
 finally:manager.close()


def test_refresh_failure_closes_and_invalidates(tmp_path,monkeypatch):
 manager,registry,ctx=setup(tmp_path)
 client=manager.clients[0]
 monkeypatch.setattr(client,'request',lambda *a: {'tools':[{'name':'bad','inputSchema':None}]})
 with pytest.raises(ValueError):manager.refresh()
 assert not client.is_alive
 assert not call(registry,ctx,'must not execute').ok
 assert not (tmp_path/'called').exists()


def test_refresh_changed_tool_set_and_schema(tmp_path,monkeypatch):
 manager,registry,ctx=setup(tmp_path)
 client=manager.clients[0];original=client.request
 def changed(method,params):
  if method=='tools/list':return {'tools':[{'name':'new_echo','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}]}
  return original(method,params)
 monkeypatch.setattr(client,'request',changed)
 try:
  tools=manager.refresh()
  assert [t.name for t in tools]==['mcp_local_new_echo']
  assert not call(registry,ctx,'old').ok
  registry.register(tools[0])
  assert registry.execute(ToolCall('2','mcp_local_new_echo','{"text":"new"}'),ctx).ok
 finally:manager.close()
