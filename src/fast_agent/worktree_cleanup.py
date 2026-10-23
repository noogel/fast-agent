"""已归档工作区清理：预览、完整证据、受保护提交、显式确认。"""
import hashlib
import json
import re
import uuid
from .runtime_store import RuntimeStore
from .worktrees import git
from .worktree_lifecycle import owned,idle
from .worktree_snapshot import capture,validate,restore


def locate(name,context):
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}',name):raise ValueError('worktree 名称无效')
    root=context.root.resolve()
    if not (root/'.git').is_dir() or (root/'.git').is_symlink():raise ValueError('需要普通主仓库')
    record=RuntimeStore(root,'worktree-archives').read(name+'.json')
    if not isinstance(record,dict) or record.get('status')!='archived':raise ValueError('仅允许清理已确认归档')
    archive_name=record.get('archive_name')
    if not isinstance(archive_name,str) or not re.fullmatch(re.escape(name)+r'-[0-9a-f]{32}',archive_name):raise ValueError('归档名称无效')
    original=RuntimeStore(root,'worktrees').path(name,create=False)
    created=RuntimeStore(root,'worktree-records').read(name+'.json')
    if created.get('path')!=str(original) or record.get('original')!=str(original) or original.exists():raise ValueError('工作区归属记录冲突')
    target=RuntimeStore(root,'archived-worktrees').path(archive_name,create=False)
    owned(root,target);idle(target)
    if git(target,['status','--porcelain','--untracked-files=no']):raise ValueError('有未交付的跟踪文件修改，拒绝清理')
    head=git(target,['rev-parse','HEAD'])
    if head!=record.get('head_commit'):raise ValueError('归档后 HEAD 变化，拒绝清理')
    # 必须已进入主仓库当前 HEAD 的祖先链，独立提交不能仅靠快照冒充交付。
    git(root,['merge-base','--is-ancestor',head,'HEAD'])
    return root,target,record,head


def digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def preview_cleanup(name,context):
    root,target,record,head=locate(name,context)
    with RuntimeStore(root,'worktrees').lock(),RuntimeStore(target,'tasks').lock():
        idle(target);snapshot=capture(target);validate(snapshot)
        return {'name':name,'path':str(target),'head_commit':head,'files':len(snapshot['entries']),
                'bytes':snapshot['bytes'],'sha256':digest(snapshot),
                'notice':'预览不会删除；清理将保存全部文件证据及受保护提交。请停止外部写入。'}


def cleanup_worktree(name,context):
    if context.sandbox!='none':raise ValueError('当前沙箱不允许清理 Git 元数据')
    root,target,record,head=locate(name,context)
    with RuntimeStore(root,'worktrees').lock(),RuntimeStore(target,'tasks').lock():
        locate(name,context)
        snapshot=capture(target);validate(snapshot)
        identifier=uuid.uuid4().hex;store=RuntimeStore(root,'worktree-cleanups')
        package={'id':identifier,'name':name,'path':str(target),'head_commit':head,'status':'prepared',
                 'snapshot':snapshot,'sha256':digest(snapshot)}
        store.write(identifier+'.json',package)
        saved=store.read(identifier+'.json');validate(saved['snapshot'])
        if digest(saved['snapshot'])!=package['sha256']:raise ValueError('落盘证据校验失败')
        if not context.authorize('worktree_cleanup',{'name':name,'path':str(target),'evidence':identifier},
            f'删除已归档 worktree {target}；完整文件证据保存为 {identifier}，提交将保留。请停止所有外部写入。确认清理？'):
            raise ValueError('清理未获授权；准备证据已保留')
        locate(name,context)
        if capture(target)!=snapshot:raise ValueError('确认期间文件变化，拒绝清理')
        git(root,['update-ref','refs/fast-agent/cleanups/'+identifier,head,'0'*40])
        package['status']='removing';store.write(identifier+'.json',package)
        record['status']='cleaning';record['cleanup_id']=identifier
        RuntimeStore(root,'worktree-archives').write(name+'.json',record)
        # 跟踪修改已拒绝；force 仅用于允许移除已完整备份的未跟踪/忽略证据。
        git(root,['worktree','remove','--force',str(target)])
        if target.exists():raise ValueError('清理结果不确定，请保留现场和证据')
        package['status']='removed';store.write(identifier+'.json',package)
        record['status']='cleaned';record['cleanup_id']=identifier
        RuntimeStore(root,'worktree-archives').write(name+'.json',record)
        return {'id':identifier,'status':'removed','notice':'证据包和提交引用保留；恢复文件使用 /worktree-evidence ID'}


