"""显式、同步、只读的探索委派；报告不自动进入主会话。"""
import uuid
from .agent import Agent
from .compaction import ContextPolicy
from .permissions import PermissionPolicy
from .delegation_reports import DelegationReports
from .tools import ToolContext, ToolRegistry, build_default_registry

READ_TOOLS = {'read_file', 'list_files', 'search_code', 'read_instructions',
              'git_status', 'git_diff', 'read_tool_output'}


def delegate(parent, task: str, *, model=None, report_id=None) -> dict:
    if not task.strip() or len(task) > 8000:
        raise ValueError('委派任务需要 1～8000 字符')
    own_model = model is not None
    model = model if own_model else parent.model
    registry = ToolRegistry()
    # 从内置实现重新构建，不能继承扩展对同名工具的替换。
    for name, tool in build_default_registry().tools.items():
        if name in READ_TOOLS and tool.effect == 'read':
            registry.register(tool)
    context = ToolContext(parent.context.root, lambda _: False, False,
                          policy=PermissionPolicy(mode='read-only'),
                          sandbox=parent.context.sandbox)
    evidence = []
    def event(item):
        if item.kind == 'tool_end':
            evidence.append({'name': item.data['name'], 'ok': item.data['result']['ok']})
    # 同一个模型客户端与 UsageTracker：子任务不能重置父进程预算。
    try:
        child = Agent(model, registry, context, max_steps=min(5, parent.max_steps),
                      on_event=event, plan=True, context_policy=ContextPolicy(max_chars=0))
        result = child.run(task)
    finally:
        setter = None if own_model else getattr(parent.model, 'set_event_handler', None)
        if callable(setter):
            setter(parent.on_event)
    report = {'id': report_id or uuid.uuid4().hex, 'task': task, 'completed': result.completed,
              'steps': result.steps, 'text': result.text[:16000],
              'text_truncated': len(result.text) > 16000, 'tools': evidence,
              'reference_only': True}
    DelegationReports(context.root).save(report)
    return report
