"""按需计算整棵 Skill 资源树指纹，不执行资源，不跟随链接。"""
import hashlib
import json
import os
import stat


def fingerprint(root,parts):
    from .skills import opened
    rows=[];total=0
    def walk(fd,prefix='',depth=0):
        nonlocal total
        if depth>16:raise ValueError('Skill 资源树深度超过 16')
        names=os.listdir(fd)
        if len(names)>1000:raise ValueError('Skill 资源树条目超限')
        for name in sorted(names):
            if len(rows)>=1000:raise ValueError('Skill 资源树超过 1000 项')
            info=os.stat(name,dir_fd=fd,follow_symlinks=False)
            path=prefix+name
            if stat.S_ISDIR(info.st_mode):
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                try:
                    rows.append({'path':path,'kind':'directory'});walk(child,path+'/',depth+1)
                finally:os.close(child)
            elif stat.S_ISREG(info.st_mode):
                child=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                with os.fdopen(child,'rb') as stream:
                    before=os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode):raise ValueError('资源不是普通文件')
                    data=stream.read(4*1024*1024-total+1)
                    after=os.fstat(stream.fileno())
                total+=len(data)
                if total>4*1024*1024:raise ValueError('Skill 资源树超过 4 MiB')
                if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise ValueError('资源读取期间变化')
                rows.append({'path':path,'kind':'file','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mode':stat.S_IMODE(before.st_mode)})
            else:raise ValueError('Skill 资源树含链接或特殊文件')
    with opened(root,parts,True) as fd:walk(fd)
    return {'tree_sha256':hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
            'entries':rows,'bytes':total}
