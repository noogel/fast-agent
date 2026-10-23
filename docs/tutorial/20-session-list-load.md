# 会话列表与启动时加载

保存的会话多了以后，不应依赖记忆或手动翻找 JSON 文件。本章补上命名会话的发现与启动入口，继续复用原有内容校验和恢复权限边界。

## 找到保存的会话

```text
/sessions
/sessions 2
/sessions --all
/sessions 2 --all
```

每页固定 20 条，按文件修改时间从新到旧排序；同一时间按名称排序。列表包含名称、字节大小、UTC 更新时间，以及总数、当前页和 `has_more`。页码从 1 开始，超出末页返回空列表。

默认隐藏 `auto-` 与 `manual-` 开头的压缩备份，`--all` 显示全部。分类依据名称前缀，因此自己命名时也应避免这两个前缀。普通 `/save 名称` 的文件会显示在默认列表中。

列表只读取文件元数据，不加载消息正文，也不请求模型。损坏 JSON 仍可能出现在列表中，只有 `/load` 或 `--load` 才校验内容。符号链接、目录和不合法名称会被跳过；会话存储目录本身若为符号链接则拒绝访问。

分页限制返回数量，当前仍会扫描目录中的全部候选文件元数据，不是数据库索引；数量特别大时仍有扫描成本。

## 启动时加载

```bash
fast-agent --workspace /path/to/repo --load 修复计划
```

进入后先处于计划模式，可以检查 `/history`、`/context`，再决定是否执行：

```text
/execute
请继续刚才保存的修复任务，重新读取源码后再修改。
```

也可以一次性只读核验：

```bash
fast-agent --workspace /path/to/repo --load 修复计划 --prompt '只读核对当前源码与此前计划'
```

`--load` 和 `--resume` 互斥：前者加载 `/save` 保存的命名会话，后者加载任务检查点。两者都不重放工具，也不从磁盘继承执行授权。启动权限为 `read-only` 时，`/execute` 仍不能提升权限。

会话不存在、JSON 损坏、消息协议无效或工作目录不一致时，启动报错退出。列表里的名称可直接用于 `--load`，但“列出成功”并不代表内容已通过校验。

## 代码流程

`SessionStore.list()` 读取元数据并排序分页 → 本地 `/sessions` 输出列表 → CLI 参数解析选择唯一恢复入口 → `SessionStore.load()` 校验文件 → `Agent.restore_history()` 刷新系统指令、清空旧读取指纹、进入计划模式 → 后续用户输入才可能发起模型调用。

元数据列表没有摘要字段，避免为了展示会话而额外向模型发送历史正文。更新时间来自文件系统，复制文件可能改变排序。

## MiniMax 验证

```bash
source .env
FAST_AGENT_READ_TIMEOUT=120 FAST_AGENT_MAX_RETRIES=1 \
  .venv/bin/python examples/real_session_load_demo.py
```

第一个进程实际调用 MiniMax 分析代码并保存计划；宿主通过列表找到 `planned`。第二个进程调用真实 `cli.main()` 的 `--load planned` 入口，使用受控交互输入依次执行 `/sessions`、`/context`、`/execute` 和修复任务。

测试夹具仅批准目标源码的编辑和精确测试命令。模型先复现失败，再修复两个函数、重跑测试并检查 Git 差异；随后保存新的会话。验收检查恢复时的计划模式、进程分离、测试未改和修改范围。受控输入覆盖 CLI 路径，不替代人工终端体验测试。

报告：`.fast-agent/validation/session-load-demo.json`。

## 对照代码

- [会话存储与列表](../../src/fast_agent/sessions.py)
- [启动参数与恢复入口](../../src/fast_agent/cli.py)
- [本地列表命令](../../src/fast_agent/session_commands.py)
- [真实验收脚本](../../examples/real_session_load_demo.py)

示例检查两个样例测试通过、测试文件未改、最终仅目标源码有差异。模型请求后台命令时应拒绝，并允许它改用同步命令；验收应核对获批操作范围，不能要求所有权限请求都被批准。

工具调用与结果的逐项核对报告见 `.fast-agent/validation/session-load-audit.json`。复现时应独立检查加载的历史、恢复模式和实际修复结果；请求超时的运行不计为完整通过。