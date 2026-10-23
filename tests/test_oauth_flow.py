import base64,hashlib
from urllib.parse import urlsplit,parse_qs,urlencode
import pytest
from fast_agent.oauth_flow import begin


def start(**overrides):
    args=dict(resource='https://mcp.example/mcp',issuer='https://auth.example',client_id='registered-client',
              redirect_uri='http://127.0.0.1:9000/callback',scope='read',metadata={
              'issuer':'https://auth.example','authorization_endpoint':'https://auth.example/authorize',
              'token_endpoint':'https://auth.example/token','code_challenge_methods_supported':['S256'],
              'response_types_supported':['code'],'token_endpoint_auth_methods_supported':['none']})
    args.update(overrides)
    return begin(**args)


def test_pkce_bound_resource_once_and_secret_repr():
    pending,url=start();query=parse_qs(urlsplit(url).query)
    assert query['code_challenge']==[base64.urlsafe_b64encode(hashlib.sha256(pending.verifier.encode()).digest()).rstrip(b'=').decode()]
    assert pending.verifier not in repr(pending) and pending.state not in repr(pending)
    callback=pending.redirect_uri+'?'+urlencode({'state':pending.state,'code':'private-code'})
    form=pending.accept(callback)
    assert form['resource']=='https://mcp.example/mcp' and form['code_verifier']==pending.verifier
    with pytest.raises(ValueError):pending.accept(callback)


def test_callback_mismatch_expiry_and_denial():
    pending,_=start()
    with pytest.raises(ValueError):pending.accept(pending.redirect_uri+'?state=wrong&code=secret')
    assert not pending.consumed
    with pytest.raises(ValueError):pending.accept('http://127.0.0.1:9001/callback?state='+pending.state+'&code=x')
    with pytest.raises(ValueError):pending.accept(pending.redirect_uri+'?state='+pending.state+'&error=access_denied')
    assert pending.consumed
    pending,_=start();pending.expires=0
    with pytest.raises(ValueError):pending.accept(pending.redirect_uri+'?state='+pending.state+'&code=x')


@pytest.mark.parametrize('overrides',[{'resource':'http://mcp.example/mcp'},{'redirect_uri':'http://localhost:9000/callback'}, {'issuer':'https://other.example'}, {'scope':'read\nadmin'}])
def test_invalid_configuration(overrides):
    with pytest.raises(ValueError):start(**overrides)
