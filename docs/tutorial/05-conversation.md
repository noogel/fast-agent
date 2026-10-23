# 多轮历史与可观察事件

阅读 [agent.py](../../src/fast_agent/agent.py) 的 history 和 emit，以及 [cli.py](../../src/fast_agent/cli.py) 的 show_event。

历史中保留 system、user、assistant 和 tool 四类消息。再次输入问题时，这些消息一起传给模型，所以它可以了解之前发生了什么。reset 会只留下系统消息，不回滚文件内容。

事件与历史的用途不同：历史用于模型上下文，事件用于向用户展示当前执行过程。model_start 表示开始调用；tool_start/tool_end 展示参数和结果；answer/error 记录结局。CLI 限制单个工具事件的展示长度，历史仍保留工具限额内的结果。

无需密钥运行：

```bash
fast-agent --mock
```

依次输入任意文本、`/reset`、`/exit`。模拟模型只说明自身用途；真实多轮推理需要接入真实模型。

执行期间 Ctrl+C 会补齐尚未完成调用的错误结果，防止下一轮发送无对应结果的工具请求。已执行的修改不自动撤销。现在可用 /save 和 /load 保存恢复，用 /trim 按整轮裁剪；超出配置预算时会自动摘要旧轮次，详见 [第九章](09-compaction-streaming.md)。详见 [第七章](07-context.md)。长对话仍可能超过模型上下文。
