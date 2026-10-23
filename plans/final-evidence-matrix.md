# 七项收尾证据矩阵

2026-10-23核对。本文按验收约定列证据，不把已有功能数量当完整复刻Claude Code。真实报告位于忽略目录`.fast-agent/validation/`，属于指定模型和夹具的一次运行；本轮重新读取报告，不代表重新发送了模型请求。全量回归最新为581项通过；下述新增分页和取消反例另有11项针对性通过。

| 项目 | 关键约束与代码 | 本地证据 | 真实场景证据 | 尚需核对 |
| --- | --- | --- | --- | --- |
| 报告读取 | delegation_reports.py独立存储、校验、每页20条；查看/汇总不改主历史 | test_delegation_reports.py：未完成状态、无效/链接拒绝、无模型调用、来源与截断；新增21条跨页顺序无遗漏 | parallel-real.json包含持久报告流程8/8，不把它单独当分页证明 | 命令入口已对照；报告只作参考 |
| worktree清理 | worktree_lifecycle.py与worktree_cleanup.py：活动/未知状态阻塞、交付祖先条件、确认后重验、证据恢复 | test_worktree_lifecycle.py、test_worktree_cleanup.py：未交付/独立提交拒绝、确认中修改、删除前后中断核实 | cleanup-real-retry.json 21/21 | 证据包与保护引用无自动到期，不能声称清理后全部磁盘释放 |
| 委派调度 | delegation_scheduler.py独立客户端、有界线程、取消后丢弃工具；batches保留未知状态 | test_delegation_scheduler.py：并发重叠、关闭、异常脱敏、恢复不重跑；新增请求返回后取消；test_usage.py原子预算 | parallel-real.json 8/8 | 协作取消不强杀远端请求，全文已复查 |
| MCP/OAuth | mcp_tools.py、oauth_session.py：连接/刷新/定义失效、资源绑定、刷新/撤销 | test_mcp*.py与test_oauth*.py；HTTPS直连/CONNECT代理；CA限量读取 | mcp-lifecycle-real.json 22/22；oauth-manager-minimax.json 6/6 | 用户不要求外部账户接入；夹具不是用户真实授权 |
| 上下文/Skill | compaction.py工具配对、失败保留；skill_tree.py整体资源版本 | test_compaction.py、test_skill_resource_versions.py等：备份失败、未完成批次、资源变更失效 | skill-tree-real.json 14/14；overflow-real.json 8/8实际拒绝后恢复 | 不保证摘要无损；不自动发现供应商窗口；接口已对照 |
| 提问/回收 | questions.py持久回答与恢复不授权；retention*.py引用保护与隔离/恢复/清除 | test_questions.py、test_retention.py、test_retention_purge.py：一次性/过期、跨工作区引用、确认中变化、中断和无解释缺失拒绝 | questions-real.json 8/8；retention-real.json 11/11 | 无自动到期证据明确保留，不当作通用存储GC |
| 评测/LSP | evaluation.py独立断言与未知费用；lsp_transport.py协议/关闭、lsp_tools.py授权 | test_evaluation.py、test_recovery_evaluation.py、test_lsp*.py：额外行为拒绝、失败用量、帧/ID/诊断版本/进程退出 | engineering-v2.json 2任务9断言；engineering-recovery-v1-retry.json 7/7；lsp-real-canonical.json 9/9 | 没有编辑器插件；限定任务不能证明任意仓库修复能力 |

## 交付边界与审计范围

- 用户已明确不接入外部OAuth账户；受控服务验收仍须保留，外部账户不作已验证声明。
- 第43–52章均已逐页DOM与全文截图检查；43–49覆盖窄屏，50–52覆盖宽屏。早期41/43章过时状态已修正；所有生成页面本地链接与锚点检查无断链。
- 本矩阵对应已读取的源码、测试与真实报告。验收限于约定能力和所列环境，不代表完整复刻Claude Code或所有平台/服务兼容。


2026-10-23用户明确回复“不用接入”：本次不要求接入外部真实账户OAuth服务。保留受控HTTPS授权、代理转发、管理器与MiniMax工具调用验收；不得称外部用户授权已验证。此项不再作为收尾阻塞。
