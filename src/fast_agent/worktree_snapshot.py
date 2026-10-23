"""清理前工作区证据：有界、无链接、逐文件校验；不包含 Git 管理指针。"""
import base64
import hashlib
import os
from pathlib import PurePosixPath
import stat

MAX_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 4000


def capture(root):
    entries=[];total=0
    def walk(directory_fd,prefix=''):
        nonlocal total
        for name in sorted(os.listdir(directory_fd)):
            relative=prefix+name
            if relative=='.git':continue
            info=os.stat(name,dir_fd=directory_fd,follow_symlinks=False)
            if len(entries)>=MAX_ENTRIES:raise ValueError('工作区证据超过 4000 项')
            mode=stat.S_IMODE(info.st_mode)
            if mode>0o777:raise ValueError('证据包含特殊权限，拒绝清理')
            if stat.S_ISDIR(info.st_mode):
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory_fd)
                try:
                    opened=os.fstat(child)
                    if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino):raise ValueError('采集期间目录已替换')
                    entries.append({'path':relative,'kind':'directory','mode':mode})
                    walk(child,relative+'/')
                finally:os.close(child)
            elif stat.S_ISREG(info.st_mode):
                fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory_fd)
                with os.fdopen(fd,'rb') as stream:
                    opened=os.fstat(stream.fileno())
                    if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino):raise ValueError('采集期间文件已替换')
                    data=stream.read(MAX_BYTES-total+1)
                    after=os.fstat(stream.fileno())
                total+=len(data)
                if total>MAX_BYTES:raise ValueError('工作区证据超过 8 MiB，不允许清理')
                if (opened.st_size,opened.st_mtime_ns,opened.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                    raise ValueError('采集期间文件已改变')
                entries.append({'path':relative,'kind':'file','mode':mode,'size':len(data),
                                'sha256':hashlib.sha256(data).hexdigest(),
                                'data':base64.b64encode(data).decode('ascii')})
            else:raise ValueError('证据包含链接或特殊文件，拒绝清理')
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:walk(fd)
    finally:os.close(fd)
    return {'version':1,'bytes':total,'entries':entries}


def validate(snapshot):
    if not isinstance(snapshot,dict) or snapshot.get('version')!=1 or not isinstance(snapshot.get('entries'),list):
        raise ValueError('证据格式无效')
    entries=snapshot['entries']
    if len(entries)>MAX_ENTRIES:raise ValueError('证据条目超限')
    seen={};total=0;decoded=[]
    for entry in entries:
        if not isinstance(entry,dict):raise ValueError('证据条目无效')
        name=entry.get('path');mode=entry.get('mode');kind=entry.get('kind')
        if not isinstance(name,str) or not name or '\\' in name or '\x00' in name:raise ValueError('证据路径无效')
        parts=name.split('/')
        if any(p in {'','.','..','.git'} for p in parts) or PurePosixPath(name).is_absolute() or name in seen:
            raise ValueError('证据路径越界或重复')
        if len(parts)>1 and seen.get('/'.join(parts[:-1]))!='directory':raise ValueError('证据父目录无效')
        if type(mode) is not int or not 0<=mode<=0o777:raise ValueError('证据权限无效')
        data=None
        if kind=='file':
            encoded=entry.get('data')
            if not isinstance(encoded,str) or len(encoded)>MAX_BYTES*2:raise ValueError('证据数据超限')
            try:data=base64.b64decode(encoded,validate=True)
            except ValueError:raise ValueError('证据编码无效') from None
            total+=len(data)
            if total>MAX_BYTES or entry.get('size')!=len(data) or entry.get('sha256')!=hashlib.sha256(data).hexdigest():
                raise ValueError('证据完整性校验失败')
        elif kind!='directory':raise ValueError('证据类型无效')
        seen[name]=kind;decoded.append((name,kind,mode,data))
    if type(snapshot.get('bytes')) is not int or total!=snapshot['bytes']:raise ValueError('证据总大小不匹配')
    return decoded


def restore(snapshot,destination):
    """只恢复到不存在的目录；先校验所有内容，绝不覆盖现有路径。"""
    decoded=validate(snapshot)
    if destination.exists() or destination.is_symlink():raise ValueError('证据恢复目标已存在')
    if destination.parent.is_symlink():raise ValueError('恢复父目录不能是链接')
    destination.mkdir(mode=0o700)
    root_fd=os.open(destination,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    def parent_fd(name):
        fd=os.dup(root_fd)
        try:
            for part in name.split('/')[:-1]:
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=child
            return fd
        except BaseException:
            os.close(fd);raise
    try:
        for name,kind,mode,data in decoded:
            fd=parent_fd(name)
            try:
                leaf=name.split('/')[-1]
                if kind=='directory':os.mkdir(leaf,mode=0o700,dir_fd=fd)
                else:
                    out=os.open(leaf,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                    with os.fdopen(out,'wb') as stream:
                        stream.write(data);stream.flush();os.fsync(stream.fileno());os.fchmod(stream.fileno(),mode)
            finally:os.close(fd)
        for name,kind,mode,_ in reversed(decoded):
            if kind=='directory':
                fd=parent_fd(name)
                try:
                    child=os.open(name.split('/')[-1],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                    try:os.fchmod(child,mode)
                    finally:os.close(child)
                finally:os.close(fd)
    finally:os.close(root_fd)
    return destination
