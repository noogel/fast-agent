"""写前检查点：恢复消息而不重放副作用，未知结果必须人工核实。"""
from dataclasses import asdict
import json
import time
import uuid
from .models import Message
from .runtime_store import RuntimeStore
from .sessions import decode_history, validate_history


class TaskStore:
    def __init__(self, root):
        self.store = RuntimeStore(root, 'tasks')

    def save(self, task_id, history, pending, active, status, audit):
        snapshot = list(history)
        for call in pending:
            error = '操作结果未知；人工核实前禁止修改，不得自动重放' if call.id == active else '此调用尚未执行，恢复时没有重放'
            snapshot.append(Message('tool', json.dumps({'ok': False, 'data': {}, 'error': error}, ensure_ascii=False), tool_call_id=call.id))
        validate_history(snapshot)
        self.store.write(task_id + '.json', {
            'version': 1, 'workspace': str(self.store.root), 'id': task_id,
            'status': status, 'updated': time.time(), 'uncertain': [active] if active else [],
            'messages': [asdict(m) for m in snapshot], 'audit': list(audit),
        })

    def load(self, task_id):
        data = self.store.read(task_id + '.json')
        if not isinstance(data, dict) or data.get('version') != 1 or data.get('workspace') != str(self.store.root) or data.get('id') != task_id:
            raise ValueError('任务记录格式或工作目录无效')
        decode_history(data.get('messages'))
        if not isinstance(data.get('uncertain'), list) or any(not isinstance(x, str) for x in data['uncertain']):
            raise ValueError('任务未知结果字段无效')
        return data

    def list(self):
        return [{'id': name, 'status': (d := self.load(name))['status'], 'uncertain': d['uncertain']} for name in self.store.names()]

    def blockers(self):
        from .jobs import JobStore
        from .edit_batches import EditBatches
        batches = ['batch:'+d['id'] for d in EditBatches(self.store.root).list() if d['status'] in {'pending','undoing'}]
        return [d['id'] for d in self.list() if d['uncertain']] + JobStore(self.store.root).blockers() + batches

    def resolve(self, task_id, note):
        if not note.strip() or len(note) > 4000:
            raise ValueError('请提供 1～4000 字符的人工核实说明')
        with self.store.lock():
            data = self.load(task_id)
            data['resolution'] = note
            data['uncertain'] = []
            data['updated'] = time.time()
            self.store.write(task_id + '.json', data)

    @staticmethod
    def new_id():
        return uuid.uuid4().hex
