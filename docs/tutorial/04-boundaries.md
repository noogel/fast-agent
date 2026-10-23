# 工具执行边界

阅读 [file_tools.py](../../src/fast_agent/file_tools.py) 和 [command_tool.py](../../src/fast_agent/command_tool.py)。

文件路径先 resolve，再判断是否属于工作目录，所以 `../` 和指向外部的符号链接都被拒绝。读取只接受 UTF-8 普通文件；文件最大 1 MiB，单次返回最多 64 KiB，可按行读取。edit_file 要求 old 非空且唯一出现，避免误改多个位置。所有创建、覆盖和精确替换均展示差异并确认；已有文件必须先读取，并检查读取后是否发生变化。

命令工具接受 argv 数组，不拼接 shell 字符串。例如 `['python3', '-c', 'print(1)']`。shell=False 不代表系统沙箱：被执行程序仍可读写当前用户可访问的文件。

本章的同步命令在默认 confirm 策略下要求交互确认，批处理默认拒绝。[任务恢复、后台命令与权限](10-tasks-jobs-permissions.md)增加显式命令白名单和独立后台工具。命令超时后清理进程组；读取输出时只保存限额内的数据，继续排空多余数据，防止管道阻塞和内存无限增长。同步命令遗留的后台进程会被清理；需要跨终端存活的有界任务使用[任务恢复、后台命令与权限](10-tasks-jobs-permissions.md)的 start_command。

无需密钥运行边界测试：

```bash
python -m pytest tests/test_tools.py -q
```

练习：为不存在的文件、非 UTF-8 文件和重复匹配添加测试，观察它们如何变为模型可以理解的失败结果。路径检查不抵御同时发生的恶意符号链接替换，不要把本项目当安全隔离系统。
