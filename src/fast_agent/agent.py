"""教学核心：保存模型决策，执行工具，再把结果交还模型。"""
import json
import uuid
from dataclasses import asdict
from typing import Callable
from .models import AgentResult, Event, Message, Model
from .providers import ProviderError, ContextWindowError, validate_reply
from .tools import ToolContext, ToolRegistry, ToolResult
from .workspace import system_prompt
from .compaction import ContextPolicy, compact_history, request_characters
from .sessions import SessionStore, decode_history, validate_history
from .tasks import TaskStore
from contextlib import nullcontext

SYSTEM = "你是 Fast Agent。按需使用工具完成用户任务；工具失败时如实说明，不得声称未执行的操作成功。"


def failure_signature(call, result):
    """仅比较失败语义；调用 ID 和命令日志的随机 ID 不代表进展。"""
    if result.ok:
        return None
    try:
        arguments = json.loads(call.arguments)
    except (ValueError, RecursionError):
        arguments = call.arguments
    data = dict(result.data)
    if call.name == 'run_command':
        data.pop('log_id', None)
    return json.dumps([call.name, arguments, result.error, data], ensure_ascii=False, sort_keys=True)


class Agent:
    def __init__(self, model: Model, registry: ToolRegistry, context: ToolContext,
                 max_steps: int = 10, on_event: Callable[[Event], None] | None = None,
                 stream: bool = False, context_policy: ContextPolicy | None = None, checkpoints: bool = False, plan: bool = False):
        self.set_steps(max_steps)
        self.checkpoints = checkpoints
        self.last_task_id = None
        self.stream = stream
        self.context_policy = context_policy or ContextPolicy()
        self.model = model
        self.registry = registry
        self.context = context
        if plan:
            self.context.mode = 'plan'
        self.on_event = on_event
        context.on_command_event = lambda kind, data: self.emit(kind, **data)
        if on_event is not None and callable(getattr(model, 'set_event_handler', None)):
            model.set_event_handler(on_event)
        self.reset()

    def reset(self) -> None:
        self.context.skill_reads.clear()
        self.context.skill_resource_reads.clear()
        self.context.skill_trees.clear()
        self.context.read_versions.clear()
        self.context.rule_versions.clear()
        self.history = [Message('system', system_prompt(SYSTEM, self.context.root, self.context.mode))]

    def set_mode(self, mode: str) -> None:
        """仅供本地入口调用，绝不注册为模型工具。切换不会自动发起任务。"""
        if mode not in {'plan', 'execute'}:
            raise ValueError('模式只能是 plan 或 execute')
        if mode == 'execute' and self.context.policy.mode == 'read-only':
            raise ValueError('启动权限为只读，不能通过 /execute 提升权限')
        prompt = system_prompt(SYSTEM, self.context.root, mode)
        self.context.mode = mode
        self.context.skill_reads.clear()
        self.context.skill_resource_reads.clear()
        self.context.skill_trees.clear()
        self.context.read_versions.clear()
        self.context.rule_versions.clear()
        self.history[0] = Message('system', prompt)

    def restore_history(self, history) -> None:
        """只恢复知识，不恢复历史中的执行授权。"""
        validate_history(history)
        prompt = system_prompt(SYSTEM, self.context.root, 'plan')
        self.history = [Message('system', prompt), *history[1:]]
        self.context.mode = 'plan'
        self.context.skill_reads.clear()
        self.context.skill_resource_reads.clear()
        self.context.skill_trees.clear()
        self.context.read_versions.clear()
        self.context.rule_versions.clear()

    def set_steps(self, steps):
        if type(steps) is not int or not 1 <= steps <= 1000:
            raise ValueError('每轮模型调用上限必须为 1～1000 的整数')
        self.max_steps = steps

    def continue_task(self, note=''):
        validate_history(self.history)
        if len(self.history) <= 1:
            raise ValueError('当前没有任务历史，请先输入任务或加载会话')
        if not isinstance(note, str) or len(note) > 4000:
            raise ValueError('继续任务的补充说明最多 4000 字符')
        prompt = ('请基于当前历史继续原任务，保留原目标与约束。先检查已完成结果和未完成事项；'
                  '不要仅因恢复而重复已完成操作，依据当前证据决定下一步；遵守当前模式和权限。')
        if note.strip():
            prompt += '\n用户补充说明：' + note
        return self.run(prompt)

    def continue_question(self,identifier):
        from .questions import Questions
        record=Questions(self.context.root).load(identifier)
        marker='[已接收问题回答 '+identifier+']'
        if any(m.role=='user' and m.content.startswith(marker) for m in self.history):
            raise ValueError('当前会话已经接收此回答，请使用 /continue 继续')
        related=False
        for message in self.history:
            if message.role=='tool':
                try:payload=json.loads(message.content)
                except (ValueError,TypeError):continue
                if isinstance(payload,dict) and isinstance(payload.get('data'),dict) and payload['data'].get('pending_question')==identifier:
                    related=True
        if not related:raise ValueError('当前会话没有此待回答问题，请先加载对应会话')
        if record['status']!='answered':raise ValueError('问题尚未回答')
        return self.run(marker+'\n原问题：'+record['question']+'\n用户回答：'+record['answer']+'\n此回答仅用于需求澄清，不授予执行权限；继续遵守当前模式。')

    def compact(self, focus=''):
        """本地显式摘要：先备份，全部校验成功后才替换内存历史。"""
        if not isinstance(focus, str) or len(focus) > 1000:
            raise ValueError('压缩关注点最多 1000 字符')
        with TaskStore(self.context.root).store.lock():
            validate_history(self.history)
            if len(self.history) <= 1:
                return None
            backup = SessionStore(self.context.root).save('manual-' + uuid.uuid4().hex, self.history)
            try:
                candidate, report = compact_history(self.history, self.tool_schemas(), self.model,
                    self.context_policy, lambda _: backup, force=True, focus=focus)
            except (ProviderError, ValueError, OSError) as exc:
                raise ValueError(f'手动压缩失败，原历史保留；备份：{backup}；{exc}') from exc
            except KeyboardInterrupt:
                raise ValueError(f'手动压缩已中断，原历史保留；备份：{backup}') from None
            if report is not None:
                self.history = candidate
                self.context.skill_reads.clear()
                self.context.skill_resource_reads.clear()
                self.context.skill_trees.clear()
            return report

    def tool_schemas(self):
        return self.registry.schemas(read_only=self.context.read_only)

    def emit(self, kind: str, **data) -> None:
        if self.on_event:
            self.on_event(Event(kind, data))

    def resume(self, task_id):
        store = TaskStore(self.context.root)
        with store.store.lock():
            data = store.load(task_id)
            history = decode_history(data['messages'])
            blockers = store.blockers()
            self.restore_history(history)
            self.context.blockers = blockers
            self.last_task_id = task_id

    def run(self, text: str) -> AgentResult:
        store = TaskStore(self.context.root)
        with store.store.lock() if self.checkpoints else nullcontext():
            if self.checkpoints:
                self.context.blockers = store.blockers()
                self.last_task_id = store.new_id()
                self.context.audit.clear()
                self.emit('task_start', task_id=self.last_task_id)
            return self._run(text, store)

    def _run(self, text, store) -> AgentResult:
        self.history[0] = Message('system', system_prompt(SYSTEM, self.context.root, self.context.mode))
        self.history.append(Message('user', text))
        step = 0
        pending = []
        active = None
        previous_failure = None
        repeated_failures = 0
        overflow_recovered = False
        def checkpoint(status='running'):
            if self.checkpoints:
                store.save(self.last_task_id, self.history, pending, active, status, self.context.audit)
        checkpoint()
        try:
            for step in range(1, self.max_steps + 1):
                schemas = self.tool_schemas()
                if self.context_policy.effective_chars and request_characters(self.history, schemas) > self.context_policy.effective_chars:
                    self.emit('compaction_start', characters=request_characters(self.history, schemas))
                    archived = lambda history: SessionStore(self.context.root).save('auto-' + uuid.uuid4().hex, history)
                    candidate, report = compact_history(self.history, schemas, self.model, self.context_policy, archived)
                    self.history = candidate
                    self.context.skill_reads.clear()
                    self.context.skill_resource_reads.clear()
                    self.context.skill_trees.clear()
                    self.emit('compaction_end', **report)
                checkpoint()
                self.emit('model_start', step=step)
                for recovery_attempt in range(2):
                    try:
                        emitted = False
                        if self.stream and callable(getattr(self.model, 'complete_stream', None)):
                            def on_text(content):
                                nonlocal emitted
                                first = not emitted
                                emitted = True
                                self.emit('text_delta', text=content, first=first)
                            try:
                                reply = validate_reply(self.model.complete_stream(self.history, schemas, on_text))
                            finally:
                                self.emit('stream_end', displayed=emitted)
                        else:
                            reply = validate_reply(self.model.complete(self.history, schemas))
                        break
                    except ContextWindowError:
                        if overflow_recovered or recovery_attempt or emitted:
                            raise
                        overflow_recovered = True
                        self.emit('compaction_start', characters=request_characters(self.history, schemas), reason='context_window')
                        archived = lambda history: SessionStore(self.context.root).save('overflow-' + uuid.uuid4().hex, history)
                        candidate, report = compact_history(self.history, schemas, self.model, self.context_policy, archived, force=True)
                        if report is None:
                            raise ProviderError('上下文窗口超限且没有可安全压缩的历史；原会话保留。')
                        self.history = candidate
                        self.context.skill_reads.clear()
                        self.context.skill_resource_reads.clear()
                        self.context.skill_trees.clear()
                        checkpoint()
                        self.emit('compaction_end', **report)
                self.history.append(Message('assistant', reply.content, reply.tool_calls))
                pending = list(reply.tool_calls)
                if not pending:
                    checkpoint('completed')
                    self.emit('answer', text=reply.content, streamed=emitted)
                    return AgentResult(reply.content, True, step, emitted)
                while pending:
                    call = pending[0]
                    tool = self.registry.tools.get(call.name)
                    active = call.id if tool is None or tool.effect != 'read' else None
                    checkpoint()
                    self.emit('tool_start', name=call.name, arguments=call.arguments)
                    result = self.registry.execute(call, self.context)
                    if active and result.data.get('uncertain'):
                        checkpoint('interrupted')
                        self.context.blockers.append(self.last_task_id or '当前未知操作')
                        for unresolved in pending:
                            error = result.error if unresolved.id == active else '此调用尚未执行'
                            self.history.append(Message('tool', json.dumps({'ok': False, 'error': error}, ensure_ascii=False), tool_call_id=unresolved.id))
                        self.emit('error', text='工具结果未知，已停止；请检查任务记录并人工核实。')
                        return AgentResult('工具结果未知，已停止；请检查任务记录并人工核实。', False, step)
                    signature = failure_signature(call, result)
                    from .tool_outputs import retain_result
                    result = retain_result(self.context.root, result)
                    self.history.append(Message('tool', json.dumps(asdict(result), ensure_ascii=False), tool_call_id=call.id))
                    pending.pop(0)
                    active = None
                    checkpoint()
                    self.emit('tool_end', name=call.name, result=asdict(result))
                    if call.name=='ask_user' and result.data.get('pending_question'):
                        for skipped in pending:
                            self.history.append(Message('tool',json.dumps(asdict(ToolResult(False,error='等待用户回答；此调用尚未执行')),ensure_ascii=False),tool_call_id=skipped.id))
                        pending.clear()
                        checkpoint('incomplete')
                        return AgentResult('等待用户回答：'+result.data['pending_question'],False,step)
                    repeated_failures = repeated_failures + 1 if signature is not None and signature == previous_failure else (1 if signature is not None else 0)
                    previous_failure = signature
                    if repeated_failures >= 3:
                        for skipped in pending:
                            self.history.append(Message('tool', json.dumps(asdict(ToolResult(False, error='重复失败保护已暂停；此调用尚未执行')), ensure_ascii=False), tool_call_id=skipped.id))
                        pending.clear()
                        checkpoint('paused_repeated_failure')
                        reason = (result.error or '工具返回失败')[:500]
                        text = f'检测到工具 {call.name} 连续 3 次重复失败，已暂停本轮。原因：{reason}。请核对原因后 /continue [补充说明]。'
                        if self.checkpoints:
                            text += f'检查点：{self.last_task_id}；可 --resume {self.last_task_id} 恢复，恢复后仍为计划模式。'
                        self.emit('error', text=text)
                        return AgentResult(text, False, step)

            text = f"已达到 {self.max_steps} 次模型调用上限，本轮未完成。可用 /steps 次数 调整后，再 /continue [补充说明]。"
            if self.checkpoints:
                text += f"检查点：{self.last_task_id}；新进程可 --resume {self.last_task_id} 恢复。恢复默认计划模式，执行修改前需 /execute。"
        except KeyboardInterrupt:
            # 所有已发送的调用都要有结果，下一轮才不会违反消息协议。
            checkpoint('interrupted')
            if active:
                self.context.blockers.append(self.last_task_id or '当前中断操作')
            for call in pending:
                result = ToolResult(False, error='用户中断；操作结果未知，请人工核实' if call.id == active else '用户中断；此调用尚未执行')
                self.history.append(Message('tool', json.dumps(asdict(result), ensure_ascii=False), tool_call_id=call.id))
            text = "已中断本轮，已发生的文件修改不会自动撤销。"
        except ProviderError as exc:
            text = str(exc)
        if not pending:
            checkpoint('incomplete')
        self.emit('error', text=text)
        return AgentResult(text, False, step)
