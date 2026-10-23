# 02 · 模型怎样请求工具

上一章知道了模型负责提出下一步。现在只追踪一个动作：读取出错的加法函数。模型没有直接访问你硬盘的能力，怎样才能拿到源码？

## 先告诉模型有哪些工具

宿主把工具的名称、用途和参数约定作为工具定义发送给模型。工具定义可以理解为菜单：它描述能申请什么，不包含执行后的结果。

下面是简化示意，省略了实际协议中的包装字段：

```json
{"name":"read_file","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}
```

模型可能返回项目内部表示的调用：

```json
{"id":"read-1","name":"read_file","arguments":"{\"path\":\"math_utils.py\"}"}
```

注意 `arguments` 是需要解析的 JSON 字符串。`id` 标识这一次请求；同一个工具调用两次，应当能分别对应各自的结果。

## 宿主把请求变成操作

处理过程可以简化成以下伪代码，不能直接替代项目实现：

```python
arguments = parse_json(call.arguments)
validate_schema(arguments)
handler = registry.lookup(call.name)
result = handler(arguments, context)
```

注册表检查工具和参数，处理函数还要检查路径、权限和当前文件状态。检查通过后才读取磁盘。文件内容最终被包装为工具结果，交回 Agent 循环。

这里有两个接口：模型面对工具定义，Python 程序面对处理函数。只修改定义而没有注册实现，工具不会凭空出现；只有函数却没有暴露定义，模型也无法按这个接口选择它。

## 出错时仍然要留下结果

文件不存在、参数类型错误和路径越界，都应产生可识别的失败。失败信息返回模型后，它可以换路径或向用户说明阻塞。不能把失败伪装成空文件，否则模型会依据错误事实继续编辑。

工具正文还可能含有“忽略用户要求”等文字。读到这段文本只是获得文件内容，不代表用户发出了新指令。

## 动手核对

在临时目录启动 Fast Agent，请它读取一个不存在的文件。查看工具结果是否报告失败，再看模型是否据此调整。真实模型不保证选择固定顺序，所以也可阅读[自定义工具教程](../tutorial/06-custom-tool.md)中的本地示例。

源码顺序：[ToolCall](../reference/src/fast_agent/models.py.md) → [ToolRegistry.execute](../reference/src/fast_agent/tools.py.md) → [文件工具](../reference/src/fast_agent/file_tools.py.md)。

**自测：工具定义里写“只能读当前目录”就足够了吗？** 不够。处理函数必须落实路径检查，文字说明不能替代执行约束。

下一章：[一轮怎样接着下一轮](messages.md)。
