"""分段读取与确认后的原子文本修改，避免覆盖读取之后的外部变更。"""
import difflib
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from .tools import ToolInputError, Tool, ToolContext, ToolResult, object_schema
from .workspace import inside, instructions, instruction_fingerprint

LIMIT = 64 * 1024
FILE_LIMIT = 1024 * 1024


def resolve_file(context: ToolContext, name: str) -> Path:
    try:
        path = inside(context.root, name)
    except ValueError as exc:
        raise ToolInputError(str(exc)) from exc
    if path.exists() and not path.is_file():
        raise ToolInputError('目标不是普通文件')
    return path


def read_bytes(path: Path) -> bytes:
    with path.open('rb') as stream:
        data = stream.read(FILE_LIMIT + 1)
    if len(data) > FILE_LIMIT:
        raise ToolInputError('文件超过 1 MiB 教学实现上限')
    return data


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_file(args: dict, context: ToolContext) -> ToolResult:
    path = resolve_file(context, args['path'])
    data = read_bytes(path)
    lines = data.decode('utf-8').splitlines(keepends=True)
    start, end = args.get('start_line', 1), args.get('end_line', max(1, len(lines)))
    if start < 1 or end < start or (start > len(lines) and lines):
        raise ToolInputError('行号从 1 开始，结束行不能早于开始行或超出有效起点')
    selected = ''.join(lines[start - 1:end])
    if len(selected.encode('utf-8')) > LIMIT:
        raise ToolInputError('返回内容超过 64 KiB，请用 start_line/end_line 缩小范围')
    rules = instructions(context.root, path)
    context.read_versions[str(path)] = (fingerprint(data), instruction_fingerprint(rules))
    context.rule_versions[str(path)] = instruction_fingerprint(rules)
    return ToolResult(True, {'content': selected, 'start_line': start, 'end_line': min(end, len(lines)),
                             'total_lines': len(lines), 'sha256': fingerprint(data), 'instructions': rules})


def checked_before(path: Path, context: ToolContext):
    if path.relative_to(context.root.resolve()).parts[0] in {'.fast-agent', '.git'}:
        raise ToolInputError('禁止用文件编辑工具修改 Agent 运行记录或 Git 内部数据')
    try:
        rules = instructions(context.root, path)
    except ValueError as exc:
        raise ToolInputError(str(exc)) from exc
    rule_hash = instruction_fingerprint(rules)
    if path.exists():
        before = read_bytes(path)
        if str(path) not in context.read_versions:
            raise ToolInputError('修改已有文件前必须先调用 read_file')
        if context.read_versions[str(path)] != (fingerprint(before), rule_hash):
            raise ToolInputError('文件或 AGENTS.md 已发生变化，请重新读取后再修改')
    else:
        before = None
        if rules and context.rule_versions.get(str(path)) != rule_hash:
            raise ToolInputError('创建文件前必须调用 read_instructions 查看适用规则')
    return before, rules


def commit_text(path: Path, before: bytes | None, content: str, rules: list[dict], context: ToolContext, *, record_edit: bool = True, batch_approved: bool = False) -> ToolResult:
    data = content.encode('utf-8')
    if len(data) > FILE_LIMIT:
        raise ToolInputError('内容超过 1 MiB 写入上限')
    old = before.decode('utf-8') if before is not None else ''
    chunks = difflib.unified_diff(old.splitlines(keepends=True), content.splitlines(keepends=True),
                                  fromfile=str(path), tofile=str(path))
    diff = ''.join(chunk if chunk.endswith('\n') else chunk + '\n\\ No newline at end of file\n' for chunk in chunks)
    if before == data:
        return ToolResult(True, {'path': str(path.relative_to(context.root.resolve())), 'changed': False})
    if len(diff.encode('utf-8')) > LIMIT:
        raise ToolInputError('修改差异超过 64 KiB，请拆分为更小的修改')
    preview = f'修改文件 {path}\n{diff}\n确认写入？'
    preview = ''.join(char if char in '\n\t' or (ord(char) >= 32 and ord(char) != 127) else f'\\x{ord(char):02x}' for char in preview)
    if not batch_approved and not context.authorize('write', str(path), preview):
        return ToolResult(False, error='用户未批准文件修改')
    def unchanged():
        current = read_bytes(path) if path.exists() else None
        if current != before or instructions(context.root, path) != rules:
            raise ToolInputError('确认期间文件或 AGENTS.md 发生变化，请重新读取')
    unchanged()
    from .edit_history import EditHistory
    history = EditHistory(context.root)
    record = history.prepare(path, before, data) if record_edit else None
    temporary = None
    try:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.fast-agent-edit-', delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        unchanged()
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    if record is not None:
        history.finish(record, 'completed')
    context.read_versions[str(path)] = (fingerprint(data), instruction_fingerprint(rules))
    return ToolResult(True, {'path': str(path.relative_to(context.root.resolve())), 'changed': True, 'sha256': fingerprint(data), **({'edit_id': record['id']} if record else {})})


