# Fast Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现可离线演示、可接入真实模型、配套中文教程的 Python 终端 Agent。

**Architecture:** 同步模型接口与工具注册表解耦，Agent 核心维护协议正确的历史并驱动工具循环。CLI 负责用户输入、确认和事件展示；模拟模型复用真实工具执行链路。

**Tech Stack:** Python >=3.11、官方 OpenAI Python SDK、pytest、标准库终端与 subprocess。

**Spec:** `../specs/2026-10-19-fast-agent-design.md`

## Global Constraints

- 项目根为 `/Users/xyz/MyProjects/awesome/fast-agent`，属于 awesome 仓库，不创建嵌套仓库。
- 使用 Python 3.11 及以上、同步调用、普通函数和数据类。
- 每轮默认 10 次模型调用；达到上限明确报告未完成。
- 真实模式读取 FAST_AGENT_API_KEY、FAST_AGENT_BASE_URL、FAST_AGENT_MODEL；模型及密钥必填，地址默认 https://api.openai.com/v1。
- 不自动加载 .env；模拟模式不需要密钥且不联网。
- 命令执行统一交互确认，非交互模式拒绝；覆盖已有文件需确认。
- 文件路径解析符号链接后检查工作目录边界；命令不是沙箱。
- 本机验证，不进行 Docker 部署，不修改其他子项目。
- 文档、注释和面向用户的错误说明使用中文。

## Review Focus

- Ctrl+C 发生在多个工具之间：必须补齐未完成调用的错误结果，保证下一轮消息有效（任务 3）。
- 模型返回空回复或畸形调用 ID：明确协议错误，不能显示任务成功（任务 1、3）。
- 命令产生大量输出或子进程持续持有输出管道：内存有界，超时后进程组终止（任务 2）。
- 文件包含非 UTF-8 字节、目标为目录或替换字符串为空：返回可理解的工具错误（任务 2）。
- 第二次模拟任务与清空历史：不能复用上次演示状态而假报成功（任务 4）。

## 文件布局与接口约定

`src/fast_agent/` 包含 `__init__.py`、`__main__.py`、`config.py`、`models.py`、`providers.py`、`agent.py`、`tools.py`、`file_tools.py`、`command_tool.py`、`cli.py`。将文件与命令工具从注册表拆出，避免单个 tools.py 过大。

`models.py` 定义 `ToolCall(id: str, name: str, arguments: str)`、`Message(role: str, content: str | None, tool_calls: list[ToolCall], tool_call_id: str | None)`、`ModelReply(content: str | None, tool_calls: list[ToolCall])`、`AgentResult(text: str, completed: bool, steps: int)` 和 `Event(kind: str, data: dict)`。列表字段使用 default_factory。

`Model.complete(messages: list[Message], tools: list[dict]) -> ModelReply` 为协议；`ToolResult(ok: bool, data: dict, error: str | None)` 为工具结果。`ToolContext(root: Path, confirm: Callable[[str], bool], interactive: bool)` 携带执行上下文。`Tool(name: str, description: str, parameters: dict, handler: Callable[[dict, ToolContext], ToolResult])` 描述工具。

后续任务全部以项目根为命令工作目录；Git 提交只添加本项目明确文件，不包含其他未跟踪子目录。

### Task 1: 模型接口与可安装包

**Files:** 创建 `pyproject.toml`、`.gitignore`、`.env.example`、`src/fast_agent/{__init__,config,models,providers}.py`；测试 `tests/test_config.py`、`tests/test_providers.py`。

**Interfaces:** `Config.from_env(env: Mapping[str,str]) -> Config`；`OpenAIModel(config: Config, client=None).complete(...) -> ModelReply`；任务 2、3 使用上述数据结构。

