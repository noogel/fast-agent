# 本地 Skill 发现与资源读取

Skill 将一套任务步骤和参考材料放进目录，供 Agent 按需读取。它不会自动新增执行权限。

## 启用与使用

```bash
.venv/bin/python -m fast_agent --workspace . --skills examples/skills
```

输入“使用 review Skill 检查当前修改”。模型可以先发现 Skill，再读取说明和资源：

| 工具 | 用途 |
| --- | --- |
| list_skills | 列出目录中包含普通 SKILL.md 文件的子目录 |
| read_skill | 按目录名称读取 SKILL.md 全文 |
| list_skill_resources | 列出该 Skill 的资源目录，path 默认是 . |
| read_skill_resource | 读取相对路径文本，支持 start_line 和 max_lines |

示例目录：

```text
examples/skills/
└── review/
    ├── SKILL.md
    └── references/
        └── checklist.md
```

首次发现只返回名称、路径和文件大小，不把所有正文塞进上下文。目录名称是标识，限定为 1～64 位字母、数字、下划线或短横线，首位须是字母或数字。`SKILL.md` 可包含 YAML frontmatter；名称与用途的解析规则见 [Skill 元数据](33-skill-metadata.md)，其他字段不作为可执行配置。

## 权限和工作区

`--skills` 必须显式提供，并指向工作区内独立子目录。不扫描用户主目录，不自动安装、下载或执行 Skill。未提供参数时，不注册这四个工具。

四个工具均为只读，计划模式可用。Skill 内容不能覆盖用户约束、AGENTS.md 或工具权限。即使说明要求“忽略审批”，命令和编辑仍由已有权限层判断。这里保障的是工具的权限边界，不保证模型永远不会受文本误导。

资源路径只允许在该 Skill 内，拒绝绝对路径和 `..`。逐层目录描述符配合 `O_NOFOLLOW` 拒绝符号链接；只读取普通 UTF-8 文本文件，拒绝 FIFO 等特殊文件。当前实现面向本项目 macOS/POSIX 环境，未实现 Windows 兼容层。

单文件最多 64 KiB；资源每次最多 200 行，默认从第一行读取 200 行。每层目录最多 200 个条目，超限报错，需要拆分目录。读取脚本只返回文本；执行必须另走命令工具与审批，不能直接把目录外资源纳入 Skill。

## 保存和恢复

读取结果作为普通工具消息进入历史，会被保存，也会占用 `/context` 中的工具结果空间。加载会话不会恢复 Skill 目录授权或注册工具；新进程需要再次提供 `--skills`。恢复仍进入计划模式。历史中的内容可能过时，继续任务时应重新读取当前说明和资源；本版本没有自动版本固定或内容刷新。

## 与其他机制的区别

- AGENTS.md：适用于工作区及目标文件的开发规则。
- Skill：按任务读取的操作步骤和参考资源。
- 本地工具扩展 / MCP：提供可以调用的外部能力。

本版本不支持自动匹配 Skill、安装市场、脚本直执行、frontmatter 权限声明或完整第三方 Skill 运行环境。资源中的链接不会自动访问。

## 代码与验证

`skills.py` 负责目录边界、只读工具与大小限制；`cli.py` 仅在显式提供目录时注册。测试覆盖发现、资源分页、链接与越界拒绝、FIFO 拒绝、权限不提升，以及离线 Agent 工具循环、保存和恢复后显式重新注册。

运行：

```bash
.venv/bin/python -m pytest -q tests/test_skills.py
```

离线模型用于确定性验证发现与读取调用链；真实模型的选择、加载及会话恢复复查见 [Skill 生命周期](36-skill-lifecycle.md)。两者分别证明工具行为与模型实际使用，不能互相代替。
