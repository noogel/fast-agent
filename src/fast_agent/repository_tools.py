"""只读仓库工具：有界检索、规则查询及 Git 检查。"""
import json
import fnmatch
from functools import lru_cache
import os
import shutil
from pathlib import Path
from .command_tool import execute_process
from .tools import Tool, ToolResult, object_schema
from .workspace import inside, instructions, instruction_fingerprint

EXCLUDE = ['.git', '.venv', 'venv', 'node_modules', '.fast-agent', '.idea', '__pycache__', '.env*']


def limit_value(args):
    value = args.get('limit', 100)
    if not 1 <= value <= 500:
        raise ValueError('结果数量限制必须为 1～500')
    return value


def rg_args(context, args):
    executable = shutil.which('rg')
    if not executable:
        raise ValueError('代码检索需要 ripgrep（rg），请先安装')
    target = inside(context.root, args.get('path', '.'))
    if not target.is_dir():
        raise ValueError('检索路径必须是工作目录内的目录')
    patterns = [item for name in EXCLUDE for item in ['--glob', f'!{name}']]
    return [executable, '--no-config', '--no-follow', '--hidden', '--no-require-git', *patterns], str(target.relative_to(context.root.resolve()))


def list_files(args, context):
    limit = limit_value(args)
    argv, target = rg_args(context, args)
    result = execute_process([*argv, '--files', '--null', '--', target], context.root)
    if result.data.get('returncode') not in {0, 1} or '超时' in (result.error or ''):
        return result
    files = [str(Path(name)) for name in result.data['output'].split('\0')[:-1]]
    return ToolResult(True, {'files': files[:limit], 'truncated': result.data['truncated'] or len(files) > limit})


def matches_glob(path, pattern):
    """以工作区为根：* 不跨目录，** 匹配零个或多个目录。"""
    names, parts = tuple(Path(path).parts), tuple(pattern.split('/'))
    @lru_cache(None)
    def match(i, j):
        if j == len(parts):
            return i == len(names)
        if parts[j] == '**':
            return match(i, j + 1) or (i < len(names) and match(i + 1, j))
        return i < len(names) and fnmatch.fnmatchcase(names[i], parts[j]) and match(i + 1, j + 1)
    return match(0, 0)


def search_code(args, context):
    limit = limit_value(args)
    if not args['query'] or '\n' in args['query']:
        raise ValueError('搜索文本必须非空且为单行')
    around = args.get('context_lines', 0)
    if not 0 <= around <= 5:
        raise ValueError('context_lines 必须为 0～5')
    pattern = args.get('glob')
    if pattern is not None and (not pattern or len(pattern) > 256 or
            any(part in {'', '.', '..'} for part in pattern.split('/')) or
            pattern.startswith('!') or '\\' in pattern):
        raise ValueError('glob 必须为工作区内相对路径模式，不能包含空段、.. 或反斜杠')
    argv, target = rg_args(context, args)
    mode = [] if args.get('regex', False) else ['--fixed-strings']
    result = execute_process([*argv, '--json', *mode, '--context', str(around),
                              '--max-filesize', '1M', '--max-count', str(limit + 1),
                              '--', args['query'], target], context.root)
    if result.data.get('returncode') not in {0, 1} or '超时' in (result.error or ''):
        return result
    entries, selected = {}, []
    truncated = result.data['truncated']
    for line in result.data['output'].splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            truncated = True
            continue
        if event.get('type') not in {'match', 'context'}:
            continue
        data = event['data']
        if 'text' not in data['path'] or 'text' not in data['lines']:
            truncated = True
            continue
        path = str(Path(data['path']['text']))
        if pattern is not None and not matches_glob(path, pattern):
            continue
        text = data['lines']['text']
        row = {'line': data['line_number'], 'text': text[:500]}
        if len(text) > 500:
            row['text_truncated'] = True
            truncated = True
        entries[(path, row['line'])] = row
        if event['type'] == 'match':
            selected.append((path, row['line']))
    truncated |= len(selected) > limit
    matches = []
    for path, number in selected[:limit]:
        row = {'path': path, **entries[(path, number)]}
        if around:
            row['before'] = [entries[(path, n)] for n in range(max(1, number-around), number) if (path, n) in entries]
            row['after'] = [entries[(path, n)] for n in range(number+1, number+around+1) if (path, n) in entries]
        candidate = {'matches': [*matches, row], 'truncated': False}  # false 比 true 多一个字节
        if len(json.dumps(candidate, ensure_ascii=False).encode('utf-8')) > 64 * 1024:
            truncated = True
            break
        matches.append(row)
    return ToolResult(True, {'matches': matches, 'truncated': bool(truncated)})


