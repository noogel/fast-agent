"""后台命令的持久化句柄、有限日志和协作取消。"""
import math
import os
import shlex
import subprocess
import sys
import time
import uuid
from .runtime_store import RuntimeStore
from .tools import ToolInputError, Tool, ToolResult, object_schema

ACTIVE = {'queued', 'running'}


class JobStore:
    def __init__(self, root):
        self.store = RuntimeStore(root, 'jobs')

    def start(self, argv, timeout=300, sandbox="none"):
        if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or '\0' in a for a in argv) or not argv[0]:
            raise ToolInputError('命令必须是有效的非空 argv 数组')
        if not math.isfinite(timeout) or not 0 < timeout <= 3600:
            raise ToolInputError('后台超时必须大于 0 且不超过 3600 秒')
        from .command_sandbox import command_launch
        command_launch(argv, self.store.root, sandbox)  # 创建任务前检查支持情况
        job = uuid.uuid4().hex
        self.store.write(job + '.json', {'id': job, 'workspace': str(self.store.root), 'argv': argv,
            'sandbox': sandbox, 'timeout': timeout, 'status': 'queued', 'updated': time.time(), 'truncated': False, 'returncode': None})
        # 不把模型凭据传给命令进程；不在日志/记录中复制环境变量。
        env = {k:v for k,v in os.environ.items() if k != 'FAST_AGENT_API_KEY'}
        try:
            subprocess.Popen([sys.executable, '-m', 'fast_agent.job_worker', str(self.store.root), job],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, env=env)
        except OSError:
            state = self.store.read(job + '.json')
            state.update(status='failed', error='无法启动后台执行器')
            self.store.write(job + '.json', state)
            raise
        return job

    def poll(self, job, offset=0):
        if type(offset) is not int or offset < 0:
            raise ToolInputError('日志偏移必须为非负整数（字节）')
        state = self.store.read(job + '.json')
        if state.get('workspace') != str(self.store.root) or state.get('id') != job:
            raise ToolInputError('后台任务工作目录或 ID 不一致')
        if state['status'] in ACTIVE and time.time() - state['updated'] > 10:
            state['status'] = 'unknown'
            state['error'] = '执行器心跳丢失；结果未知，不能据此重启命令'
        path = self.store.path(job + '.log')
        output = b''
        if path.exists():
            with path.open('rb') as stream:
                stream.seek(offset)
                output = stream.read(65536)
        state.update(output=output.decode('utf-8', errors='replace'), next_offset=offset + len(output))
        return state

    def list(self):
        return [{'id': n, 'status': self.poll(n)['status']} for n in self.store.names() if len(n) == 32]

    def blockers(self):
        return ['job:' + j['id'] for j in self.list()
                if j['status'] == 'unknown' and not self.store.path(j['id'] + '.resolved').exists()]

    def resolve(self, job, note):
        if not note.strip() or len(note) > 4000:
            raise ToolInputError('请提供 1～4000 字符的人工核实说明')
        if self.poll(job)['status'] != 'unknown':
            raise ToolInputError('仅结果未知的后台任务需要人工解除阻止')
        self.store.write(job + '.resolved', {'note': note, 'updated': time.time()})

    def cancel(self, job):
        state = self.poll(job)
        if state['status'] in ACTIVE | {'unknown'}:
            self.store.write(job + '.cancel', {'requested': time.time()})
        return state


def start_command(args, context):
    argv = args['argv']
    if not context.authorize('command', argv, f'启动后台命令 {shlex.join(argv)}？'):
        return ToolResult(False, error='后台命令未获批准')
    job = JobStore(context.root).start(argv, args.get('timeout', 300), context.sandbox)
    return ToolResult(True, {'job_id': job, 'status': 'queued'})


def poll_command(args, context):
    wait = args.get('wait_seconds', 0)
    if not math.isfinite(wait) or not 0 <= wait <= 10:
        raise ToolInputError('等待时间必须在 0～10 秒之间')
    jobs = JobStore(context.root)
    deadline = time.monotonic() + wait
    while True:
        state = jobs.poll(args['job_id'], args.get('offset', 0))
        if state['status'] not in ACTIVE or time.monotonic() >= deadline:
            return ToolResult(True, state)
        time.sleep(min(.1, max(0, deadline-time.monotonic())))


def cancel_command(args, context):
    if not context.authorize('cancel', args['job_id'], f"取消后台任务 {args['job_id']}？"):
        return ToolResult(False, error='取消任务未获批准')
    state = JobStore(context.root).cancel(args['job_id'])
    return ToolResult(True, {'job_id': args['job_id'], 'status': state['status'], 'cancel_requested': True})


def job_tools():
    string = {'type': 'string'}
    return [
        Tool('start_command', '后台启动经批准的 argv 命令；返回 job_id，必须 poll_command 核实退出码；超时默认 300 秒，最大 3600', object_schema({'argv': {'type':'array','items':string}, 'timeout': {'type':'number'}}, ['argv']), start_command),
        Tool('poll_command', '查询后台状态和最多 64 KiB 日志；offset 是字节偏移，wait_seconds 最多 10 秒；成功启动不代表命令成功', object_schema({'job_id':string,'offset':{'type':'integer'},'wait_seconds':{'type':'number'}}, ['job_id']), poll_command, 'read'),
        Tool('cancel_command', '经确认请求取消后台任务；之后 poll_command 核实 cancelled 状态', object_schema({'job_id':string}, ['job_id']), cancel_command),
    ]
