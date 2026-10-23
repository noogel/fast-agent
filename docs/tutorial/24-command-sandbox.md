# 命令写入与网络隔离

确认命令只表示用户同意执行；`cwd` 只决定相对路径起点，都不能阻止程序访问工作区之外。现在可在 macOS 显式启用操作系统的命令限制：

```bash
fast-agent --workspace . --sandbox macos
```

## 范围与边界

`run_command` 与 `start_command` 使用同一策略：禁止网络操作，禁止工作区之外写入（仅允许写入 `/dev/null` 空设备以兼容 Git 等程序），额外禁止写入工作区根目录的 `.git`、`.fast-agent` 和 `.env`。子进程沿用 macOS 限制；后台记录持久保存所选沙箱模式，不因终端退出变为普通命令。

这是写入和网络约束，不是完整的不可信代码安全隔离：外部文件仍可读取，进程交互等系统能力没有全部封禁；不要用它执行恶意程序或保护机器上的读取机密。需要完整隔离时应使用独立受控运行环境。macOS 的 sandbox-exec 可用性以本机验证为准，Linux/Windows 目前不能使用这个后端。

沙箱模式只给命令基础环境变量：PATH、语言、时区和终端类型；HOME、TMPDIR 指向工作区，并关闭 Python 字节码写入。依赖环境变量中的构建配置、外部缓存、下载依赖、Git 写操作的命令可能失败。失败会作为真实工具结果反馈，不自动放宽策略。默认 `--sandbox none` 保持原行为，但同步和后台命令都不再自动继承 FAST_AGENT_API_KEY。

普通模式移除一个环境变量并不能防止命令读取磁盘凭据。文件工具本身也不是操作系统沙箱。当前选项仅作用于命令进程。

## 代码路径

[command_sandbox.py](../../src/fast_agent/command_sandbox.py) 将原始 argv 包装为 sandbox-exec 调用，并生成环境变量；调用方仍对原始命令进行审批。配置不支持时提前报错，运行时沙箱启动失败则返回失败，不会回退到无沙箱执行。

[jobs.py](../../src/fast_agent/jobs.py) 将模式写入任务记录；[job_worker.py](../../src/fast_agent/job_worker.py) 在真正创建命令子进程时应用同样策略。日志由沙箱外的 Agent 执行器写入，因此命令不需要获得 `.fast-agent` 写权限。

## 验证

默认测试校验参数构造、凭据移除、不支持平台拒绝执行。本机原生测试需显式启用：

```bash
FAST_AGENT_TEST_SANDBOX=1 .venv/bin/python -m pytest tests/test_sandbox.py -q
```

原生测试实际检查工作区文件可写，外部文件、指向外部文件的符号链接、`.env` 与运行记录不能写，网络绑定被拒绝，后台命令同样不能越界写入。不要把参数字符串检查当成操作系统隔离的证据。

MiniMax 验证复用 [real_usage_demo.py](../../examples/real_usage_demo.py)，设置 `FAST_AGENT_DEMO_SANDBOX=macos`：预算暂停并保存后恢复，模型修改示例函数，在沙箱内执行 unittest，独立核对退出码与测试文件未修改。

沙箱示例验证失败测试复现、修复后沙箱内测试通过、测试文件未改，以及会话恢复。预算暂停是独立断言，见 [用量与预算](23-usage-budget.md)；沙箱修复成功不能替代预算验证，也不能将组合报告中的失败项改写为成功。