"""权限由本地入口设置；模型参数不能提升权限。"""
from dataclasses import dataclass, field
import json
from pathlib import Path


@dataclass(frozen=True)
class PermissionPolicy:
    mode: str = 'confirm'
    commands: list[list[str]] = field(default_factory=list)

    def __post_init__(self):
        if self.mode not in {'confirm', 'read-only', 'restricted'}:
            raise ValueError('未知权限模式')
        if not isinstance(self.commands, list) or any(not isinstance(a, list) or not a or any(not isinstance(x, str) for x in a) for a in self.commands):
            raise ValueError('命令白名单必须是非空 argv 数组的列表')

    @classmethod
    def load(cls, mode: str, path: Path | None):
        if path is None:
            return cls(mode)
        if mode != 'restricted':
            raise ValueError('仅 restricted 模式接受命令白名单')
        if path.stat().st_size > 65536:
            raise ValueError('权限配置超过 64 KiB')
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or set(data) != {'commands'}:
            raise ValueError('权限文件必须只有 commands 字段')
        return cls(mode, data['commands'])
