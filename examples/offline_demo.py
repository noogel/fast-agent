"""无需密钥：复用真实 Agent 和文件工具完成演示。"""
from fast_agent.cli import run_demo

result = run_demo()
print(result.text)
raise SystemExit(0 if result.completed else 1)
