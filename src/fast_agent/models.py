"""内部数据结构：与任何一家模型 SDK 解耦。"""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class Message:
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass
class ModelReply:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class AgentResult:
    text: str
    completed: bool
    steps: int
    streamed: bool = False


@dataclass
class Event:
    kind: str
    data: dict


class Model(Protocol):
    def complete(self, messages: list[Message], tools: list[dict]) -> ModelReply: ...
