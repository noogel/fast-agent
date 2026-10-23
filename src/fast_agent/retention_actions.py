"""引用核对后的可恢复隔离；不永久删除记录。"""
import hashlib
import os
import uuid
import stat
import time
from .retention import preview_retention, CANDIDATES
from .runtime_store import RuntimeStore
from .tasks import TaskStore


def digest(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):raise ValueError('记录必须是普通文件')
        with os.fdopen(fd,'rb',closefd=False) as stream:
            data=stream.read(16*1024*1024+1)
        after=os.fstat(fd)
        if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            raise ValueError('读取期间记录变化')
        if len(data)>16*1024*1024:raise ValueError('记录超限')
        return hashlib.sha256(data).hexdigest()
    finally:os.close(fd)


def source(root,relative):
    import re
    parts=relative.split('/')
    if len(parts)!=2 or parts[0] not in CANDIDATES or not re.fullmatch(r'[0-9a-f]{32}\.json',parts[1]):
        raise ValueError('回收记录路径无效')
    return RuntimeStore(root,parts[0]).path(parts[1])


def quarantine(context, fingerprint, days=30):
    store=RuntimeStore(context.root,'retention')
    with TaskStore(context.root).store.lock(),store.lock():
        preview=preview_retention(context.root,days)
        if preview['fingerprint']!=fingerprint:raise ValueError('记录已变化，请重新预览')
        if preview['blockers']:raise ValueError('存在活动或未核实引用，拒绝回收')
        rows=preview['candidates']
        if not rows:raise ValueError('没有可回收记录')
        if not context.authorize('storage_quarantine',{'paths':[r['path'] for r in rows]},
                '将这些无引用过期记录移入可恢复隔离区？请先停止其他记录写入进程。'):
            raise ValueError('未授权回收')
        current=preview_retention(context.root,days)
        if current!=preview:raise ValueError('确认期间记录已变化，请重新预览')
        identifier=uuid.uuid4().hex
        record={'id':identifier,'status':'moving','entries':[
            {'path':row['path'],'sha256':row['sha256'],'slot':str(i)+'.record'} for i,row in enumerate(rows)]}
        store.write(identifier+'.json',record)
        target=RuntimeStore(context.root,'retention-'+identifier)
        for row in record['entries']:
            original=source(context.root,row['path']);dest=target.path(row['slot'])
            if dest.exists() or digest(original)!=row['sha256']:raise ValueError('记录变化，回收停止；请核实批次')
            os.link(original,dest,follow_symlinks=False)
            if digest(dest)!=row['sha256'] or not os.path.samestat(original.lstat(),dest.lstat()):
                raise ValueError('移动期间记录变化，副本保留供核实')
            original.unlink()
        record['status']='quarantined'
        record['quarantined_at']=time.time()
        store.write(identifier+'.json',record)
        return record


def restore(context,identifier):
    import re
    if not re.fullmatch(r'[0-9a-f]{32}',identifier):raise ValueError('回收批次 ID 无效')
    store=RuntimeStore(context.root,'retention')
    with TaskStore(context.root).store.lock(),store.lock():
        record=store.read(identifier+'.json')
        if not isinstance(record,dict) or record.get('id')!=identifier or record.get('status') not in {'moving','quarantined','restoring','restored'}:
            raise ValueError('回收记录无效')
        entries=record.get('entries')
        if not isinstance(entries,list) or not 1<=len(entries)<=1000:raise ValueError('回收清单无效')
        target=RuntimeStore(context.root,'retention-'+identifier)
        paths=set()
        for i,row in enumerate(entries):
            if not isinstance(row,dict) or row.get('slot')!=str(i)+'.record':raise ValueError('回收槽位无效')
            original=source(context.root,row['path']);saved=target.path(row['slot'])
            if row['path'] in paths:raise ValueError('回收路径重复')
            paths.add(row['path'])
            # 允许崩溃后部分已恢复；两个副本同时存在或均不存在都需人工处理。
            if original.exists()==saved.exists():raise ValueError('回收副本冲突或缺失')
            if digest(saved if saved.exists() else original)!=row['sha256']:raise ValueError('回收内容变化，不覆盖')
        if not context.authorize('storage_restore',{'id':identifier},'恢复隔离记录到原路径，不覆盖已有内容？'):
            raise ValueError('未授权恢复')
        record['status']='restoring';store.write(identifier+'.json',record)
        for row in entries:
            original=source(context.root,row['path']);saved=target.path(row['slot'])
            if saved.exists():
                if original.exists() or digest(saved)!=row['sha256']:raise ValueError('恢复期间内容变化')
                # 独占创建硬链接再移除隔离名字，避免覆盖并发创建的目标。
                os.link(saved,original,follow_symlinks=False)
                if digest(original)!=row['sha256'] or not os.path.samestat(saved.lstat(),original.lstat()):
                    raise ValueError('恢复期间记录变化，副本保留供核实')
                saved.unlink()
            elif not original.exists() or digest(original)!=row['sha256']:
                raise ValueError('确认期间已恢复记录发生变化')
        record['status']='restored';store.write(identifier+'.json',record)
        return record
