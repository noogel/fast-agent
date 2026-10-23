# MCP Streamable HTTP 接入计划

用户授权推荐方案连续实现。沿用上一阶段 MCP 2025 工具子集，增加远程 HTTP 连接，不改模型工具循环。复用权限、持久任务阻塞、恢复重新授权和 uncertain 暂停。

## 接口与边界

配置 servers 条目支持互斥 argv 或 url；URL 支持 HTTPS，明文仅回环默认允许，其余地址须 allow_insecure_http=true。不允许内嵌凭据、查询或片段。可指定 bearer_env 环境变量名；不接受明文 token 配置、不自动复用 FAST_AGENT_API_KEY、不跟随重定向、不关闭 TLS 验证。调用确认展示服务、目标URL和参数，不展示凭据或会话ID。

新增 mcp_http.py：同步外观包装 asyncio.run + httpx2.AsyncClient，单次请求有总超时。实现 initialize/initialized、POST JSON/SSE、有限通知/反向请求处理、MCP-Session-Id 和 MCP-Protocol-Version、DELETE best effort关闭。HTTP仅接受2025-11-25/2025-06-18协议。不建立后台GET流，不支持旧HTTP+SSE，不自动重试POST、404重初始化或Last-Event-ID续传，连接失败需人工核实并显式重连。401/403展示安全认证提示；不得泄露错误响应原文、token或session。

保持现有参数schema子集与文本/结构化返回边界。响应累计限1MiB，每事件64KiB，最多100消息；包括心跳的总时间受限。TLS默认验证；回环直连，其它地址使用现有代理环境。远程服务不受本机命令沙箱约束，连接确认明确告知；限制的是本地命令而非Agent HTTP传输。

传输提供统一is_alive属性，CLI不能假定每个连接都有process。恢复/部分断线重连统一关闭旧连接；HTTP只结束会话，不终止远端服务。

## 顺序步骤

1. 增加真实HTTP测试服务，红测握手/JSON/SSE/session/version/认证和拒绝请求。
2. 实现HttpClient、SSE有界解析、配置与Manager/CLI接入；测试401/403/404/重定向/超限/慢流/无效响应/无重放。
3. 官方SDK独立HTTP服务互操作（JSON和SSE）；MiniMax临时仓库修复、保存恢复、新session重连和测试。
4. 第30章中文教程、README/导航；独立审查、全量测试、严格文档构建、浏览器验证和本地提交。

真实验证使用回环HTTP与测试Bearer，不连接未经提供的第三方MCP账号。报告不得声称已部署到LAN或验证外网TLS/OAuth。

## 完成证据（2026-10-20）

- HTTP 专项 35 项通过；启用真实 macOS 沙箱的全量测试 382 项通过。
- 官方 SDK 1.30.0 独立服务 JSON/SSE 互操作通过，证书拒绝、慢流截止、无重放和 CLI 恢复有回归覆盖。
- MiniMax-M3 完整验证 20/20 断言通过：失败测试、源文件修复、HTTP 诊断、保存恢复、新会话、重跑测试、两次 DELETE、服务仍存活。报告在忽略目录 `.fast-agent/validation/mcp-http-demo.json`。
- 首次模型最终总结发生连接错误（19/20）；保留 `mcp-http-demo-first-failed.json`。现有代理显式探测成功后重跑完整流程，退出码 0。
- 文档严格构建、compileall、diff check 通过；浏览器确认第 30 章正常，截图在忽略的 validation 目录。
- 独立审查提出 SSE CR-only/BOM 兼容问题，已先红测再修复；复查无新增重要问题。
- 真实端点为回环 HTTP/SSE，未部署远端主机、未验证外部 HTTPS/OAuth。
