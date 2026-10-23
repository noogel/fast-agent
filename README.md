# Fast Agent

一个从零讲解 Agent 工作原理的 Python 教学项目：代码能读懂，工具会真正执行，离线也能体验完整循环。

```text
用户输入 → 模型回复 → 有工具调用？
                       ├─ 否 → 最终回答
                       └─ 是 → 校验参数 → 执行工具 → 保存结果 → 再调用模型
```

## 五分钟运行

需要 Python 3.11 或更新版本。命令工具支持 macOS/Linux。代码检索还需要 ripgrep（`rg`，macOS 可用 `brew install ripgrep` 安装）。

```bash
cd /Users/xyz/MyProjects/awesome/fast-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
fast-agent --demo
```

演示在临时目录创建 `note.txt`，由模拟模型依次调用真实的 `read_file → edit_file → read_file`，将“旧标题”改为“Fast Agent”，最后检查磁盘内容。演示结束清理临时目录，不需要密钥、不联网、不执行系统命令。

模拟模型是预设协议响应，不具有真实自然语言推理能力。`fast-agent --mock` 可体验终端交互，但不会自动理解任意任务。

## 接入真实模型

服务必须支持 **Chat Completions 的 tools 工具调用协议**。模型名称由你实际使用的服务决定。

```bash
export FAST_AGENT_BASE_URL='https://api.openai.com/v1'
export FAST_AGENT_MODEL='你的模型名称'
# 通过终端交互读取密钥，避免把实际值写进命令历史（下例使用 zsh）。
read -rs 'FAST_AGENT_API_KEY?API Key: '
export FAST_AGENT_API_KEY
fast-agent --workspace /你的练习目录
```

上面的 `read` 语法适用于 macOS 默认 zsh；bash 使用 `read -rs -p 'API Key: ' FAST_AGENT_API_KEY`。`.env.example` 仅作参考，程序**不会自动加载 .env**。不设置 BASE_URL 时默认使用 `https://api.openai.com/v1`。

可以输入：“读取 note.txt，把里面唯一的旧标题替换成 Fast Agent，然后再次读取确认。”工具事件展示参数和结果；这不是模型的隐藏思维链。

- `--plan` 启动计划模式；`/plan` 切回只读规划，`/execute` 显式切换执行（仍遵循启动权限）。
- `/load`、`/resume` 和 `--resume` 恢复后始终进入计划模式，修改前需 `/execute`。
- `/tasks` 查看任务，`/resume ID` 恢复检查点；CLI 默认自动保存。
- `/jobs` 查看后台任务，`/job ID` 读日志，`/cancel ID` 请求取消。
- `--permissions read-only|confirm|restricted` 选择权限，详见第 10 章。
- `/usage` 查看进程内请求/token 用量；`FAST_AGENT_MAX_REQUESTS` 设置请求总预算（含摘要和重试，0 不限）。
- `--sandbox macos` 限制同步/后台命令的写入和网络；不提供外部读取隔离。
- `/batches`、`/undo-batch ID` 查看和显式恢复多文件批次。
- `/plans`、`/show-plan 名称` 查看持久任务计划。
- `--checks checks.json` 显式加载质量检查；`/check-runs`、`/check-run ID` 查看执行证据。
- `--extensions extensions.json` 显式加载受审批的本地进程工具。
- `/reset`：清空会话历史，不撤销文件操作，也不解除未知操作的阻止。
- `/history` 查看消息，`/context` 查看消息数、轮数和字符数。
- `/save 名称` 保存到工作目录 `.fast-agent/sessions/`，同名会覆盖；`/load 名称` 校验后恢复。
- `/trim N` 保留系统消息与最近 N 轮完整结构；`/trim 0` 清空历史轮次。
- `/exit` 或输入等待期间 Ctrl+C：退出。
- 执行期间 Ctrl+C：中断本轮，补齐工具结果，保留会话。
- `--max-steps 5`：限制每轮任务模型调用次数，默认 10，额外摘要调用不计入。
- 默认流式输出；`--no-stream` 关闭。
- 模型连接超时默认 10 秒，读取超时 60 秒，最多重试 2 次；通过 `FAST_AGENT_CONNECT_TIMEOUT`、`FAST_AGENT_READ_TIMEOUT`、`FAST_AGENT_MAX_RETRIES` 设置。收到任何流式片段后不自动重试。
- `--context-chars 60000 --keep-rounds 2` 设置自动摘要预算和保留轮数；`--context-chars 0` 关闭自动摘要。
- `--prompt '你的任务'`：单轮运行后退出，拒绝需要交互确认的命令和文件修改。

## 内置工具

