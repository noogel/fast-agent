"""实际TLS服务器授权与MCP工具链；不代替外部用户授权。"""
import datetime,ipaddress,json,ssl,threading,time,hashlib,base64,os
import pytest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs,urlencode
from urllib.request import build_opener,ProxyHandler,HTTPSHandler
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fast_agent.oauth_session import OAuthSession
from fast_agent.mcp_http import HttpClient


@pytest.fixture
def connect_proxy():
    """仅转发到本机TLS夹具，不解密、不记录隧道负载。"""
    import socket, select
    seen=[]
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_CONNECT(self):
            host,port=self.path.rsplit(':',1)
            if host!='127.0.0.1':
                self.send_error(403); return
            seen.append(self.path)
            with socket.create_connection((host,int(port)),timeout=3) as upstream:
                self.send_response(200);self.end_headers()
                ends=[self.connection,upstream]
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    ready,_,_=select.select(ends,[],[],0.2)
                    for source in ready:
                        data=source.recv(65536)
                        if not data:return
                        (upstream if source is self.connection else self.connection).sendall(data)
    proxy=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
    thread=threading.Thread(target=proxy.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{proxy.server_port}',seen
    finally:proxy.shutdown();proxy.server_close();thread.join()


@pytest.mark.parametrize('via_proxy',[False,True])
def test_https_login_refresh_mcp_revoke(tmp_path,monkeypatch,connect_proxy,via_proxy):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    now=datetime.datetime.now(datetime.timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1))
          .not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
          .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
          .sign(key,hashes.SHA256()))
    certpath=tmp_path/'ca.pem';keypath=tmp_path/'key.pem'
    certpath.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keypath.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    import certifi
    trustpath=tmp_path/'trust.pem'
    trustpath.write_bytes(Path(certifi.where()).read_bytes()+certpath.read_bytes())
    monkeypatch.setenv('SSL_CERT_FILE',str(trustpath));monkeypatch.setenv('NO_PROXY','127.0.0.1,localhost');monkeypatch.setenv('no_proxy','127.0.0.1,localhost')
    if via_proxy:
        proxy_url,proxy_seen=connect_proxy
        for name in ('HTTPS_PROXY','https_proxy'):monkeypatch.setenv(name,proxy_url)
        for name in ('NO_PROXY','no_proxy'):monkeypatch.setenv(name,'')
    state={'access':'access-one','refresh':'refresh-one','revoked':False,'grants':[],'tools':0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,value,status=200):
            body=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if urlsplit(self.path).path=='/authorize':
                query=parse_qs(urlsplit(self.path).query)
                assert query['client_id']==['client'] and query['resource']==[base+'/mcp']
                assert query['response_type']==['code'] and query['code_challenge_method']==['S256']
                state['challenge']=query['code_challenge'][0]
                state['redirect_uri']=query['redirect_uri'][0]
                state['code_used']=False
                self.send_response(302)
                self.send_header('Location',state['redirect_uri']+'?'+urlencode({'code':'test-code','state':query['state'][0]}))
                self.send_header('Content-Length','0');self.end_headers();return
            if self.path=='/.well-known/oauth-protected-resource/mcp':return self.reply({'resource':base+'/mcp','authorization_servers':[base]})
            if self.path=='/.well-known/oauth-authorization-server':return self.reply({'issuer':base,'authorization_endpoint':base+'/authorize','token_endpoint':base+'/token','revocation_endpoint':base+'/revoke','code_challenge_methods_supported':['S256'],'response_types_supported':['code'],'token_endpoint_auth_methods_supported':['none']})
            self.reply({},404)
        def do_DELETE(self):self.reply({})
        def do_POST(self):
            data=self.rfile.read(int(self.headers['Content-Length']))
            if self.path in {'/token','/revoke'}:
                form=parse_qs(data.decode())
                if self.path=='/revoke':
                    assert form['token']==[state['refresh']];state['revoked']=True;return self.reply({})
                assert form['resource']==[base+'/mcp'] and form['client_id']==['client']
                grant=form['grant_type'][0];state['grants'].append(grant)
                if grant=='authorization_code':
                    assert form['code']==['test-code'] and not state['code_used']
                    assert form['redirect_uri']==[state['redirect_uri']]
                    challenge=base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'][0].encode()).digest()).rstrip(b'=').decode()
                    assert challenge==state['challenge']
                    state['code_used']=True
                else:
                    assert form['refresh_token']==[state['refresh']]
                    state['access']='access-two';state['refresh']='refresh-two'
                return self.reply({'access_token':state['access'],'refresh_token':state['refresh'],'expires_in':300,'token_type':'Bearer'})
            assert self.headers['Authorization']=='Bearer '+state['access']
            message=json.loads(data);method=message['method']
            if 'id' not in message:return self.reply({},202)
            if method=='initialize':result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'protected','version':'1'}}
            elif method=='tools/list':result={'tools':[{'name':'echo','description':'返回服务验收代号','inputSchema':{'type':'object','properties':{},'required':[],'additionalProperties':False}}]}
            else:state['tools']+=1;result={'content':[{'type':'text','text':'HTTPS-OAUTH-OK'}]}
            self.reply({'jsonrpc':'2.0','id':message['id'],'result':result})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);base=f'https://127.0.0.1:{server.server_port}'
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(certpath,keypath);server.socket=tls.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    workspace=tmp_path/'repo';workspace.mkdir()
    session=OAuthSession(workspace,tmp_path/'credentials',base+'/mcp','client',lambda _:True,issuer=base)
    client=None
    try:
        with ThreadPoolExecutor() as pool:
            visits=[]
            def show(url):
                def visit():
                    trust=ssl.create_default_context(cafile=str(certpath))
                    with build_opener(ProxyHandler({}),HTTPSHandler(context=trust)).open(url,timeout=3) as response:return response.status
                visits.append(pool.submit(visit))
            assert session.login(show)['refresh_available'];assert visits[0].result()==200
        trust=ssl.create_default_context(cafile=str(certpath))
        client=HttpClient(base+'/mcp',3,token_provider=session.access_token,tls_context=trust)
        client.initialize();client.request('tools/list',{})
        assert client.request('tools/call',{'name':'echo','arguments':{}})['content'][0]['text']=='HTTPS-OAUTH-OK'
        tokens=session.store.load(*session.identity());tokens.expires_at=time.time()-1;session.store.save(tokens)
        client.request('tools/call',{'name':'echo','arguments':{}})
        assert state['grants']==['authorization_code','refresh_token'] and state['tools']==2 and state['code_used']
        from fast_agent.mcp_tools import MCPManager
        from fast_agent.tools import ToolContext
        configpath=workspace/'mcp.json'
        configpath.write_text(json.dumps({'servers':[{'name':'protected','url':base+'/mcp','ca_file':str(certpath),
            'oauth':{'issuer':base,'client_id':'client','directory':str(tmp_path/'credentials')}}]}))
        # 管理器只能依赖显式ca_file，不能偶然依赖测试环境里的临时CA。
        monkeypatch.setenv('SSL_CERT_FILE',certifi.where())
        manager=MCPManager(configpath,ToolContext(workspace,lambda _:True,True))
        try:
            discovered=manager.start()
            assert len(discovered)==1
            assert discovered[0].handler({},manager.context).ok
            stale=discovered[0]
            refreshed=manager.refresh()
            with pytest.raises(ValueError,match='失效'):stale.handler({},manager.context)
            assert refreshed[0].handler({},manager.context).ok
        finally:manager.close()
        assert not manager.status()[0]['connected']
        # 仅显式设置开关才联网使用已有模型配置，普通pytest不读取凭据。
        if os.environ.get('FAST_AGENT_OAUTH_REAL')=='1' and not via_proxy:
            from dataclasses import replace
            from fast_agent.agent import Agent
            from fast_agent.config import Config
            from fast_agent.providers import OpenAIModel
            from fast_agent.tools import ToolContext,ToolRegistry
            model=OpenAIModel(replace(Config.from_env(os.environ),max_requests=6))
            events=[];registry=ToolRegistry()
            context=ToolContext(workspace,lambda _:True,True)
            real_manager=MCPManager(configpath,context)
            try:
                for tool in real_manager.start():registry.register(tool)
                agent=Agent(model,registry,context,max_steps=6,on_event=lambda event:events.append(event))
                tokens=session.store.load(*session.identity());tokens.expires_at=time.time()-1;session.store.save(tokens)
                result=agent.run('必须实际调用已提供的 MCP 工具，然后报告工具给出的验收代号。')
                calls=[e.data for e in events if e.kind=='tool_end']
                checks={'completed':result.completed,'actual_tool':bool(calls) and all(c['result']['ok'] for c in calls),
                    'code':'HTTPS-OAUTH-OK' in result.text,'refresh':state['grants'].count('refresh_token')==2,
                    'server_received':state['tools']>=3,'approved':bool(context.audit) and all(a['allowed'] for a in context.audit)}
                report={'verified':all(checks.values()),'checks':checks,'usage':model.usage.snapshot()}
                destination=Path(__file__).resolve().parents[1]/'.fast-agent/validation/oauth-manager-minimax.json'
                destination.write_text(json.dumps(report,ensure_ascii=False,indent=2))
                assert all(checks.values())
            finally:
                real_manager.close()
                model.client.close()
        client.close();assert session.logout(revoke=True)['remote_revoked'] and state['revoked']
        assert session.store.load(*session.identity()) is None
        if via_proxy:
            # Discovery、token客户端及后续manager分别创建连接；不是只设置变量。
            assert len(proxy_seen)>=3
            assert set(proxy_seen)=={f'127.0.0.1:{server.server_port}'}
    finally:
        if client:client.close()
        session.close();server.shutdown();server.server_close();thread.join()
