"""有界的 MCP 2025 stdio 传输。同步客户端每次仅允许一个请求。"""
import json
import os
import selectors
import signal
import subprocess
import time

VERSIONS = {'2025-11-25', '2025-06-18', '2024-11-05'}
MAX_FRAME = 65536


def decode(raw):
    def reject(value):
        raise ValueError('非有限 JSON 数值')
    value = json.loads(raw, parse_constant=reject)
    json.dumps(value, allow_nan=False)
    return value


class StdioClient:
    def __init__(self, argv, env, root, timeout):
        self.timeout = timeout
        self.sequence = 0
        self.buffer = b''
        self.closed = False
        self.process = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        start_new_session=True, bufsize=0)
        self.selector = selectors.DefaultSelector()
        for stream in (self.process.stdout, self.process.stderr):
            os.set_blocking(stream.fileno(), False)
            self.selector.register(stream, selectors.EVENT_READ)
        os.set_blocking(self.process.stdin.fileno(), False)

    @property
    def is_alive(self):
        return not self.closed and self.process.poll() is None

    def close(self):
        if self.closed:
            return
        self.closed = True
        # 即便父进程已经退出，也清理仍持有管道的同组后代。
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            if self.process.poll() is None:
                self.process.kill()
        try:
            self.process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            if self.process.poll() is None:
                self.process.kill()
        self.process.wait(timeout=2)
        self.selector.close()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()

    def send(self, message, deadline=None):
        raw = (json.dumps(message, ensure_ascii=False, allow_nan=False)+'\n').encode()
        if len(raw) > MAX_FRAME:
            raise ValueError('MCP 请求超过 64 KiB')
        deadline = deadline if deadline is not None else time.monotonic()+self.timeout
        with selectors.DefaultSelector() as writable:
            writable.register(self.process.stdin, selectors.EVENT_WRITE)
            while raw:
                if time.monotonic() >= deadline:
                    raise ValueError('MCP 写入超时')
                if not writable.select(max(0, deadline-time.monotonic())):
                    raise ValueError('MCP 写入超时')
                count = os.write(self.process.stdin.fileno(), raw)
                raw = raw[count:]

    def request(self, method, params):
        if self.closed:
            raise ValueError('MCP 连接已关闭，需重新授权启动')
        self.sequence += 1
        request_id = self.sequence
        deadline = time.monotonic()+self.timeout
        total = 0
        messages = 0
        try:
            self.send({'jsonrpc':'2.0', 'id':request_id, 'method':method, 'params':params}, deadline)
            while True:
                while b'\n' in self.buffer:
                    line, self.buffer = self.buffer.split(b'\n', 1)
                    messages += 1
                    if len(line) > MAX_FRAME or messages > 100 or time.monotonic() > deadline:
                        raise ValueError('MCP 消息超限或超时')
                    message = decode(line)
                    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                        raise ValueError('MCP JSON-RPC 响应无效')
                    if 'method' in message:
                        if 'id' in message:
                            self.send({'jsonrpc':'2.0', 'id':message['id'], 'error':{'code':-32601, 'message':'Client capability not supported'}}, deadline)
                        # 通知是非可信数据，不执行服务端指令。
                        continue
                    if type(message.get('id')) is not int or message['id'] != request_id:
                        raise ValueError('MCP 响应 id 不匹配')
                    if ('result' in message) == ('error' in message):
                        raise ValueError('MCP 响应格式无效')
                    if 'error' in message:
                        raise ValueError('MCP 服务返回协议错误')
                    if not isinstance(message['result'], dict):
                        raise ValueError('MCP result 必须为对象')
                    return message['result']
                if len(self.buffer) > MAX_FRAME:
                    raise ValueError('MCP 帧超过 64 KiB')
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise ValueError('MCP 请求超时')
                ready = self.selector.select(remaining)
                if not ready:
                    raise ValueError('MCP 请求超时')
                for key, _ in ready:
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        self.selector.unregister(key.fileobj)
                        if key.fileobj is self.process.stdout:
                            raise ValueError('MCP 服务断开')
                        continue
                    total += len(chunk)
                    if total > 1048576:
                        raise ValueError('MCP 响应流超过 1 MiB')
                    if key.fileobj is self.process.stdout:
                        self.buffer += chunk
        except BaseException:
            self.close()
            raise

    def initialize(self):
        result = self.request('initialize', {'protocolVersion':'2025-11-25', 'capabilities':{},
            'clientInfo':{'name':'fast-agent', 'version':'0.1.0'}})
        if (not isinstance(result.get('protocolVersion'), str) or result['protocolVersion'] not in VERSIONS
                or not isinstance(result.get('capabilities'), dict)
                or not isinstance(result['capabilities'].get('tools'), dict)):
            raise ValueError('MCP 协议版本或 tools 能力不支持')
        self.send({'jsonrpc':'2.0', 'method':'notifications/initialized'})
