"""进程内模型用量：计数实际尝试，缺失账单信息不伪装成零。"""

from threading import RLock


class UsageTracker:
    def __init__(self, max_requests=0):
        self._lock = RLock()
        self.max_requests = max_requests
        self.requests = 0
        self.records = {}

    def begin(self):
        with self._lock:
            return self._begin()

    def _begin(self):
        if self.max_requests and self.requests >= self.max_requests:
            from .providers import ProviderError
            raise ProviderError('模型请求预算已耗尽；本次未发送请求。请保存会话后按新的预算启动。')
        self.requests += 1
        return self.requests

    def observe(self, request_id, usage):
        with self._lock:
            self._observe(request_id, usage)

    def _observe(self, request_id, usage):
        values = [getattr(usage, key, None) for key in
                  ('prompt_tokens', 'completion_tokens', 'total_tokens')]
        if all(type(v) is int and v >= 0 for v in values) and values[2] == sum(values[:2]):
            # 流式服务可能重复发送累计值；按请求覆盖，不能重复累加。
            self.records[request_id] = values

    def snapshot(self):
        with self._lock:
            return self._snapshot()

    def _snapshot(self):
        totals = [sum(values[i] for values in self.records.values()) for i in range(3)]
        return dict(requests=self.requests, max_requests=self.max_requests,
                    prompt_tokens=totals[0], completion_tokens=totals[1],
                    total_tokens=totals[2], unknown_requests=self.requests - len(self.records))
