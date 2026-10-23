"""流式协议组装：展示文本增量，完整结束之后才交出工具请求。"""
import json
from .models import ModelReply, ToolCall
from .providers import ProviderError, validate_reply

MAX_OUTPUT = 1024 * 1024


def collect_stream(stream, on_text) -> ModelReply:
    text = []
    calls = {}
    finished = None
    size = 0
    try:
        for chunk in stream:
            choices = chunk.choices
            if not choices:  # 部分服务在末尾单独发送用量信息。
                continue
            if len(choices) != 1 or choices[0].index != 0:
                raise ProviderError('流式回复包含不支持的多个候选')
            choice = choices[0]
            delta = choice.delta
            content = getattr(delta, 'content', None)
            updates = getattr(delta, 'tool_calls', None) or []
            if finished is not None and (content or updates or choice.finish_reason is not None):
                raise ProviderError('结束标记之后仍收到模型内容')
            if content:
                if not isinstance(content, str):
                    raise ProviderError('流式文本格式错误')
                size += len(content.encode('utf-8'))
                if size > MAX_OUTPUT:
                    raise ProviderError('流式回复超过 1 MiB 上限')
                text.append(content)
                on_text(content)
            for update in updates:
                index = update.index
                if type(index) is not int or not 0 <= index < 128:
                    raise ProviderError('工具调用索引无效或数量过多')
                entry = calls.setdefault(index, {'id': '', 'name': '', 'arguments': ''})
                function = update.function
                values = {'id': update.id, 'name': getattr(function, 'name', None),
                          'arguments': getattr(function, 'arguments', None)}
                for key, value in values.items():
                    if value is not None:
                        if not isinstance(value, str):
                            raise ProviderError('工具调用片段格式无效')
                        size += len(value.encode('utf-8'))
                        if size > MAX_OUTPUT:
                            raise ProviderError('流式回复超过 1 MiB 上限')
                        entry[key] += value
            if choice.finish_reason is not None:
                if choice.finish_reason not in {'stop', 'tool_calls'}:
                    raise ProviderError('流式输出未正常完成，可能被截断或过滤；工具未执行')
                finished = choice.finish_reason
        if finished is None:
            raise ProviderError('流式连接结束但缺少完成标记；工具未执行')
        if calls and sorted(calls) != list(range(len(calls))):
            raise ProviderError('流式工具索引不连续')
        assembled = [ToolCall(**calls[index]) for index in sorted(calls)]
        for call in assembled:
            try:
                arguments = json.loads(call.arguments)
            except ValueError:
                raise ProviderError('流式工具参数不完整或不是有效 JSON；工具未执行') from None
            if not isinstance(arguments, dict):
                raise ProviderError('流式工具参数必须为 JSON 对象')
        return validate_reply(ModelReply(''.join(text) or None, assembled))
    finally:
        stream.close()
