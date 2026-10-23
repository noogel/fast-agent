"""模型适配：协议转换在这里完成，Agent 不接触 SDK 对象。"""
from .config import Config
from .models import Event, Message, ModelReply, ToolCall
import time
from .request_policy import retryable, retry_delay


class ProviderError(Exception):
    """可安全展示给用户的模型错误。"""


class ContextWindowError(ProviderError):
    """服务明确拒绝上下文长度，且尚未输出任何响应片段。"""


def context_window_rejection(exc):
    if getattr(exc, 'status_code', None) not in (400, 413, 422):
        return False
    body = getattr(exc, 'body', None)
    if not isinstance(body, dict):
        return False
    error = body.get('error', body)
    if not isinstance(error,dict):return False
    return error.get('code') in ('context_length_exceeded', 'context_window_exceeded') or (
        exc.status_code==400 and error.get('type')=='bad_request_error'
        and error.get('message')=='invalid params, context window exceeds limit (2013)')


def error_hint(exc: Exception) -> str:
    """只映射状态与已知异常类型，不展示请求或供应商原始文本。"""
    status = getattr(exc, 'status_code', None)
    if status in (401, 403):
        return '认证失败，请检查密钥和权限。'
    if status == 429:
        return '请求受限，请检查配额或稍后重试。'
    if status in (400, 404, 422):
        return '请求被拒绝，请检查接口、模型名称及工具调用支持。'
    from openai import APITimeoutError, APIConnectionError
    import httpx2
    if isinstance(exc, (APITimeoutError, httpx2.TimeoutException)):
        return '模型请求超时，请检查网络或稍后重试。'
    if isinstance(exc, (APIConnectionError, httpx2.NetworkError, httpx2.RemoteProtocolError)):
        return '模型连接失败，请检查网络与代理。'
    return '模型调用失败，请检查网络、服务状态或响应协议。'


def validate_reply(reply: ModelReply) -> ModelReply:
    ids = [call.id for call in reply.tool_calls]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ProviderError("模型返回了缺失或重复的工具调用 ID。")
    if any(not isinstance(c.name, str) or not c.name or not isinstance(c.arguments, str) for c in reply.tool_calls):
        raise ProviderError("模型返回了无效的工具调用格式。")
    if reply.content is not None and not isinstance(reply.content, str):
        raise ProviderError("模型返回了无效的文本内容。")
    if not reply.tool_calls and not (reply.content or "").strip():
        raise ProviderError("模型返回空回复，本轮未完成。")
    return reply


def to_wire(message: Message) -> dict:
    result = {"role": message.role, "content": message.content}
    if message.tool_calls:
        result["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in message.tool_calls
        ]
    if message.tool_call_id is not None:
        result["tool_call_id"] = message.tool_call_id
    return result


