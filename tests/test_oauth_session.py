import time
import httpx2
import pytest
from fast_agent.oauth_session import OAuthSession
from fast_agent.oauth_tokens import Tokens


def session(tmp_path,serve):
    workspace=tmp_path/'repo';workspace.mkdir()
    obj=OAuthSession(workspace,tmp_path/'credentials','https://mcp.example/mcp','client',lambda _:True,issuer='https://auth.example',transport=httpx2.MockTransport(serve))
    obj.metadata={'token_endpoint':'https://auth.example/token','revocation_endpoint':'https://auth.example/revoke'}
    return obj


def test_refresh_rotation_and_logout(tmp_path):
    calls=[]
    def serve(request):
        calls.append(str(request.url))
        return httpx2.Response(200,json={'access_token':'new-access','refresh_token':'rotated','expires_in':300,'token_type':'Bearer'})
    obj=session(tmp_path,serve)
    try:
        obj.store.save(Tokens('old','refresh',time.time()-1,'','https://mcp.example/mcp','https://auth.example','client'))
        assert obj.access_token()=='new-access'
        assert obj.store.load(*obj.identity()).refresh_token=='rotated'
        assert obj.access_token()=='new-access' and len(calls)==1
        assert obj.logout(revoke=True)['remote_revoked']
        assert obj.store.load(*obj.identity()) is None
    finally:obj.close()


def test_unknown_refresh_invalidates_old_credentials(tmp_path):
    def fail(request):raise httpx2.ReadTimeout('private-refresh')
    obj=session(tmp_path,fail)
    try:
        obj.store.save(Tokens('old','refresh',0,'','https://mcp.example/mcp','https://auth.example','client'))
        with pytest.raises(ValueError) as caught:obj.access_token()
        assert 'private-refresh' not in str(caught.value)
        assert obj.store.load(*obj.identity()) is None
    finally:obj.close()


def test_workspace_storage_rejected(tmp_path):
    with pytest.raises(ValueError,match='工作区外'):
        OAuthSession(tmp_path,tmp_path/'secrets','https://mcp.example/mcp','client',lambda _:True)


def test_login_callback_exchange_and_store(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import urlsplit,parse_qs,urlencode
    from urllib.request import build_opener,ProxyHandler
    requests=[]
    def serve(request):
        requests.append(parse_qs(request.content.decode()))
        return httpx2.Response(200,json={'access_token':'access','refresh_token':'refresh','expires_in':300,'token_type':'Bearer'})
    obj=session(tmp_path,serve)
    obj.metadata.update(issuer='https://auth.example',authorization_endpoint='https://auth.example/authorize',
        code_challenge_methods_supported=['S256'],response_types_supported=['code'],token_endpoint_auth_methods_supported=['none'])
    opener=build_opener(ProxyHandler({}))
    with ThreadPoolExecutor() as pool:
        futures=[]
        def show(url):
            query=parse_qs(urlsplit(url).query)
            callback=query['redirect_uri'][0]+'?'+urlencode({'code':'test-code','state':query['state'][0]})
            def visit():
                with opener.open(callback,timeout=3) as response:return response.status
            futures.append(pool.submit(visit))
        try:
            status=obj.login(show)
            assert futures[0].result()==200 and status['refresh_available']
            assert obj.store.load(*obj.identity()).access_token=='access'
            assert requests[0]['resource']==['https://mcp.example/mcp']
            assert requests[0]['code']==['test-code'] and 'code_verifier' in requests[0]
        finally:obj.close()
