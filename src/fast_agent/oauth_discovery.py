"""有界、无凭据 OAuth 元数据发现；跨来源访问先获显式许可。"""
import json
import re
from urllib.parse import urlsplit
import httpx2
from .oauth_flow import secure_endpoint,validate_metadata


class DiscoveryError(ValueError):pass


def origin(url):
    parts=urlsplit(secure_endpoint(url))
    return parts.scheme,parts.hostname,parts.port or 443


def resource_metadata_urls(resource):
    parts=urlsplit(secure_endpoint(resource));base=f'{parts.scheme}://{parts.netloc}'
    paths=[base+'/.well-known/oauth-protected-resource'+parts.path.rstrip('/')]
    root=base+'/.well-known/oauth-protected-resource'
    if root not in paths:paths.append(root)
    return paths


def issuer_metadata_urls(issuer):
    parts=urlsplit(secure_endpoint(issuer));base=f'{parts.scheme}://{parts.netloc}';path=parts.path.rstrip('/')
    urls=[base+'/.well-known/oauth-authorization-server'+path,base+'/.well-known/openid-configuration'+path]
    if path:urls.append(issuer.rstrip('/')+'/.well-known/openid-configuration')
    return urls


def challenge_metadata(header):
    if not header:return None
    if not isinstance(header,str) or len(header)>8192:raise DiscoveryError('认证 challenge 超限')
    # 定位 Bearer 段，拒绝重复 resource_metadata，避免不同挑战混淆。
    match=re.search(r'(?:^|,)\s*Bearer\s+',header,re.I)
    if not match:return None
    segment=header[match.end():]
    boundary=re.search(r',\s*[A-Za-z][A-Za-z0-9_-]*\s+(?![=])',segment)
    if boundary:segment=segment[:boundary.start()]
    values=re.findall(r'(?:^|,)\s*resource_metadata\s*=\s*"([^"\\]*)"\s*(?=,|$)',segment,re.I)
    if len(values)>1:raise DiscoveryError('资源元数据挑战重复')
    if re.search(r'\bresource_metadata\s*=',segment,re.I) and not values:raise DiscoveryError('资源元数据挑战格式无效')
    return secure_endpoint(values[0]) if values else None


class Discovery:
    def __init__(self,approve,*,transport=None,tls_context=True):
        self.approve=approve
        self.http=httpx2.Client(timeout=10,follow_redirects=False,trust_env=True,transport=transport,verify=tls_context)
        self.approved=set()

    def close(self):self.http.close()

    def allow(self,url,resource):
        host=origin(url)
        if host!=origin(resource) and host not in self.approved:
            if not self.approve(url):raise DiscoveryError('未批准访问授权服务来源')
            self.approved.add(host)

    def fetch(self,url,resource):
        self.allow(url,resource)
        try:
            # 新建的专用客户端没有认证头；不保留服务设置的 cookie。
            self.http.cookies.clear()
            with self.http.stream('GET',url,headers={'Accept':'application/json'}) as response:
                if response.status_code==404:return None
                if response.status_code!=200:raise DiscoveryError('OAuth 元数据请求失败或重定向被拒绝')
                if response.headers.get('content-type','').split(';')[0].strip() not in {'application/json'}:
                    raise DiscoveryError('OAuth 元数据不是 JSON')
                data=bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data)>65536:raise DiscoveryError('OAuth 元数据超过 64 KiB')
                value=json.loads(data)
                if not isinstance(value,dict):raise DiscoveryError('OAuth 元数据必须是对象')
                return value
        except DiscoveryError:raise
        except Exception:raise DiscoveryError('OAuth 元数据读取失败') from None
        finally:self.http.cookies.clear()

    def discover(self,resource,*,challenge=None,issuer=None):
        secure_endpoint(resource)
        challenge_url=challenge_metadata(challenge)
        candidates=[challenge_url] if challenge_url else resource_metadata_urls(resource)
        protected=None
        for url in candidates:
            protected=self.fetch(url,resource)
            if protected is not None:break
        if protected is None or protected.get('resource')!=resource:raise DiscoveryError('资源元数据缺失或资源不匹配')
        issuers=protected.get('authorization_servers')
        if not isinstance(issuers,list) or not 1<=len(issuers)<=10 or any(not isinstance(i,str) for i in issuers):
            raise DiscoveryError('授权服务列表无效')
        for item in issuers:secure_endpoint(item)
        if issuer is None:
            if len(issuers)!=1:raise DiscoveryError('多个授权服务，请显式选择 issuer')
            issuer=issuers[0]
        if issuer not in issuers:raise DiscoveryError('选择的 issuer 不属于此资源')
        metadata=None
        for url in issuer_metadata_urls(issuer):
            metadata=self.fetch(url,resource)
            if metadata is not None:break
        checked=validate_metadata(metadata,issuer)
        for key in ('authorization_endpoint','token_endpoint'):
            self.allow(checked[key],resource)
        return {'resource':resource,'issuer':issuer,'metadata':metadata}
