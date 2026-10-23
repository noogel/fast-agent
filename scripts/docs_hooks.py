"""仅将项目 Python 源码转换成只读参考页，不公开项目根目录。"""
from pathlib import Path
import re
from mkdocs.structure.files import File


def on_files(files, config):
    root = Path(config.config_file_path).parent
    for folder in ('src/fast_agent', 'examples'):
        for path in sorted((root / folder).glob('*.py')):
            relative = path.relative_to(root).as_posix()
            files.append(File.generated(config, f'reference/{relative}.md',
                content=f'# {path.name}\n\n源码位置：`{relative}`\n\n```python\n{path.read_text()}\n```\n'))
    return files


def on_page_markdown(markdown, page, **kwargs):
    if page.file.src_uri.startswith('tutorial/'):
        return re.sub(r'\]\(\.\./\.\./((?:src/fast_agent|examples)/[^)]+\.py)\)',
                      r'](../reference/\1.md)', markdown)
    return markdown
