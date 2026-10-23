# 更精确的代码检索

仓库中可能同时存在正式实现、示例和测试里的同名函数。Agent 需要逐步缩小范围，找到正确代码，再读取和修改。本章扩展现有 `search_code` 工具，保持计划模式可用。

## 三个新参数

```json
{
  "query": "^def (add|multiply)",
  "regex": true,
  "glob": "src/**/*.py",
  "context_lines": 1,
  "limit": 20
}
```

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `regex` | `false` | 默认固定文本；为 true 时使用 ripgrep 的 Rust 正则语法 |
| `glob` | 不筛选 | 以工作区为根的路径模式；`*` 不跨目录，独立的 `**` 匹配零个或多个目录 |
| `context_lines` | `0` | 每个匹配前后各返回 0～5 行 |

`src/**/*.py` 同时匹配 `src/a.py` 和 `src/core/a.py`。`*.py` 只匹配工作区根目录的文件；任意层级使用 `**/*.py`。支持单段中的 `?` 和字符集合，不支持花括号展开、反斜杠或否定模式。模式不能包含绝对路径、空段、`.`、`..`。

`path` 仍然指定搜索目录；glob 始终相对于工作区，不会因为设置了 `path` 而改变基准。

默认查询 `a.b` 只匹配字面量 `a.b`。设置 `regex=true` 后，点号才表示任意字符。无效正则返回工具错误，模型可修改参数；没有匹配是成功的空列表。正则不启用 PCRE2，不支持其回溯引用和环视扩展。

## 模型收到什么

```json
{
  "matches": [{
    "path": "src/math_utils.py",
    "line": 2,
    "text": "def add(a, b):\n",
    "before": [{"line": 1, "text": "# 加法函数\n"}],
    "after": [{"line": 3, "text": "    return a - b\n"}]
  }],
  "truncated": false
}
```

`before`、`after` 只在请求上下文时出现。文件起止位置的上下文自然少于指定行数。相邻匹配的上下文可能重叠，每条结果都保留自己的行号。

搜索不会登记编辑所需的读取指纹。即使上下文已经显示目标代码，编辑前仍必须 `read_file`，以读取适用规则和校验文件版本。

## 忽略规则与输出预算

不能简单地给 ripgrep 添加正向 `--glob`：它可能覆盖忽略规则。当前实现先使用原有的忽略策略搜索，再对返回事件进行路径筛选。这样 `.gitignore`、运行目录和 `.env*` 的排除继续生效，也不跟随符号链接。

这个选择有明确限制：glob 是结果筛选，不保证减少底层扫描。底层输出超过 64 KiB 时，即使筛选后为空，`truncated` 仍为 true，不能解释为仓库中没有目标。此时先缩小 `path` 或增加查询精度。

结果最多 500 条，默认 100 条；每行最多 500 字符，截断行带 `text_truncated=true`。包含上下文后的结果数据也限制为 64 KiB。底层输出、匹配数量、行文本或结果数据被截断，都会设置顶层 `truncated=true`。读取不超过 1 MiB 的文件，并保留原有命令超时。

## MiniMax 完整验证

```bash
source .env
.venv/bin/python examples/real_precise_search_demo.py
```

脚本创建临时 Git 仓库，其中包含正式源码、同名示例、被忽略的 Python 文件和文本干扰项。MiniMax 在规划与执行阶段都实际调用新增检索参数，再读取文件、复现失败、批量修复、测试和检查 Git 差异。第三个独立进程加载会话，以计划模式只读核验。

宿主程序核对检索结果只包含正式源码、上下文存在、干扰文件与测试未改、修复后测试通过、恢复没有重放操作。详细报告写入 `.fast-agent/validation/precise-search-demo.json`。

## 对照代码

- [仓库检索工具](../../src/fast_agent/repository_tools.py)
- [真实模型验收](../../examples/real_precise_search_demo.py)

示例验证：MiniMax 用时 118.23 秒完成三个进程阶段，规划与执行都使用了新增检索参数；修复后 3 项样例测试通过，仅修改 `src/math_utils.py`，测试及干扰文件未变，恢复后保持计划模式。项目
