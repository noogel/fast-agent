"""会话持久化与整轮裁剪：保留工具调用和结果之间的关联。"""
from __future__ import annotations
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import tempfile
import stat
from datetime import datetime, timezone

from .models import Message, ToolCall
from .providers import to_wire

MAX_SESSION_BYTES = 16 * 1024 * 1024
NAME = re.compile(r'[\w-]{1,64}', re.UNICODE)


def validate_history(history: list[Message]) -> None:
    """验证可继续发送给模型的消息序列，包括中断后已补齐结果的轮次。"""
    if not history or history[0].role != 'system' or not isinstance(history[0].content, str):
        raise ValueError('会话必须以系统消息开始')
    pending: set[str] = set()
    has_user = False
    previous = None
    for index, message in enumerate(history):
        if message.role not in {'system', 'user', 'assistant', 'tool'}:
            raise ValueError('未知消息角色')
        if message.content is not None and not isinstance(message.content, str):
            raise ValueError('消息内容必须为文本或 null')
        if not isinstance(message.tool_calls, list):
            raise ValueError('工具调用必须是列表')
        if message.role != 'assistant' and message.tool_calls:
            raise ValueError('只有 assistant 可以发起工具调用')
        if message.role != 'tool' and message.tool_call_id is not None:
            raise ValueError('只有 tool 消息可以携带结果关联 ID')
        if pending and message.role != 'tool':
            raise ValueError('工具请求缺少配对结果')
        if message.role == 'system':
            if index != 0:
                raise ValueError('系统消息只能位于开头')
        elif message.role == 'user':
            if not isinstance(message.content, str):
                raise ValueError('用户消息必须为文本')
            has_user = True
        elif message.role == 'assistant':
            if not has_user or previous not in {'user', 'tool'}:
                raise ValueError('assistant 消息顺序错误')
            ids = []
            for call in message.tool_calls:
                if not isinstance(call, ToolCall) or not all(isinstance(v, str) and v for v in (call.id, call.name)) or not isinstance(call.arguments, str):
                    raise ValueError('工具调用格式无效')
                ids.append(call.id)
            if len(ids) != len(set(ids)):
                raise ValueError('工具调用 ID 重复')
            pending = set(ids)
            if not pending and not (message.content or '').strip():
                raise ValueError('assistant 回复为空')
        else:
            if not isinstance(message.tool_call_id, str) or message.tool_call_id not in pending:
                raise ValueError('工具结果没有配对的调用 ID')
            if not isinstance(message.content, str):
                raise ValueError('工具结果必须为文本')
            pending.remove(message.tool_call_id)
        previous = message.role
    if pending:
        raise ValueError('会话包含尚未返回结果的工具调用')


def rounds(history: list[Message]) -> list[list[Message]]:
    validate_history(history)
    groups: list[list[Message]] = []
    for message in history[1:]:
        if message.role == 'user':
            groups.append([])
        groups[-1].append(message)
    return groups


def trim_history(history: list[Message], keep: int) -> list[Message]:
    if type(keep) is not int or keep < 0:
        raise ValueError('保留轮数必须是非负整数')
    groups = rounds(history)
    selected = groups[-keep:] if keep else []
    return [history[0], *(m for group in selected for m in group)]


def context_stats(history: list[Message]) -> dict:
    groups = rounds(history)
    return {'messages': len(history), 'rounds': len(groups),
            'completed_rounds': sum(g[-1].role == 'assistant' and not g[-1].tool_calls for g in groups),
            'characters': len(json.dumps([to_wire(m) for m in history], ensure_ascii=False))}


def format_history(history: list[Message], limit: int = 500) -> str:
    lines = []
    for index, message in enumerate(history, 1):
        text = json.dumps(to_wire(message), ensure_ascii=False)
        calls = ', '.join(json.dumps({'id': c.id, 'name': c.name}, ensure_ascii=False) for c in message.tool_calls)
        label = f'{index}. [{message.role}]' + (f' 工具调用 {calls}' if calls else '')
        lines.append(f'{label} {text[:limit]}' + (' …（展示已截断）' if len(text) > limit else ''))
    return '\n'.join(lines)


