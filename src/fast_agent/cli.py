"""终端入口：展示事件，不隐藏真实执行过程。"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from .agent import Agent
from .compaction import ContextPolicy
from .config import Config
from .models import AgentResult, Event
from .providers import MockModel, OpenAIModel
from .permissions import PermissionPolicy
from .tools import ToolContext, build_default_registry
from .session_commands import HELP, handle_session_command


def show_event(event: Event) -> None:
    if event.kind == 'task_start':
        print(f"[任务] {event.data['task_id']}（已启用检查点）")
    elif event.kind == 'command_output':
        text = ''.join(c if c in '\n\t' or (ord(c) >= 32 and ord(c) != 127) else f'\\x{ord(c):02x}' for c in event.data['text'])
        print(text, end='', flush=True)
    elif event.kind == 'command_output_end':
        print(flush=True)
    elif event.kind == 'model_retry':
        print(f"[重试] {event.data['reason']} 等待 {event.data['delay']} 秒，准备第 {event.data['attempt']}/{event.data['max_attempts']} 次请求", flush=True)
    elif event.kind == 'model_start':
        print(f"[模型] 第 {event.data['step']} 步")
    elif event.kind == 'text_delta':
        if event.data.get('first'):
            print('[回复] ', end='', flush=True)
        text = ''.join(c if c in '\n\t' or (ord(c) >= 32 and ord(c) != 127) else f'\\x{ord(c):02x}' for c in event.data['text'])
        print(text, end='', flush=True)
    elif event.kind == 'stream_end' and event.data['displayed']:
        print(flush=True)
    elif event.kind == 'compaction_start':
        print(f"[上下文] {event.data['characters']} 字符，正在生成历史摘要……", flush=True)
    elif event.kind == 'compaction_end':
        if event.data.get('scope') == 'current_task':
            print(f"[上下文] 已摘要当前任务较早的 {event.data['summarized_tool_batches']} 个工具批次，保留目标和最近结果。", flush=True)
        print(f"[上下文] {event.data['before']} → {event.data['after']} 字符；原会话备份：{event.data['backup']}", flush=True)
    elif event.kind in {'tool_start', 'tool_end'}:
        # JSON 转义控制字符，限制终端展示长度；历史中的结果仍保留完整限额。
        data = event.data
        if event.kind == 'tool_end' and data.get('name') == 'run_command':
            result = data['result']
            data = {**data, 'result': {**result, 'data': {k: v for k, v in result['data'].items() if k != 'output'}}}
        print(f"[{event.kind}] {json.dumps(data, ensure_ascii=False)[:2000]}", flush=True)


def confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in {'y', 'yes'}
    except EOFError:
        return False


def run_demo(on_event=show_event) -> AgentResult:
    with tempfile.TemporaryDirectory(prefix='fast-agent-demo-') as directory:
        root = Path(directory)
        sample = root / 'note.txt'
        sample.write_text('旧标题\n', encoding='utf-8')
        agent = Agent(MockModel(), build_default_registry(), ToolContext(root, lambda prompt: prompt.startswith(f'修改文件 {sample.resolve()}'), True), on_event=on_event)
        result = agent.run('演示：将 note.txt 中的旧标题改为 Fast Agent')
        # 不只相信模型措辞：由程序检查磁盘上的实际结果。
        if not result.completed or sample.read_text(encoding='utf-8') != 'Fast Agent\n':
            return AgentResult('离线演示验证失败。', False, result.steps)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Fast Agent：Python Agent 中文教学项目')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--mock', action='store_true', help='使用不联网的模拟模型')
    modes.add_argument('--demo', action='store_true', help='在临时目录运行完整离线演示')
    parser.add_argument('--workspace', type=Path, default=Path.cwd(), help='文件工具工作目录')
    parser.add_argument('--max-steps', type=int, default=10, help='每轮最多调用模型的次数，1～1000，默认 10')
    parser.add_argument('--prompt', help='执行一轮后退出（拒绝需要交互确认的操作）')
    parser.add_argument('--plan', action='store_true', help='从只读计划模式启动；交互输入 /execute 后才允许按原权限执行')
    parser.add_argument('--no-stream', action='store_true', help='关闭流式输出')
    parser.add_argument('--context-chars', type=int, default=60000, help='请求字符预算，默认 60000；0 关闭字符阈值，窗口阈值独立生效（不是 token 数）')
    parser.add_argument('--context-window', type=int, default=0, help='模型窗口 token 数；0 不启用窗口估算')
    parser.add_argument('--output-tokens', type=int, default=4096, help='窗口模式输出预留及 max_tokens 上限，默认 4096')
    parser.add_argument('--keep-rounds', type=int, default=2, help='自动压缩保留最近完整轮次，默认 2，至少 1')
    parser.add_argument('--permissions', choices=['confirm', 'read-only', 'restricted'], default='confirm', help='权限策略；默认逐次确认修改与命令')
    parser.add_argument('--mcp', type=Path, help='显式加载 stdio 或 HTTP MCP 服务，连接和调用分别授权')
    parser.add_argument('--lsp', type=Path, help='显式加载语言服务 JSON；每次符号/定义/诊断查询需批准启动')
    parser.add_argument('--skills', type=Path, help='显式启用工作区内 Skill 目录，仅提供只读发现与资源访问')
    parser.add_argument('--extensions', type=Path, help='显式加载本地进程工具扩展 JSON，仍需命令授权')
    parser.add_argument('--checks', type=Path, help='显式加载质量检查 JSON，加载不自动执行或授权')
    parser.add_argument('--sandbox', choices=['none', 'macos'], default='none', help='命令写入与网络隔离；macos 需要 sandbox-exec，不限制读取')
    parser.add_argument('--allow-commands', type=Path, help='restricted 模式的精确 argv 白名单 JSON')
    recovery = parser.add_mutually_exclusive_group()
    recovery.add_argument('--load', help='加载命名会话，进入计划模式，不重放工具')
    recovery.add_argument('--resume', help='恢复任务检查点，不重放工具；配合 --prompt 继续只读分析')
    args = parser.parse_args(argv)
    model = None
    mcp = None
    try:
        from .extensions import load_extensions
        extension_tools = load_extensions(args.extensions)
        from .quality_checks import load_checks
        checks = load_checks(args.checks)
        permissions = PermissionPolicy.load(args.permissions, args.allow_commands)
        policy = ContextPolicy(args.context_chars, args.keep_rounds, args.context_window, args.output_tokens if args.context_window else 0)
        if args.max_steps <= 0:
            raise ValueError('最大步数必须大于 0')
        if args.demo:
            result = run_demo()
            print(result.text)
            return 0 if result.completed else 1
        root = args.workspace.resolve()
        if not root.is_dir():
            raise ValueError('工作目录不存在或不是目录')
        from .command_sandbox import command_launch
        command_launch([], root, args.sandbox)
        from dataclasses import replace
        model = MockModel() if args.mock else OpenAIModel(replace(Config.from_env(os.environ), max_output_tokens=policy.output_tokens))
        interactive = args.prompt is None and sys.stdin.isatty()
        registry = build_default_registry()
        from .lsp_tools import lsp_tools
        for tool in lsp_tools(args.lsp):
            registry.register(tool)
        from .skills import skill_tools
        for tool in skill_tools(root, args.skills):
            registry.register(tool)
        for tool in extension_tools:
            registry.register(tool)
        context = ToolContext(root, confirm, interactive, policy=permissions, sandbox=args.sandbox, quality_checks=checks, mode='plan' if args.plan else 'execute')
        from .user_questions import terminal_question
        if interactive:
            context.ask_user = terminal_question
        from .mcp_tools import MCPManager
        mcp = MCPManager(args.mcp, context)
        agent = Agent(model, registry, context, args.max_steps, show_event, stream=not args.no_stream, context_policy=policy, checkpoints=True, plan=args.plan)
        if args.load:
            from .sessions import SessionStore
            agent.restore_history(SessionStore(root).load(args.load))
        if args.resume:
            agent.resume(args.resume)
        def connect_mcp():
            nonlocal mcp
            if mcp.clients and all(c.is_alive for c in mcp.clients):
                print('[MCP] 服务已连接。')
                return
            from .tasks import TaskStore
            if context.read_only or context.blockers or TaskStore(root).blockers():
                print('[MCP] 当前为只读模式或存在未核实操作；核实后切换执行模式，再输入 /mcp-connect。')
                return
            mcp.close()
            for name in list(registry.tools):
                if name.startswith('mcp_'):
                    del registry.tools[name]
            try:
                mcp = MCPManager(args.mcp, context)
                discovered = mcp.start()
                for name in list(registry.tools):
                    if name.startswith('mcp_'):
                        del registry.tools[name]
                for tool in discovered:
                    registry.register(tool)
                if args.mcp:
                    print(f'[MCP] 已发现 {len(discovered)} 个工具；每次调用仍需授权。')
            except (ValueError, OSError) as exc:
                print(f'[MCP] 未连接：{exc}；可核实配置后输入 /mcp-connect。')
        if args.mcp:
            connect_mcp()
        print('[模式] ' + ('计划模式：只读分析，/execute 切换执行' if agent.context.mode == 'plan' else '执行模式') + f'；启动权限：{permissions.mode}；命令沙箱：{args.sandbox}')
        if args.prompt is not None:
            result = agent.run(args.prompt)
            if not result.streamed:
                print(result.text)
            return 0 if result.completed else 1
        print('Fast Agent' + ('【模拟模式】' if args.mock else '') + '；' + HELP)
        while True:
            try:
                text = input('你> ').strip()
            except (EOFError, KeyboardInterrupt):
                print('\n已退出。')
                return 0
            if text == '/exit':
                return 0
            if text.split(maxsplit=1)[0:1] in [['/mcp-login'],['/mcp-auth-status'],['/mcp-logout'],['/mcp-revoke']]:
                from .mcp_oauth import action
                try:
                    command,name=text.split(maxsplit=1)
                    operation={'/mcp-login':'login','/mcp-auth-status':'status','/mcp-logout':'logout','/mcp-revoke':'revoke'}[command]
                    if operation in {'logout','revoke'}:
                        mcp.close()
                        for tool_name in list(registry.tools):
                            if tool_name.startswith('mcp_'):del registry.tools[tool_name]
                    print(json.dumps(action(mcp,name,operation),ensure_ascii=False,indent=2))
                except (ValueError,OSError) as exc:print(f'[OAuth] {exc}')
            elif text == '/mcp-connect':
                connect_mcp()
            elif text == '/mcp-status':
                print(json.dumps(mcp.status(), ensure_ascii=False, indent=2))
            elif text == '/mcp-disconnect':
                mcp.close()
                for name in list(registry.tools):
                    if name.startswith('mcp_'):del registry.tools[name]
                print('[MCP] 已断开并移除工具定义。')
            elif text == '/mcp-refresh':
                try:
                    discovered = mcp.refresh()
                    for name in list(registry.tools):
                        if name.startswith('mcp_'):del registry.tools[name]
                    for tool in discovered:registry.register(tool)
                    print(f'[MCP] 已刷新 {len(discovered)} 个工具。')
                except (ValueError, OSError) as exc:
                    if not all(row['connected'] for row in mcp.status()):
                        for name in list(registry.tools):
                            if name.startswith('mcp_'):del registry.tools[name]
                    print(f'[MCP] 刷新失败：{exc}')
            elif text == '/reset':
                agent.reset()
                print('历史已清空。')
            elif text:
                previous_history = agent.history
                handled = handle_session_command(text, agent)
                if handled and text.split(maxsplit=1)[0] in {'/load', '/resume'} and agent.history is not previous_history:
                    mcp.close()
                    for name in list(registry.tools):
                        if name.startswith('mcp_'):
                            del registry.tools[name]
                    if args.mcp:
                        print('[MCP] 已关闭旧连接；切换执行模式后 /mcp-connect 重新授权发现工具。')
                elif not handled:
                    result = agent.run(text)
                    if not result.streamed:
                        print(result.text)
    except (ValueError, OSError) as exc:
        print(f'错误：{exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('已中断。', file=sys.stderr)
        return 130

    finally:
        if mcp is not None:
            mcp.close()
        if isinstance(model, OpenAIModel):
            model.client.close()
