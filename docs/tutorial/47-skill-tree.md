# 整套 Skill 材料变了怎么办

只记录读过的文件，会漏掉新加入的规则或尚未读取的资源。`inspect_skill_tree` 对指定 Skill 的全部资源计算版本：路径、内容、文件权限均进入指纹，包含空目录，不执行任何脚本。

```text
先调用 inspect_skill_tree(name="review")，再 read_skill 与 read_skill_resource。
```

检查后，读取说明或资源都会在读取前后核对资源树。发生变化就拒绝正文，并清除已读说明和资源标记；重新调用 inspect_skill_tree 接受新版本，再从说明和资源第一页重新读取。也可传 `expected_tree_sha256` 要求特定版本，不匹配时拒绝。

检查最多 1000 项、4 MiB、16 层；链接和特殊文件拒绝，超限不做部分指纹。资源版本检查可以覆盖二进制，但正文读取仍只支持现有的 UTF-8 文本接口。会话恢复、压缩和重置清除树标记，需要重新检查。

这不是文件系统快照：外部进程仍可修改文件；也没有后台监控。它提供有界、按需的乐观版本校验，执行脚本前仍需权限和现有规则检查。

源码：[skill_tree.py](../../src/fast_agent/skill_tree.py)、[skills.py](../../src/fast_agent/skills.py)。

## 验收证据

MiniMax指定场景验收14/14通过，覆盖资源树版本、变更拒绝与显式重新读取。本地报告为`.fast-agent/validation/skill-tree-real.json`；报告来自临时夹具，不能推断任意服务、工作区或资源都已经验收。失败记录保留，模型口头结论不替代工具结果和磁盘断言。
