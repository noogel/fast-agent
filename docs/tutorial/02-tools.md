# 模型请求调用，程序真正执行

工具定义是发给模型的一份说明：名称、用途、参数 JSON Schema。模型返回 ToolCall，其中 id 用于关联结果、name 用于选择工具、arguments 是 JSON 字符串。

阅读 [tools.py](../../src/fast_agent/tools.py)。ToolRegistry.register 注册工具；schemas 导出模型可理解的定义；execute 解析参数、检查类型、调用 handler，最后生成 ToolResult。

参数错误不能直接让整个程序崩溃：返回 `ok=False` 和 error，模型下一步就可以根据错误调整。注册表仅实现项目使用的 JSON Schema 子集，不支持完整规范。

无需密钥，在项目根运行：

```bash
python examples/custom_tool.py
python -m pytest tests/test_tools.py -q
```

练习：把示例的 text 改成数字，观察类型校验；把工具名改为不存在的名称，观察错误结果。不要因为模型声称某项操作已执行就相信它，真正的执行发生在 handler。
