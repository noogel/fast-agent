"""无需密钥：注册并调用一个纯函数工具。"""
from pathlib import Path
from fast_agent.models import ToolCall
from fast_agent.tools import Tool, ToolContext, ToolRegistry, ToolResult, object_schema


def count_characters(args: dict, context: ToolContext) -> ToolResult:
    return ToolResult(True, {'count': len(args['text'])})


registry = ToolRegistry()
registry.register(Tool('count_characters', '统计字符串中的字符数',
                       object_schema({'text': {'type': 'string'}}, ['text']), count_characters))
result = registry.execute(ToolCall('example-1', 'count_characters', '{"text":"你好，Agent"}'),
                          ToolContext(Path.cwd(), lambda _: False, False))
print(result)
assert result.ok and result.data['count'] == 8
