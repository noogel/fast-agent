"""需要环境变量配置：只调用模型一次，不执行工具。"""
import os
from fast_agent.config import Config
from fast_agent.models import Message
from fast_agent.providers import OpenAIModel, ProviderError

try:
    model = OpenAIModel(Config.from_env(os.environ))
    print(model.complete([Message('user', '用一句话解释什么是 Agent。')], []).content)
except (ValueError, ProviderError) as exc:
    print(exc)
    raise SystemExit(1)