| 工具 | 参数 | 行为 |
|---|---|---|
| `read_file` | `path, start_line?, end_line?` | 分段读取并附带 AGENTS.md；文件上限 1 MiB，单次输出上限 64 KiB |
| `write_file` | `path, content` | 创建或覆盖文本，展示差异并确认；已有文件须先读取 |
| `edit_file` | `path, old, new` | 唯一精确替换；先读取、检查外部变化、确认差异后原子写入 |
| `multi_edit_file` | `path, edits: [{old, new}]` | 同一文件按顺序预演 1–100 处精确替换，全部成功后确认整体 diff 并原子写入 |
| `edit_files` | `files: [{path, old, new}]` | 多文件整体预检/审批，逐文件写入，保留批次快照用于显式恢复 |
| `list_edit_batches` | 无 | 查看批次编号、路径与状态 |
| `get_plan` / `list_plans` | `name` / 无 | 查询持久任务计划 |
| `update_plan` | `name, expected_revision, title, steps` | 经确认更新计划，拒绝版本冲突 |
| `list_checks` / `run_checks` | 无 / `names` | 查询或执行用户配置的检查，保留实际退出码 |
| `get_check_run` | `run_id` | 读取历史检查证据 |
| `run_command` | `argv, timeout?` | 程序及参数数组；每次确认，默认 10 秒、最多 60 秒 |
| `list_files` | `path?, limit?` | 列出文件，默认最多 100 项，最大 500 |
| `search_code` | `query, path?, limit?` | 固定文本搜索，返回路径和行号 |
| `read_instructions` | `path` | 新建文件前读取沿途 AGENTS.md |
| `git_status` | 无 | 工作目录内 Git 状态 |
| `git_diff` | `path?, staged?` | 工作目录内未暂存或暂存差异 |
| `read_command_log` | `log_id, offset?, tail?, limit?` | 只读同步命令日志，最多保存 1 MiB，按字节分页 |
| `start_command` | `argv, timeout?` | 后台命令，返回持久化 job_id |
| `poll_command` | `job_id, offset?, wait_seconds?` | 有界日志、状态和退出码 |
| `cancel_command` | `job_id` | 经确认请求取消后台命令 |

文件操作限制在工作目录，修改前检测文件和规则变化。读取快照仅在内存存在，恢复会话后需重新读文件。检索遵循忽略规则并排除运行目录与 .env*；禁用 ripgrep 用户配置及符号链接跟随。Git 工具禁用外部过滤器等隐式执行路径。同步命令实时展示最多 64 KiB、传回模型最多 4 KiB 前缀，磁盘日志最多保留 1 MiB，超时、中断或结束时清理同组子进程。独立后台任务最多保存 1 MiB 日志，通过 job_id 查询和取消。

**边界说明：** 工作目录限制不是命令沙箱。默认无沙箱模式下，获准执行的程序可以访问当前用户有权限访问的资源，也会继承除模型 API 密钥之外的环境变量；可选 `--sandbox macos` 提供写入与网络限制，但不限制外部读取；命令确认前请阅读参数。文件路径校验不抵御并发符号链接替换。CLI 默认自动保存任务检查点，也可手动保存会话、恢复与裁剪，超过字符预算时自动摘要旧轮次，并保留原始备份；真实服务会收到相关文件内容和工具结果。

## 浏览器阅读教程

```bash
.venv/bin/python -m pip install -e '.[docs]'
.venv/bin/python -m mkdocs serve
```

访问 http://127.0.0.1:8000/，支持中文搜索、章节导航和代码参考页。修改 Markdown 后自动刷新，Ctrl+C 停止。静态构建：`.venv/bin/python -m mkdocs build --strict`，产物位于 `site/`。

## 阅读顺序

1. [单次模型调用](docs/tutorial/01-model.md)
2. [工具协议与注册](docs/tutorial/02-tools.md)
3. [Agent 循环](docs/tutorial/03-loop.md)
4. [文件、命令和执行边界](docs/tutorial/04-boundaries.md)
5. [多轮对话与事件](docs/tutorial/05-conversation.md)
6. [添加自己的工具](docs/tutorial/06-custom-tool.md)
7. [保存、恢复与裁剪上下文](docs/tutorial/07-context.md)
8. [本地仓库开发完整链路](docs/tutorial/08-repository.md)
9. [自动上下文压缩与流式交互](docs/tutorial/09-compaction-streaming.md)
10. [任务恢复、后台命令与权限](docs/tutorial/10-tasks-jobs-permissions.md)
11. [模型请求的超时、重试与执行边界](docs/tutorial/11-model-request-reliability.md)
12. [计划模式与执行模式](docs/tutorial/12-plan-execute.md)
13. [批量精确编辑](docs/tutorial/13-multi-edit.md)
14. [命令执行实时输出](docs/tutorial/14-command-streaming.md)
15. [长日志保留与按需读取](docs/tutorial/15-command-logs.md)
16. [单次长任务的上下文压缩](docs/tutorial/16-active-compaction.md)