- [ ] 编写测试：缺模型或密钥抛出配置错误；默认地址准确；配置 repr 不含测试密钥；假客户端收到工具定义及含 tool_call_id 的回传消息；空响应/重复或缺失调用 ID 抛出协议错误；认证错误不泄漏密钥。
- [ ] 创建本地 .venv，确认 Python >=3.11，安装测试所需依赖，运行 `.venv/bin/python -m pytest tests/test_config.py tests/test_providers.py -q`，确认因待实现接口失败。
- [ ] 实现配置及协议结构、SDK 消息转换和可注入客户端；SDK 网络超时 30 秒、禁用隐式重试，异常转换为不含原始凭据的 ProviderError。真实客户端按需创建，模拟路径不初始化 SDK 客户端。
- [ ] 完成 pyproject 打包配置与 dev 依赖，安装 `pip install -e '.[dev]'`；记录实际验证的依赖版本。
- [ ] 运行本任务测试，全部通过后仅提交本任务文件：`feat: add model protocol and provider adapter`。

### Task 2: 工具注册和执行边界

**Files:** 创建 `src/fast_agent/{tools,file_tools,command_tool}.py`；测试 `tests/test_tools.py`、`tests/test_file_tools.py`、`tests/test_command_tool.py`。

**Interfaces:** `ToolRegistry.register(tool: Tool) -> None`、`schemas() -> list[dict]`、`execute(call: ToolCall, context: ToolContext) -> ToolResult`；`build_default_registry() -> ToolRegistry`。

- [ ] 编写测试：未知工具、非法 JSON、缺参数、错误类型及多余字段返回 ok=False；成功调用可 JSON 序列化；重复注册拒绝。文件测试断言越界、符号链接越界、目录、无效 UTF-8、空或非唯一替换失败；覆盖拒绝保留原文；合法读写与替换成功。
- [ ] 编写命令测试：无交互或拒绝时不启动进程；参数数组按字面传递；非零退出保留退出码；超时终止进程组；大量输出不超过限制，子进程持管道仍受超时约束。测试用 sys.executable 启动受控脚本，不依赖系统网络。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_tools.py tests/test_file_tools.py tests/test_command_tool.py -q`，确认缺失实现导致失败。
- [ ] 实现注册、参数校验、错误序列化；文件读取上限 64 KiB，写入文本上限 64 KiB，精确替换要求 old 非空且唯一；所有文件路径 resolve 后检查归属。覆盖确认由 ToolContext 注入。
- [ ] 实现 `run_command` 的 argv 数组，shell=False、默认 10 秒且最大 60 秒；以新进程组运行，用 selectors 分块读取合计最多 64 KiB 输出，继续排空但不缓存溢出内容。超时或中断杀死进程组并回收子进程，结果携带退出码及截断标记。
- [ ] 运行本任务测试全部通过，提交 `feat: add validated file and command tools`。

### Task 3: Agent 循环与历史管理

**Files:** 创建 `src/fast_agent/agent.py`、`tests/test_agent.py`。

**Interfaces:** `Agent(model: Model, registry: ToolRegistry, context: ToolContext, max_steps: int = 10, on_event: Callable[[Event],None] | None = None)`；`run(text: str) -> AgentResult`；`reset() -> None`。消费任务 1、2 的模型、消息及注册表。

- [ ] 编写序列模型测试：直接回答 completed=True 且 steps=1；工具结果按 ID 回传；多工具顺序执行；错误结果可被下一次模型调用看到；连续工具调用恰好 10 次后 completed=False；第二轮保留历史；reset 只保留系统消息。
- [ ] 编写中断与协议测试：工具执行期间 KeyboardInterrupt 补齐当前及剩余调用结果；ProviderError 不输出成功；空回答无工具不算完成；max_steps<=0 拒绝。用 assert 验证每个 assistant 工具 ID 都有对应 tool 消息。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_agent.py -q`，确认接口尚未实现导致失败。
- [ ] 实现同步循环，先记录 assistant 调用再执行工具；每轮执行最后一批工具并保存结果后才返回步数限制提示。发送 model_start、tool_start、tool_end、answer、error 事件；不展示隐藏思维链。
- [ ] 运行本任务及前两任务测试全部通过，提交 `feat: implement bounded agent loop`。

