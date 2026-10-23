"""工作区外的私有令牌仓储；目录0700、文件0600，不进入运行记录。"""
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
from .oauth_tokens import Tokens,secret
from .oauth_flow import secure_endpoint


class CredentialStore:
    def __init__(self,directory):
        self.directory=Path(directory).absolute()

    def prepare(self):
        # 不穿越符号链接；不改变已有宽松目录的权限以掩盖配置问题。
        for part in [*reversed(self.directory.parents),self.directory]:
            if part.is_symlink():raise ValueError('凭据目录不能经过符号链接')
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        info=self.directory.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)&0o077:
            raise ValueError('凭据目录必须仅当前用户可访问')

    def key(self,resource,issuer,client_id):
        secure_endpoint(resource);secure_endpoint(issuer)
        if not isinstance(client_id,str) or not 1<=len(client_id)<=1024:raise ValueError('客户端ID无效')
        return hashlib.sha256(json.dumps([resource,issuer,client_id]).encode()).hexdigest()

    def path(self,resource,issuer,client_id):
        self.prepare()
        path=self.directory/(self.key(resource,issuer,client_id)+'.json')
        if path.is_symlink():raise ValueError('凭据文件不能是链接')
        return path

    def validate(self,tokens):
        self.key(tokens.resource,tokens.issuer,tokens.client_id)
        if not secret(tokens.access_token) or (tokens.refresh_token is not None and not secret(tokens.refresh_token)):
            raise ValueError('凭据内容无效')
        if type(tokens.expires_at) not in (int,float) or not math.isfinite(tokens.expires_at):raise ValueError('凭据过期时间无效')
        if not isinstance(tokens.scope,str) or len(tokens.scope)>2000 or any(ord(c)<32 or ord(c)>126 for c in tokens.scope):raise ValueError('凭据scope无效')

    def save(self,tokens):
        self.validate(tokens)
        path=self.path(tokens.resource,tokens.issuer,tokens.client_id)
        temp=None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory,delete=False) as stream:
                temp=Path(stream.name);os.fchmod(stream.fileno(),0o600)
                stream.write(json.dumps(asdict(tokens)).encode());stream.flush();os.fsync(stream.fileno())
            os.replace(temp,path)
        finally:
            if temp:temp.unlink(missing_ok=True)

    def load(self,resource,issuer,client_id):
        path=self.path(resource,issuer,client_id)
        try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        except FileNotFoundError:return None
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)&0o077:
                raise ValueError('凭据文件权限或类型无效')
            with os.fdopen(fd,'rb',closefd=False) as stream:data=stream.read(65537)
            if len(data)>65536:raise ValueError('凭据文件超限')
            try:tokens=Tokens(**json.loads(data))
            except Exception:raise ValueError('凭据文件损坏') from None
            self.validate(tokens)
            if (tokens.resource,tokens.issuer,tokens.client_id)!=(resource,issuer,client_id):raise ValueError('凭据绑定不匹配')
            return tokens
        finally:os.close(fd)

    def delete(self,resource,issuer,client_id):
        self.path(resource,issuer,client_id).unlink(missing_ok=True)

    @contextmanager
    def lock(self,resource,issuer,client_id):
        self.prepare()
        fd=os.open(self.directory/(self.key(resource,issuer,client_id)+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            info=os.fstat(fd)
            if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)&0o077 or not stat.S_ISREG(info.st_mode):raise ValueError('凭据锁权限无效')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('此凭据正在授权或刷新') from None
            yield
        finally:os.close(fd)
