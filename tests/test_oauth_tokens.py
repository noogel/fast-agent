from urllib.parse import parse_qs
import httpx2
import pytest
from fast_agent.oauth_tokens import TokenClient,TokenError,parse_tokens


def token(**extra):
    data={'access_token':'private-access','refresh_token':'private-refresh','token_type':'Bearer','expires_in':3600}
    data.update(extra)
    return parse_tokens(data,resource='https://mcp.example/mcp',issuer='https://auth.example',client_id='client',scope='read')


def test_refresh_rotation_binding_and_private_status():
    calls=[]
    def serve(request):
        calls.append(parse_qs(request.content.decode()))
        assert not request.headers.get('cookie') and not request.headers.get('authorization')
        return httpx2.Response(200,json={'access_token':'new-access','refresh_token':'new-refresh','expires_in':60,'token_type':'Bearer'})
    client=TokenClient(transport=httpx2.MockTransport(serve))
    try:updated=client.refresh(token(),'https://auth.example/token')
    finally:client.close()
    assert updated.refresh_token=='new-refresh' and calls[0]['resource']==['https://mcp.example/mcp']
    assert 'new-access' not in repr(updated) and 'new-refresh' not in str(updated.status())


@pytest.mark.parametrize('response',[httpx2.Response(302,headers={'location':'https://evil.example'}),httpx2.Response(400,text='private-refresh'),httpx2.Response(200,json={'access_token':'secret'})])
def test_failure_no_replay_or_leak(response):
    calls=[]
    def serve(request):calls.append(request);return response
    client=TokenClient(transport=httpx2.MockTransport(serve))
    try:
        with pytest.raises(TokenError) as error:client.refresh(token(),'https://auth.example/token')
        assert 'private-refresh' not in str(error.value) and len(calls)==1
    finally:client.close()


@pytest.mark.parametrize('extra',[{'expires_in':True},{'expires_in':float('inf')},{'scope':'read write'},{'access_token':'bad\nvalue'}])
def test_bad_token_response(extra):
    with pytest.raises(TokenError):token(**extra)
