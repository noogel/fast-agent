"""工具注册表：模型提供参数，程序负责校验和执行。"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from .models import ToolCall
from .permissions import PermissionPolicy


class ToolInputError(ValueError):
    """已知发生在副作用之前的业务校验失败。"""


@dataclass
class ToolResult:
    ok: bool
    data: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class ToolContext:
    root: Path
    confirm: Callable[[str], bool]
    interactive: bool
    read_versions: dict[str, tuple[str, str]] = field(default_factory=dict)
    rule_versions: dict[str, str] = field(default_factory=dict)
    policy: PermissionPolicy = field(default_factory=PermissionPolicy)
    audit: list[dict] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    mode: str = 'execute'
    sandbox: str = 'none'
    quality_checks: dict = field(default_factory=dict)
    skill_reads: dict = field(default_factory=dict)
    skill_resource_reads: dict = field(default_factory=dict)
    skill_trees: dict = field(default_factory=dict)
    ask_user: Callable[[str, list[str]], str | None] | None = None
    on_command_event: Callable[[str, dict], None] | None = None

    @property
    def read_only(self):
        return self.mode == 'plan' or self.policy.mode == 'read-only'


    def authorize(self, effect, target, prompt):
        from .jobs import JobStore
        allowed = False
        if not JobStore(self.root).blockers() and not self.blockers and not self.read_only:
            if effect == 'command' and self.policy.mode == 'restricted' and target in self.policy.commands:
                allowed = True
            elif self.interactive:
                allowed = self.confirm(prompt)
        self.audit.append({'effect': effect, 'target': target, 'allowed': bool(allowed)})
        return allowed


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict, ToolContext], ToolResult]
    effect: str = "write"


def object_schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def validate(value, schema: dict) -> None:
    """实现本项目需要的 JSON Schema 子集，不冒充通用验证器。"""
    types = {"object": dict, "string": str, "array": list, "number": (int, float), "integer": int, "boolean": bool}
    kind = schema["type"]
    if not isinstance(value, types[kind]) or (kind in {"number", "integer"} and isinstance(value, bool)):
        raise ValueError(f"参数类型应为 {kind}")
    if kind == "object":
        props = schema.get("properties", {})
        if set(schema.get("required", [])) - value.keys():
            raise ValueError("缺少必填参数")
        if not schema.get("additionalProperties", True) and value.keys() - props.keys():
            raise ValueError("包含未知参数")
        for key, item in value.items():
            if key in props:
                validate(item, props[key])
    elif kind == "array":
        for item in value:
            validate(item, schema["items"])


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self.tools:
            raise ValueError("工具名称重复")
        self.tools[tool.name] = tool

    def schemas(self, read_only: bool = False) -> list[dict]:
        return [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in self.tools.values() if not read_only or t.effect == 'read']

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        tool = self.tools.get(call.name)
        if tool is None:
            return ToolResult(False, error=f"未知工具：{call.name}")
        try:
            args = json.loads(call.arguments)
            validate(args, tool.parameters)
            from .jobs import JobStore
            if tool.effect != 'read' and (context.read_only or context.blockers or JobStore(context.root).blockers()):
                context.audit.append({'tool': call.name, 'allowed': False})
                return ToolResult(False, error='权限拒绝：计划/只读模式或存在未核实的操作结果；请人工核实任务记录')
        except (ValueError, OSError) as exc:
            return ToolResult(False, error=f"工具校验失败：{exc}")
        try:
            return tool.handler(args, context)
        except ToolInputError as exc:
            return ToolResult(False, error=f"工具执行失败：{exc}")
        except Exception as exc:
            # handler 已开始，不能从异常类型推断副作用尚未发生。
            uncertain = tool.effect != 'read'
            detail = str(exc) if not uncertain and isinstance(exc, (ValueError, OSError)) else '工具内部异常，本次操作未确认完成。'
            return ToolResult(False, {"uncertain": True} if uncertain else {}, error=detail)


def build_default_registry() -> ToolRegistry:
    from .file_tools import file_tools
    from .command_tool import command_tool
    from .repository_tools import repository_tools
    from .jobs import job_tools
    from .command_logs import command_log_tool
    from .edit_batches import batch_tools
    from .task_plans import plan_tools
    from .quality_checks import check_tools
    from .user_questions import question_tool
    from .tool_outputs import output_tool
    registry = ToolRegistry()
    for tool in [question_tool(), output_tool(), *file_tools(), *batch_tools(), *plan_tools(), *check_tools(), command_tool(), command_log_tool(), *repository_tools(), *job_tools()]:
        registry.register(tool)
    return registry