## 源码地图

```text
src/fast_agent/
  config.py        环境配置
  models.py        消息、模型回复、工具调用和事件类型
  providers.py     OpenAI 适配器、请求重试与离线模拟模型
  request_policy.py  临时故障识别、退避与 Retry-After
  streaming.py     流式文本和工具参数组装
  compaction.py    按整轮自动摘要、预算检查与原始会话备份
  tools.py         工具注册、参数校验、执行与结果类型
  file_tools.py    分段读取、差异确认、冲突检查与原子修改
  repository_tools.py  检索、规则查询与 Git 检查
  workspace.py     路径边界和分层 AGENTS.md
  command_tool.py  命令确认、超时与有界输出
  permissions.py   只读、确认和精确命令白名单
  tasks.py          检查点、未知结果与人工核实
  runtime_store.py  原子记录与执行锁
  jobs.py / job_worker.py  后台命令、日志和取消
  agent.py         核心循环与历史管理（建议重点阅读）
  sessions.py      原子保存、消息校验、整轮裁剪和统计
  session_commands.py  本地会话命令
  cli.py           终端交互与离线演示
examples/          可运行示例，复用主实现
tests/            协议、边界与端到端测试
```

## 验证

```bash
python -m pytest -q
python examples/offline_demo.py
python examples/custom_tool.py
# 需要真实模型配置：
python examples/single_call.py
python examples/real_session_demo.py
python examples/real_repository_demo.py
python examples/real_context_stream_demo.py
python examples/real_reliability_demo.py
python examples/real_plan_demo.py
python examples/real_multi_edit_demo.py
python examples/real_command_stream_demo.py
python examples/real_command_log_demo.py
python examples/real_active_compaction_demo.py
```

开发验证环境：macOS、Python 3.13.5、OpenAI SDK 3.35.1、pytest 9.1.1。真实模型连接需要自行配置；离线演示通过不代表某个远端模型已验证。第一版是本地 CLI，无 Docker 或部署服务。

已用 MiniMax-M3 验证跨进程会话恢复和临时仓库测试修复。现已提供自动上下文摘要和终端流式输出；新增任务检查点、跨进程恢复、后台命令和权限策略；现已补充请求预算、macOS 命令写入与网络隔离、多文件恢复、持久计划、配置质量检查和本地扩展；不包含分布式调度或完整 IDE 集成。后台任务默认最长 300 秒，可设为最多一小时。

- [17 · 文件编辑记录与安全撤销](docs/tutorial/17-edit-undo.md)：`/changes` 查看持久记录，`/undo ID` 经确认恢复已有文件；真实验证使用 `examples/real_edit_undo_demo.py`。

- [18 · 更精确的代码检索](docs/tutorial/18-precise-search.md)：文件 glob、显式正则与匹配上下文；真实验证使用 `examples/real_precise_search_demo.py`。

- [19 · 手动上下文压缩](docs/tutorial/19-manual-compaction.md)：`/compact [关注点]` 主动摘要，先备份并保留完整工具消息；真实验证使用 `examples/real_manual_compaction_demo.py`。

- [20 · 会话列表与启动时加载](docs/tutorial/20-session-list-load.md)：`/sessions` 分页查找，`--load 名称` 以计划模式恢复；真实验证使用 `examples/real_session_load_demo.py`。

- [21 · 任务继续执行与步数控制](docs/tutorial/21-continue-steps.md)：`/continue [说明]` 继续原任务，`/steps [次数]` 查看或调整上限；真实验证使用 `examples/real_continue_steps_demo.py`。

- [22 · 重复失败检测与暂停](docs/tutorial/22-repeated-failures.md)：连续三次相同失败时保存检查点，用户可补充信息后继续；真实验证使用 `examples/real_repeated_failure_demo.py`。

- [23 · 调用用量与请求预算](docs/tutorial/23-usage-budget.md)：统计普通调用、摘要与重试；请求耗尽后保存并恢复。

- [24 · 命令写入与网络隔离](docs/tutorial/24-command-sandbox.md)：macOS 同步与后台命令统一限制写入和网络，失败不降级。

- [25 · 多文件修改批次与安全恢复](docs/tutorial/25-edit-batches.md)：整体预检与审批、中断收据、冲突保护和显式恢复。

