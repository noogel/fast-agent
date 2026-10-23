# MCP OAuth：授权、刷新与登出

OAuth 让 Fast Agent 代表用户访问受保护的 HTTP MCP 服务。授权码与令牌只经过本地控制层和 HTTP 传输层，不进入模型消息。stdio 继续使用环境凭据。

## 配置与操作

在 MCP 配置中为 HTTP 服务添加 OAuth，不能同时配置 `bearer_env`：

```json
{
  "servers": [
    {
      "name": "secured",
      "url": "https://mcp.example/mcp",
      "oauth": {
        "issuer": "https://auth.example",
        "client_id": "your-registered-public-client",
        "scope": "read",
        "port": 9000
      }
    }
  ]
}
```

这些是示例地址，需换成实际服务及预注册公共客户端信息。回调为 `http://127.0.0.1:9000/callback`，必须与注册信息一致；端口 0 可动态分配，但需授权服务支持。凭据默认保存在 `~/.local/share/fast-agent/oauth`，可配置绝对路径 `directory`，但不能位于当前工作区内。

使用私有 CA 的 HTTPS 服务，可在服务配置顶层添加 `"ca_file": "/绝对路径/company-ca.pem"`。该文件必须是最多 1 MiB 的普通 PEM 文件，不能是符号链接；证书添加到默认信任集合，仍校验服务主机名。OAuth 元数据/令牌请求和 MCP 传输共用这项显式信任配置；不要把服务证书错误改成关闭 TLS 校验。启动确认会显示 CA 文件路径。

```text
/mcp-login secured
/mcp-auth-status secured
/mcp-connect
/mcp-logout secured
```

登录需执行模式和交互确认。终端展示授权 URL，由用户在浏览器完成授权；本机回调收到匹配的 state 后交换授权码并保存凭据。登录不自动执行工具。状态仅显示有效期、scope 等脱敏信息。登出关闭现有 MCP 连接并移除工具定义，清除本地凭据；需要时重新 `/mcp-connect`。

`/mcp-revoke secured` 另请求远端撤销。本地删除和远端撤销是两个结果：服务未声明撤销端点或网络失败时，本地仍已删除，不能声称远端成功撤销。仅本地登出无需先完成在线发现。

## 执行链路与代码

1. `oauth_discovery.py` 无凭据读取资源元数据，支持 challenge 和 well-known；按 OAuth/OIDC 顺序发现授权方。资源/issuer 必须匹配，跨来源需批准，拒绝重定向和超过 64 KiB 的响应，不发送 Cookie。
2. `oauth_flow.py` 生成 PKCE S256 与随机 state；事务五分钟有效。授权码只能消费一次，交换表单绑定目标 resource。
3. `oauth_callback.py` 先绑定回环端口再生成授权 URL。校验 Host/path/state，不记录 URL 或授权码；超时、取消、成功后关闭端口。
4. `oauth_tokens.py` 交换/刷新/撤销不自动重试，检查 Bearer、有效期和 scope 不扩张，错误不包含服务原文。
5. `oauth_store.py` 按 resource/issuer/client ID 绑定，要求当前用户拥有的 0700 目录、0600 文件，拒绝链接，原子替换并使用凭据锁。它是文件权限隔离，不是钥匙串加密；当前用户权限的其他进程仍可能读取。
6. `oauth_session.py` 串联上述组件。MCP 每次请求通过令牌提供器检查凭据，到期前 30 秒刷新；连接关闭只使用已有令牌尝试结束远端会话，不刷新。

## 失败如何处理

刷新前移除旧凭据。若服务已经轮换刷新令牌，但响应丢失或新令牌保存失败，旧令牌不会被重复尝试；需要重新登录。这牺牲部分便利性，保留明确的未知状态边界。

错误 Host/state 不消费有效授权事务；用户取消则结束等待。401 或工具响应失败不会自动重放工具。凭据不可用时请求停止，先检查状态，再显式登录。未授权的计划模式不能自动提升权限。

## 验收与限制

已通过受控元数据/令牌 HTTP transport 测试、本机真实回调到令牌交换和保存的控制层测试、请求头更新与静态凭据互斥测试。完整本地 HTTPS 授权/MCP 链路也已通过（临时受信证书，未关闭 TLS）；示例不接入外部用户账户；受控服务结果不代表外部用户授权成功。

