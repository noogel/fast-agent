# MCP 工具发现、调用与恢复

上一章的本地扩展由配置手工声明工具 schema，每次调用启动一次进程。本章加入 **MCP stdio 工具客户端**：服务启动后完成握手，主动查询它提供的工具，再把模型的工具调用转成 JSON-RPC 消息。连接可在多轮调用间复用。

## 先运行示例

在 `fast-agent` 项目目录安装官方 SDK 示例依赖：

```bash
.venv/bin/python -m pip install -e '.[mcp-test]'
```

客户端本身不依赖 SDK；示例服务使用官方 Python SDK 1.x（示例互操作版本见下方验收）。创建显式配置 `mcp.json`，把两个绝对路径换成本机项目路径：

```json
{
  "servers": [
    {
      "name": "repo",
      "argv": ["/绝对路径/fast-agent/.venv/bin/python", "/绝对路径/fast-agent/examples/mcp_inspector_server.py"],
      "timeout": 15
    }
  ]
}
```

服务以 `--workspace` 指定的目录作为工作目录，示例只读取该目录下的 `math_utils.py`。准备包含 `add` 函数的文件，然后启动：

```bash
.venv/bin/fast-agent --workspace /你的测试仓库 --mcp /绝对路径/mcp.json
```

先确认启动服务，再提问“调用 MCP 检查 add 的实现”。模型可见名称是 `mcp_repo_inspect_source`。每次调用前会展示服务名、工具名和实际参数，等待确认。启动服务与调用工具是两次不同的授权。

`--prompt` 没有交互确认，不会自动执行 MCP。restricted 命令白名单也不替代 MCP 授权。`--plan`、只读权限或存在未核实操作时，CLI 仍可进入会话，但不启动服务。先核实操作并 `/execute`，再输入 `/mcp-connect`。只读启动策略不能通过 `/execute` 提升权限。连接失败或用户拒绝启动不会丢失交互入口。

## 一次调用经过哪些消息

```text
CLI --mcp → 用户确认启动 → 本地服务进程
  → initialize(protocolVersion, clientInfo, capabilities={})
  ← 服务协议版本与 tools 能力
  → notifications/initialized
  → tools/list（必要时携带 cursor 分页）
  ← 工具名、描述和 inputSchema
  → 注册到 ToolRegistry → 发送模型工具 schema

模型返回 tool_call(mcp_repo_inspect_source, {})
  → 参数校验、权限/模式检查、用户确认
  → tools/call(name=inspect_source, arguments={})
  ← content / structuredContent / isError
  → ToolResult → role=tool，关联模型的 tool_call_id
  → 下一轮模型读取结果并决定是否继续
```

JSON-RPC 的 `id` 由 MCP 客户端递增分配，用来匹配服务响应；模型的 `tool_call_id` 属于模型会话。两者用途不同，不能混用。Agent 原有循环无需知道 MCP 字节协议：它仍然执行注册工具，并把 ToolResult 交回模型。

## 保存会话并不保存执行授权

`/save 名称` 保存模型消息及工具结果。下次 `--load 名称 --mcp mcp.json` 先恢复成计划模式，不启动服务、不重放调用。用户 `/execute` 后用 `/mcp-connect` 重新确认启动、握手和发现工具，再要求模型重新检查。

同一进程内成功 `/load` 或 `/resume` 也会关闭旧服务并移除工具；切换执行后需要 `/mcp-connect` 重新授权发现。部分服务故障时，重连会先关闭整组旧连接，再重新授权，避免留下旧进程。配置更新只在显式新连接时读取，工具列表在连接期间固定，不自动响应 tools/list_changed 做权限变化。连接故障后需核实 uncertain 操作，才能显式重连。

## 失败为什么要暂停

服务超时、断线、非法 JSON、响应 ID 不符、超限响应或不支持的返回内容，可能发生在服务已经写入之后。因此结果标记 `uncertain=true`，关闭连接，不自动重试。服务返回 `isError=true` 也按可能有副作用处理。现有 Agent 检查点会暂停后续写入，用户核实后才能继续。

