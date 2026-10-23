import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from fast_agent.mcp_http import HttpClient
from fast_agent.mcp_tools import MCPManager, load_servers
from fast_agent.tools import ToolContext, build_default_registry
from fast_agent.models import ToolCall


@contextmanager
def endpoint(mode='json',tls=None):
    records=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_DELETE(self):
            records.append(('DELETE',dict(self.headers),None))
            self.send_response(200);self.end_headers()
        def do_POST(self):
            q=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            records.append(('POST',dict(self.headers),q))
            method=q.get('method')
            if mode=='auth' and self.headers.get('Authorization')!='Bearer test-token':
                self.send_response(401);self.end_headers();self.wfile.write(b'test-token private-server-error');return
            if method=='initialize':
                result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'http-test','version':'1'}}
            elif 'id' not in q or method is None:
                if mode=='notification-slow':time.sleep(.6)
                self.send_response(202);self.end_headers();return
            elif method=='tools/list':
                result={'tools':[{'name':'echo','description':'Echo','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}]}
            else:
                if mode in ('401','403','404','redirect'):
                    self.send_response(302 if mode=='redirect' else int(mode));self.send_header('Location','/leak');self.end_headers();return
                if mode=='slow':time.sleep(.6)
                result={'content':[{'type':'text','text':q['params']['arguments']['text']}]}
            body=json.dumps({'jsonrpc':'2.0','id':q['id'],'result':result}).encode()
            if mode=='huge' and method=='tools/call':body=b'x'*1100000
            self.send_response(200)
            if method=='initialize':self.send_header('Mcp-Session-Id','test-session')
            self.send_header('Content-Type','text/event-stream' if mode in ('sse','heartbeat','reverse') else 'application/json')
            self.end_headers()
            try:
                if mode=='heartbeat' and method=='tools/call':
                    for _ in range(30):
                        self.wfile.write(b': heartbeat\n\n');self.wfile.flush();time.sleep(.04)
                    return
                if mode=='reverse':
                    reverse={'jsonrpc':'2.0','id':'r','method':'sampling/createMessage','params':{}}
                    if method=='tools/call':
                        self.wfile.write(b'data: '+json.dumps(reverse).encode()+b'\n\n');self.wfile.flush()
                    self.wfile.write(b'data: '+body+b'\n\n')
                elif mode in ('sse','heartbeat'):self.wfile.write(b': heartbeat\r\nid: event1\r\ndata: \r\n\r\nevent: message\r\ndata: '+body+b'\r\n\r\n')
                else:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    if tls is not None:
        server.socket=tls.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'{"https" if tls else "http"}://127.0.0.1:{server.server_port}/mcp',records
    finally:server.shutdown();server.server_close();thread.join()


@pytest.mark.parametrize('mode',['json','sse'])
def test_http_roundtrip_session_headers_and_close(mode):
    with endpoint(mode) as (url,records):
        client=HttpClient(url,2)
        try:
            client.initialize()
            assert client.request('tools/list',{})['tools'][0]['name']=='echo'
            assert client.request('tools/call',{'name':'echo','arguments':{'text':'中文'}})['content'][0]['text']=='中文'
        finally:client.close()
        assert records[-1][0]=='DELETE'
        for _,headers,q in records[1:]:
            assert headers.get('Mcp-Session-Id')=='test-session'
            assert headers.get('Mcp-Protocol-Version')=='2025-11-25'
        assert not client.is_alive


def test_manager_http_authorization_and_no_secret_in_audit(tmp_path,monkeypatch):
    monkeypatch.setenv('MCP_TEST_TOKEN','test-token')
    with endpoint('auth') as (url,records):
        path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':[{'name':'remote','url':url,'bearer_env':'MCP_TEST_TOKEN'}]}))
        prompts=[];ctx=ToolContext(tmp_path,lambda p:prompts.append(p) or True,True)
        manager=MCPManager(path,ctx);registry=build_default_registry()
        try:
            for tool in manager.start():registry.register(tool)
            result=registry.execute(ToolCall('h','mcp_remote_echo','{"text":"hello"}'),ctx)
            assert result.ok
            assert url in prompts[0] and url in prompts[1]
            assert 'test-token' not in json.dumps(prompts+ctx.audit)
        finally:manager.close()
        assert len([q for _,_,q in records if q and q.get('method')=='tools/call'])==1


@pytest.mark.parametrize('mode',['401','403','404','redirect','slow','huge'])
def test_http_failure_closes_without_replaying(mode):
    with endpoint(mode) as (url,records):
        client=HttpClient(url,.15 if mode=='slow' else 2)
        client.initialize()
        with pytest.raises(ValueError):client.request('tools/call',{'name':'echo','arguments':{'text':'x'}})
        assert not client.is_alive
        assert len([q for _,_,q in records if q and q.get('method')=='tools/call'])==1


def test_auth_error_does_not_echo_body():
    with endpoint('auth') as (url,records):
        with pytest.raises(ValueError) as error:HttpClient(url,2).initialize()
        assert '401' in str(error.value)
        assert 'test-token' not in str(error.value)


@pytest.mark.parametrize('url',['http://example.com/mcp','https://user:secret@example.com/mcp','https://example.com/mcp?token=secret','https://example.com/mcp#x','file:///tmp/server'])
def test_unsafe_urls_rejected(tmp_path,url):
    path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':[{'name':'x','url':url}]}))
    with pytest.raises(ValueError):load_servers(path)

def test_notification_timeout_is_safe_error_and_closes():
    with endpoint('notification-slow') as (url,records):
        client=HttpClient(url,.15)
        with pytest.raises(ValueError):client.initialize()
        assert not client.is_alive


def test_heartbeat_cannot_extend_total_deadline():
    with endpoint('heartbeat') as (url,records):
        client=HttpClient(url,.2)
        client.initialize()
        started=time.monotonic()
        with pytest.raises(ValueError):client.request('tools/call',{'name':'echo','arguments':{'text':'x'}})
        assert time.monotonic()-started<1
        assert not client.is_alive


def test_reverse_request_gets_method_not_found():
    with endpoint('reverse') as (url,records):
        client=HttpClient(url,2)
        try:
            client.initialize()
            assert client.request('tools/call',{'name':'echo','arguments':{'text':'x'}})['content'][0]['text']=='x'
        finally:client.close()
        assert any(q and q.get('error',{}).get('code')==-32601 for _,_,q in records)


def test_sse_utf8_and_crlf_split_across_chunks():
    from fast_agent.mcp_http import SSEMessages
    parser=SSEMessages();messages=[]
    for byte in 'id: a\r\nevent: message\r\ndata: {"text":\r\ndata: "中文"}\r\n\r\n'.encode():
        messages.extend(parser.feed(bytes([byte])))
    assert json.loads(messages[0])=={'text':'中文'}


def test_denied_connection_sends_no_requests(tmp_path):
    with endpoint() as (url,records):
        path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':[{'name':'remote','url':url}]}))
        manager=MCPManager(path,ToolContext(tmp_path,lambda _:False,True))
        with pytest.raises(ValueError,match='授权'):manager.start()
        assert records==[]


def test_missing_credential_sends_no_requests(tmp_path,monkeypatch):
    monkeypatch.delenv('MCP_MISSING_TOKEN',raising=False)
    with endpoint() as (url,records):
        path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':[{'name':'remote','url':url,'bearer_env':'MCP_MISSING_TOKEN'}]}))
        manager=MCPManager(path,ToolContext(tmp_path,lambda _:True,True))
        with pytest.raises(ValueError,match='凭据'):manager.start()
        assert records==[]

@pytest.mark.parametrize('wire',[
    b'data: {"ok":true}\r\r',
    b'\xef\xbb\xbfdata: {"ok":true}\n\n',
])
def test_sse_cr_only_and_initial_bom(wire):
    from fast_agent.mcp_http import SSEMessages
    parser=SSEMessages();messages=[]
    for byte in wire:messages.extend(parser.feed(bytes([byte])))
    assert [json.loads(m) for m in messages]==[{'ok':True}]

@pytest.mark.parametrize('json_response',[True,False])
def test_official_sdk_http_interoperability(tmp_path,json_response):
    pytest.importorskip('mcp.server.fastmcp')
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'examples/mcp_http_fixture.py'
    spec=importlib.util.spec_from_file_location('http_fixture',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'math_utils.py').write_text('def add(a,b):\n    return a - b\n')
    with module.http_fixture(tmp_path,json_response) as fixture:
        client=HttpClient(fixture['url'],5,fixture['token'])
        try:
            client.initialize()
            assert client.request('tools/list',{})['tools'][0]['name']=='inspect_source'
            result=client.request('tools/call',{'name':'inspect_source','arguments':{}})
            assert json.loads(result['content'][0]['text'])['contains_subtraction']
        finally:client.close()
        assert fixture['process'].poll() is None  # 客户端关闭不杀远端服务
        rows=[json.loads(line) for line in fixture['audit'].read_text().splitlines()]
        assert any(r['method']=='DELETE' and r['status']==200 for r in rows)
        assert any(r['issued_session'] for r in rows)
        expected='application/json' if json_response else 'text/event-stream'
        assert any(r['content_type'].startswith(expected) for r in rows)

def test_cli_http_load_and_reconnect(tmp_path,monkeypatch):
    from fast_agent.cli import main
    from fast_agent.models import ModelReply
    with endpoint() as (url,records):
        path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':[{'name':'remote','url':url}]}))
        class Model:
            count=0
            def complete(self,messages,tools):
                self.count+=1
                assert any(t['function']['name']=='mcp_remote_echo' for t in tools)
                if self.count%2:
                    return ModelReply(tool_calls=[ToolCall('x'+str(self.count),'mcp_remote_echo','{"text":"你好"}')])
                results=[json.loads(m.content) for m in messages if m.role=='tool']
                assert results[-1]['ok'] and results[-1]['data']['content'][0]['text']=='你好'
                return ModelReply('完成')
        entries=iter(['call','/save before','/load before','/execute','/mcp-connect','call','/exit'])
        monkeypatch.setattr('fast_agent.cli.MockModel',Model)
        monkeypatch.setattr('fast_agent.cli.confirm',lambda _:True)
        monkeypatch.setattr('sys.stdin.isatty',lambda:True)
        monkeypatch.setattr('builtins.input',lambda _:next(entries))
        assert main(['--mock','--no-stream','--workspace',str(tmp_path),'--mcp',str(path)])==0
        assert sum(q and q.get('method')=='initialize' for _,_,q in records if q)==2
        assert sum(q and q.get('method')=='tools/call' for _,_,q in records if q)==2
        assert sum(method=='DELETE' for method,_,_ in records)==2


def test_http_unknown_result_pauses_checkpoint(tmp_path):
    from fast_agent.agent import Agent
    from fast_agent.models import ModelReply
    from fast_agent.tasks import TaskStore
    with endpoint('404') as (url,records):
        path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':[{'name':'remote','url':url}]}))
        ctx=ToolContext(tmp_path,lambda _:True,True);manager=MCPManager(path,ctx)
        registry=build_default_registry()
        for tool in manager.start():registry.register(tool)
        class Model:
            count=0
            def complete(self,messages,tools):
                self.count+=1
                assert self.count==1
                return ModelReply(tool_calls=[ToolCall('lost','mcp_remote_echo','{"text":"hello"}')])
        agent=Agent(Model(),registry,ctx,checkpoints=True)
        try:
            assert not agent.run('检查').completed
            assert TaskStore(tmp_path).load(agent.last_task_id)['status']=='interrupted'
            assert TaskStore(tmp_path).blockers()
            assert len([q for _,_,q in records if q and q.get('method')=='initialize'])==1
        finally:manager.close()


@pytest.mark.parametrize('change',[
    {'argv':['python']}, {'bearer_env':'FAST_AGENT_API_KEY'}, {'bearer_env':'bad variable'},
    {'bearer_env':None}, {'allow_insecure_http':'yes'}, {'headers':{'Authorization':'secret'}},
])
def test_http_config_rejects_ambiguous_or_embedded_credentials(tmp_path,change):
    path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':[{'name':'remote','url':'https://example.com/mcp',**change}]}))
    with pytest.raises(ValueError):load_servers(path)


def test_sse_event_and_line_limits():
    from fast_agent.mcp_http import SSEMessages
    with pytest.raises(ValueError):SSEMessages().feed(b': heartbeat\n\n'*101)
    with pytest.raises(ValueError):SSEMessages().feed(b'data: '+b'x'*65536)

def test_untrusted_tls_certificate_is_not_bypassed(tmp_path):
    import shutil
    import ssl
    import subprocess
    openssl=shutil.which('openssl')
    if openssl is None:pytest.skip('需要 openssl 创建一次性测试证书')
    cert=tmp_path/'test-cert.pem';key=tmp_path/'test-key.pem'
    subprocess.run([openssl,'req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                    '-subj','/CN=localhost','-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
    with endpoint(tls=context) as (url,records):
        client=HttpClient(url,2)
        with pytest.raises(ValueError):client.initialize()
        assert not client.is_alive and records==[]


def test_http_refresh_preserves_session_invalidates_old_tool(tmp_path):
    with endpoint() as (url,records):
        path=tmp_path/'mcp.json';path.write_text(json.dumps({'servers':[{'name':'remote','url':url}]}))
        ctx=ToolContext(tmp_path,lambda _:True,True)
        manager=MCPManager(path,ctx);registry=build_default_registry()
        try:
            for tool in manager.start():registry.register(tool)
            old_session=manager.clients[0]._session
            tools=manager.refresh()
            assert old_session==manager.clients[0]._session
            assert not registry.execute(ToolCall('x','mcp_remote_echo','{"text":"old"}'),ctx).ok
            for tool in tools:registry.tools[tool.name]=tool
            assert registry.execute(ToolCall('y','mcp_remote_echo','{"text":"new"}'),ctx).ok
            assert sum(q.get('method')=='tools/list' for _,_,q in records if q)==2
        finally:manager.close()


def test_oauth_provider_runs_before_network_loop():
    import asyncio
    attempts=[]
    def provider():
        with pytest.raises(RuntimeError):asyncio.get_running_loop()
        attempts.append(1)
        return 'test-token'
    with endpoint('auth') as (url,records):
        client=HttpClient(url,2,token_provider=provider)
        client.initialize()
        client.request('tools/list',{})
        client.request('tools/call',{'name':'echo','arguments':{'text':'oauth-evidence'}})
        client.close()
    assert len(attempts)==3
    assert all(headers.get('Authorization')=='Bearer test-token' for _,headers,_ in records)
