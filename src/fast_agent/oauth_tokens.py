"""OAuth 令牌请求；调用方负责端点批准和私有仓储，不自动重放。"""
from dataclasses import dataclass,field
import json
import math
import time
import httpx2
from .oauth_flow import secure_endpoint


class TokenError(ValueError):pass


@dataclass(repr=False)
class Tokens:
    access_token:str=field(repr=False)
    refresh_token:str|None=field(repr=False)
    expires_at:float
    scope:str
    resource:str
    issuer:str
    client_id:str

    def status(self):
        return {'expires_at':self.expires_at,'expired':time.time()>=self.expires_at,
                'refresh_available':bool(self.refresh_token),'scope':self.scope,'resource':self.resource}


def secret(value):
    return isinstance(value,str) and 1<=len(value)<=16384 and all(33<=ord(c)<=126 for c in value)


def parse_tokens(value,*,resource,issuer,client_id,scope,previous_refresh=None):
    if not isinstance(value,dict) or not isinstance(value.get('token_type'),str) or value['token_type'].lower()!='bearer' or not secret(value.get('access_token')):
        raise TokenError('令牌响应格式无效')
    lifetime=value.get('expires_in')
    if type(lifetime) not in (int,float) or not math.isfinite(lifetime) or not 0<lifetime<=31536000:
        raise TokenError('令牌有效期缺失或无效')
    refresh=value.get('refresh_token',previous_refresh)
    if refresh is not None and not secret(refresh):raise TokenError('刷新令牌格式无效')
    granted=value.get('scope',scope)
    if not isinstance(granted,str) or len(granted)>2000 or any(ord(c)<32 or ord(c)>126 for c in granted):
        raise TokenError('令牌 scope 无效')
    if not set(granted.split()).issubset(set(scope.split())):raise TokenError('服务返回未经请求的权限')
    return Tokens(value['access_token'],refresh,time.time()+lifetime,granted,resource,issuer,client_id)


class TokenClient:
    def __init__(self,*,transport=None,tls_context=True):
        self.http=httpx2.Client(timeout=15,follow_redirects=False,trust_env=True,transport=transport,verify=tls_context)

    def close(self):self.http.close()

    def post(self,endpoint,form,*,revocation=False):
        secure_endpoint(endpoint)
        try:
            self.http.cookies.clear()
            with self.http.stream('POST',endpoint,data=form,headers={'Accept':'application/json'}) as response:
                if response.status_code!=200:raise TokenError('令牌服务拒绝请求；不自动重试，请重新授权或核实状态')
                if revocation:return None
                if response.headers.get('content-type','').split(';')[0].strip()!='application/json':raise TokenError('令牌响应不是 JSON')
                data=bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data)>65536:raise TokenError('令牌响应超限')
                return json.loads(data)
        except TokenError:raise
        except Exception:raise TokenError('令牌请求结果未知；不自动重试，请重新授权或核实状态') from None
        finally:self.http.cookies.clear()

    def exchange(self,pending,callback,issuer):
        form=pending.accept(callback)
        value=self.post(pending.token_endpoint,form)
        return parse_tokens(value,resource=pending.resource,issuer=issuer,client_id=pending.client_id,scope=pending.scope)

    def refresh(self,tokens,endpoint):
        if not tokens.refresh_token:raise TokenError('没有刷新令牌，请重新授权')
        value=self.post(endpoint,{'grant_type':'refresh_token','refresh_token':tokens.refresh_token,
                                'client_id':tokens.client_id,'resource':tokens.resource})
        return parse_tokens(value,resource=tokens.resource,issuer=tokens.issuer,client_id=tokens.client_id,
                            scope=tokens.scope,previous_refresh=tokens.refresh_token)

    def revoke(self,tokens,endpoint):
        return self.post(endpoint,{'token':tokens.refresh_token or tokens.access_token,
                          'token_type_hint':'refresh_token' if tokens.refresh_token else 'access_token',
                          'client_id':tokens.client_id},revocation=True)