### Task 4: 终端与离线完整演示

**Files:** 创建 `src/fast_agent/{cli,__main__}.py`、`examples/offline_demo.py`、`tests/test_cli.py`、`tests/test_demo.py`；更新 `providers.py` 和 pyproject 入口。

**Interfaces:** `MockModel.complete(...) -> ModelReply`；`cli.main(argv: list[str] | None = None) -> int`；`run_demo() -> AgentResult`。命令入口 `fast-agent` 与 `python -m fast_agent`。

- [ ] 编写测试：--help 退出 0；真实模式缺配置退出非零且信息清晰；--mock 不访问网络；/exit、/reset、EOF 和 Ctrl+C 正常处理；非交互命令拒绝；确认仅显式 y/yes 同意。
- [ ] 编写离线集成测试：临时目录中初始标题真实变化，事件依次包含 read_file、edit_file、read_file；最终结果验证复读内容；故意工具失败不能总结成功；重复运行互不污染。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_cli.py tests/test_demo.py -q`，确认缺失功能失败。
- [ ] 实现 --mock、--demo、--workspace、--max-steps、--prompt 和交互终端；--demo 创建临时目录并准备样本，走同一 Agent 和工具链。--mock 普通对话明确标注模拟，不假装理解任意任务；演示按历史中的实际工具结果推进。
- [ ] 运行上述测试，以及 `.venv/bin/fast-agent --help`、`.venv/bin/fast-agent --demo`、`.venv/bin/python examples/offline_demo.py`，要求退出 0 且演示报告真实修改成功。
- [ ] 提交 `feat: add terminal interface and offline demo`。

### Task 5: 中文教程与交付验证

**Files:** 创建 `README.md`、`docs/tutorial/01-model.md` 至 `06-custom-tool.md`、`examples/single_call.py`、`examples/custom_tool.py`；更新 `.env.example`。

**Interfaces:** 教程引用已有公共接口；自定义工具示例通过 ToolRegistry.register 添加一个纯函数工具，避免另写 Agent 核心。

- [ ] 编写六章中文教程：单次调用、工具协议、循环、执行边界、多轮和事件、自定义工具。每章链接真实源码，明确离线/联网要求。
- [ ] README 写出安装、环境变量 export、交互、离线演示、测试命令和目录结构；说明仅工作目录约束不是命令沙箱，不自动加载 .env，不支持的模型可能拒绝 tools。
- [ ] 在未配置 FAST_AGENT_* 的子进程中执行离线演示；执行自定义工具示例；确认输出符合教程。单次真实调用示例在缺配置时清晰退出，不自动查找或复用其他项目密钥。
- [ ] 运行 `.venv/bin/python -m pytest -q` 和 `.venv/bin/python -m compileall -q src examples`，全部成功；如有用户授权的模型配置，执行一次真实工具调用，否则在报告中标明未验证。
- [ ] 从 awesome 根仅暂存 fast-agent 文件并执行 `git diff --cached --check`，提交 `docs: add Chinese agent tutorial and usage examples`。
- [ ] 最终检查 diff 范围及状态，报告实际测试数量、演示结果、真实模型验证状态和启动命令，不将文档完成等同于功能完成。

## 自检与执行交接

已对照设计核对：核心循环、四个工具、配置、错误回传、交互确认、模拟模式、教程与验证均有对应任务。模型协议和工具类型沿用统一接口；审查重点均进入所属任务测试。实现记录见同目录 execution-notes.md。

推荐在当前会话顺序执行：这五个任务共享接口且有明确依赖，顺序实现更便于保持教学代码一致。用户已批准在当前会话顺序实现。

完成说明：实现已完成，历史计划的步骤勾选保留原样；实际测试文件合并、一次聚焦提交等调整及验证结果见 execution-notes.md。真实模型未连接，未进行远端部署。
