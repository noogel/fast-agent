# 问题没回答，退出后怎样继续

模型调用 ask_user 时，问题会先保存到当前工作区的私有记录。交互入口可立即回答；没有交互入口或用户跳过时，工具结果携带问题 ID，Agent 暂停本轮，同批剩余工具标记为未执行。

```text
/save waiting
/answer 问题ID 只修复 A
/load waiting
/continue-question 问题ID
```

可以用 `/questions [页码]` 找回问题 ID，用 `/question ID` 查看问题与已存答案；这些查询不调用模型。

`/answer` 只保存回答，不请求模型，不执行任务。回答一次写入，不能覆盖；默认有效期一天，过期的待回答问题需要重新提出。加载对应会话后，`/continue-question` 核实该问题确实出现在当前历史中，再将问题和回答明确交给模型继续。

恢复仍回到计划模式。需求回答不会批准命令、写文件或权限提升；同一会话重复导入答案会被拒绝。复制的不同会话属于独立历史，不保证跨所有会话的全局一次性消费。

当前会话需显式保存或启用任务检查点，单独的问题记录不能重建整段对话。摘要若不再保留原工具记录，应加载完整备份再继续此问题。已回答问题的引用保护、隔离与恢复见[存储回收](46-storage-retention.md)；待回答问题不能作为回收候选。

源码：[questions.py](../../src/fast_agent/questions.py)、[user_questions.py](../../src/fast_agent/user_questions.py)、[agent.py](../../src/fast_agent/agent.py)。

## 验收证据

MiniMax指定场景验收8/8通过，覆盖真实提问、答复持久化、恢复后继续与权限边界。本地报告为`.fast-agent/validation/questions-real.json`；报告来自临时夹具，不能推断任意服务、工作区或资源都已经验收。失败记录保留，模型口头结论不替代工具结果和磁盘断言。
