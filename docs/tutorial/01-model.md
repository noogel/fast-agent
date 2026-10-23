# 先理解一次模型调用

模型本身只接收消息并生成回复。配置来自环境，适配器将我们的 Message 转换为 SDK 消息格式。

阅读 [config.py](../../src/fast_agent/config.py)、[models.py](../../src/fast_agent/models.py) 和 [providers.py](../../src/fast_agent/providers.py) 中的 OpenAIModel。重点观察：API Key 不出现在配置 repr 中；默认连接超时 10 秒、读取超时 60 秒，SDK 隐式重试关闭，宿主最多重试 2 次（详见[模型请求的超时、重试与执行边界](11-model-request-reliability.md)）；供应商异常转换成不含原始请求的提示。

在项目根、激活虚拟环境且配置真实模型后执行：

```bash
python examples/single_call.py
```

这一步没有 Agent 循环，也没有执行工具。`Model.complete(messages, tools)` 是我们定义的接口，OpenAIModel 和 MockModel 都实现它，因此后面可以替换模型而不改 Agent。

练习：修改示例中的用户问题，观察回复。没有密钥时使用 `fast-agent --mock --prompt 你好`，但模拟回复不是智能推理。