- [26 · 持久任务计划与版本冲突](docs/tutorial/26-task-plans.md)：保存结构化步骤，拒绝过期更新，恢复后重新查询最新计划。

- [27 · 配置质量检查与执行证据](docs/tutorial/27-quality-checks.md)：显式加载检查命令，保留实际退出码和日志，恢复后重跑。

- [28 · 显式加载本地工具扩展](docs/tutorial/28-local-extensions.md)：独立进程协议、参数校验、权限继承与真实模型验证。


### MCP stdio 工具（第 29 章）

使用 `--mcp /绝对路径/mcp.json` 显式加载本地服务；启动与调用分别确认。
计划模式和恢复会话先不启动，核实后 `/execute` → `/mcp-connect`。
服务配置格式为 `{"servers":[{"name":"repo","argv":["/绝对路径/python","/绝对路径/server.py"],"timeout":15}]}`。
支持 2025-11-25/2025-06-18/2024-11-05 stdio 工具子集、有限参数 schema、文本和对象返回；HTTP 接入见第 30 章；不支持 OAuth 或最新协议的全部能力。
客户端无新增运行依赖；官方 SDK 示例安装 `pip install -e '.[mcp-test]'`。
教程见 [MCP 工具发现、调用与恢复](docs/tutorial/29-mcp-tools.md)，完整 MiniMax 验证脚本为 `examples/real_mcp_demo.py`。

### MCP Streamable HTTP（第 30 章）

配置支持 `{"servers":[{"name":"repo","url":"https://mcp.example.com/mcp","bearer_env":"MY_MCP_TOKEN","timeout":15}]}`。凭据从独立环境变量读取，连接和调用分别确认；支持 JSON/SSE、会话头和关闭时尽力 DELETE。恢复后重新授权连接，不自动重放失败请求。

教程见 [MCP 远程 HTTP 服务](docs/tutorial/30-mcp-http.md)，完整 MiniMax 验证脚本为 `examples/real_mcp_http_demo.py`。官方 SDK 示例与互操作测试需要 `.[mcp-test]`；客户端复用现有 httpx2 依赖。

### 上下文诊断（第 31 章）

`/context` 展示系统提示、对话、工具结果、工具定义的精确字符分项、预算余量和最大的五个工具定义；不调用模型、不修改会话。见 [中文教程](docs/tutorial/31-context-diagnostics.md)。

### 本地 Skill（第 32 章）

通过 `--skills examples/skills` 显式加载工作区内 Skill 目录，提供发现、读取 SKILL.md、列资源和分段读取四个只读工具。Skill 不授予命令执行权限；恢复后须再次配置。见 [中文教程](docs/tutorial/32-local-skills.md)。本阶段真实模型验证待 MiniMax 服务恢复后补齐。

Skill 发现现已支持受限 `name`/`description` 元数据、多行用途和错误隔离，见 [第 33 章](docs/tutorial/33-skill-metadata.md)。不支持权限声明或自动执行。

新增 [工具结果统一限额](docs/tutorial/34-tool-output-retention.md) 和 [主动提问](docs/tutorial/35-user-questions.md)。完整能力对齐仍在推进，未完成项目见 [路线清单](plans/parity-roadmap.md)。

`/skills` 查看读取状态，`/skill 名称 任务` 读取最新说明并发起任务；详见 [第 36 章](docs/tutorial/36-skill-lifecycle.md)。

`/worktree-create 名称` 经确认创建基于 HEAD 的独立目录，再用 `--workspace` 启动会话。见 [第 37 章](docs/tutorial/37-worktree-isolation.md)；首版不自动合并清理，启用命令沙箱时拒绝创建。

`/delegate 任务` 启动最多 5 步的只读探索子 Agent，报告独立落盘，详见 [第 38 章](docs/tutorial/38-readonly-delegation.md)。

`/worktree-export 名称` 导出含基点与校验值的本地补丁包，见 [第 39 章](docs/tutorial/39-worktree-delivery.md)。需要先用新版创建工作区；不会自动应用到主目录。

Skill 已读资源支持 SHA-256 与 `expected_sha256` 分页版本检查；变化使加载标记失效，详见 [第 40 章](docs/tutorial/40-skill-resource-versions.md)。

教学总览：[Agent 整体架构与功能地图](docs/architecture.md)。新增 `/delegations [页码]`、`/delegation ID` 查看独立委派报告，不调用模型或修改主会话。

`/worktree-archive 名称` 和 `/worktree-restore 名称` 支持保留全部内容的归档恢复；不释放磁盘空间。见 [第 41 章](docs/tutorial/41-worktree-archive.md)。