目前支持预注册公共客户端，要求授权服务明确支持授权码、PKCE S256 和 `none` 客户端认证；不支持动态注册或客户端密钥认证。发现和令牌客户端沿用 HTTP 库的环境代理配置；受控 CONNECT 代理转发已通过下方本地验收；日常回环场景应确保 NO_PROXY 包含 localhost、127.0.0.1。

协议依据：[MCP 2025-11-25 Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)。

## 超时边界不要混淆

授权交互和令牌刷新在 MCP 网络事件循环之外完成。MCP 的总截止时间覆盖真正的 POST、响应和 SSE，不涵盖用户确认时间或前置 OAuth 请求。OAuth 元数据与令牌请求分别使用自身网络超时；回调等待有五分钟上限。不能把这些阶段加起来的耗时宣传成单个 MCP timeout。

本地受保护 HTTP MCP 验证覆盖 initialize、tools/list、tools/call 与关闭：每个普通请求之前获取凭据，初始化通知和关闭使用当前凭据，不再触发授权或刷新。`tests/test_oauth_https.py` 验证完整本地 HTTPS 授权服务链路。

## 运行完整 HTTPS 链路测试

安装开发依赖后可运行：

```sh
python -m pip install -e '.[dev]'
python -m pytest tests/test_oauth_https.py -q
```

测试为临时工作区生成一天有效的本地证书，显式信任该证书且检查主机名。实际 HTTPS 服务提供资源发现、令牌、撤销和 MCP 端点；回调仍是 OAuth 规定的本机 HTTP。测试完成授权保存、两次工具调用（第二次触发刷新）、撤销及本地凭据删除。证书与令牌均为夹具，不访问用户外部账号。

授权步骤实际访问 `/authorize`，由该端点重定向到回调；令牌端点检查 PKCE S256、redirect URI、resource、client ID 和授权码单次使用。它不是直接给客户端塞入一个令牌。测试信任文件同时保留公共 CA 与临时 CA，不关闭 TLS 校验。

需要额外运行真实模型链路时，在已有模型环境配置下显式执行：

```sh
FAST_AGENT_OAUTH_REAL=1 python -m pytest tests/test_oauth_https.py -q
```

普通测试不执行模型请求。启用后，通过实际 `MCPManager` 加载带 OAuth/CA 的配置，发现并注册工具，模型经 Agent 循环调用；调用前令牌被设为过期，核对刷新与服务端收到请求。新结果写入 `.fast-agent/validation/oauth-manager-minimax.json`。外部服务的交互授权仍需实际目标服务和用户完成登录。

真实模型示例通过实际 `MCPManager` 入口，检查工具发现、调用、令牌刷新与恢复边界。普通回归另覆盖刷新后的旧工具失效与断开状态；私有 CA 通过配置生效，而非依赖环境中的测试 CA。

`HttpClient` 可由程序传入校验证书且校验主机名的 SSLContext；拒绝关闭校验的上下文。此接口用于明确的信任配置，不能绕过证书错误。

### 附加 CA 文件的读取边界

`ca_file` 是宿主显式配置的信任材料。读取时以禁止末级符号链接、非阻塞方式打开，随后对已打开的描述符检查普通文件类型与1 MiB大小限制，并且实际最多读取1 MiB加1字节。这样文件在检查后增长也不能引起无界读取，替换成管道也不会等待写入者。超过上限直接拒绝；证书仍由SSL解析，保留系统根证书和主机名校验。

这不是对父目录的隔离或不可变快照保证；CA所在目录仍应由用户控制。测试覆盖大小检查失真时的实际读取上限、末级链接拒绝，并重新运行真实本地HTTPS授权与MCP链路。

### 验证环境代理实际生效

HTTPS测试分别运行直连与代理两种模式。代理模式只在测试进程内清空NO_PROXY并设置HTTPS_PROXY到临时回环CONNECT服务；授权发现、令牌交换/刷新与撤销照常验证TLS证书。代理记录目标地址和连接数，不读取解密后的授权内容。断言至少三个实际隧道且目标只能是本次本地HTTPS服务，避免只设置环境变量就声称代理已使用。

两种模式均通过，记录 `oauth-proxy-tests.log`。这里证明OAuth HTTP客户端的环境代理路径；MCP回环传输仍按自身策略直连，浏览器回调也直接访问回环地址。不能由此推断某个外部代理当前可用，或外部用户账户授权已完成。普通pytest不使用MiniMax凭据；显式真实模型开关只在直连参数运行一次，避免参数化后重复付费调用。
