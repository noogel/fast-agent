"""本地显式 worktree 创建：固定 HEAD、独立目录，不合并、不自动删除。"""
import os
import hashlib
import tempfile
import uuid
from pathlib import Path
import re
import subprocess
from .runtime_store import RuntimeStore


def git(root,args):
    # 不继承 GIT_DIR/GIT_WORK_TREE 或模型凭据，避免被环境重定向到其他仓库。
    env={k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','HOME','TMPDIR'}}
    env.update(GIT_TERMINAL_PROMPT='0',GIT_OPTIONAL_LOCKS='0')
    result=subprocess.run(['git','-c','core.hooksPath=/dev/null',*args],cwd=root,env=env,
                          capture_output=True,text=True,timeout=60)
    if result.returncode:raise ValueError('Git 操作失败：'+result.stderr[:1000])
    return result.stdout.strip()


def create_worktree(name,context):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}',name):
        raise ValueError('worktree 名称需为 1～48 位字母、数字、下划线或短横线')
    if context.sandbox!='none':
        raise ValueError('当前命令沙箱不允许创建 worktree 的 Git 元数据；本操作不会绕过沙箱')
    root=context.root.resolve()
    # 首版只从普通主仓库根目录创建，避免共享 Git 元数据越出工作区。
    if not (root/'.git').is_dir() or (root/'.git').is_symlink():
        raise ValueError('请从具有普通 .git 目录的主仓库根目录创建 worktree')
    if git(root,['rev-parse','--show-toplevel'])!=str(root):raise ValueError('工作目录必须是 Git 仓库根目录')
    revision=git(root,['rev-parse','--verify','HEAD^{commit}'])
    store=RuntimeStore(root,'worktrees')
    target=store.path(name,create=False)
    if RuntimeStore(root,'worktree-archives').path(name+'.json',create=False).exists():
        raise ValueError('名称已有归档生命周期记录，请使用其他名称或恢复原工作区')
    if target.exists():raise ValueError('目标目录已存在，拒绝覆盖')
    prompt=f'创建独立 worktree {target}，基于已提交 HEAD {revision}；不复制未提交修改，检出可能运行仓库配置的过滤器。是否创建？'
    if not context.authorize('worktree_create',{'path':str(target),'commit':revision},prompt):
        raise ValueError('worktree 创建未获授权')
    with store.lock():
        if RuntimeStore(root,'worktree-archives').path(name+'.json',create=False).exists():
            raise ValueError('名称已有归档生命周期记录，拒绝复用')
        target=store.path(name)
        if target.exists():raise ValueError('目标目录已存在，拒绝覆盖')
        try:
            git(root,['worktree','add','--detach',str(target),revision])
            RuntimeStore(root,'worktree-records').write(name+'.json',{'path':str(target),'base_commit':revision})
        except (ValueError,OSError,subprocess.TimeoutExpired,KeyboardInterrupt):
            raise ValueError('worktree 创建未确认完成；请用 git worktree list 检查，禁止直接重试覆盖或自动清理') from None
    return {'path':str(target),'commit':revision,'detached':True,
            'notice':'工作区文件独立，但共享 Git 对象库；不是安全沙箱。使用 --workspace 新目录启动会话，先检查规则和配置。'}


def patch_bytes(root, base):
    """磁盘暂存 Git 输出，内存读取上限 2 MiB；禁用外部 diff/textconv。"""
    env={k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','HOME','TMPDIR'}}
    env.update(GIT_TERMINAL_PROMPT='0',GIT_OPTIONAL_LOCKS='0')
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        result=subprocess.run(['git','-c','core.hooksPath=/dev/null','diff','--no-ext-diff',
                               '--no-textconv','--no-color','--no-relative','--src-prefix=a/','--dst-prefix=b/',
                               '--binary','--full-index',base,'--'],
                              cwd=root,env=env,stdout=output,stderr=errors,timeout=60)
        if result.returncode:raise ValueError('补丁生成失败，未交付')
        output.seek(0)
        data=output.read(2*1024*1024+1)
        if len(data)>2*1024*1024:raise ValueError('补丁超过 2 MiB，未交付')
        return data


def export_worktree(name, context):
    """导出已知创建基点到工作目录的完整补丁；不触碰父目录文件。"""
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}',name):
        raise ValueError('worktree 名称无效')
    root=context.root.resolve()
    target=RuntimeStore(root,'worktrees').path(name,create=False)
    record=RuntimeStore(root,'worktree-records').read(name+'.json')
    base=record.get('base_commit','')
    if record.get('path')!=str(target) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',base):
        raise ValueError('worktree 创建记录无效，不能推测交付基点')
    if target.is_symlink() or git(target,['rev-parse','--show-toplevel'])!=str(target):
        raise ValueError('worktree 路径不匹配')
    common=Path(git(target,['rev-parse','--git-common-dir']))
    if not common.is_absolute():common=target/common
    if common.resolve()!=root/'.git':raise ValueError('worktree 不属于当前主仓库')
    if not context.authorize('worktree_export',{'path':str(target),'base_commit':base},
                             f'导出 {target} 的变更补丁到本地交付记录？不会应用或删除工作区。'):
        raise ValueError('worktree 导出未获授权')
    def state():
        if any(line.startswith('160000 ') for line in git(target,['ls-files','--stage']).splitlines()):
            raise ValueError('首版不交付子模块，请使用专门的子模块流程')
        if git(target,['ls-files','--unmerged']):raise ValueError('存在未解决冲突，未交付')
        # 仅运行记录例外；其他未跟踪文件都必须显式暂存或忽略。
        if git(target,['ls-files','--others','--exclude-standard','--','.',':(exclude).fast-agent']):
            raise ValueError('存在未跟踪文件，请显式暂存需要交付的文件或设置忽略规则')
        if git(target,['ls-files','--','.fast-agent']):
            raise ValueError('运行记录不得作为受跟踪文件交付')
        return git(target,['rev-parse','HEAD']),git(target,['status','--porcelain','--untracked-files=no'])
    with RuntimeStore(root,'worktrees').lock():
        before=state()
        patch=patch_bytes(target,base)
        after=state()
        if before!=after or patch!=patch_bytes(target,base) or after!=state():
            raise ValueError('采集期间工作区发生变化，请停止写入后重新导出')
        if not patch:raise ValueError('没有可交付差异')
        # Git 二进制补丁本身是 ASCII；非 UTF-8 路径/文本仍需无损保存。
        import base64
        identifier=uuid.uuid4().hex
        package={'id':identifier,'source':str(target),'base_commit':base,'head_commit':after[0],
                 'status':after[1],'sha256':hashlib.sha256(patch).hexdigest(),
                 'patch_base64':base64.b64encode(patch).decode('ascii'),
                 'notice':'需在精确 base 的干净检出中检查补丁并重跑测试；未自动应用、合并或删除。'}
        store=RuntimeStore(root,'worktree-exports')
        store.write(identifier+'.json',package)
        return {'id':identifier,'path':str(store.path(identifier+'.json')),
                'base_commit':base,'sha256':package['sha256'],'bytes':len(patch),'notice':package['notice']}
