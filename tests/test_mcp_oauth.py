import json
import pytest
from fast_agent.mcp_tools import load_servers
from fast_agent.mcp_http import HttpClient,MCPHTTPError


def test_config_oauth_and_exclusive_static_token(tmp_path):
    path=tmp_path/'mcp.json'
    spec={'name':'secured','url':'https://mcp.example/mcp','oauth':{'issuer':'https://auth.example','client_id':'client','port':9000}}
    path.write_text(json.dumps({'servers':[spec]}))
    assert load_servers(path)[0]['oauth']['port']==9000
    spec['bearer_env']='MCP_KEY';path.write_text(json.dumps({'servers':[spec]}))
    with pytest.raises(ValueError):load_servers(path)


def test_token_provider_rotates_and_no_refresh_on_close():
    calls=[]
    def provider():calls.append(1);return 'token-'+str(len(calls))
    client=HttpClient('https://mcp.example/mcp',1,token_provider=provider)
    assert client.headers()['Authorization']=='Bearer token-1'
    assert client.headers()['Authorization']=='Bearer token-2'
    assert client.headers(refresh=False)['Authorization']=='Bearer token-2'
    client.close()
    assert len(calls)==2 and client._token is None and client._token_provider is None


def test_token_failure_is_sanitized():
    def fail():raise ValueError('private-secret')
    client=HttpClient('https://mcp.example/mcp',1,token_provider=fail)
    with pytest.raises(MCPHTTPError) as error:client.headers()
    assert 'private-secret' not in str(error.value)
    client.close()
