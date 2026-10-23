"""MCP 2025 Streamable HTTP 子集：有界 POST JSON/SSE，不自动重放。"""
import asyncio
import ipaddress
import json
import re
from urllib.parse import urlsplit
import httpx2
from .mcp_transport import decode, MAX_FRAME

class MCPHTTPError(ValueError):
    """仅携带本地生成的安全提示，不包含服务响应或凭据。"""


HTTP_VERSIONS = {'2025-11-25', '2025-06-18'}


def loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_url(url, allow_insecure=False):
    if (not isinstance(url, str) or len(url)>2048 or type(allow_insecure) is not bool
            or any(ord(c)<=32 or ord(c)>=127 for c in url) or '\\' in url):
        raise MCPHTTPError('MCP URL 无效')
    try:
        parts = urlsplit(url)
        port = parts.port
        if (parts.scheme not in {'https','http'} or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment or port == 0):
            raise MCPHTTPError()
    except ValueError:
        raise MCPHTTPError('MCP URL 必须是无凭据、查询或片段的 HTTP(S) 地址') from None
    if parts.scheme == 'http' and not loopback(parts.hostname) and not allow_insecure:
        raise MCPHTTPError('远程 MCP 默认要求 HTTPS；明文地址须显式 allow_insecure_http=true')
    return parts


class SSEMessages:
    """按字节增量分行；UTF-8 可以跨网络分块，不能逐块直接 decode。"""
    def __init__(self):
        self.buffer = b''
        self.data = []
        self.size = 0
        self.events = 0
        self.skip_lf = False
        self.first_line = True

    def feed(self, chunk):
        self.buffer += chunk
        output = []
        while True:
            if self.skip_lf and self.buffer:
                if self.buffer.startswith(b'\n'):
                    self.buffer = self.buffer[1:]
                self.skip_lf = False
            positions = [p for p in (self.buffer.find(b'\n'), self.buffer.find(b'\r')) if p>=0]
            if not positions:
                break
            index = min(positions)
            self.skip_lf = self.buffer[index:index+1] == b'\r'
            separator = 1
            raw, self.buffer = self.buffer[:index], self.buffer[index+separator:]
            if len(raw)>MAX_FRAME:
                raise MCPHTTPError('MCP SSE 行超过 64 KiB')
            line = raw.decode('utf-8')
            if self.first_line:
                line = line.removeprefix('\ufeff')
                self.first_line = False
            if not line:
                self.events += 1
                if self.events>100:
                    raise MCPHTTPError('MCP SSE 事件超过 100 条')
                if self.data:
                    text = '\n'.join(self.data)
                    if text.strip():
                        output.append(text)
                self.data, self.size = [], 0
            elif line.startswith('data:') or line == 'data':
                value = line[5:] if ':' in line else ''
                if value.startswith(' '):
                    value = value[1:]
                self.size += len(value.encode('utf-8'))+1
                if self.size>MAX_FRAME:
                    raise MCPHTTPError('MCP SSE 消息超过 64 KiB')
                self.data.append(value)
            # event/id/retry/comment 不作为指令执行；本阶段不支持事件续传。
        if len(self.buffer)>MAX_FRAME:
            raise MCPHTTPError('MCP SSE 行超过 64 KiB')
        return output


