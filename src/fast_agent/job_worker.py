"""独立执行器：终端退出后继续运行；取消/超时清理整个进程组。"""
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from .runtime_store import RuntimeStore
from .command_sandbox import command_launch

LOG_LIMIT = 1024 * 1024


def run(root, job):
    store = RuntimeStore(Path(root), 'jobs')
    with store.lock(job + '.lock'):
        state = store.read(job + '.json')
        if state['status'] != 'queued':
            return  # 永远不重启已经运行过的命令
        if store.path(job + '.cancel').exists():
            state.update(status='cancelled', updated=time.time())
            store.write(job + '.json', state)
            return
        process = None
        try:
            state.update(status='running', updated=time.time())
            store.write(job + '.json', state)
            launch, env = command_launch(state['argv'], root, state.get('sandbox', 'none'))
            process = subprocess.Popen(launch, cwd=root, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True, env=env)
            deadline = time.monotonic() + state['timeout']
            last_heartbeat = 0
            size = 0
            fd = os.open(store.path(job + '.log'), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb', buffering=0) as log, selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map() or process.poll() is None:
                    if store.path(job + '.cancel').exists():
                        state['status'] = 'cancelled'
                        break
                    if time.monotonic() >= deadline:
                        state['status'] = 'timed_out'
                        break
                    if time.monotonic() - last_heartbeat >= .5:
                        state['updated'] = time.time()
                        store.write(job + '.json', state)
                        last_heartbeat = time.monotonic()
                    for key, _ in selector.select(.05):
                        chunk = os.read(key.fileobj.fileno(), 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        kept = chunk[:max(0, LOG_LIMIT-size)]
                        log.write(kept)
                        size += len(kept)
                        state['truncated'] |= len(kept) < len(chunk)
                if state['status'] == 'running':
                    state['status'] = 'succeeded' if process.poll() == 0 else 'failed'
        except Exception:
            state.update(status='unknown' if process else 'failed', error='后台执行器异常，请检查日志；不要自动重放')
        finally:
            if process:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                process.stdout.close()
                state['returncode'] = process.returncode
            state['updated'] = time.time()
            store.write(job + '.json', state)


if __name__ == '__main__':
    run(sys.argv[1], sys.argv[2])
