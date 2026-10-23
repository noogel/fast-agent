# MCP 远程 HTTP 服务

上一章通过标准输入输出连接本地进程。本章增加 Streamable HTTP：Agent 可以连接一个已经运行的 MCP 服务，模型仍然使用相同的工具调用循环。

## 配置与启动

在显式传入的 MCP 配置中，`url` 与 `argv` 二选一：

```json
{
  "servers": [
    {
      "name": "repo",
      "url": "https://mcp.example.com/mcp",
      "bearer_env": "MY_MCP_TOKEN",
      "timeout": 15
    }
  ]
}
```

`bearer_env` 是环境变量名称，实际凭据由你在启动进程前注入；不要把 token 写进配置、提示词或会话。无认证服务可省略它。客户端不会借用 `FAST_AGENT_API_KEY`，也不允许将其指定为 MCP 凭据来源。

```bash
fast-agent --mcp /绝对路径/mcp.json
```

连接和每次工具调用分别确认；确认内容包含服务名、目标 URL 与调用参数。计划模式先不连接，切换 `/execute` 后使用 `/mcp-connect`。远程工具按有副作用的工具处理，服务声明只读也不会自动绕过确认。

## 一次请求如何走完

1. `MCPManager.start()` 取得连接授权，再读取独立凭据，创建 `HttpClient`。
2. 客户端 POST `initialize`，协商协议；服务返回的会话标识只保存在内存中。
3. 客户端发送 `notifications/initialized`，随后通过 `tools/list` 注册工具。
4. 模型选择 `mcp_repo_工具名`。现有权限层确认后，将参数封装为 `tools/call` 发给 HTTP 服务。
5. 客户端读取 JSON，或从 SSE 流中找到匹配请求 ID 的结果。结果转换成原有工具消息，再由 Agent 发回模型。

后续请求携带协商后的 `MCP-Protocol-Version`；服务提供会话标识时还携带 `MCP-Session-Id`。通知与反向请求响应使用独立 POST。服务要求客户端执行反向能力时，本实现返回“不支持的方法”，不会启动额外模型调用。

代码入口是 `src/fast_agent/mcp_http.py`。`SSEMessages` 负责跨网络分片拼接 UTF-8、CR/LF/CRLF 和多行 data；`HttpClient.exchange()` 处理 HTTP 状态与消息；`mcp_tools.py` 复用工具注册、授权和错误暂停。CLI 通过统一的 `is_alive` 判断连接状态，HTTP 的这个值表示本地连接对象尚未关闭，并非后台健康探测。

## 在本机练习 HTTP 服务

安装官方 SDK 示例依赖：

```bash
.venv/bin/python -m pip install -e '.[mcp-test]'
mkdir -p /tmp/fast-agent-http-demo
printf 'def add(a, b):\n    return a - b\n' > /tmp/fast-agent-http-demo/math_utils.py
export FAST_AGENT_MCP_DEMO_TOKEN='local-demo-only'
.venv/bin/python examples/mcp_http_server.py \
  --workspace /tmp/fast-agent-http-demo --port 8765
```

示例仅监听回环地址。另一个终端注入同一测试 token，在配置中使用 `http://127.0.0.1:8765/mcp`，`bearer_env` 填 `FAST_AGENT_MCP_DEMO_TOKEN`。示例工具 `inspect_source` 读取固定源文件，返回是否仍含错误的减法。加 `--json-response` 可以练习 JSON 响应，默认使用 SSE。

该示例是教学用固定 Bearer 认证，不是生产认证服务。真正的远程接入应提供实际服务 URL 和独立凭据。

## 保存、恢复与失败处理

`/save 名称` 保存对话。`/load 名称` 或启动时 `--load 名称` 恢复时先关闭现有连接，并进入计划模式。确认工作区后 `/execute`、`/mcp-connect`，重新授权并创建 HTTP 会话；旧会话标识和 token 不随会话文件保存。

关闭时尽力发送 DELETE，清除本地凭据与会话标识；它不会停止远端服务。DELETE 最多等待两秒，网络失败或服务不支持删除时不能保证远端已清理，需要服务自己的会话过期机制。

401/403 提示检查认证；404 表示端点或会话不可用。超时、断线、格式错误等会关闭连接并保留执行结果不确定的暂停状态。客户端不会自动重试 POST，也不会在 404 后悄悄初始化并重放工具。先核实远端操作结果，再显式重连继续。

## 工程边界

- 默认验证 HTTPS 证书，不跟随重定向。回环 HTTP 可直接使用；非回环明文地址须显式设置 `allow_insecure_http: true`，意味着接受凭据和数据的明文传输。
- URL 不允许内嵌凭据、查询或片段。回环请求直连；其他地址沿用当前进程代理环境。
- 每次请求总超时包含持续心跳；请求和单条 JSON/事件上限 64 KiB，SSE 累计响应上限 1 MiB、最多 100 个事件。拒绝压缩响应，避免解压导致无界内存使用。
- 支持 2025-11-25、2025-06-18 的工具子集，沿用上一章有限参数 schema、文本和对象返回。OAuth 授权见 [OAuth 生命周期](50-mcp-oauth.md)。暂不支持旧版 HTTP+SSE、后台 GET 通知流、Last-Event-ID 断点续传或完整 MCP 能力。
- 本机命令沙箱约束本地命令，不能隔离远端服务执行；Agent 自身的 HTTP 传输也不属于命令沙箱。远端结果属于不可信数据，仍需检查文件差异和测试证据。
- 客户端使用同步入口包装异步请求，适用于当前 CLI；不应直接嵌入已有的 asyncio 事件循环。

协议参考：[MCP Streamable HTTP 官方规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。

## 验证与复现

`tests/test_mcp_http.py` 包含真实 HTTP 服务器测试、JSON/SSE、独立官方 SDK 互操作、认证拒绝、证书拒绝、超时、超限、无重放和 CLI 恢复测试。

配置好现有 MiniMax 环境后运行：

```bash
.venv/bin/python examples/real_mcp_http_demo.py
```

脚本创建临时 Git 仓库和独立 SDK HTTP 服务，让 MiniMax 调用工具诊断、执行失败测试、修改源码、重新测试，然后保存对话并在新 Agent 中恢复，建立新 HTTP 会话复查。最后独立运行测试、检查源文件差异、两次 DELETE 与服务仍存活等断言。报告只保留会话变化的布尔值，不记录 token 或会话标识。

示例真实验证通过回环 HTTP/SSE 完成，覆盖真实网络传输和 MiniMax 调用；不代表已经部署到局域网或验证外部 HTTPS 服务。外部 HTTPS 的真实连通性需要实际端点另行验证。

验证应同时核对 HTTP/SSE 协议测试、真实模型工具调用和恢复后的再次调用。连接失败时保留失败报告，检查实际网络路径后再有限重试；配置了代理不等于请求已经经过代理。