def write_file(args: dict, context: ToolContext) -> ToolResult:
    path = resolve_file(context, args['path'])
    before, rules = checked_before(path, context)
    return commit_text(path, before, args['content'], rules, context)


def unique_match(text: str, old: str) -> bool:
    """从下一字符继续搜索，避免漏掉重叠的第二个匹配。"""
    first = text.find(old)
    return bool(old) and first >= 0 and text.find(old, first + 1) < 0


def edit_file(args: dict, context: ToolContext) -> ToolResult:
    path = resolve_file(context, args['path'])
    before, rules = checked_before(path, context)
    if before is None:
        raise ToolInputError('待编辑文件不存在')
    text = before.decode('utf-8')
    old = args['old']
    if not unique_match(text, old):
        raise ToolInputError('待替换文本必须非空且唯一匹配')
    return commit_text(path, before, text.replace(old, args['new'], 1), rules, context)


def multi_edit_file(args: dict, context: ToolContext) -> ToolResult:
    """全部替换先在内存预演，再复用单文件确认和原子提交。"""
    edits = args['edits']
    if not 1 <= len(edits) <= 100:
        raise ToolInputError('批量替换数量必须为 1 到 100')
    path = resolve_file(context, args['path'])
    before, rules = checked_before(path, context)
    if before is None:
        raise ToolInputError('待编辑文件不存在')
    text = before.decode('utf-8')
    for index, edit in enumerate(edits, 1):
        old = edit['old']
        if not unique_match(text, old):
            raise ToolInputError(f'第 {index} 处待替换文本必须非空且唯一匹配；本批次未写入')
        text = text.replace(old, edit['new'], 1)
        if len(text.encode('utf-8')) > FILE_LIMIT:
            raise ToolInputError(f'第 {index} 处替换后内容超过 1 MiB；本批次未写入')
    return commit_text(path, before, text, rules, context)


def file_tools() -> list[Tool]:
    string = {'type': 'string'}
    edits_schema = {'type': 'array', 'minItems': 1, 'maxItems': 100,
                    'items': object_schema({'old': string, 'new': string}, ['old', 'new'])}
    return [
        Tool('multi_edit_file', '先 read_file，再按 edits 顺序在内存精确替换，每个 old 须在当时文本中非空且唯一匹配；1 到 100 处，全部成功才确认整体差异并原子写入单个文件，任一失败均不写入', object_schema({'path': string, 'edits': edits_schema}, ['path', 'edits']), multi_edit_file),
        Tool('read_file', '读取 UTF-8 文件，最大 1 MiB；支持从 1 开始的 start_line/end_line（含结束行），返回最多 64 KiB，附带沿途 AGENTS.md', object_schema({'path': string, 'start_line': {'type': 'integer'}, 'end_line': {'type': 'integer'}}, ['path']), read_file, 'read'),
        Tool('write_file', '创建或覆盖文本，展示差异并确认；已有文件必须先 read_file，新文件先 read_instructions；父目录必须存在', object_schema({'path': string, 'content': string}, ['path', 'content']), write_file),
        Tool('edit_file', '先读取文件后精确替换唯一的 old；展示差异并确认，检测并拒绝外部修改', object_schema({'path': string, 'old': string, 'new': string}, ['path', 'old', 'new']), edit_file),
    ]
