"""隔离记录永久清除：保留期、引用复核及逐项删除日志。"""
import math
import re
import time
from pathlib import Path
from .runtime_store import RuntimeStore
from .retention import preview_retention
from .retention_actions import source,digest
from .tasks import TaskStore


def preview_purge(root,identifier,days=30):
    if not isinstance(identifier,str) or not re.fullmatch(r'[0-9a-f]{32}',identifier):raise ValueError('隔离批次 ID 无效')
    if type(days) is not int or not 7<=days<=3650:raise ValueError('隔离保留期必须为 7～3650 天')
    store=RuntimeStore(root,'retention');record=store.read(identifier+'.json')
    if not isinstance(record,dict) or record.get('id')!=identifier or record.get('status') not in {'quarantined','purging'}:
        raise ValueError('仅允许清除已隔离或中断清除批次')
    stamp=record.get('quarantined_at')
    if type(stamp) not in (int,float) or not math.isfinite(stamp) or time.time()-stamp<days*86400:
        raise ValueError('隔离保留期未到或时间记录无效；旧批次需先恢复再隔离')
    entries=record.get('entries')
    if not isinstance(entries,list) or not 1<=len(entries)<=1000:raise ValueError('隔离清单无效')
    target=RuntimeStore(root,'retention-'+identifier);ids=[];ignored={'retention/'+identifier+'.json'}
    for i,row in enumerate(entries):
        if not isinstance(row,dict) or row.get('slot')!=str(i)+'.record':raise ValueError('隔离槽位无效')
        original=source(root,row['path'])
        if original.exists():raise ValueError('原路径已存在，拒绝永久清除')
        if not isinstance(row.get('sha256'),str) or not re.fullmatch(r'[0-9a-f]{64}',row['sha256']):raise ValueError('隔离哈希无效')
        record_id=Path(row['path']).stem
        if record_id in ids:raise ValueError('重复隔离 ID')
        ids.append(record_id)
        saved=target.path(row['slot'])
        if saved.exists():
            if digest(saved)!=row['sha256']:raise ValueError('隔离内容发生变化')
        elif record['status']!='purging' or row.get('purge_state') not in {'deleting','deleted'}:
            raise ValueError('隔离内容缺失且没有删除意图证据')
        ignored.add('retention-'+identifier+'/'+row['slot'])
    scan=preview_retention(root,reference_ids=ids,ignore_references=ignored)
    blockers=[b for b in scan['blockers'] if b!='retention/'+identifier+'.json']
    if blockers:raise ValueError('存在活动或未知记录，拒绝永久清除')
    references=scan['queried_references']
    if any(references.values()):raise ValueError('仍有记录引用隔离内容，拒绝永久清除')
    return {'id':identifier,'fingerprint':scan['fingerprint'],'days':days,'record':record,
            'notice':'永久删除不可恢复；保留批次清单、哈希及删除状态，不删除工作区或清理证据包。'}


def purge(context,identifier,fingerprint,days=30):
    store=RuntimeStore(context.root,'retention')
    with TaskStore(context.root).store.lock(),store.lock():
        preview=preview_purge(context.root,identifier,days)
        if preview['fingerprint']!=fingerprint:raise ValueError('预览已过期，请重新预览')
        if not context.authorize('storage_purge',{'id':identifier,'entries':preview['record']['entries']},
                '永久删除此隔离批次全部记录，不可恢复。请停止外部写入者。确认永久删除？'):
            raise ValueError('未授权永久删除')
        if preview_purge(context.root,identifier,days)!=preview:raise ValueError('确认期间内容或引用变化')
        record=preview['record'];record['status']='purging'
        store.write(identifier+'.json',record)
        target=RuntimeStore(context.root,'retention-'+identifier)
        for row in record['entries']:
            saved=target.path(row['slot'])
            row['purge_state']='deleting';store.write(identifier+'.json',record)
            if saved.exists():
                if digest(saved)!=row['sha256']:raise ValueError('删除前隔离内容变化')
                saved.unlink()
            row['purge_state']='deleted';store.write(identifier+'.json',record)
        record['status']='purged';record['purged_at']=time.time()
        store.write(identifier+'.json',record)
        return record