class HttpClient:
    def __init__(self, url, timeout, token=None, allow_insecure=False, *, token_provider=None, tls_context=None):
        import ssl
        if tls_context is not None and (not isinstance(tls_context,ssl.SSLContext) or not tls_context.check_hostname or tls_context.verify_mode!=ssl.CERT_REQUIRED):
            raise MCPHTTPError("TLS 上下文必须校验证书及主机名")
        self.tls_context=tls_context if tls_context is not None else True
        parts = validate_url(url, allow_insecure)
        if token is not None and (not isinstance(token,str) or not re.fullmatch(r'[\x21-\x7e]{1,4096}',token)):
            raise MCPHTTPError('MCP Bearer 凭据无效或未配置')
        self.url = url
        self.timeout = timeout
        self._token = token
        self._token_provider = token_provider
        self._session = None
        self.version = None
        self.sequence = 0
        self.closed = False
        # 回环验证和本地服务不能绕到外部代理。
        self.trust_env = not loopback(parts.hostname)

    @property
    def is_alive(self):
        return not self.closed

    def headers(self, refresh=True):
        if refresh and self._token_provider is not None:
            try:token=self._token_provider()
            except Exception:raise MCPHTTPError("OAuth 凭据不可用，请检查授权状态或重新登录") from None
            if not isinstance(token,str) or not re.fullmatch(r"[\x21-\x7e]{1,16384}",token):raise MCPHTTPError("OAuth 令牌格式无效")
            self._token=token
        headers = {'Accept':'application/json, text/event-stream', 'Content-Type':'application/json', 'Accept-Encoding':'identity'}
        if self._token:
            headers['Authorization'] = 'Bearer '+self._token
        if self._session:
            headers['Mcp-Session-Id'] = self._session
        if self.version:
            headers['Mcp-Protocol-Version'] = self.version
        return headers

    async def exchange(self, http, message, expected_id=None, initializing=False):
        raw = json.dumps(message, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(raw)>MAX_FRAME:
            raise MCPHTTPError('MCP 请求超过 64 KiB')
        async with http.stream('POST', self.url, content=raw, headers=self.headers(refresh=False)) as response:
            if response.status_code in (401,403):
                raise MCPHTTPError(f'MCP HTTP {response.status_code}：认证或权限失败，请检查独立服务凭据')
            if response.status_code == 404:
                raise MCPHTTPError('MCP HTTP 404：端点或会话不可用，核实后显式重新连接')
            if expected_id is None:
                if response.status_code != 202:
                    raise MCPHTTPError('MCP 通知/回复未被接受（需要 HTTP 202）')
                return None
            if response.status_code != 200:
                raise MCPHTTPError(f'MCP HTTP {response.status_code}：请求失败，不跟随重定向或自动重试')
            session = response.headers.get('mcp-session-id')
            if initializing and session is not None:
                if not re.fullmatch(r'[\x21-\x7e]{1,256}',session):
                    raise MCPHTTPError('MCP 会话标识无效')
                self._session = session
            elif session is not None and session != self._session:
                raise MCPHTTPError('MCP 服务在响应中更换会话标识')
            if response.headers.get('content-encoding','identity').lower() != 'identity':
                raise MCPHTTPError('MCP 不支持压缩响应')
            kind = response.headers.get('content-type','').split(';')[0].strip().lower()
            if kind not in {'application/json','text/event-stream'}:
                raise MCPHTTPError('MCP 响应必须为 JSON 或 SSE')
            parser = SSEMessages()
            body = bytearray()
            total = 0
            async for chunk in response.aiter_raw():
                total += len(chunk)
                if total>1048576:
                    raise MCPHTTPError('MCP 响应超过 1 MiB')
                if kind == 'application/json':
                    body.extend(chunk)
                    if len(body)>MAX_FRAME:
                        raise MCPHTTPError('MCP JSON 消息超过 64 KiB')
                    continue
                for text in parser.feed(chunk):
                    result = await self.message(http, decode(text), expected_id)
                    if result is not None:
                        return result
            if kind == 'application/json':
                result = await self.message(http, decode(bytes(body).decode('utf-8')), expected_id)
                if result is not None:
                    return result
            raise MCPHTTPError('MCP 响应结束但未收到匹配结果；不自动重放')

    async def message(self, http, message, expected_id):
        if not isinstance(message,dict) or message.get('jsonrpc') != '2.0':
            raise MCPHTTPError('MCP JSON-RPC 响应无效')
        if 'method' in message:
            if not isinstance(message['method'],str):
                raise MCPHTTPError('MCP 反向请求方法无效')
            if 'id' in message:
                if type(message['id']) not in (int,str):
                    raise MCPHTTPError('MCP 反向请求 id 无效')
                await self.exchange(http, {'jsonrpc':'2.0','id':message['id'],
                    'error':{'code':-32601,'message':'Client capability not supported'}})
            return None
        if type(message.get('id')) is not int or message['id'] != expected_id:
            raise MCPHTTPError('MCP 响应 id 不匹配')
        if ('result' in message) == ('error' in message) or 'error' in message:
            raise MCPHTTPError('MCP 服务返回协议错误')
        if not isinstance(message['result'],dict):
            raise MCPHTTPError('MCP result 必须为对象')
        return message['result']

    async def perform(self, message, expected_id=None, initializing=False):
        # 总截止时间覆盖 DNS/连接/headers/body/SSE/反向请求，不被心跳重置。
        async with asyncio.timeout(self.timeout):
            async with httpx2.AsyncClient(timeout=self.timeout, follow_redirects=False,
                                          trust_env=self.trust_env,verify=self.tls_context) as http:
                return await self.exchange(http,message,expected_id,initializing)

    def request(self, method, params):
        if self.closed:
            raise MCPHTTPError('MCP HTTP 连接已关闭，需重新授权')
        self.sequence += 1
        try:
            # 授权交互/刷新在网络事件循环外进行；MCP截止时间只覆盖实际MCP请求。
            self.headers()
            return asyncio.run(self.perform({'jsonrpc':'2.0','id':self.sequence,'method':method,'params':params},
                                            self.sequence,method=='initialize'))
        except (httpx2.HTTPError, TimeoutError, ValueError, RecursionError) as exc:
            self.close()
            if isinstance(exc, MCPHTTPError):
                raise
            raise MCPHTTPError('MCP HTTP 连接、响应或超时异常；结果需核实') from None
        except BaseException:
            self.close()
            raise

    def initialize(self):
        try:
            result = self.request('initialize',{'protocolVersion':'2025-11-25','capabilities':{},
                'clientInfo':{'name':'fast-agent','version':'0.1.0'}})
            version = result.get('protocolVersion')
            if (not isinstance(version,str) or version not in HTTP_VERSIONS
                    or not isinstance(result.get('capabilities'),dict)
                    or not isinstance(result['capabilities'].get('tools'),dict)):
                raise MCPHTTPError('MCP HTTP 协议版本或 tools 能力不支持')
            self.version = version
            asyncio.run(self.perform({'jsonrpc':'2.0','method':'notifications/initialized'}))
        except (httpx2.HTTPError, TimeoutError, ValueError, RecursionError) as exc:
            self.close()
            if isinstance(exc, MCPHTTPError):
                raise
            raise MCPHTTPError('MCP HTTP 初始化失败，请检查服务连接和响应') from None
        except BaseException:
            self.close()
            raise

    async def terminate(self):
        async with asyncio.timeout(min(self.timeout,2)):
            async with httpx2.AsyncClient(timeout=min(self.timeout,2),follow_redirects=False,trust_env=self.trust_env,verify=self.tls_context) as http:
                async with http.stream('DELETE',self.url,headers=self.headers(refresh=False)):
                    pass  # 405 等失败不重试；本地连接始终关闭，远端可能依赖TTL回收。

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self._session:
                asyncio.run(self.terminate())
        except Exception:
            pass
        finally:
            self._session = None
            self._token = None
            self._token_provider = None