def restore_evidence(identifier,context):
    if not isinstance(identifier,str) or not re.fullmatch(r'[0-9a-f]{32}',identifier):raise ValueError('证据 ID 无效')
    root=context.root.resolve();store=RuntimeStore(root,'worktree-cleanups')
    package=store.read(identifier+'.json')
    if package.get('id')!=identifier or digest(package['snapshot'])!=package.get('sha256'):raise ValueError('证据包校验失败')
    validate(package['snapshot'])
    target=RuntimeStore(root,'restored-evidence').path(identifier,create=False)
    if not context.authorize('worktree_evidence',{'id':identifier,'path':str(target)},f'恢复证据文件到新目录 {target}？'):
        raise ValueError('证据恢复未获授权')
    RuntimeStore(root,'restored-evidence').path(identifier)
    restore(package['snapshot'],target)
    return {'path':str(target),'head_commit':package['head_commit'],'notice':'文件证据目录，不是 Git worktree；未执行任何恢复的脚本。'}


def reconcile_cleanup(identifier,context):
    if not isinstance(identifier,str) or not re.fullmatch(r'[0-9a-f]{32}',identifier):raise ValueError('证据 ID 无效')
    root=context.root.resolve();store=RuntimeStore(root,'worktree-cleanups')
    with RuntimeStore(root,'worktrees').lock():
        package=store.read(identifier+'.json')
        if package.get('id')!=identifier or package.get('status') not in {'removing','removed'}:
            raise ValueError('没有待核实清理记录')
        validate(package['snapshot'])
        if digest(package['snapshot'])!=package.get('sha256'):raise ValueError('证据校验失败')
        name=package.get('name')
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}',name):raise ValueError('工作区名称无效')
        journals=RuntimeStore(root,'worktree-archives');record=journals.read(name+'.json')
        if record.get('cleanup_id')!=identifier or record.get('status') not in {'cleaning','cleaned'}:raise ValueError('清理归属不匹配')
        archive_name=record.get('archive_name','')
        if not re.fullmatch(re.escape(name)+r'-[0-9a-f]{32}',archive_name):raise ValueError('归档路径无效')
        target=RuntimeStore(root,'archived-worktrees').path(archive_name,create=False)
        if package.get('path')!=str(target):raise ValueError('清理路径不匹配')
        def inspect():
            if git(root,['rev-parse','refs/fast-agent/cleanups/'+identifier])!=package['head_commit']:raise ValueError('受保护提交缺失或变化')
            registered=('worktree '+str(target)) in git(root,['worktree','list','--porcelain']).splitlines()
            if target.exists():
                if not registered:raise ValueError('目录存在但 Git 注册缺失，需人工核实')
                owned(root,target);idle(target)
                if capture(target)!=package['snapshot']:raise ValueError('工作区变化，不能确认未删除')
                return 'retained'
            if registered:raise ValueError('目录缺失但 Git 注册仍在，需人工核实')
            return 'removed'
        status=inspect()
        if not context.authorize('worktree_cleanup_reconcile',{'id':identifier,'status':status},f'仅将清理状态核实为 {status}，不执行删除或移动。是否确认？'):
            raise ValueError('核实未获授权')
        if inspect()!=status:raise ValueError('核实期间状态变化')
        package['status']=status;store.write(identifier+'.json',package)
        record['status']='cleaned' if status=='removed' else 'archived'
        journals.write(name+'.json',record)
        return {'id':identifier,'status':status}
