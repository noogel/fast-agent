"""工作目录边界与分层 AGENTS.md 规则。"""
import hashlib
from pathlib import Path


def inside(root: Path, name: str) -> Path:
    root = root.resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError('路径超出工作目录')
    return path


def instructions(root: Path, target: Path | None = None) -> list[dict]:
    root = root.resolve()
    parent = (target.parent if target is not None else root).resolve()
    if not parent.is_relative_to(root):
        raise ValueError('规则目标超出工作目录')
    directories = [root]
    for part in parent.relative_to(root).parts:
        directories.append(directories[-1] / part)
    result = []
    total = 0
    for directory in directories:
        source = directory / 'AGENTS.md'
        if source.is_symlink():
            raise ValueError('AGENTS.md 不能是符号链接')
        if source.exists():
            if not source.is_file():
                raise ValueError('AGENTS.md 不是普通文件')
            with source.open('rb') as stream:
                data = stream.read(65537)
            total += len(data)
            if total > 65536:
                raise ValueError('适用的 AGENTS.md 合计超过 64 KiB')
            result.append({'path': source.relative_to(root).as_posix(), 'content': data.decode('utf-8')})
    return result


def instruction_fingerprint(rules: list[dict]) -> str:
    import json
    return hashlib.sha256(json.dumps(rules, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def system_prompt(base: str, root: Path, mode: str = 'execute') -> str:
    rules = instructions(root)
    guidance = ('\n你正在处理本地仓库。先检索并读取相关代码再修改；read_file 会返回沿途 AGENTS.md，'
                '越靠近目标文件的规则越具体。修改需用户确认。先复现测试失败，修改后运行测试并检查 git_diff。'
                '工具失败必须如实报告。新文件写入前先用 read_instructions 查看该路径适用的规则。')
    mode_instruction = ('\n当前为计划模式：只能读取、检索和分析，禁止修改文件、执行命令、启动或取消后台任务。'
                        '最终用中文给出目标、依据、拟修改文件、实施步骤、验证方法和待确认事项；区分推测与已验证事实。'
                        '不能声称已运行测试或完成修复。等待用户在本地输入 /execute，模型不能自行切换权限。'
                        if mode == 'plan' else
                        '\n当前为执行模式：遵循现有权限策略执行用户任务；若依据历史计划修改，必须先重新读取相关文件和规则。')
    return base + guidance + ''.join(f"\n--- {r['path']} ---\n{r['content']}" for r in rules) + mode_instruction
