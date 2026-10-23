"""有界并发只读委派：每个任务独立客户端，取消在模型边界生效。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import uuid
from .delegation_reports import DelegationReports
from .delegation import delegate
from .providers import ProviderError


class CancellableModel:
    def __init__(self, model, cancelled):
        self.model, self.cancelled = model, cancelled

    def complete(self, messages, tools):
        if self.cancelled.is_set():
            raise ProviderError('委派已取消；未发送新请求。')
        reply = self.model.complete(messages, tools)
        if self.cancelled.is_set():
            raise ProviderError('委派已取消；返回中的工具不会执行。')
        return reply

    def set_event_handler(self, handler):
        self.model.set_event_handler(handler)


def _execute(parent, tasks, identifiers, *, workers=2, cancelled=None):
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 5:
        raise ValueError('并发委派需要 1～5 个任务')
    if any(not isinstance(t, str) or not t.strip() or len(t)>8000 for t in tasks):
        raise ValueError('每个委派任务需要 1～8000 字符')
    if type(workers) is not int or not 1 <= workers <= 3:
        raise ValueError('并发数必须为 1～3')
    factory=getattr(parent.model,'fork',None)
    if not callable(factory):
        raise ValueError('此模型不支持独立客户端，拒绝并发共享模型')
    cancelled=cancelled if cancelled is not None else Event()
    def run(task, identifier):
        model=None
        try:
            model=factory()
            return delegate(parent,task,model=CancellableModel(model,cancelled),report_id=identifier)
        except Exception:
            report={'id':identifier,'task':task,'completed':False,'steps':0,
                    'text':'委派客户端或执行失败；结论不可用。','text_truncated':False,
                    'tools':[],'reference_only':True}
            # 存储失败向调用方报错，不能声称已有持久证据。
            DelegationReports(parent.context.root).save(report)
            return report
        finally:
            if model is not None:
                model.client.close()
    executor=ThreadPoolExecutor(max_workers=workers)
    futures=[]
    try:
        futures=[executor.submit(run,task,identifier) for task,identifier in zip(tasks,identifiers)]
        try:
            return [f.result() for f in futures]
        except KeyboardInterrupt:
            cancelled.set()
            # 已发送请求等待配置超时；不声称强制终止远端请求。
            return [f.result() for f in futures]
    finally:
        cancelled.set()
        executor.shutdown(wait=True)


def delegate_parallel(parent, tasks, *, workers=2, cancelled=None):
    from .delegation_batches import DelegationBatches
    if not isinstance(tasks,list) or not 1<=len(tasks)<=5 or any(not isinstance(t,str) or not t.strip() or len(t)>8000 for t in tasks):
        raise ValueError('需要 1～5 个有效委派任务，每个最多 8000 字符')
    if type(workers) is not int or not 1<=workers<=3:
        raise ValueError('并发数必须为 1～3')
    if not callable(getattr(parent.model,'fork',None)):
        raise ValueError('此模型不支持独立客户端')
    batches=DelegationBatches(parent.context.root)
    from .tasks import TaskStore
    with TaskStore(parent.context.root).store.lock(), batches.store.lock():
        record=batches.start(tasks)
        reports=_execute(parent,tasks,[r['report_id'] for r in record['tasks']],workers=workers,cancelled=cancelled)
        batches.finish(record['id'])
        return reports
