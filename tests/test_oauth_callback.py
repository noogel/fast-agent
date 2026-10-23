from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request,build_opener,ProxyHandler
urlopen=build_opener(ProxyHandler({})).open
from urllib.error import HTTPError
import pytest
from fast_agent.oauth_callback import Callback

META={'issuer':'https://auth.example','authorization_endpoint':'https://auth.example/authorize','token_endpoint':'https://auth.example/token','code_challenge_methods_supported':['S256'],'response_types_supported':['code'],'token_endpoint_auth_methods_supported':['none']}


def test_real_loopback_host_state_and_close(capsys):
    with Callback() as callback:
        callback.start('https://mcp.example/mcp',META,'https://auth.example','client','read')
        with ThreadPoolExecutor() as pool:
            future=pool.submit(callback.wait,5)
            base=callback.redirect_uri
            with pytest.raises(HTTPError):urlopen(base+'?state=wrong&code=secret',timeout=2)
            with pytest.raises(HTTPError):urlopen(Request(base+'?state='+callback.pending.state+'&code=secret',headers={'Host':'evil.example'}),timeout=2)
            with urlopen(base+'?state='+callback.pending.state+'&code=private-code',timeout=2) as response:assert response.status==200
            form=future.result(timeout=3)
        assert form['code']=='private-code' and form['resource']=='https://mcp.example/mcp'
        assert callback.server.socket.fileno()==-1
    output=capsys.readouterr()
    assert 'private-code' not in output.out+output.err


def test_timeout_closes_listener():
    with Callback() as callback:
        callback.start('https://mcp.example/mcp',META,'https://auth.example','client')
        with pytest.raises(ValueError,match='超时'):callback.wait(0.01)
        assert callback.server.socket.fileno()==-1
