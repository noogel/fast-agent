"""显式 MCP 配置、工具发现和逐次授权；不信任服务自报的只读标注。"""
import copy
import json
import math
import os
import re
import ssl
from pathlib import Path
from .command_sandbox import command_launch
from .extensions import schema_checked
from .mcp_transport import StdioClient, decode
from .tools import Tool, ToolResult, ToolInputError


def load_servers(path):
    if path is None:
        return []
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('MCP 配置超过 64 KiB')
    value = decode(raw)
    if not isinstance(value, dict) or set(value) != {'servers'} or not isinstance(value['servers'], list) or not 1 <= len(value['servers']) <= 8:
        raise ValueError('MCP 配置需要 1～8 个 servers')
    names = set()
    for spec in value['servers']:
        if not isinstance(spec, dict) or set(spec)-{'name', 'argv', 'timeout', 'url', 'bearer_env', 'allow_insecure_http', 'oauth','ca_file'}:
            raise ValueError('MCP 服务配置字段无效')
        if 'oauth' in spec:
            from .mcp_oauth import validate_oauth
            validate_oauth(spec)
        name, argv, timeout = spec.get('name'), spec.get('argv'), spec.get('timeout', 30)
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,16}', name) or name in names:
            raise ValueError('MCP 服务名称无效或重复')
        if ('argv' in spec) == ('url' in spec):
            raise ValueError('MCP 服务必须二选一配置 argv 或 url')
        if 'url' in spec:
            from .mcp_http import validate_url
            validate_url(spec['url'],spec.get('allow_insecure_http',False))
            if 'ca_file' in spec:
                path=spec['ca_file']
                if not isinstance(path,str) or not Path(path).is_absolute() or not spec['url'].startswith('https://'):
                    raise ValueError('MCP ca_file 需要 HTTPS 服务和绝对路径')
            credential = spec.get('bearer_env')
            if 'bearer_env' in spec and (not isinstance(credential,str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}',credential) or credential=='FAST_AGENT_API_KEY'):
                raise ValueError('MCP bearer_env 必须是独立服务凭据的环境变量名')
        else:
            if set(spec)&{'bearer_env','allow_insecure_http','ca_file'}:
                raise ValueError('stdio 不接受 HTTP 配置字段')
            if not isinstance(argv, list) or not 1 <= len(argv) <= 64 or any(not isinstance(s, str) or not s or len(s)>4000 or '\0' in s for s in argv):
                raise ValueError('MCP argv 无效')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError('MCP timeout 必须在 0～60 秒之间')
        names.add(name)
    return value['servers']


def strip_titles(schema, depth=0):
    """title 是 SDK 生成的展示标注；不删除任何验证约束。"""
    if depth > 4 or not isinstance(schema, dict):
        raise ValueError('MCP schema 嵌套或类型无效')
    title = schema.pop('title', None)
    if title is not None and (not isinstance(title, str) or len(title) > 1000):
        raise ValueError('MCP schema title 无效')
    if isinstance(schema.get('properties'), dict):
        for value in schema['properties'].values():
            strip_titles(value, depth+1)
    if 'items' in schema:
        strip_titles(schema['items'], depth+1)


class MCPManager:
    def __init__(self, path, context):
        self.specs = load_servers(path)
        self.context = context
        self.clients = []
        self.oauth_sessions = []
        self.started = False
        self.generation = 0

    def close(self):
        self.generation += 1
        for client in self.clients:
            client.close()
        for session in self.oauth_sessions:
            session.close()

    def start(self):
        if self.started:
            raise ValueError('MCP 管理器不能重复启动')
        from .tasks import TaskStore
        if TaskStore(self.context.root).blockers():
            raise ValueError('存在未核实操作，不能启动 MCP 服务')
        self.started = True
        tools = []
        try:
            for spec in self.specs:
                # 服务启动本身就是执行代码，不复用命令白名单自动批准。
                remote = 'url' in spec
                target = {'server':spec['name'], **({'url':spec['url']} if remote else {'argv':spec['argv']})}
                if 'ca_file' in spec:target['ca_file']=spec['ca_file']
                prompt = ('连接远程 MCP（参数将发往该服务；远端不受本机沙箱限制）' if remote else '启动 MCP 服务 ')
                if not self.context.authorize('mcp_start', target, prompt+json.dumps(target, ensure_ascii=False)+'？'):
                    raise ValueError('MCP 启动未获授权')
                if remote:
                    from .mcp_http import HttpClient
                    token = os.environ.get(spec['bearer_env'], '') if 'bearer_env' in spec else None
                    provider=None
                    if 'oauth' in spec:
                        from .mcp_oauth import make_session
                        session=make_session(spec,self.context)
                        self.oauth_sessions.append(session)
                        provider=session.access_token
                    from .mcp_tls import tls_context
                    tls=tls_context(spec)
                    client = HttpClient(spec['url'],spec.get('timeout',30),token,spec.get('allow_insecure_http',False),token_provider=provider,tls_context=tls)
                else:
                    argv, env = command_launch(spec['argv'], self.context.root, self.context.sandbox)
                    env = {k:v for k,v in env.items() if k in {'PATH','LANG','LC_ALL','LC_CTYPE','TZ','TERM','HOME','TMPDIR','PYTHONDONTWRITEBYTECODE'}}
                    client = StdioClient(argv, env, self.context.root, spec.get('timeout',30))
                self.clients.append(client)
                client.initialize()
                tools.extend(self.discover(spec, client, len(tools)))
            return tools
        except BaseException:
            self.close()
            raise

    def status(self):
        return [{'server':spec['name'], 'transport':'http' if 'url' in spec else 'stdio',
                 'connected':i<len(self.clients) and self.clients[i].is_alive}
                for i,spec in enumerate(self.specs)]

    def refresh(self):
        from .tasks import TaskStore
        if self.context.read_only or self.context.blockers or TaskStore(self.context.root).blockers():
            raise ValueError('当前模式或未核实操作不允许刷新 MCP')
        if not self.clients or not all(row['connected'] for row in self.status()):
            raise ValueError('MCP 未全部连接，请先重连')
        if not self.context.authorize('mcp_refresh', {'servers':[s['name'] for s in self.specs]}, '重新向 MCP 服务发现工具？'):
            raise ValueError('MCP 刷新未获授权')
        self.generation += 1
        tools=[]
        try:
            for spec,client in zip(self.specs,self.clients):
                tools.extend(self.discover(spec,client,len(tools)))
            return tools
        except BaseException:
            self.close()
            raise

    def discover(self,spec,client,offset=0):
        tools=[]
        cursor = None
        seen = set()
        for _ in range(16):
            reply = client.request('tools/list', {} if cursor is None else {'cursor':cursor})
            entries = reply.get('tools')
            if not isinstance(entries, list):
                raise ValueError('MCP tools/list 无效')
            for entry in entries:
                if offset + len(tools) >= 64:
                    raise ValueError('MCP 工具总数超过 64')
                tool = self.make_tool(spec, client, entry)
                if any(t.name == tool.name for t in tools):
                    raise ValueError('MCP 工具名称重复')
                tools.append(tool)
            cursor = reply.get('nextCursor')
            if cursor is None:
                break
            if not isinstance(cursor,str) or len(cursor)>1000 or cursor in seen:
                raise ValueError('MCP 分页游标无效或重复')
            seen.add(cursor)
        else:
            raise ValueError('MCP 工具分页超过 16 页')
        return tools

    def make_tool(self, spec, client, entry):
        if not isinstance(entry, dict):
            raise ValueError('MCP 工具声明无效')
        name = entry.get('name')
        description = entry.get('description', name)
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', name):
            raise ValueError('MCP 工具名不支持')
        if not isinstance(description,str) or len(description)>1000:
            raise ValueError('MCP 工具说明超过 1000 字符')
        schema = copy.deepcopy(entry.get('inputSchema'))
        strip_titles(schema)
        schema_checked(schema)
        if schema['type'] != 'object':
            raise ValueError('MCP 参数必须为 object')
        public_name = 'mcp_'+spec['name']+'_'+name
        generation = self.generation
        def invoke(args, context):
            if generation != self.generation:
                raise ToolInputError('MCP 工具定义已失效，请使用刷新后的工具')
            try:
                encoded = json.dumps(args, ensure_ascii=False, allow_nan=False)
            except ValueError:
                raise ToolInputError('MCP 参数包含非有限数值') from None
            if len(encoded.encode()) > 32768:
                raise ToolInputError('MCP 参数超过 32 KiB')
            if client.closed:
                raise ToolInputError('MCP 服务已关闭；核实之前结果后重新启动')
            target = {'server':spec['name'], 'tool':name, 'arguments':args}
            if 'url' in spec:
                target['url'] = spec['url']
            if not context.authorize('mcp_call', target, '调用 MCP 工具 '+json.dumps(target, ensure_ascii=False)+'？'):
                return ToolResult(False, error='MCP 调用未获授权')
            try:
                reply = client.request('tools/call', {'name':name, 'arguments':args})
                content = reply.get('content')
                if (not isinstance(content,list) or type(reply.get('isError',False)) is not bool
                        or any(not isinstance(c,dict) or c.get('type')!='text' or not isinstance(c.get('text'),str) for c in content)
                        or ('structuredContent' in reply and not isinstance(reply['structuredContent'],dict))):
                    raise ValueError('当前仅支持 MCP 文本和对象结构化结果')
                data = {'server':spec['name'], 'tool':name, 'content':content}
                if 'structuredContent' in reply:
                    data['structuredContent'] = reply['structuredContent']
                failed = reply.get('isError', False)
                # 服务报告失败仍可能已经写入，不能当作可安全重试的纯校验错误。
                if failed:
                    data['uncertain'] = True
                return ToolResult(not failed, data, 'MCP 工具报告失败，请核实副作用' if failed else None)
            except (ValueError, OSError, RecursionError) as exc:
                from .mcp_http import MCPHTTPError
                hint = ('；'+str(exc)) if isinstance(exc,MCPHTTPError) else ''
                client.close()
                return ToolResult(False, {'uncertain':True, 'server':spec['name'], 'tool':name},
                                  'MCP 调用结果不确定，连接已关闭；请核实，不自动重试'+hint)
        return Tool(public_name, description+'（外部 MCP 服务，调用需要授权）', schema, invoke, 'write')