本地参数校验失败或调用被拒绝发生在发送前，不标成未知副作用。服务请求客户端 sampling、elicitation 等能力会得到 Method not found，不会触发额外模型请求或终端操作。

## 当前兼容范围与限制

- 客户端支持 MCP **2025-11-25** stdio 生命周期与工具子集，可接受服务协商到 2025-06-18 或 2024-11-05；其它版本拒绝。不是最新协议的完整实现。
- 支持最多 8 个本地服务、总计 64 个工具、每服务最多 16 页发现。服务名为 1～16 位字母/数字/连字符，工具名为 1～40 位字母/数字/下划线/连字符。
- 参数是严格 JSON Schema 子集：object、array、string、integer、number、boolean，required、properties、items、description；最多 4 层，每对象最多 20 个字段。未知字段禁止；省略 additionalProperties 时也收紧为 false。SDK 的 title 仅作为展示标注移除。`$ref`、enum、anyOf、default、nullable 等未实现字段会拒绝整个连接，绝不偷偷删除验证约束。
- 支持文本 content 和对象 structuredContent；图片、音频、资源内容暂不支持。服务 outputSchema 不作为本地验证器使用。
- 每个协议帧不超过 64 KiB，调用参数不超过 32 KiB，一次请求 stdout/stderr 累计读取不超过 1 MiB、消息不超过 100 条。请求超时最大 60 秒。stderr 在等待响应时排空并丢弃，不混入协议或直接打印终端。
- 本地服务执行使用 argv，不经过 shell。只传基础环境变量，不继承模型密钥或其它环境凭据。当前没有服务专用 env/secrets 配置。
- 外部工具统一标记 write，不信任 readOnlyHint 自动放行。`--sandbox macos` 对服务进程同样限制写入和网络，但仍允许外部读取；无法隔离恶意代码。服务脚本及配置必须可信，普通进程也可能读取磁盘敏感文件。
- 退出终止服务进程组；主动脱离进程组的程序不在此清理保证内。实现使用 POSIX 管道与进程组，面向 macOS/Linux，不支持 Windows。
- HTTP/SSE、连接显式刷新和 OAuth 分别见 [HTTP 接入](30-mcp-http.md)、[连接管理](43-mcp-lifecycle.md)、[OAuth](50-mcp-oauth.md)。不支持 resources/prompts、客户端 sampling/elicitation；显式刷新不等于自动处理工具列表变更通知，不能把工具接入等同于全 MCP 支持。

## 验证方式

```bash
.venv/bin/python -m pytest tests/test_mcp.py -q
# 已通过环境变量配置 MiniMax 后执行；报告包含临时示例源码，不含 API key。
mkdir -p .fast-agent/validation
.venv/bin/python examples/real_mcp_demo.py > .fast-agent/validation/mcp-demo.json
```

完整脚本使用官方 SDK 服务、真实 MiniMax、临时 Git 仓库和 macOS 沙箱：MCP 发现错误 → 失败测试 → 精确修改 → MCP 确认修复 → 测试通过 → 保存会话 → 关闭旧服务 → 新 Agent 恢复计划模式 → 重新授权连接 → 再次 MCP 诊断与测试。最后独立执行测试、核对 Git 差异、确认测试文件未修改及服务已退出。实际验收：官方 SDK 1.30.0 + MiniMax-M3 完整验证 16 项全部通过；首轮工具依次包含 MCP 诊断、失败检查、源码读取/修改、MCP 复查和成功检查，恢复后再次 MCP 诊断与检查均成功。

源码入口：`mcp_transport.py` 负责消息与进程，`mcp_tools.py` 负责发现与授权，`cli.py` 负责连接生命周期。参阅 [MCP 生命周期规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)、[工具规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)及 [官方 Python SDK 1.x](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)。

下一章：[MCP 远程 HTTP 服务](30-mcp-http.md)，补充 Streamable HTTP 接入。
