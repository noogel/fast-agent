"""LSP Content-Length 帧；复用进程管道/进程组清理，不复用 MCP 协议。"""
import json,os,selectors,time,subprocess
from .mcp_transport import StdioClient,decode

LIMIT=1024*1024


class LSPClient(StdioClient):
    def __init__(self,argv,env,root,timeout=10):
        super().__init__(argv,env,root,timeout)
        self.root=root;self.diagnostics={};self.received_bytes=0

    def send(self,message,deadline=None):
        body=json.dumps(message,ensure_ascii=False,allow_nan=False).encode()
        if len(body)>LIMIT:raise ValueError('LSP 请求超限')
        raw=f'Content-Length: {len(body)}\r\n\r\n'.encode()+body
        deadline=deadline or time.monotonic()+self.timeout
        with selectors.DefaultSelector() as writable:
            writable.register(self.process.stdin,selectors.EVENT_WRITE)
            while raw:
                if time.monotonic()>=deadline:raise ValueError('LSP 写入超时')
                if not writable.select(max(0,deadline-time.monotonic())):raise ValueError('LSP 写入超时')
                raw=raw[os.write(self.process.stdin.fileno(),raw):]

    def receive(self,deadline):
        while True:
            if time.monotonic()>=deadline:raise ValueError('LSP 响应超时')
            boundary=self.buffer.find(b'\r\n\r\n')
            if boundary>=0:
                if boundary>8192:raise ValueError('LSP 头部超限')
                lengths=[]
                for line in self.buffer[:boundary].split(b'\r\n'):
                    key,sep,value=line.partition(b':')
                    if not sep:raise ValueError('LSP 帧头无效')
                    if key.lower()==b'content-length':lengths.append(value.strip())
                if len(lengths)!=1 or not lengths[0].isdigit():raise ValueError('LSP 长度无效')
                size=int(lengths[0])
                if size>LIMIT:raise ValueError('LSP 帧超限')
                end=boundary+4+size
                if len(self.buffer)>=end:
                    body=self.buffer[boundary+4:end];self.buffer=self.buffer[end:]
                    value=decode(body)
                    if not isinstance(value,dict) or value.get('jsonrpc')!='2.0':raise ValueError('LSP JSON-RPC 无效')
                    return value
            elif len(self.buffer)>8192:raise ValueError('LSP 帧头超限')
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ValueError('LSP 响应超时')
            ready=self.selector.select(remaining)
            if not ready:raise ValueError('LSP 响应超时')
            for key,_ in ready:
                chunk=os.read(key.fd,8192)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    if key.fileobj is self.process.stdout:raise ValueError('LSP 服务断开')
                    continue
                self.received_bytes+=len(chunk)
                if self.received_bytes>2*LIMIT:raise ValueError('LSP 输出流超限')
                if key.fileobj is self.process.stdout:self.buffer+=chunk

    def notice(self,message,deadline):
        if 'id' in message:
            self.send({'jsonrpc':'2.0','id':message['id'],'error':{'code':-32601,'message':'Client capability not supported'}},deadline)
        elif message.get('method')=='textDocument/publishDiagnostics':
            params=message.get('params',{})
            if not isinstance(params,dict):raise ValueError('LSP 诊断参数无效')
            uri=params.get('uri');items=params.get('diagnostics')
            if isinstance(uri,str) and isinstance(items,list):
                if uri not in self.diagnostics and len(self.diagnostics)>=32:raise ValueError('LSP 诊断文档数量超限')
                self.diagnostics[uri]={'version':params.get('version'),'items':items[:200],'truncated':len(items)>200}

    def request(self,method,params):
        if self.closed:raise ValueError('LSP 已关闭')
        self.received_bytes=len(self.buffer)
        self.sequence+=1;identifier=self.sequence;deadline=time.monotonic()+self.timeout
        try:
            self.send({'jsonrpc':'2.0','id':identifier,'method':method,'params':params},deadline)
            for _ in range(100):
                message=self.receive(deadline)
                if 'method' in message:self.notice(message,deadline);continue
                if type(message.get('id')) is not int or message['id']!=identifier or 'error' in message or 'result' not in message:
                    raise ValueError('LSP 响应不匹配或请求失败')
                return message['result']
            raise ValueError('LSP 消息数量超限')
        except BaseException:self.close();raise

    def wait_diagnostics(self,uri,version):
        """只返回指定版本；旧版本/无版本通知不能冒充当前文件诊断。"""
        if self.closed:raise ValueError('LSP 已关闭')
        deadline=time.monotonic()+self.timeout
        self.received_bytes=len(self.buffer)
        try:
            for _ in range(100):
                result=self.diagnostics.get(uri)
                if result is not None and type(result.get('version')) is int and result['version']==version:
                    return result
                message=self.receive(deadline)
                if 'method' not in message:raise ValueError('LSP 未预期响应')
                self.notice(message,deadline)
            raise ValueError('LSP 消息数量超限')
        except BaseException:self.close();raise

    def initialize(self):
        result=self.request('initialize',{'processId':os.getpid(),'rootUri':self.root.as_uri(),
            'capabilities':{'general':{'positionEncodings':['utf-16']},'textDocument':{'publishDiagnostics':{'versionSupport':True}}}})
        if not isinstance(result,dict) or not isinstance(result.get('capabilities'),dict):
            self.close();raise ValueError('LSP 初始化能力无效')
        if result['capabilities'].get('positionEncoding','utf-16')!='utf-16':
            self.close();raise ValueError('LSP 暂只支持 UTF-16 位置编码')
        self.send({'jsonrpc':'2.0','method':'initialized','params':{}})
        return result['capabilities']

    def shutdown(self):
        """尽力协议关闭；短截止时间后仍清理进程组，不无限等待服务退出。"""
        if self.closed:return False
        previous_timeout=self.timeout
        self.timeout=min(previous_timeout,.5)
        try:
            if self.request('shutdown',None) is not None:return False
            self.send({'jsonrpc':'2.0','method':'exit'})
            self.process.wait(timeout=.5)
            return self.process.returncode==0
        except (ValueError,OSError,subprocess.TimeoutExpired):
            return False
        finally:
            self.timeout=previous_timeout
            self.close()
