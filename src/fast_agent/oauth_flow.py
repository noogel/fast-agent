"""OAuth 授权码事务：PKCE S256、资源绑定和一次性回调。无网络副作用。"""
import base64
from dataclasses import dataclass,field
import hashlib
import secrets
import time
from urllib.parse import urlencode,urlsplit,parse_qs
from .mcp_http import validate_url


def secure_endpoint(url):
    parts=validate_url(url)
    if parts.scheme!='https':raise ValueError('OAuth 端点必须使用 HTTPS')
    return url


def validate_metadata(metadata,issuer):
    secure_endpoint(issuer)
    if not isinstance(metadata,dict) or metadata.get('issuer')!=issuer:
        raise ValueError('OAuth issuer 不匹配')
    for key in ('authorization_endpoint','token_endpoint'):
        secure_endpoint(metadata.get(key))
    for key in ('code_challenge_methods_supported','response_types_supported','token_endpoint_auth_methods_supported'):
        if not isinstance(metadata.get(key),list) or any(not isinstance(v,str) for v in metadata[key]):
            raise ValueError('OAuth 能力元数据格式无效')
    if 'S256' not in metadata.get('code_challenge_methods_supported',[]):
        raise ValueError('授权服务未声明 PKCE S256 支持')
    if 'code' not in metadata.get('response_types_supported',[]):
        raise ValueError('授权服务不支持授权码响应')
    if 'none' not in metadata.get('token_endpoint_auth_methods_supported',[]):
        raise ValueError('当前仅支持预注册公共客户端')
    return {key:metadata[key] for key in ('issuer','authorization_endpoint','token_endpoint')}


@dataclass(repr=False)
class Authorization:
    resource:str
    client_id:str
    redirect_uri:str
    token_endpoint:str
    scope:str
    verifier:str=field(repr=False)
    state:str=field(repr=False)
    expires:float
    consumed:bool=False

    def accept(self,callback):
        if self.consumed or time.monotonic()>self.expires:raise ValueError('授权事务已使用或已过期')
        if not isinstance(callback,str) or len(callback)>8192 or any(ord(c)<=32 for c in callback):raise ValueError('授权回调无效')
        parts=urlsplit(callback);expected=urlsplit(self.redirect_uri)
        if parts.fragment or (parts.scheme,parts.netloc,parts.path)!=(expected.scheme,expected.netloc,expected.path):
            raise ValueError('授权回调地址不匹配')
        query=parse_qs(parts.query,keep_blank_values=True,strict_parsing=True,max_num_fields=10)
        state=query.get('state',[])
        if len(state)!=1 or not secrets.compare_digest(state[0].encode(),self.state.encode()):raise ValueError('授权 state 不匹配')
        self.consumed=True
        if 'error' in query:raise ValueError('授权服务拒绝或用户取消授权')
        codes=query.get('code',[])
        if len(codes)!=1 or not codes[0] or len(codes[0])>4096:raise ValueError('授权码无效')
        return {'grant_type':'authorization_code','client_id':self.client_id,'code':codes[0],
                'code_verifier':self.verifier,'redirect_uri':self.redirect_uri,'resource':self.resource}


def begin(resource,metadata,issuer,client_id,redirect_uri,scope=''):
    secure_endpoint(resource)
    endpoints=validate_metadata(metadata,issuer)
    redirect=validate_url(redirect_uri)
    if redirect.scheme!='http' or redirect.hostname!='127.0.0.1' or not redirect.port:
        raise ValueError('回调必须是带显式端口的 127.0.0.1 HTTP 地址')
    if not isinstance(client_id,str) or not 1<=len(client_id)<=1024 or any(ord(c)<33 for c in client_id):
        raise ValueError('客户端 ID 无效')
    if not isinstance(scope,str) or len(scope)>2000 or any(ord(c)<32 or ord(c)>126 for c in scope):
        raise ValueError('OAuth scope 无效')
    verifier=secrets.token_urlsafe(32);state=secrets.token_urlsafe(32)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    pending=Authorization(resource,client_id,redirect_uri,endpoints['token_endpoint'],scope,verifier,state,time.monotonic()+300)
    parameters={'response_type':'code','client_id':client_id,'redirect_uri':redirect_uri,
                'resource':resource,'state':state,'code_challenge':challenge,'code_challenge_method':'S256'}
    if scope:parameters['scope']=scope
    return pending,endpoints['authorization_endpoint']+'?'+urlencode(parameters)
