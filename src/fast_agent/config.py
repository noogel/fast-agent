"""配置只来自环境，不搜索其他项目中的凭据。"""
from dataclasses import dataclass, field
import math
from typing import Mapping
from urllib.parse import urlparse


@dataclass(frozen=True)
class Config:
    api_key: str = field(repr=False)
    model: str
    base_url: str = "https://api.openai.com/v1"

    connect_timeout: float = 10.0
    read_timeout: float = 60.0
    max_retries: int = 2
    max_requests: int = 0
    max_output_tokens: int = 0

    def __post_init__(self):
        if type(self.max_output_tokens) is not int or self.max_output_tokens < 0:
            raise ValueError("输出 token 上限必须是非负整数")
        if type(self.max_requests) is not int or self.max_requests < 0:
            raise ValueError("模型请求预算必须是非负整数，0 表示不限")
        for name, value, maximum in [('连接超时', self.connect_timeout, 120), ('读取超时', self.read_timeout, 600)]:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError(f'{name}必须大于 0 且不超过 {maximum} 秒')
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 5:
            raise ValueError('模型最大重试次数必须是 0～5 的整数')

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        key = env.get("FAST_AGENT_API_KEY", "").strip()
        model = env.get("FAST_AGENT_MODEL", "").strip()
        url = env.get("FAST_AGENT_BASE_URL", "https://api.openai.com/v1").strip()
        if not key or not model:
            raise ValueError("请设置 FAST_AGENT_API_KEY 和 FAST_AGENT_MODEL，或使用 --demo。")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("FAST_AGENT_BASE_URL 必须是无内嵌凭据的 HTTP(S) 地址。")
        try:
            connect = float(env.get('FAST_AGENT_CONNECT_TIMEOUT', '10'))
            read = float(env.get('FAST_AGENT_READ_TIMEOUT', '60'))
            retries = int(env.get('FAST_AGENT_MAX_RETRIES', '2'))
            requests = int(env.get('FAST_AGENT_MAX_REQUESTS', '0'))
        except (ValueError, TypeError):
            raise ValueError('超时必须为数字，重试次数与请求预算必须为整数') from None
        return cls(key, model, url, connect, read, retries, requests)