def read_instructions(args, context):
    target = inside(context.root, args['path'])
    rules = instructions(context.root, target)
    context.rule_versions[str(target)] = instruction_fingerprint(rules)
    return ToolResult(True, {'instructions': rules})


def git_read(args, context, diff=False):
    # 清理可能改变 Git 目标的环境变量，禁用 fsmonitor 和外部 diff/textconv。
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env['GIT_TERMINAL_PROMPT'] = '0'
    base = ['git', '--no-pager', '--no-optional-locks', '--literal-pathspecs', '-c', 'core.fsmonitor=false']
    configured = execute_process([*base, 'config', '--null', '--name-only', '--get-regexp', '^filter\\.'], context.root, env=env)
    if configured.data.get('returncode') not in {0, 1} or configured.data.get('truncated') or '超时' in (configured.error or ''):
        return ToolResult(False, error='无法完整检查 Git 过滤器配置，拒绝执行')
    prefixes = {name.rsplit('.', 1)[0] for name in configured.data['output'].split('\0') if name}
    if len(prefixes) > 256:
        return ToolResult(False, error='Git 过滤器配置过多，拒绝执行')
    for prefix in sorted(prefixes):
        base.extend(['-c', prefix + '.clean=', '-c', prefix + '.process=', '-c', prefix + '.required=false'])
    target = inside(context.root, args.get('path', '.'))
    relative = str(target.relative_to(context.root.resolve()))
    if diff:
        command = ['diff', '--no-ext-diff', '--no-textconv', '--no-color']
        if args.get('staged', False):
            command.append('--cached')
    else:
        command = ['status', '--short', '--untracked-files=normal']
    return execute_process([*base, *command, '--', relative], context.root, env=env)


def repository_tools():
    string = {'type': 'string'}
    discovery = {'path': string, 'limit': {'type': 'integer'}}
    return [
        Tool('list_files', '列出代码文件，遵循忽略规则，默认最多 100 项，最大 500；排除凭据和运行目录', object_schema(discovery, []), list_files, 'read'),
        Tool('search_code', '默认固定文本；regex=true 使用 Rust 正则；glob 为工作区相对路径筛选（* 不跨目录，** 跨目录），context_lines=0～5 返回上下文。遵循忽略规则；默认100项最大500，truncated=true 时缩小 path/query；编辑前仍需 read_file', object_schema({**discovery, 'query': string, 'glob': string, 'regex': {'type': 'boolean'}, 'context_lines': {'type': 'integer'}}, ['query']), search_code, 'read'),
        Tool('read_instructions', '读取目标文件从工作目录开始沿途的 AGENTS.md；创建文件前必须调用', object_schema({'path': string}, ['path']), read_instructions, 'read'),
        Tool('git_status', '只读查看工作目录内 Git 状态', object_schema({}, []), git_read, 'read'),
        Tool('git_diff', '只读查看工作目录内差异，staged=true 查看暂存区；可限定文件路径', object_schema({'path': string, 'staged': {'type': 'boolean'}}, []), lambda args, ctx: git_read(args, ctx, True), 'read'),
    ]
