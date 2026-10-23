"""多文件批次：整体预检与确认，逐文件原子写入，保留中断恢复快照。"""
from datetime import datetime, timezone
import difflib
import re
import uuid
from .file_tools import (resolve_file, checked_before, commit_text, read_bytes,
                         unique_match, FILE_LIMIT, LIMIT)
from .runtime_store import RuntimeStore
from .tools import Tool, ToolInputError, ToolResult, object_schema
from .workspace import instructions


def verify(entries, context):
    for path, before, after, rules in entries:
        if read_bytes(path) != before or instructions(context.root, path) != rules:
            raise ToolInputError('批次文件或 AGENTS.md 已发生变化；本次尚未写入')


def approve(entries, context, title):
    diff = ''.join(''.join(chunk if chunk.endswith('\n') else chunk+'\n\\ No newline at end of file\n' for chunk in difflib.unified_diff(
        before.decode('utf-8').splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=str(path), tofile=str(path))) for path, before, after, _ in entries)
    if len(diff.encode('utf-8')) > LIMIT:
        raise ToolInputError('批次总差异超过 64 KiB，请拆分修改')
    preview = title + '\n' + diff + '\n确认写入？'
    preview = ''.join(c if c in '\n\t' or (ord(c)>=32 and ord(c)!=127) else f'\\x{ord(c):02x}' for c in preview)
    return context.authorize('write', [str(e[0]) for e in entries], preview)


class EditBatches:
    def __init__(self, root):
        self.store = RuntimeStore(root, 'batches')

    def load(self, ident):
        if not re.fullmatch('[0-9a-f]{32}', ident):
            raise ValueError('批次编号无效')
        record = self.store.read(ident+'.json')
        if (not isinstance(record, dict) or record.get('id') != ident
                or record.get('workspace') != str(self.store.root)
                or record.get('status') not in {'pending','completed','undoing','undone'}
                or not isinstance(record.get('files'), list) or not 1 <= len(record['files']) <= 20):
            raise ValueError('批次记录格式无效')
        for item in record['files']:
            if (not isinstance(item, dict) or any(not isinstance(item.get(k), str) for k in ('path','before','after'))
                    or any(len(item[k].encode('utf-8')) > FILE_LIMIT for k in ('before','after'))):
                raise ValueError('批次快照无效或超过上限')
        return record

    def list(self):
        return [{'id': ident, 'status': (r:=self.load(ident))['status'],
                 'paths':[e['path'] for e in r['files']]} for ident in self.store.names()]

    def apply(self, entries, context, record=None):
        # 先写普通文件，规则文件由深到浅最后写，避免本批次使后续规则校验失效。
        entries = sorted(entries, key=lambda e: (e[0].name == 'AGENTS.md', -len(e[0].parts)))
        if record is None:
            record = {'id':uuid.uuid4().hex, 'workspace':str(self.store.root), 'status':'pending',
                      'created_at':datetime.now(timezone.utc).isoformat(),
                      'files':[{'path':str(path.relative_to(self.store.root)),
                                'before':before.decode('utf-8'), 'after':after}
                               for path,before,after,_ in entries]}
        self.store.write(record['id']+'.json', record)
        applied = []
        try:
            for path, before, after, rules in entries:
                # 只在整体授权和预检后调用；仍逐文件检查确认期间的变化。
                result = commit_text(path, before, after, rules, context,
                                     record_edit=False, batch_approved=True)
                if not result.ok:
                    raise RuntimeError('批次写入未确认完成')
                applied.append(str(path.relative_to(self.store.root)))
            record['status'] = 'undone' if record['status']=='undoing' else 'completed'
            self.store.write(record['id']+'.json', record)
        except Exception:
            context.blockers.append('batch:'+record['id'])
            return ToolResult(False, {'batch_id':record['id'], 'applied':applied, 'uncertain':True},
                              '批次未全部确认完成；请核实文件和任务记录，再使用 /undo-batch 显式恢复，不要自动重放')
        return ToolResult(True, {'batch_id':record['id'], 'paths':applied, 'status':record['status']})

    def undo(self, ident, context):
        from .tasks import TaskStore
        with RuntimeStore(context.root, 'tasks').lock():
            context.blockers = [b for b in dict.fromkeys(context.blockers + TaskStore(context.root).blockers()) if b != 'batch:'+ident]
            try:
                return self._undo(ident, context)
            finally:
                context.blockers = list(dict.fromkeys(context.blockers + TaskStore(context.root).blockers()))

    def _undo(self, ident, context):
        record = self.load(ident)
        if record['status']=='undone':
            raise ValueError('此批次已经恢复')
        entries = []
        seen = set()
        for item in record['files']:
            path = resolve_file(context, item['path'])
            if path in seen or path.relative_to(self.store.root).parts[0] in {'.git','.fast-agent'}:
                raise ValueError('批次包含重复或内部路径')
            seen.add(path)
            current = read_bytes(path)
            if current not in (item['before'].encode('utf-8'), item['after'].encode('utf-8')):
                raise ValueError('批次文件存在外部修改，拒绝整批恢复')
            entries.append((path, current, item['before'], instructions(context.root,path)))
        if not approve(entries, context, '恢复多文件批次 '+ident):
            return ToolResult(False, error='批次恢复未获批准或存在未核实任务')
        verify(entries, context)
        record['status']='undoing'
        return self.apply(entries, context, record)


def edit_files(args, context):
    files = args['files']
    if not 1 <= len(files) <= 20:
        raise ToolInputError('一次批次支持 1～20 个已有文件')
    entries = []
    seen = set()
    total = 0
    for item in files:
        path = resolve_file(context, item['path'])
        if path in seen:
            raise ToolInputError('同一批次不能重复指定同一文件')
        seen.add(path)
        before, rules = checked_before(path, context)
        if before is None:
            raise ToolInputError('多文件批次只修改已有文件')
        text = before.decode('utf-8')
        if not unique_match(text,item['old']):
            raise ToolInputError('每个文件的 old 必须非空且唯一匹配；整批未写入')
        after = text.replace(item['old'],item['new'],1)
        size = len(after.encode('utf-8'))
        total += len(before) + size
        if size > FILE_LIMIT or total > 8*1024*1024:
            raise ToolInputError('批次快照超过单文件 1 MiB 或总计 8 MiB 上限')
        entries.append((path,before,after,rules))
    if not approve(entries, context, '修改多文件批次'):
        return ToolResult(False,error='用户未批准多文件批次')
    verify(entries, context)
    return EditBatches(context.root).apply(entries,context)


def batch_tools():
    string={'type':'string'}
    return [Tool('edit_files', '先读取全部目标；一次确认整体差异后逐文件写入，最多20个已有文件，每个old非空且唯一；不是跨文件原子事务，中断保留批次快照供用户恢复',
        object_schema({'files':{'type':'array','items':object_schema({'path':string,'old':string,'new':string},['path','old','new'])}},['files']), edit_files),
        Tool('list_edit_batches', '列出多文件批次编号、路径和状态；pending/undoing 需要人工核实，不自动重放',
             object_schema({},[]), lambda args,ctx: ToolResult(True,{'batches':EditBatches(ctx.root).list()}), 'read')]
