# 由浅入深理解 agent

从“模型为什么能帮我修复测试”开始，逐步理解工具、消息、上下文与工程边界。无需先学完全部功能章节，也无需先记架构层名。全组沿用同一个加法函数失败案例。

| 顺序 | 章节 | 读完能回答什么 |
| --- | --- | --- |
| 01 | [从一个失败测试认识 Agent](decisions.md) | Agent 比单次模型调用多了什么？ |
| 02 | [模型怎样请求工具](tools.md) | 一段请求怎样变成真实操作？ |
| 03 | [一轮怎样接着下一轮](messages.md) | 每轮发什么消息，结果怎样配对？ |
| 04 | [对话变长后怎么办](context.md) | 保存、裁剪和压缩怎样选择？ |
| 05 | [怎样放心地修改代码](safety.md) | 权限、版本、撤销与沙箱有什么区别？ |
| 06 | [中断后怎样继续](recovery.md) | 哪些状态能恢复，哪些操作不能直接重试？ |
| 07 | [怎样扩展和分工](extensions.md) | Tool、Skill、MCP、子 Agent 各适合什么？ |
| 08 | [回看分层架构](../architecture.md) | 学过的能力怎样组成整体？ |

## 怎样使用这条学习路线

每篇先给一个具体问题，再解释机制与取舍，最后指向当前源码、可动手核对的行为和自测题。这里讲设计原因；逐步操作放在“实践指南”中。示意消息和推演场景不是新的真实模型验收记录。

## 借鉴哪些官方文档

- [Anthropic：Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)：先区分固定工作流和动态 Agent，再讨论适用条件。借鉴其由简单问题逐步引入复杂性的讲法。本文不据此判断其当前产品功能。
- [OpenAI Agents SDK：Context management](https://openai.github.io/openai-agents-python/context/)：分别讨论程序侧上下文与模型可见信息。借鉴这种概念拆分，避免把所有状态都叫“记忆”。
- [LangGraph：Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：围绕检查点、线程和恢复组织持久化说明。借鉴其围绕运行状态解释恢复的写法，不声称 Fast Agent 实现了 LangGraph 的恢复机制。

下文关于 Fast Agent 的行为以本项目代码为准；外部框架的概念用于帮助理解，不作为功能已实现的证据。
