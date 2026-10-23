# Skill 名称与用途元数据

上一章通过目录名发现 Skill。本章让 `list_skills` 同时返回用途，使模型能先选择相关 Skill，再读取正文。

## 编写说明

```yaml
---
name: review
description: >
  检查 Python 修改的测试证据和 Git 差异。
  适用于小型代码审查。
---
```

在结束分隔符之后写 Markdown 操作说明。`name` 必须与目录名一致；`description` 最多 1024 字符。字段均可省略，无 frontmatter 的旧文件返回目录名和空用途。

只支持 name 和 description。暂不接受其他字段，包括 allowed-tools、模型选择和自动执行声明。不要把其他工具生态中的 Skill 配置直接当作本项目的权限授权。

## 发现和错误处理

`list_skills` 返回名称、用途、路径和大小，不返回正文。磁盘端会有界读取文件用于校验，因此这不是“从不读取正文文件”；节省的是发给模型的上下文。每个文件仍受 64 KiB 限制，头部受 8 KiB 限制。

一个文件格式错误时，它进入 errors 列表，其他合法 Skill 仍可发现。`read_skill` 也重新校验头部，错误时不返回正文。已有脚本、文件读取和命令审批边界保持生效。

用途描述仍是本地文件内容，不能覆盖用户要求或提升权限。本版本由模型根据描述选择，不实现自动评分或自动激活。

## 解析实现

`skill_metadata.py` 先限制 YAML 标记数量，并拒绝锚点、别名和显式标签，再使用 SafeLoader 生成语法节点。只接受顶层映射及字符串标量，显式拒绝重复键；不实例化 YAML 中的 Python 对象。普通文本、引号字符串和折叠多行描述均可使用。

参考 [PyYAML 官方文档](https://pyyaml.org/wiki/PyYAMLDocumentation)。运行依赖已声明 `PyYAML>=6,<7`，新环境通过项目安装命令获取。

## 本地验证

测试覆盖多行中文描述、旧文件兼容、名称不一致、重复键、错误类型、别名/标签、未知权限字段、超限与错误邻居隔离。继续复用上一章权限及保存恢复测试。

```bash
.venv/bin/python -m pytest -q tests/test_skill_metadata.py tests/test_skills.py
```

元数据测试核对解析、校验和目录展示；真实模型如何选择并读取 Skill，见 [Skill 生命周期](36-skill-lifecycle.md)。