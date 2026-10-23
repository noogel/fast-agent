"""可恢复归档：移动整份 Git worktree，不删除修改、忽略文件或运行证据。"""
import itertools
from pathlib import Path
import re
import subprocess
import uuid
from .runtime_store import RuntimeStore
from .worktrees import git


def idle(root):
    terminal={'tasks':{'completed','incomplete','interrupted','paused_repeated_failure'},
              'jobs':{'succeeded','failed','cancelled','timed_out'},
              'batches':{'completed','undone'},
              'delegation-batches':{'completed','incomplete'},
              'retention':{'quarantined','restored','purged'}}
    for category,allowed in terminal.items():
        store=RuntimeStore(root,category)
        paths=list(itertools.islice(store.path('index',create=False).parent.glob('*.json'),1001))
        if len(paths)>1000:raise ValueError('任务记录过多，请先人工检查；不自动归档')
        for path in paths:
            data=store.read(path.name)
            if not isinstance(data,dict) or data.get('status') not in allowed or data.get('uncertain'):
                raise ValueError('存在活动、未知或无效任务记录，拒绝移动工作区')


def owned(root,target):
    if git(target,['rev-parse','--show-toplevel'])!=str(target):raise ValueError('工作区根路径不匹配')
    common=Path(git(target,['rev-parse','--git-common-dir']))
    if not common.is_absolute():common=target/common
    if common.resolve()!=root/'.git':raise ValueError('工作区不属于当前主仓库')
    if any(line.startswith('160000 ') for line in git(target,['ls-files','--stage']).splitlines()):
        raise ValueError('首版不归档含子模块的工作区')


def relocate(name,context,restore=False):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}',name):raise ValueError('worktree 名称无效')
    if context.sandbox!='none':raise ValueError('当前命令沙箱不允许移动 Git 元数据')
    root=context.root.resolve()
    if not (root/'.git').is_dir() or (root/'.git').is_symlink():raise ValueError('请从普通主仓库根目录操作')
    active=RuntimeStore(root,'worktrees');archives=RuntimeStore(root,'archived-worktrees')
    journals=RuntimeStore(root,'worktree-archives')
    original=active.path(name,create=False)
    created=RuntimeStore(root,'worktree-records').read(name+'.json')
    if not isinstance(created,dict) or created.get('path')!=str(original):raise ValueError('创建记录不匹配')
    with active.lock():
        journal_path=journals.path(name+'.json',create=False)
        previous=journals.read(name+'.json') if journal_path.exists() else None
        if restore:
            if not isinstance(previous,dict) or previous.get('status')!='archived':raise ValueError('没有已确认的归档可恢复')
            archive_name=previous.get('archive_name','')
            if not re.fullmatch(re.escape(name)+r'-[0-9a-f]{32}',archive_name):raise ValueError('归档路径记录无效')
            source=archives.path(archive_name,create=False);destination=original
        else:
            if previous is not None and (not isinstance(previous,dict) or previous.get('status')!='restored'):raise ValueError('已有归档或未确认的移动记录，请人工核实')
            archive_name=name+'-'+uuid.uuid4().hex
            source=original;destination=archives.path(archive_name,create=False)
        if destination.exists():raise ValueError('目标已存在，拒绝覆盖')
        owned(root,source)
        with RuntimeStore(source,'tasks').lock():
            idle(source)
            action='恢复' if restore else '归档'
            if not context.authorize('worktree_restore' if restore else 'worktree_archive',{'source':str(source),'destination':str(destination)},
                f'{action}整个 worktree 到 {destination}，保留所有修改、忽略文件及任务证据，不释放磁盘空间。请先停止该目录所有 Agent、编辑器写入和外部命令。是否继续？'):
                raise ValueError('工作区移动未获授权')
            idle(source)
            head=git(source,['rev-parse','HEAD'])
            journal={'name':name,'archive_name':archive_name,'original':str(original),'head_commit':head,
                     'status':'moving_restore' if restore else 'moving_archive'}
            (active if restore else archives).path(name if restore else archive_name)
            journals.write(name+'.json',journal)
            try:
                git(root,['worktree','move',str(source),str(destination)])
                owned(root,destination)
                if source.exists() or git(destination,['rev-parse','HEAD'])!=head:raise ValueError('移动后状态不一致')
                journal['status']='restored' if restore else 'archived'
                journals.write(name+'.json',journal)
            except (ValueError,OSError,subprocess.TimeoutExpired,KeyboardInterrupt):
                raise ValueError('工作区移动结果未确认；保留移动记录。请人工核实 git worktree list 和两端路径，不自动重试或删除') from None
    return {'path':str(destination),'status':journal['status'],'head_commit':head,
            'notice':'未删除任何工作内容；归档仍是 Git worktree。恢复到原路径后再使用原任务证据，绝对路径记录未重写。'}


def archive_worktree(name,context):return relocate(name,context)
def restore_worktree(name,context):return relocate(name,context,True)


def reconcile_worktree(name,context):
    """只核对并修复移动日志，不移动、不删除或重放任何操作。"""
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}',name):
        raise ValueError('worktree 名称无效')
    root=context.root.resolve()
    if not (root/'.git').is_dir() or (root/'.git').is_symlink():
        raise ValueError('请从普通主仓库根目录操作')
    active=RuntimeStore(root,'worktrees');journals=RuntimeStore(root,'worktree-archives')
    with active.lock():
        record=journals.read(name+'.json')
        if not isinstance(record,dict) or record.get('name')!=name or record.get('status') not in {'moving_archive','moving_restore'}:
            raise ValueError('没有待核实的移动记录')
        original=active.path(name,create=False)
        archive_name=record.get('archive_name','')
        if not isinstance(archive_name,str) or not re.fullmatch(re.escape(name)+r'-[0-9a-f]{32}',archive_name):
            raise ValueError('归档路径记录无效')
        archived=RuntimeStore(root,'archived-worktrees').path(archive_name,create=False)
        created=RuntimeStore(root,'worktree-records').read(name+'.json')
        if not isinstance(created,dict) or created.get('path')!=str(original) or record.get('original')!=str(original):
            raise ValueError('创建记录或原路径不匹配')
        def inspect():
            if original.exists()==archived.exists():
                raise ValueError('两端同时存在或同时缺失，不能自动核实')
            target=original if original.exists() else archived
            owned(root,target)
            if git(target,['rev-parse','HEAD'])!=record.get('head_commit'):
                raise ValueError('HEAD 已变化，不能自动核实')
            idle(target)
            return target,'restored' if target==original else 'archived'
        target,status=inspect()
        with RuntimeStore(target,'tasks').lock():
            if not context.authorize('worktree_reconcile',{'name':name,'path':str(target),'status':status},
                f'已核对唯一工作区 {target}，只将移动记录确认为 {status}，不移动或删除文件。请停止外部写入，是否确认？'):
                raise ValueError('移动状态核实未获授权')
            if inspect()!=(target,status):raise ValueError('确认期间工作区发生变化')
            record['status']=status
            journals.write(name+'.json',record)
            return {'path':str(target),'status':status,'notice':'仅更新移动日志，文件和任务证据未改动'}
