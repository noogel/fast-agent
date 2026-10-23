"""POSIX 命令工具：确认、超时、进程组清理和有界输出。"""
import codecs
import math
import os
import selectors
import shlex
import signal
import subprocess
import time
from .command_logs import CommandLog
from .command_sandbox import command_launch
from .tools import ToolInputError, Tool, ToolContext, ToolResult, object_schema

LIMIT = 64 * 1024


def run_command(args: dict, context: ToolContext) -> ToolResult:
    argv = args['argv']
    timeout = args.get('timeout', 10)
    if not argv or not argv[0] or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ToolInputError("命令不能为空；超时必须大于 0 且不超过 60 秒")
    if os.name != 'posix':
        raise ToolInputError("命令工具目前支持 macOS/Linux")
    if not context.authorize("command", argv, f"执行命令 {shlex.join(argv)}？"):
        return ToolResult(False, error="命令执行需要交互确认，当前请求未获批准")
    try:
        launch, env = command_launch(argv, context.root, context.sandbox)
    except ValueError as exc:
        raise ToolInputError(str(exc)) from None
    def output(text):
        if context.on_command_event:
            context.on_command_event('command_output', {'text': text})
    try:
        with CommandLog(context.root) as log:
            result = execute_process(launch, context.root, timeout, env=env, on_output=output, on_bytes=log.append)
        preview = result.data['output'].encode('utf-8')
        result.data['display_truncated'] = result.data['truncated']
        if len(preview) > 4096:
            result.data['output'] = preview[:4096].decode('utf-8', errors='ignore')
            result.data['truncated'] = True
        result.data.update(log_id=log.id, stored_bytes=log.stored, storage_truncated=log.truncated)
        return result
    finally:
        if context.on_command_event:
            context.on_command_event('command_output_end', {})

def execute_process(argv, root, timeout=10, env=None, on_output=None, on_bytes=None) -> ToolResult:
    """受限输出的通用进程执行器；调用者负责权限决策。"""
    process = subprocess.Popen(argv, cwd=root, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True, env=env)
    output = bytearray()
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    def emit(data, final=False):
        text = decoder.decode(data, final=final)
        if text and on_output:
            on_output(text)
    truncated = False
    timed_out = False
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(min(remaining, 0.05)):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if on_bytes:
                        on_bytes(chunk)
                    space = LIMIT - len(output)
                    retained = chunk[:space]
                    output.extend(retained)
                    emit(retained)
                    truncated |= len(chunk) > space
    finally:
        # 即使父命令已退出，仍清理同组后台进程，避免遗留任务。
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()
    emit(b'', final=True)
    data = {'output': output.decode('utf-8', errors='replace'), 'returncode': process.returncode, 'truncated': truncated}
    if timed_out:
        return ToolResult(False, data, "命令执行超时，已终止进程组")
    return ToolResult(process.returncode == 0, data, None if process.returncode == 0 else "命令以非零状态退出")


def command_tool() -> Tool:
    return Tool('run_command', '经用户确认执行程序和参数数组；不经 shell；默认超时 10 秒，最大 60 秒',
                object_schema({'argv': {'type': 'array', 'items': {'type': 'string'}}, 'timeout': {'type': 'number'}}, ['argv']), run_command)
