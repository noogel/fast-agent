"""短生命周期回环回调，不记录 URL/授权码，不打开浏览器。"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import time
from .oauth_flow import begin


class Callback:
    def __init__(self,port=0):
        if type(port) is not int or not 0<=port<=65535:raise ValueError('回调端口无效')
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if owner.pending is None or self.headers.get_all('Host') != [f'127.0.0.1:{owner.port}'] or not self.path.startswith('/callback?') or len(self.path)>8192:
                    self.reply(400);return
                try:owner.form=owner.pending.accept(owner.redirect_uri+self.path[len('/callback'):])
                except ValueError:
                    if owner.pending.consumed:owner.failed=True
                    self.reply(400);return
                self.reply(200)
            def reply(self,status):
                body=('授权回调已接收，可返回终端。' if status==200 else '授权回调无效。').encode()
                self.send_response(status)
                self.send_header('Content-Type','text/plain; charset=utf-8')
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','no-store')
                self.send_header('Referrer-Policy','no-referrer')
                self.end_headers();self.wfile.write(body)
        self.pending=None;self.form=None;self.failed=False
        class Server(HTTPServer):
            def get_request(self):
                connection,address=super().get_request()
                connection.settimeout(1)
                return connection,address
            def handle_error(self,*args):pass
        self.server=Server(('127.0.0.1',port),Handler)
        self.server.timeout=0.25
        self.port=self.server.server_address[1]
        self.redirect_uri=f'http://127.0.0.1:{self.port}/callback'

    def start(self,resource,metadata,issuer,client_id,scope=''):
        if self.pending is not None:raise ValueError('回调事务已启动')
        self.pending,url=begin(resource,metadata,issuer,client_id,self.redirect_uri,scope)
        return url

    def wait(self,timeout=300):
        if self.pending is None:raise ValueError('回调事务尚未启动')
        if type(timeout) not in (int,float) or not 0<timeout<=300:raise ValueError('回调等待需为0～300秒')
        deadline=min(time.monotonic()+timeout,self.pending.expires)
        try:
            while time.monotonic()<deadline:
                self.server.handle_request()
                if self.form is not None:return self.form
                if self.failed:raise ValueError('用户取消或授权回调失败')
            raise ValueError('等待授权回调超时')
        finally:self.close()

    def close(self):self.server.server_close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
