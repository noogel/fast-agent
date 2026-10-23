import httpx2
import pytest
from fast_agent.oauth_discovery import Discovery,DiscoveryError,challenge_metadata,issuer_metadata_urls

RESOURCE='https://mcp.example/service'
ISSUER='https://auth.example/tenant'
META={'issuer':ISSUER,'authorization_endpoint':'https://auth.example/authorize','token_endpoint':'https://auth.example/token',
      'code_challenge_methods_supported':['S256'],'response_types_supported':['code'],'token_endpoint_auth_methods_supported':['none']}


def test_well_known_fallback_and_no_cookies_or_credentials():
    seen=[];approved=[]
    def serve(request):
        seen.append(str(request.url))
        assert not request.headers.get('authorization') and not request.headers.get('cookie')
        url=str(request.url)
        if url=='https://mcp.example/.well-known/oauth-protected-resource':
            return httpx2.Response(200,json={'resource':RESOURCE,'authorization_servers':[ISSUER]},headers={'set-cookie':'secret=value'})
        if url==issuer_metadata_urls(ISSUER)[2]:return httpx2.Response(200,json=META)
        return httpx2.Response(404)
    discovery=Discovery(lambda url:approved.append(url) or True,transport=httpx2.MockTransport(serve))
    try:result=discovery.discover(RESOURCE)
    finally:discovery.close()
    assert result['issuer']==ISSUER
    assert seen==['https://mcp.example/.well-known/oauth-protected-resource/service','https://mcp.example/.well-known/oauth-protected-resource',*issuer_metadata_urls(ISSUER)]
    assert len(approved)==1


def test_challenge_and_cross_origin_denied_before_network():
    assert challenge_metadata('Bearer resource_metadata="https://mcp.example/meta", scope="read"')=='https://mcp.example/meta'
    seen=[]
    discovery=Discovery(lambda _:False,transport=httpx2.MockTransport(lambda req:seen.append(req)))
    try:
        with pytest.raises(DiscoveryError,match='批准'):
            discovery.discover(RESOURCE,challenge='Bearer resource_metadata="https://other.example/meta"')
    finally:discovery.close()
    assert not seen


@pytest.mark.parametrize('reply',[httpx2.Response(302,headers={'location':'https://other.example'}),httpx2.Response(200,json={'resource':'wrong'}),httpx2.Response(200,content=b'x'*65537,headers={'content-type':'application/json'})])
def test_redirect_mismatch_and_oversize_fail_closed(reply):
    discovery=Discovery(lambda _:True,transport=httpx2.MockTransport(lambda _:reply))
    try:
        with pytest.raises(ValueError):discovery.discover(RESOURCE)
    finally:discovery.close()
