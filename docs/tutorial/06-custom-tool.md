# 添加自己的工具

运行可直接修改的 [custom_tool.py](../../examples/custom_tool.py)：

```bash
python examples/custom_tool.py
```

示例提供一个字符计数工具，只有三部分：handler 函数、参数定义、注册调用。handler 的签名是 `(args: dict, context: ToolContext) -> ToolResult`。不需要修改 Agent.run。

如果希望真实 Agent 使用它，在构建注册表后调用 register，再把注册表传给 Agent；也可将工具加入 build_default_registry。模型能否选对工具取决于名称、说明、参数和模型能力，注册成功不保证模型每次都会调用。

练习：新增一个将摄氏温度转换为华氏温度的纯函数工具，返回 `(celsius * 9 / 5) + 32`。为 0°C→32°F、负数、错误类型和缺参数写测试，再运行 `python -m pytest -q`。先做无副作用的工具，理解协议后再加入外部系统。

学习到这里，你已掌握模型接口、工具注册、Agent 循环、错误回传、上下文与事件。后续可探索流式输出和上下文管理；第一版保持精简，便于逐行阅读。
