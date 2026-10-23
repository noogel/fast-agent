"""MCP OAuth 配置与本地入口，配置文件不允许携带令牌。"""
from pathlib import Path
from .oauth_flow import secure_endpoint
from .oauth_session import OAuthSession


def validate_oauth(spec):
    value=spec['oauth']
    if 'bearer_env' in spec or 'url' not in spec:raise ValueError('OAuth 仅用于 HTTP 且不能与 bearer_env 混用')
    secure_endpoint(spec['url'])
    if not isinstance(value,dict) or set(value)-{'issuer','client_id','scope','port','directory'}:raise ValueError('OAuth 配置字段无效')
    secure_endpoint(value.get('issuer'))
    client=value.get('client_id')
    if not isinstance(client,str) or not 1<=len(client)<=1024 or any(ord(c)<33 for c in client):raise ValueError('OAuth client_id 无效')
    scope=value.get('scope','')
    if not isinstance(scope,str) or len(scope)>2000 or any(ord(c)<32 or ord(c)>126 for c in scope):raise ValueError('OAuth scope 无效')
    port=value.get('port',0)
    if type(port) is not int or not 0<=port<=65535:raise ValueError('OAuth 回调端口无效')
    directory=value.get('directory')
    if directory is not None and (not isinstance(directory,str) or not Path(directory).is_absolute()):raise ValueError('OAuth directory 需要绝对路径')


def make_session(spec,context):
    from .mcp_tls import tls_context
    value=spec['oauth']
    directory=value.get('directory',str(Path.home()/'.local/share/fast-agent/oauth'))
    return OAuthSession(context.root,directory,spec['url'],value['client_id'],
        lambda url:context.authorize('mcp_oauth',{'server':spec['name'],'url':url},'允许 OAuth 操作/访问 '+url+'？'),
        issuer=value['issuer'],scope=value.get('scope',''),port=value.get('port',0),tls_context=tls_context(spec) or True)


def action(manager,name,operation,show_url=print):
    specs=[s for s in manager.specs if s['name']==name and 'oauth' in s]
    if len(specs)!=1:raise ValueError('没有匹配的 OAuth 服务')
    session=make_session(specs[0],manager.context)
    try:
        if operation=='login':return session.login(show_url)
        if operation=='status':
            tokens=session.store.load(session.resource,session.issuer,session.client_id)
            return tokens.status() if tokens else {'authorized':False}
        if operation in {'logout','revoke'}:
            return session.logout(revoke=operation=='revoke')
        raise ValueError('OAuth 操作无效')
    finally:session.close()
