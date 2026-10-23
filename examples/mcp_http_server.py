"""教学用官方 SDK HTTP 服务：仅监听回环，以独立测试 Bearer 认证。"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
from mcp.server.fastmcp import FastMCP
import uvicorn


def create_app(root, token, json_response=False, audit=None):
    server = FastMCP('Fast Agent HTTP inspector', json_response=json_response)

    @server.tool()
    def inspect_source() -> dict:
        """检查固定 math_utils.py 的 add 实现是否仍错误使用减法。"""
        source = (root/'math_utils.py').read_text(encoding='utf-8')
        return {'filename':'math_utils.py', 'contains_subtraction':'return a - b' in source}

    app = server.streamable_http_app()

    async def authenticated(scope, receive, send):
        if scope['type'] != 'http':
            return await app(scope,receive,send)
        headers = dict(scope['headers'])
        if not secrets.compare_digest(headers.get(b'authorization',b''),('Bearer '+token).encode()):
            await send({'type':'http.response.start','status':401,'headers':[(b'content-type',b'text/plain')]})
            await send({'type':'http.response.body','body':b'Unauthorized'})
            return
        async def record_send(message):
            if audit and message['type']=='http.response.start':
                response_headers = dict(message.get('headers',[]))
                # 不记录认证、session值、请求参数或代码；仅记录验证需要的状态。
                row = {'method':scope['method'], 'status':message['status'],
                       'has_session':b'mcp-session-id' in headers,
                       'issued_session':b'mcp-session-id' in response_headers,
                       'content_type':response_headers.get(b'content-type',b'').decode('ascii')}
                with audit.open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(row)+'\n')
            await send(message)
        await app(scope,receive,record_send)
    return authenticated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--json-response',action='store_true')
    parser.add_argument('--ready-file',type=Path)
    parser.add_argument('--audit-file',type=Path)
    args = parser.parse_args()
    token = os.environ.get('FAST_AGENT_MCP_DEMO_TOKEN','')
    if not token:
        raise SystemExit('请先设置独立的 FAST_AGENT_MCP_DEMO_TOKEN（不是模型密钥）')
    root = args.workspace.resolve()
    app = create_app(root,token,args.json_response,args.audit_file)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',args.port))
        port = listener.getsockname()[1]
        listener.listen(128)
        if args.ready_file:
            args.ready_file.write_text(json.dumps({'url':f'http://127.0.0.1:{port}/mcp'}))
        uvicorn.Server(uvicorn.Config(app,log_level='error')).run(sockets=[listener])


if __name__ == '__main__':
    main()
