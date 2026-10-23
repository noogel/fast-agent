# MCP stdio 工具接入

用户授权按推荐方案连续实现。目标：在同步 Agent 中发现并调用本地 MCP 工具，保留授权、检查点、恢复、中文教学及 MiniMax 验证。

## 设计

实现有界同步 JSON-RPC stdio 客户端，明确支持 2025-11-25、2025-06-18 和 2024-11-05 生命周期协议；不宣称最新协议或完整 MCP 实现。相较引入异步 SDK 全栈，此方案保持教学代码可追踪；远程 HTTP/OAuth 后续单独设计。

显式 --mcp 配置（servers 数组，name/argv/timeout），启动逐次授权；每个服务维持一个子进程。initialize → notifications/initialized → 分页 tools/list → tools/call。名称使用 mcp_<server>_<tool>，限制数量并检测冲突。schema 沿用严格子集，拒绝不能验证的约束。外部工具均为 write，计划模式不调用。启动授权和工具调用授权分开，不接受服务 annotations 提升权限。

使用现有命令沙箱；子进程仅继承基础环境，不传模型密钥。stdout 只解析协议，stderr 在等待请求响应时排空但不直接显示。限制每帧 64 KiB、一次请求总读取 1 MiB、通知次数、总超时、工具总数和分页数。调用失败不自动重试；协议/传输异常终止进程并返回 uncertain。拒绝服务发来的 sampling/elicitation 等反向请求。退出关闭进程组。恢复只恢复历史，重新启动和发现服务，不能恢复授权。

## 顺序执行

1. 编写真实子进程协议测试并观察缺少模块的失败。
2. 实现传输、发现、授权与 CLI 生命周期；覆盖错误、超时、分页、schema、进程退出。
3. 本地 MCP 示例服务 + MiniMax 修复/测试/保存恢复验证。
4. 中文第 29 章、README、导航；全量测试、严格文档构建、浏览器检查和独立审查。

验收：权限拒绝不调用，计划模式不暴露；有效工具调用正确关联 id；异常不能假装成功；恢复重新发现且不重放；真实模型通过 MCP 获取源码诊断、修复测试并在恢复后再次调用。


## 实现与审查记录

- 新增 mcp_transport.py / mcp_tools.py，CLI --mcp 与 /mcp-connect。
- 启动前检查持久任务阻塞；计划/恢复只读时延迟启动，拒绝启动保留终端。
- 独立审查发现并修复部分服务断开不能重连、交互恢复沿用旧连接、反向请求回复未共享总截止时间；均已验证。
- 官方 Python SDK 1.30.0 服务互操作通过；24 项初版测试后补充非法协议版本类型，最终 MCP 26 项通过。
- 全量回归 347 项通过（含原生 macOS 沙箱），严格文档构建、compileall、git diff --check 通过。
- MiniMax 首次首轮请求超时，原报告保留在 .fast-agent/validation/mcp-demo-first-timeout.json；已检查现有代理连通性，并显式通过代理重跑。完整结果单独记录，不将失败视为成功。


## 最终验收（2026-10-20）

`examples/real_mcp_demo.py` 退出码 0，`.fast-agent/validation/mcp-demo.json` verified=true，16 项断言全部通过。官方 SDK 1.30.0 / MiniMax-M3；首轮 7 次模型请求（含 1 次未报告用量的请求），恢复后 2 次。

- 首轮：MCP 诊断 true → run_checks 失败 → read_file/edit_file → MCP 诊断 false → run_checks 通过 → git_diff。
- 保存历史后关闭旧服务；新 Agent 恢复计划模式，无工具重放。执行模式重新授权连接、重新发现工具。
- 恢复后：MCP 诊断 false → 新 run_checks 通过。独立 unittest 退出 0，Git 修改仅 math_utils.py，测试文件未变。
- 检查命令与 MCP 服务均运行于 macOS 沙箱。服务进程退出检查全部成功。
- 已通过浏览器检查第 29 章和导航；文档明确支持的协议/schema/返回子集及未支持 HTTP/OAuth/Windows 等边界。

本轮约定的本地 MCP 工具闭环完成；未声称全 MCP 协议或全部第三方服务兼容。没有远端部署，也没有推送 Git。
