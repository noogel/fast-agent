"""同步命令私有日志：有界存储，恢复后按字节分页读取。"""
import os
import re
import uuid
from .runtime_store import RuntimeStore
from .tools import Tool, ToolInputError, ToolResult, object_schema

STORAGE_LIMIT = 1024 * 1024


class CommandLog:
    def __init__(self, root):
        self.store = RuntimeStore(root, 'command-logs')
        self.id = uuid.uuid4().hex
        self.stored = 0
        self.truncated = False

    def __enter__(self):
        fd = os.open(self.store.path(self.id + '.log'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.stream = os.fdopen(fd, 'wb')
        return self

    def append(self, chunk):
        retained = chunk[:STORAGE_LIMIT-self.stored]
        self.stream.write(retained)
        self.stored += len(retained)
        self.truncated |= len(retained) < len(chunk)

    def __exit__(self, *exc):
        try:
            self.stream.flush()
            os.fsync(self.stream.fileno())
        finally:
            self.stream.close()
        self.store.write(self.id + '.json', {'stored_bytes':self.stored, 'storage_truncated':self.truncated})


def read_command_log(args, context):
    log_id = args['log_id']
    offset, limit = args.get('offset',0), args.get('limit',4096)
    tail = args.get('tail',False)
    if not re.fullmatch('[0-9a-f]{32}', log_id):
        raise ToolInputError('日志 ID 必须为 32 位小写十六进制字符')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 65536:
        raise ToolInputError('偏移必须非负，读取长度必须为 1～65536 字节')
    if tail and 'offset' in args:
        raise ToolInputError('tail 与 offset 不能同时指定')
    store = RuntimeStore(context.root, 'command-logs')
    path = store.path(log_id + '.log', create=False)
    # 没有元数据可能表示命令仍在运行或进程中断，不能误报日志完整。
    metadata_path = store.path(log_id + '.json', create=False)
    metadata = store.read(log_id+'.json') if metadata_path.exists() else {}
    with path.open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        if size > STORAGE_LIMIT:
            raise ToolInputError('日志文件超过存储上限')
        start = max(0,size-limit) if tail else min(offset,size)
        stream.seek(start)
        data = stream.read(min(limit,size-start))
    return ToolResult(True, {'log_id':log_id,'output':data.decode('utf-8',errors='replace'),
        'offset':start,'next_offset':start+len(data),'stored_bytes':size,'eof':start+len(data)>=size,
        'storage_truncated':metadata.get('storage_truncated'), 'finalized':bool(metadata)})


def command_log_tool():
    return Tool('read_command_log','只读同步命令日志；offset 为字节偏移，tail=true 读取已保存日志尾部，两者不能同时使用；limit 默认4096、最大65536；存储最多1MiB，截断后尾部不是原始输出末尾',
        object_schema({'log_id':{'type':'string'},'offset':{'type':'integer'},'limit':{'type':'integer'},'tail':{'type':'boolean'}},['log_id']),read_command_log,'read')