class OpenAIModel:
    def __init__(self, config: Config, client=None):
        from .usage import UsageTracker
        self.usage = UsageTracker(config.max_requests)
        self.config = config
        self.on_event = None
        if client is None:
            from openai import OpenAI, Timeout
            timeout = Timeout(config.read_timeout, connect=config.connect_timeout,
                              write=config.connect_timeout, pool=config.connect_timeout)
            # SDK 内部重试关闭，统一由此层控制次数、流式边界和可观察事件。
            client = OpenAI(api_key=config.api_key, base_url=config.base_url, timeout=timeout, max_retries=0)
        self.client = client

    def fork(self):
        """独立 HTTP 客户端和事件处理器，共享原子请求预算。"""
        child = OpenAIModel(self.config)
        child.usage = self.usage
        return child

    def set_event_handler(self, handler):
        self.on_event = handler

    def _request(self, operation, progress):
        attempts = self.config.max_retries + 1
        for attempt in range(1, attempts + 1):
            request_id = self.usage.begin()
            try:
                return operation(request_id)
            except ProviderError:
                raise  # 参数、截断、协议校验失败不重试
            except Exception as exc:
                if not progress['started'] and context_window_rejection(exc):
                    raise ContextWindowError('模型上下文窗口超限。') from None
                delay = None
                if not progress['started'] and attempt < attempts and retryable(exc):
                    delay = retry_delay(exc, attempt)
                if delay is None:
                    suffix = '已收到响应内容，不自动重试；本次响应中的工具未执行。' if progress['started'] else ''
                    raise ProviderError(f'{error_hint(exc)}已尝试 {attempt} 次。{suffix}') from None
                if self.on_event:
                    self.on_event(Event('model_retry', {'attempt': attempt + 1, 'max_attempts': attempts,
                        'delay': round(delay, 2), 'reason': error_hint(exc)}))
                # KeyboardInterrupt 不被捕获；等待期间 Ctrl+C 会立即停止。
                time.sleep(delay)

    def complete_stream(self, messages: list[Message], tools: list[dict], on_text) -> ModelReply:
        from .streaming import collect_stream
        kwargs = {'model': self.config.model, 'messages': [to_wire(m) for m in messages], 'stream': True,
                  'stream_options': {'include_usage': True}}
        if self.config.max_output_tokens:
            kwargs['max_tokens'] = self.config.max_output_tokens
        if tools:
            kwargs['tools'] = tools
        progress = {'started': False}
        def operation(request_id):
            stream = self.client.chat.completions.create(**kwargs)
            def observed():
                for chunk in stream:
                    # 即使是角色/推理/工具参数片段，也不允许断流后偷偷重新生成。
                    progress['started'] = True
                    self.usage.observe(request_id, getattr(chunk, 'usage', None))
                    yield chunk
            try:
                return collect_stream(observed(), on_text)
            finally:
                stream.close()
        return self._request(operation, progress)

    def complete(self, messages: list[Message], tools: list[dict]) -> ModelReply:
        kwargs = {'model': self.config.model, 'messages': [to_wire(m) for m in messages]}
        if self.config.max_output_tokens:
            kwargs['max_tokens'] = self.config.max_output_tokens
        if tools:
            kwargs['tools'] = tools
        progress = {'started': False}
        def operation(request_id):
            response = self.client.chat.completions.create(**kwargs)
            progress['started'] = True
            self.usage.observe(request_id, getattr(response, 'usage', None))
            choice = response.choices[0]
            if getattr(choice, 'finish_reason', None) not in {None, 'stop', 'tool_calls'}:
                raise ProviderError('模型输出未正常完成，可能被截断或过滤；本轮未完成。')
            message = choice.message
            calls = [ToolCall(c.id, c.function.name, c.function.arguments) for c in message.tool_calls or []]
            return validate_reply(ModelReply(message.content, calls))
        return self._request(operation, progress)


class MockModel:
    """确定性的教学替身：由本轮实际工具结果推进，不进行语言推理。"""
    def complete(self, messages: list[Message], tools: list[dict]) -> ModelReply:
        import json
        start = max(i for i, m in enumerate(messages) if m.role == 'user')
        if messages[start].content != '演示：将 note.txt 中的旧标题改为 Fast Agent':
            return ModelReply('【模拟模式】这里不进行真实推理。使用 --demo 查看完整文件工具循环。')
        results = [json.loads(m.content) for m in messages[start + 1:] if m.role == 'tool']
        if any(not r['ok'] for r in results):
            return ModelReply('【模拟模式】工具执行失败，未完成修改。')
        if not results:
            call = ToolCall('demo-read', 'read_file', json.dumps({'path': 'note.txt'}))
        elif len(results) == 1:
            if results[0]['data'].get('content') != '旧标题\n':
                return ModelReply('【模拟模式】示例内容不符合预期，未修改。')
            call = ToolCall('demo-edit', 'edit_file', json.dumps({'path': 'note.txt', 'old': '旧标题', 'new': 'Fast Agent'}))
        elif len(results) == 2:
            call = ToolCall('demo-verify', 'read_file', json.dumps({'path': 'note.txt'}))
        else:
            if results[-1]['data'].get('content') != 'Fast Agent\n':
                return ModelReply('【模拟模式】复读验证失败。')
            return ModelReply('【模拟模式】已将标题修改为 Fast Agent，并通过再次读取验证。')
        return ModelReply(tool_calls=[call])
