import os,shutil
import sys,time,json
import pytest
from fast_agent.lsp_transport import LSPClient


def test_real_clangd_symbols_and_definition(tmp_path):
    executable=shutil.which('clangd')
    if not executable:pytest.skip('需要本地 clangd')
    path=tmp_path/'main.cpp';text='int answer() { return 42; }\nint main() { return answer(); }\n';path.write_text(text)
    client=LSPClient([executable,'--background-index=0'],dict(os.environ),tmp_path,10)
    try:
        capabilities=client.initialize();assert capabilities.get('documentSymbolProvider')
        client.send({'jsonrpc':'2.0','method':'textDocument/didOpen','params':{'textDocument':{'uri':path.as_uri(),'languageId':'cpp','version':1,'text':text}}})
        symbols=client.request('textDocument/documentSymbol',{'textDocument':{'uri':path.as_uri()}})
        assert {'answer','main'}<={item['name'] for item in symbols}
        definitions=client.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':{'line':1,'character':23}})
        assert definitions and definitions[0]['uri']==path.as_uri()
    finally:client.close()
    assert client.process.poll() is not None


def test_real_clangd_diagnostics_current_version(tmp_path):
    executable=shutil.which('clangd')
    if not executable:pytest.skip('需要本地 clangd')
    path=tmp_path/'broken.cpp';text='int main() { return missing_name; }\n';path.write_text(text)
    client=LSPClient([executable,'--background-index=0'],dict(os.environ),tmp_path,10)
    try:
        client.initialize()
        client.send({'jsonrpc':'2.0','method':'textDocument/didOpen','params':{
            'textDocument':{'uri':path.as_uri(),'languageId':'cpp','version':1,'text':text}}})
        result=client.wait_diagnostics(path.as_uri(),1)
        assert result['version']==1
        assert any(item.get('severity')==1 and 'missing_name' in item['message'] for item in result['items'])
    finally:client.close()
    assert client.process.poll() is not None


def test_expired_write_does_not_write(tmp_path):
    client=LSPClient([sys.executable,'-c','import time;time.sleep(10)'],dict(os.environ),tmp_path,.1)
    try:
        with pytest.raises(ValueError,match='写入超时'):
            client.send({'jsonrpc':'2.0','method':'test'},time.monotonic()-1)
    finally:client.close()


def test_diagnostics_timeout_closes_process(tmp_path):
    client=LSPClient([sys.executable,'-c','import time;time.sleep(10)'],dict(os.environ),tmp_path,.1)
    client.diagnostics['file:///old']={'version':1,'items':[]}
    with pytest.raises(ValueError,match='超时'):
        client.wait_diagnostics('file:///old',2)
    assert client.closed and client.process.poll() is not None


def test_graceful_shutdown_real_clangd(tmp_path):
    executable=shutil.which('clangd')
    if not executable:pytest.skip('需要 clangd')
    client=LSPClient([executable,'--background-index=0'],dict(os.environ),tmp_path,10)
    client.initialize()
    assert client.shutdown() is True
    assert client.closed and client.process.poll() is not None


def test_shutdown_unresponsive_server_is_bounded(tmp_path):
    client=LSPClient([sys.executable,'-c','import time;time.sleep(10)'],dict(os.environ),tmp_path,10)
    started=time.monotonic()
    assert client.shutdown() is False
    assert time.monotonic()-started<3
    assert client.closed and client.process.poll() is not None


def frame(value):
    body=json.dumps(value).encode()
    return f'Content-Length: {len(body)}\r\n\r\n'.encode()+body


@pytest.mark.parametrize('raw',[
    b'Content-Length: 1048577\r\n\r\n',
    b'Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}',
    frame({'jsonrpc':'2.0','id':999,'result':None}),
    frame({'jsonrpc':'2.0','method':'textDocument/publishDiagnostics','params':[]}),
])
def test_invalid_peer_closes(tmp_path,raw):
    script=f'import sys,time;sys.stdout.buffer.write({raw!r});sys.stdout.buffer.flush();time.sleep(2)'
    client=LSPClient([sys.executable,'-c',script],dict(os.environ),tmp_path,1)
    with pytest.raises(ValueError):client.request('test',{})
    assert client.closed and client.process.poll() is not None


def test_server_edit_request_is_rejected(tmp_path):
    source=tmp_path/'kept.txt';source.write_text('keep')
    request={'jsonrpc':'2.0','id':'edit','method':'workspace/applyEdit','params':{
        'edit':{'changes':{source.as_uri():[{'newText':'overwrite'}]}}}}
    output=frame(request)+frame({'jsonrpc':'2.0','id':1,'result':[]})
    script=f'import sys,time;sys.stdout.buffer.write({output!r});sys.stdout.buffer.flush();time.sleep(2)'
    client=LSPClient([sys.executable,'-c',script],dict(os.environ),tmp_path,1)
    sent=[]
    original=client.send
    def capture(message,deadline=None):
        sent.append(message);return original(message,deadline)
    client.send=capture
    try:
        assert client.request('textDocument/documentSymbol',{})==[]
        assert sent[-1]['id']=='edit' and sent[-1]['error']['code']==-32601
        assert source.read_text()=='keep'
    finally:client.close()
