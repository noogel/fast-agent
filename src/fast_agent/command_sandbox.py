"""命令启动边界；macOS 模式限制写入和网络，不承诺读取隔离。"""
import json
import os
from pathlib import Path
import sys


def command_launch(argv, root, mode='none'):
    if mode not in {'none', 'macos'}:
        raise ValueError('未知命令沙箱模式')
    # 无沙箱模式同样不能把模型凭据自动传给子进程。
    env = {key: value for key, value in os.environ.items() if key != 'FAST_AGENT_API_KEY'}
    if mode == 'none':
        return list(argv), env
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').is_file():
        raise ValueError('macos 命令沙箱需要 macOS 的 sandbox-exec；不会退回普通执行')
    root = Path(root).resolve()
    quote = lambda path: json.dumps(str(path), ensure_ascii=False)
    profile = '(version 1)(allow default)(deny network*)'
    profile += f'(deny file-write* (require-all (require-not (subpath {quote(root)})) (require-not (literal "/dev/null"))))'
    for name in ('.git', '.fast-agent', '.env'):
        profile += f'(deny file-write* (subpath {quote(root / name)}))'
    # macOS 沙箱模式仅保留构建常用基础变量，防止环境中的其它凭据泄露。
    env = {key: value for key, value in env.items()
           if key in {'PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'TERM'}}
    env.update(HOME=str(root), TMPDIR=str(root), PYTHONDONTWRITEBYTECODE='1')
    return ['/usr/bin/sandbox-exec', '-p', profile, *argv], env
