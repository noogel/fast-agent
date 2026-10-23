# 从一个任务理解 Agent：修复失败测试

先不用记架构名词。假设你在项目目录中对 Fast Agent 说：

> 帮我修复加法函数的失败测试，不要修改测试文件。

源码里有一个错误：

```python
def add(a, b):
    return a - b
```

测试要求 `add(2, 3)` 等于 `5`，现在却得到 `-1`。我们沿着这一个任务，认识 Agent 中最重要的五个角色。

以下是教学示意，不是每次模型都会采取的固定顺序。可运行的真实模型例子是 [real_repository_demo.py](reference/examples/real_repository_demo.py.md)：它创建临时仓库，限制修改范围，并用实际测试和 Git 差异核对结果。真实调用需要已有模型配置；先理解过程即可。

## 1. 模型：决定下一步做什么

模型看到用户任务和可用工具，可能提出：“先读取源码和测试。”它输出类似这样的工具请求：

```json
{"id":"read-1","name":"read_file","arguments":"{\"path\":\"src/math_utils.py\"}"}
```

这里的 `read_file` 是工具名，`arguments` 是 JSON 字符串。**模型输出这段请求时，文件还没有被读取。**

在代码里，它对应 [ToolCall](reference/src/fast_agent/models.py.md)。[providers.py](reference/src/fast_agent/providers.py.md) 负责把模型服务的回复转换成项目内部的结构。

## 2. 工具：真正去读、改、运行

Agent 的宿主程序收到请求后，通过 [ToolRegistry.execute](reference/src/fast_agent/tools.py.md) 找到对应函数，校验参数，再调用文件工具。

读取结果会告诉模型：当前函数使用了减法。模型也可以请求 `run_command` 执行测试；程序实际运行后，返回失败信息和退出码。

工具负责接触真实工作区。模型说“测试通过了”不能代替执行测试；没有工具结果，就没有这项执行证据。

## 3. 上下文：记住目标和刚发生的事

第一次询问模型时，消息里有规则、用户任务，以及可用的工具定义。执行读取后，程序把模型的工具请求和工具结果都加入历史，再询问模型。

历史可以简化为：

```text
system：工作区规则和行为要求
user：修复加法测试，不要改测试文件
assistant：请求 read_file，调用 ID 为 read-1
tool：返回源码，tool_call_id 为 read-1
```

`tool_call_id` 把结果与请求配对。模型因此知道自己刚读到了什么，而不是每次从零开始猜测。后续的测试失败、编辑结果、复测结果也会追加进去。

[Message](reference/src/fast_agent/models.py.md) 表示一条消息；[Agent.history](reference/src/fast_agent/agent.py.md) 保存当前历史。历史是当时的记录，文件后来变化时仍需重新读取。

## 4. 权限：请求不等于允许

模型判断应该把 `a - b` 改成 `a + b`，于是请求 `edit_file`。程序还需要检查：路径是否在允许范围内、规则是否满足、文件是否被外部修改，以及当前权限是否允许写入。

交互确认模式下，用户看到差异并批准后才写入。计划模式会拒绝写入。执行测试命令也受命令权限约束。

因此，即使模型提出错误或越界的请求，宿主程序也有机会拒绝它，并把失败原因返回模型。入口在 [ToolContext](reference/src/fast_agent/tools.py.md)，具体文件检查在 [file_tools.py](reference/src/fast_agent/file_tools.py.md)。

## 5. Agent 循环：把这些步骤接起来

工具结果不会自己送回模型，是 [Agent.run](reference/src/fast_agent/agent.py.md) 持续组织这个过程。可以先用下面的伪代码理解，再读完整实现：

```python
history.append(user_task)
for step in range(max_steps):
    reply = model.complete(history, tool_definitions)
    history.append(reply)
    if not reply.tool_calls:
        return reply.text
    for call in reply.tool_calls:
        result = registry.execute(call, context)
        history.append(tool_message(call.id, result))
```

这是伪代码，省略了协议转换、压缩、错误处理和暂停逻辑。它说明：**循环不替模型做决策，而是保证请求被处理、结果被记录、下一轮有据可依。**

在这个任务里，一条合理的完整路径是：读取源码与测试 → 运行测试复现失败 → 请求编辑并通过权限检查 → 重跑测试 → 查看 Git 差异 → 给出总结。达到步数上限、连续失败或用户取消时，也可能暂停而未完成。

## 怎样判断任务真的完成了

核对三件事：测试确实从失败变为通过；差异只包含预期的源码修改；测试文件没有被改掉。模型回复“完成”只是一份总结，不是独立验收。

现在你已经认识五个角色：**模型做决定，工具做操作，上下文存记录，权限守边界，循环组织下一轮。**

## 遇到问题，再学习下一项能力

| 具体问题 | 接着学习 |
| --- | --- |
| 想自己加一个工具 | [工具协议](tutorial/02-tools.md) → [自定义工具](tutorial/06-custom-tool.md) |
| 关闭程序后想接着做 | [保存和恢复会话](tutorial/07-context.md) |
| 对话越来越长 | [自动压缩](tutorial/09-compaction-streaming.md) |
| 每次都要重复项目操作方法 | [本地 Skill](tutorial/32-local-skills.md) |
| 要调用别的服务提供的工具 | [MCP 工具](tutorial/29-mcp-tools.md) |
| 想把只读探索交给另一段独立对话 | [子 Agent 委派](tutorial/38-readonly-delegation.md) |

学过几个具体功能后，再看 [由浅入深理解 agent](deep-dive/index.md)，把这些熟悉的代码放回整体结构中。架构图用来整理知识，不要求你入门时先记住所有层名。
