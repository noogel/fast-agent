# 工程核心目标验收

本次目标：调用量与预算、命令执行隔离、多文件修改恢复、持久任务计划、质量检查及扩展能力，保持中文教学并完成真实 MiniMax 验证。

## 逐项证据

| 要求 | 实现 | 关键验证 |
| --- | --- | --- |
| 调用量与预算 | usage.py、providers.py、FAST_AGENT_MAX_REQUESTS、/usage | test_usage.py 覆盖重试与摘要、未知用量、流式累计；usage-demo.json 8 项通过；组合验证请求发送前预算拒绝 |
| 命令执行隔离 | command_sandbox.py、--sandbox macos，同步与后台共用 | test_sandbox.py 原生验证工作区可写、外部/符号链接写入被拒、网络被拒、后台同样限制；组合验证沙箱内实际执行测试 |
| 多文件修改恢复 | edit_batches.py、edit_files、/batches、/undo-batch | test_edit_batches.py 覆盖审批、冲突、部分写入、中断阻塞、AGENTS.md 依赖排序；真实修复/撤销/恢复 11 项通过 |
| 持久任务计划 | task_plans.py、get_plan/update_plan/list_plans、/plans、/show-plan | test_task_plans.py 覆盖 CAS、字段、权限与确认期间变化；真实创建/完成/恢复重新查询 10 项通过 |
| 质量检查 | quality_checks.py、--checks、run_checks、/check-runs | test_quality_checks.py 覆盖真实退出码、拒绝、异常/中断终态；真实失败、通过、恢复后重跑 10 项通过 |
| 本地工具扩展 | extensions.py、--extensions、ext_ 工具 | test_extensions.py 覆盖独立进程、参数、权限、schema、异常/超时、非有限数据，以及 CLI 实际接线；MiniMax 扩展/修复/质量检查/恢复 12 项通过 |
| 中文教学和文档站 | 第 23～28 章、README、导航、源码参考页 | 严格 MkDocs 构建；浏览器逐章检查。文档清楚区分原子单文件写入与多文件部分状态、历史状态与执行证据 |
| 完整组合流程 | examples/real_engineering_demo.py | engineering-demo.json 18 项通过：Git 仓库两文件修复、计划、扩展、沙箱检查、预算拒绝、保存加载、撤销后测试失败、重新修复通过，最终 diff 只有目标源码 |

真实报告位于本机 `.fast-agent/validation/`，不提交 Git。组合脚本退出码 0，报告 verified=true；不是将失败报告修改为成功。早期 sandbox-demo.json 的组合预算暂停未通过，原始失败和分范围审计均保留；首次 engineering-demo-incomplete-gate.json 的 16 项断言漏掉 Git 检查失败；增加精确 /dev/null 空设备写入例外及分轮全部检查断言后，最终 engineering-demo.json 18 项通过。实际两轮修复后的 tests/diff 均返回 0，首次 tests 返回 1。

## 边界

- 请求预算是模型实例/进程内次数限制，未知 token 用量保持未知，不是账户级费用上限。
- macOS 后端限制命令写入与网络，仅额外允许写入 /dev/null 空设备，不限制外部读取，不可用于安全执行恶意代码；其它系统没有等价沙箱后端。
- 多文件操作不提供全局原子事务；只批量修改已有文件，部分失败可核实后显式恢复。
- 计划状态不是测试证据；历史检查记录不代表当前代码仍然通过，也不是强制最终回答门禁。
- 本地扩展是显式受信任进程协议，不宣称支持 MCP、远端服务发现、子 Agent 调度或完整 IDE 集成。
- 所有本次验证在本机 macOS 与 MiniMax 服务执行，没有 Docker 部署；本次目标不包含远端部署。

以上覆盖本轮约定的本地工程核心闭环，不等于完全复刻 Claude Code。

最终本机回归：321 项通过（启用原生沙箱测试）；MkDocs 严格构建、compileall 和 git diff --check 通过。