def decode_history(raw) -> list[Message]:
    if not isinstance(raw, list):
        raise ValueError('消息字段必须为列表')
    history = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {'role', 'content', 'tool_calls', 'tool_call_id'} or not isinstance(item['tool_calls'], list):
            raise ValueError('保存的消息字段无效')
        calls = []
        for call in item['tool_calls']:
            if not isinstance(call, dict) or set(call) != {'id', 'name', 'arguments'}:
                raise ValueError('保存的工具调用字段无效')
            calls.append(ToolCall(**call))
        history.append(Message(item['role'], item['content'], calls, item['tool_call_id']))
    validate_history(history)
    return history


class SessionStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def directory(self, create=False):
        directory = self.workspace
        for part in ('.fast-agent', 'sessions'):
            directory = directory / part
            if directory.is_symlink():
                raise ValueError('会话目录不能是符号链接')
            if create:
                directory.mkdir(mode=0o700, exist_ok=True)
        return directory

    def path(self, name: str, create: bool = False) -> Path:
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError('会话名称只能含字母、数字、汉字、下划线或短横线，长度 1～64')
        path = self.directory(create) / f'{name}.json'
        if path.is_symlink():
            raise ValueError('会话文件不能是符号链接')
        return path

    def save(self, name: str, history: list[Message]) -> Path:
        validate_history(history)
        data = {'version': 1, 'workspace': str(self.workspace), 'messages': [asdict(m) for m in history]}
        encoded = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        if len(encoded) > MAX_SESSION_BYTES:
            raise ValueError('会话超过 16 MiB，请先裁剪历史')
        path = self.path(name, create=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.saving-', delete=False) as stream:
                temporary = Path(stream.name)
                os.fchmod(stream.fileno(), 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return path

    def list(self, page=1, include_backups=False):
        """仅列元数据；加载时才解析正文并验证协议。"""
        if type(page) is not int or not 1 <= page <= 1000000:
            raise ValueError('页码必须为 1～1000000 的整数')
        directory = self.directory()
        entries = []
        for path in directory.glob('*.json'):
            if not NAME.fullmatch(path.stem):
                continue
            if not include_backups and path.stem.startswith(('auto-', 'manual-')):
                continue
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue  # 列表过程中被移走的文件留待下一次刷新。
            if not stat.S_ISREG(info.st_mode):
                continue
            entries.append({'name': path.stem, 'bytes': info.st_size,
                            'updated_at': datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                            '_order': info.st_mtime_ns})
        entries.sort(key=lambda row: (-row['_order'], row['name']))
        selected = entries[(page-1)*20:page*20]
        return {'sessions': [{k:v for k,v in row.items() if k != '_order'} for row in selected],
                'page': page, 'page_size': 20, 'total': len(entries),
                'has_more': page*20 < len(entries), 'include_backups': include_backups}

    def load(self, name: str) -> list[Message]:
        path = self.path(name)
        if not path.is_file():
            raise ValueError('会话文件不存在或不是普通文件')
        with path.open('rb') as stream:
            encoded = stream.read(MAX_SESSION_BYTES + 1)
        if len(encoded) > MAX_SESSION_BYTES:
            raise ValueError('会话文件超过 16 MiB')
        try:
            data = json.loads(encoded)
            if not isinstance(data, dict) or set(data) != {'version', 'workspace', 'messages'}:
                raise ValueError('会话文件字段无效')
            if type(data['version']) is not int or data['version'] != 1:
                raise ValueError('不支持的会话版本')
            if data['workspace'] != str(self.workspace):
                raise ValueError('会话工作目录不一致，拒绝加载')
            return decode_history(data['messages'])
        except (TypeError, KeyError, UnicodeError, RecursionError) as exc:
            raise ValueError('会话内容格式无效') from exc
