"""授权控制层：工作区外仓储、明确批准、刷新前失效和本地登出。"""
from pathlib import Path
import time
from .oauth_callback import Callback
from .oauth_discovery import Discovery
from .oauth_store import CredentialStore
from .oauth_tokens import TokenClient,parse_tokens


class OAuthSession:
    def __init__(self,workspace,directory,resource,client_id,approve,*,issuer=None,scope='',port=0,transport=None,tls_context=True):
        workspace=Path(workspace).resolve();directory=Path(directory).absolute()
        if directory.resolve().is_relative_to(workspace):raise ValueError('OAuth 凭据目录必须位于工作区外')
        self.store=CredentialStore(directory)
        self.resource,self.client_id,self.issuer=resource,client_id,issuer
        self.scope,self.port,self.approve=scope,port,approve
        self.discovery=Discovery(approve,transport=transport,tls_context=tls_context)
        self.client=TokenClient(transport=transport,tls_context=tls_context)
        self.metadata=None

    def discover(self):
        result=self.discovery.discover(self.resource,issuer=self.issuer)
        self.issuer=result['issuer'];self.metadata=result['metadata']

    def identity(self):
        if self.metadata is None:self.discover()
        return self.resource,self.issuer,self.client_id

    def login(self,show_url):
        identity=self.identity()
        if not self.approve(self.metadata['authorization_endpoint']):raise ValueError('未批准开始授权')
        with self.store.lock(*identity),Callback(self.port) as callback:
            url=callback.start(self.resource,self.metadata,self.issuer,self.client_id,self.scope)
            show_url(url)
            form=callback.wait()
            value=self.client.post(callback.pending.token_endpoint,form)
            tokens=parse_tokens(value,resource=self.resource,issuer=self.issuer,client_id=self.client_id,scope=self.scope)
            self.store.save(tokens)
            return tokens.status()

    def access_token(self):
        identity=self.identity()
        with self.store.lock(*identity):
            tokens=self.store.load(*identity)
            if tokens is None:raise ValueError('尚未授权，请先登录')
            if tokens.expires_at>time.time()+30:return tokens.access_token
            if not self.approve(self.metadata['token_endpoint']):raise ValueError('未批准刷新凭据')
            # 先使旧刷新凭据不可再使用；网络或落盘失败需要重新登录。
            self.store.delete(*identity)
            updated=self.client.refresh(tokens,self.metadata['token_endpoint'])
            self.store.save(updated)
            return updated.access_token

    def logout(self,*,revoke=False):
        identity=(self.resource,self.issuer,self.client_id) if self.issuer else self.identity()
        if not self.approve(self.resource):raise ValueError('未批准登出')
        with self.store.lock(*identity):
            tokens=self.store.load(*identity)
            self.store.delete(*identity)
            if revoke and tokens:
                if self.metadata is None:self.discover()
                endpoint=self.metadata.get('revocation_endpoint')
                if not endpoint:raise ValueError('本地凭据已移除；服务未声明撤销端点')
                self.discovery.allow(endpoint,self.resource)
                self.client.revoke(tokens,endpoint)
            return {'local_deleted':True,'remote_revoked':bool(revoke and tokens)}

    def close(self):
        self.discovery.close();self.client.close()
