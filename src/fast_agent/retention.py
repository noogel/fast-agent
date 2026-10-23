"""运行记录回收预览：有界扫描，引用或未知状态一律保留。"""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import stat
from .runtime_store import RuntimeStore,LIMIT

CANDIDATES={'questions','delegations','tool-outputs'}
ACTIVE={'running','pending','paused','unknown','removing','cleaning','moving_archive','moving_restore','undoing','moving','restoring','purging'}

def retention_policy():
    return {
        'worktree-cleanups':{'automatic_expiry':None,'reason':'恢复文件与任务证据的唯一快照；普通回收永不删除'},
        'git_cleanup_refs':{'automatic_expiry':None,'prefix':'refs/fast-agent/cleanups/','reason':'防止清理记录所指提交被Git垃圾回收；普通回收永不删除'},
        'retention':{'automatic_expiry':None,'reason':'隔离/永久删除的审计清单；内容删除后仍保留哈希与状态'},
    }


def preview_retention(root,min_age_days=30, *, reference_ids=(), ignore_references=()):
    if type(min_age_days) is not int or not 1<=min_age_days<=3650:raise ValueError('保留期需为 1～3650 天')
    root=Path(root).resolve();base=root/'.fast-agent'
    if base.is_symlink():raise ValueError('运行记录目录不能是链接')
    records=[];blockers=[];total=0
    if not base.exists():return {'candidates':[],'protected':[],'blockers':[],'retained_evidence':[],
        'retention_policy':retention_policy(),'fingerprint':hashlib.sha256(b'').hexdigest()}
    def walk_error(error):raise ValueError('运行记录扫描失败') from error
    pending=[base];scanned=set();visited_directories=0;workspace_entries=0
    while pending:
        current=pending.pop()
        if current.is_symlink() or not current.is_dir():raise ValueError('工作区运行目录必须是普通目录，不能是链接')
        if current in scanned:raise ValueError('工作区扫描循环')
        scanned.add(current)
        if len(scanned)>32:raise ValueError('独立工作区扫描超过 32 项')
        for directory,dirs,files in os.walk(current,followlinks=False,onerror=walk_error):
            visited_directories+=1
            if visited_directories>1000:raise ValueError('运行目录扫描超限')
            for name in dirs:
                if (Path(directory)/name).is_symlink():raise ValueError('运行记录含链接目录，不能回收')
            if Path(directory)==current:
                for category in {'worktrees','archived-worktrees','restored-evidence'} & set(dirs):
                    for workspace in (current/category).iterdir():
                        workspace_entries+=1
                        if workspace_entries>1000:raise ValueError('独立工作区目录条目超限')
                        if workspace.is_symlink():raise ValueError('独立工作区不能是链接')
                        if workspace.is_dir():
                            runtime=workspace/'.fast-agent'
                            if runtime.is_symlink():raise ValueError('工作区运行目录不能是链接')
                            if runtime.exists():pending.append(runtime)
                            if len(pending)+len(scanned)>32:raise ValueError('独立工作区扫描超过 32 项')
                    dirs.remove(category)
            for name in files:
                path=Path(directory)/name
                if path.is_symlink():raise ValueError('运行记录含链接，不能回收')
                if path.suffix not in {'.json','.record'}:continue
                if len(records)>=1000:raise ValueError('记录扫描超过 1000 项')
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                with os.fdopen(fd,'rb') as stream:
                    before=os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode):raise ValueError('记录必须是普通文件')
                    data=stream.read(LIMIT+1)
                    after=os.fstat(stream.fileno())
                    if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                        raise ValueError('扫描期间记录变化')
                total+=len(data)
                if len(data)>LIMIT or total>32*1024*1024:raise ValueError('记录扫描数据超限')
                try:value=json.loads(data)
                except (ValueError,RecursionError):raise ValueError('存在损坏记录，不能判断引用') from None
                if not isinstance(value,dict):raise ValueError('运行记录格式无效')
                relative=path.relative_to(base).as_posix()
                if value.get('status') in ACTIVE or value.get('uncertain'):blockers.append(relative)
                reference_data=data
                if path.parent.name=='worktree-cleanups':
                    from .worktree_snapshot import validate
                    snapshot=value.get('snapshot')
                    if snapshot is None:raise ValueError('清理证据缺少快照，不能判断引用')
                    decoded=validate(snapshot)
                    extra=b'\n'.join(payload for _,kind,_,payload in decoded if kind=='file')
                    total+=len(extra)
                    if total>32*1024*1024:raise ValueError('快照引用扫描数据超限')
                    reference_data+=extra
                records.append({'path':relative,'data':data,'reference_data':reference_data,'value':value,'mtime':after.st_mtime})
    candidates=[];protected=[];now=time.time()
    for record in records:
        parts=record['path'].split('/');identifier=Path(parts[-1]).stem
        if len(parts)!=2 or parts[0] not in CANDIDATES or not re.fullmatch(r'[0-9a-f]{32}',identifier):continue
        reasons=[];value=record['value']
        if now-record['mtime']<min_age_days*86400:reasons.append('保留期未到')
        if parts[0]=='questions' and value.get('status')!='answered':reasons.append('问题未回答')
        references=[other['path'] for other in records if other is not record and identifier.encode() in other['reference_data']]
        if references:reasons.append('仍有记录引用')
        row={'path':record['path'],'sha256':hashlib.sha256(record['data']).hexdigest(),'bytes':len(record['data']),
             'references':references,'reasons':reasons}
        (protected if reasons else candidates).append(row)
    fingerprint=hashlib.sha256()
    for record in sorted(records,key=lambda r:r['path']):
        fingerprint.update(record['path'].encode()+b'\0'+record['data'])
    return {'candidates':candidates,'protected':protected,'blockers':blockers,
            'retention_policy':retention_policy(),
            'retained_evidence':[{'path':r['path'],'bytes':len(r['data']),'sha256':hashlib.sha256(r['data']).hexdigest(),
                'automatic_expiry':None} for r in records if Path(r['path']).parent.name in {'worktree-cleanups','retention'}],
            'queried_references':{identifier:[r['path'] for r in records if r['path'] not in ignore_references and identifier.encode() in r['reference_data']] for identifier in reference_ids},
            'fingerprint':fingerprint.hexdigest(),'notice':'仅预览，不删除。存在阻塞记录时不能回收；清理证据包和工作区不属于候选。'}
