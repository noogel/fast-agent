"""编辑收据与显式撤销；pending 表示不能自动推断的中断结果。"""
from datetime import datetime, timezone
import re
import uuid
from .runtime_store import RuntimeStore


class EditHistory:
    def __init__(self, root):
        self.store = RuntimeStore(root, 'edits')

    def prepare(self, path, before, after):
        ident = uuid.uuid4().hex
        record = {'id': ident, 'path': str(path.relative_to(self.store.root)),
                  'before': before.decode('utf-8') if before is not None else None,
                  'after': after.decode('utf-8'), 'status': 'pending',
                  'created_at': datetime.now(timezone.utc).isoformat()}
        self.store.write(ident + '.json', record)
        return record

    def finish(self, record, status):
        record = {**record, 'status': status}
        self.store.write(record['id'] + '.json', record)

    def load(self, ident):
        if not re.fullmatch('[0-9a-f]{32}', ident):
            raise ValueError('编辑编号无效')
        record = self.store.read(ident + '.json')
        if (not isinstance(record, dict) or record.get('id') != ident
                or not isinstance(record.get('path'), str)
                or not isinstance(record.get('after'), str)
                or not (record.get('before') is None or isinstance(record['before'], str))
                or record.get('status') not in {'pending', 'completed', 'undoing', 'undone'}):
            raise ValueError('编辑记录损坏')
        return record

    def list(self):
        result = []
        for ident in self.store.names():
            record = self.load(ident)
            result.append({key: record.get(key) for key in ('id', 'path', 'status', 'created_at')})
        return sorted(result, key=lambda row: row['created_at'] or '', reverse=True)

    def undo(self, ident, context):
        from .file_tools import resolve_file, read_bytes, commit_text, FILE_LIMIT
        from .workspace import instructions
        from .tools import ToolInputError
        # 与 Agent.run 共用工作区执行锁，防止两个进程同时修改。
        with RuntimeStore(context.root, 'tasks').lock():
            from .tasks import TaskStore
            context.blockers = list(dict.fromkeys(context.blockers + TaskStore(context.root).blockers()))
            record = self.load(ident)
            if record['status'] != 'completed' or record['before'] is None:
                raise ValueError('仅能撤销已完成的已有文本文件编辑；中断记录需人工核实')
            path = resolve_file(context, record['path'])
            if path.relative_to(context.root.resolve()).parts[0] in {'.git', '.fast-agent'}:
                raise ValueError('禁止撤销内部记录')
            if any(len(record[key].encode('utf-8')) > FILE_LIMIT for key in ('before', 'after')):
                raise ValueError('编辑快照超过上限')
            before = read_bytes(path)
            if before != record['after'].encode('utf-8'):
                raise ValueError('文件已有后续修改，拒绝撤销覆盖')
            rules = instructions(context.root, path)
            self.finish(record, 'undoing')
            try:
                result = commit_text(path, before, record['before'], rules, context, record_edit=False)
            except ToolInputError:
                self.finish(record, 'completed')
                raise
            except Exception:
                # 文件可能已替换：保留 undoing，不能自动重试。
                context.blockers.append('撤销结果未知：' + ident)
                raise
            self.finish(record, 'undone' if result.ok else 'completed')
            return result
