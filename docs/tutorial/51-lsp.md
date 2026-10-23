# LSP：从文本检索走向符号与诊断

**通信层、Agent 工具与 CLI 配置已接入，真实 clangd 本地验证及 MiniMax 三工具/保存恢复验证通过。** 这不是编辑器插件，也不等同完整 IDE 集成；当前支持范围与权限边界见下文。

## 配置后交给 Agent 查询

创建 `lsp.json`，使用本机已安装语言服务的命令：

```json
{
  "argv": ["clangd", "--background-index=0"],
  "language_id": "cpp",
  "extensions": [".cpp"],
  "timeout": 10
}
```

```sh
fast-agent --workspace /你的项目目录 --lsp lsp.json
```

配置只加载工具，不自动启动进程。可请 Agent“查询 main.cpp 的函数符号，再定位 answer 调用的定义，并检查诊断”。注册的工具是 `lsp_symbols`、`lsp_definition`、`lsp_diagnostics`，都要求 `path`；定义查询另需从 1 开始的 `line` 和 `column`，输入列按 Unicode 字符计数，客户端转换成 UTF-16。返回的服务位置仍是 LSP 零基 UTF-16，不应直接当成编辑工具行列。

每次调用独立启动服务、打开文件、查询并关闭，避免恢复会话时复用旧诊断。代价是项目分析需要重新预热。执行模式每次确认启动；计划模式及未获批准时拒绝，不因名字带“查询”就把启动任意程序当作无副作用读取。

文件限定在当前工作区及配置的扩展名内，确认前后与分析结束后核对正文，变化则拒绝旧结果。语言服务本身可读取其他项目资料，启动沿用 `--sandbox` 设置；这不是读取隔离。定义可能指向外部库，返回 URI 是来源数据，不会自动读取该文件或扩大文件工具权限。

## 为什么搜索之外还需要语言服务

搜索 `answer` 能找到文本，但不能可靠地区分函数定义、调用和同名变量。语言服务分析代码语法与项目配置，可以回答“这个调用指向哪个定义”，也能返回错误位置和说明。

Fast Agent 作为 LSP 客户端，clangd 等程序作为语言服务。模型不直接操作语言服务进程。客户端需要限制消息大小、等待时间和允许的服务端请求，再把结果交给上层工具。

## 一次可运行的流程

本地安装 clangd 后，在项目目录运行：

```sh
python -m pytest tests/test_lsp_transport.py tests/test_lsp_tools.py -q
```

测试找不到 clangd 时会跳过真实语言服务部分，跳过不能算真实验收。其他边界测试仍运行。

真实测试使用临时 C++ 文件，依次完成：

1. 启动独立进程，发送 `initialize`，声明 UTF-16 位置编码。
2. 发送 `initialized` 和 `textDocument/didOpen`，带上文件 URI、正文和版本号。
3. 请求 `textDocument/documentSymbol`，获取函数符号；请求 `textDocument/definition`，核对调用的定义位置。
4. 对包含未定义名称的文件等待 `textDocument/publishDiagnostics`，核对当前版本的错误。
5. 关闭进程与管道，检查进程确实退出。

源码：[lsp_transport.py](../../src/fast_agent/lsp_transport.py)。它复用 stdio 进程管理，但使用 LSP 的 `Content-Length` 帧，不能把 MCP 的逐行 JSON 协议直接套过来。

## 为什么诊断必须关联版本

文件更新为版本 2 时，服务可能仍在发送版本 1 的诊断。把旧诊断当成当前错误会误导下一次编辑。`wait_diagnostics(uri, version)` 只接受 URI 与版本匹配的通知；无版本或旧版本不能证明当前文件已完成检查。等待超时也不等于“没有错误”。

目前该接口要求服务发送诊断版本。后续接入其他语言服务时，必须处理其能力差异，不能偷偷放宽版本检查。

## 通信边界

单帧正文最多 1 MiB，头部最多 8 KiB；一次请求或诊断等待累计输出最多 2 MiB、最多 100 条消息，并受截止时间限制。标准错误输出也计入总量，但不会当成协议消息。

服务端请求返回不支持；即使服务请求修改文件，客户端也不会执行。正常结束先发送 `shutdown`，收到空结果后发送 `exit`，短暂等待服务退出；协议等待与退出等待分别最多半秒。无响应、错误或超时仍清理进程组与管道，不能为了优雅关闭而无限等待。

源码入口：[lsp_tools.py](../../src/fast_agent/lsp_tools.py)负责配置、授权、文件版本与位置转换；[cli.py](../../src/fast_agent/cli.py)负责显式注册。结果是语言服务提供的数据，不是可信指令，也不能代替编译和测试。

## 真实模型验收

本地 clangd 测试证明语言服务能工作，不能证明模型实际会调用工具。已有模型环境变量后运行：

```sh
python examples/real_lsp_demo.py
```

脚本只在临时目录创建 C++ 样例，注册三个 LSP 工具，授权仅允许该样例的语言服务启动。独立检查函数名称、定义位置、当前版本错误诊断、源文件不变，以及保存恢复后的计划模式和消息一致性。通过与否取决于工具记录与磁盘事实，而非模型总结。它会消耗真实模型请求；输出用量中的未知值应保留。

验证通过 9/9 项，2 次模型请求，服务报告 3603 token，未知用量请求数为 0。结果保存于 `.fast-agent/validation/lsp-real-canonical.json`。恢复时系统提示会切换为计划模式，因此消息一致性比较的是非系统消息；不能要求旧执行授权也恢复。定义查询使用正常样例 `main.cpp`，错误诊断使用 `broken.cpp`，路径在比较前统一为真实路径。

该场景只证明临时 C++ 样例的工具查询与恢复行为，不代表所有语言服务都兼容。

协议反例测试已覆盖超大帧、重复长度头、不匹配的请求 ID、无效诊断参数，以及服务端 `workspace/applyEdit` 请求。后者收到“不支持”响应，测试核对本地文件未被修改。测试使用真实子进程管道，异常后检查进程退出，而不仅是模拟一个错误返回值。

协议参考：[LSP 3.17](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/)。
