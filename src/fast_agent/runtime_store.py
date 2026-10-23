"""私有运行记录：有界 JSON、原子替换和进程级互斥（POSIX）。"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile

LIMIT = 16 * 1024 * 1024


class RuntimeStore:
    def __init__(self, root: Path, category: str):
        self.root = root.resolve()
        self.category = category

    def path(self, name: str, create: bool = True) -> Path:
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}', name) or name in {'.', '..'}:
            raise ValueError('运行记录名称无效')
        directory = self.root
        for part in ('.fast-agent', self.category):
            directory = directory / part
            if directory.is_symlink():
                raise ValueError('运行记录目录不能是符号链接')
            if create:
                directory.mkdir(mode=0o700, exist_ok=True)
        target = directory / name
        if target.is_symlink():
            raise ValueError('运行记录不能是符号链接')
        return target

    def write(self, name, data):
        encoded = json.dumps(data, ensure_ascii=False, indent=2).encode()
        if len(encoded) > LIMIT:
            raise ValueError('运行记录超过 16 MiB')
        target = self.path(name)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                os.fchmod(stream.fileno(), 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def read(self, name):
        with self.path(name).open('rb') as stream:
            data = stream.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError('运行记录超过 16 MiB')
        return json.loads(data)

    def names(self):
        return sorted(p.stem for p in self.path('index', create=False).parent.glob('*.json'))

    @contextmanager
    def lock(self, name='active.lock'):
        fd = os.open(self.path(name), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError('当前工作目录已有任务在执行，请等待其结束') from exc
            yield
        finally:
            os.close(fd)
