# 显式加载本地工具扩展

不修改 Agent 源码，也可以接入自己的分析器或内部工具。当前实现使用一次性本地进程协议，不在 Agent 进程内导入扩展 Python 模块。

## 配置与启动

```json
{
  "tools": [{
    "name": "inspect_source",
    "description": "分析指定示例源码",
    "argv": ["python", "inspect_source.py"],
    "timeout": 30,
    "parameters": {
      "type": "object",
      "properties": {"filename": {"type": "string"}},
      "required": ["filename"],
      "additionalProperties": false
    }
  }]
}
```

```bash
fast-agent --workspace . --extensions extensions.json
```

模型看到的名称为 `ext_inspect_source`。工具名称加前缀以避免覆盖内置工具。配置只在用户显式指定时加载，不自动发现工作区脚本，也不接受文档里的“安装扩展”指令作为授权。

配置最大 64 KiB、最多 16 个工具；名称最多 48 字符。argv 使用数组，命令从工作区执行，相对脚本路径相对工作区。配置和脚本都是用户信任的本地代码：固定 argv 并不固定脚本内容，也不防止脚本之后被修改。

## 一次调用的协议

模型提供的参数先经过工具 schema 校验，再序列化为一个 JSON 字符串，追加为 argv 的最后一个参数。不是 shell 拼接，不会把参数中的分号或命令替换语法当 shell 执行。扩展可读取 `sys.argv[-1]`：

```python
import json
import sys

args = json.loads(sys.argv[-1])
print(json.dumps({
    "ok": True,
    "data": {"filename": args["filename"]},
    "error": None,
}, ensure_ascii=False))
```

标准输出需要是唯一的 JSON 对象，且包含 `ok`（布尔）、`data`（对象）、`error`（字符串或 null）。失败时 `error` 不能为空。stdout/stderr 在现有命令执行器中合并，因此不要把调试文本打印到这两个通道污染协议；需要额外日志时由扩展自己按授权范围处理。

参数最大 32 KiB，结构化返回最多 4 KiB，单次超时大于 0 且不超过 60 秒。非零退出、超时、过长输出、非 JSON、非有限数值或错误信封不会被当成成功；Agent 按可能已有副作用的未知结果暂停，保留命令日志编号供核实，不自动重放。

## 权限与 schema 范围

[extensions.py](../../src/fast_agent/extensions.py) 复用命令审批、超时、进程组清理、长日志与 `--sandbox` 策略。所有外部工具按写操作处理，不允许扩展配置自称只读；计划模式和启动只读模式不运行它们。

支持 object、array、string、integer、number、boolean，object 的 properties/required/additionalProperties=false，array 的 items，以及 description。嵌套最多 4 层，每个对象最多 20 个字段。不支持的 schema 关键字会在加载时报错，避免向模型宣称某种约束却没有实际校验。它不是完整 JSON Schema 实现。

配置不等于执行授权。restricted 白名单比较包含 JSON 参数的完整实际 argv；动态参数通常需要交互确认。命令默认不继承模型 API 密钥；macOS 沙箱限制仍仅覆盖已说明的写入与网络范围，不提供完整机密读取保护。

此协议不是 MCP，不包含远端服务发现、子 Agent 委派或自动执行技能。它先提供可检查的本地扩展入口，后续协议可复用同一工具注册和权限边界。

## 验证

本地测试实际启动扩展进程，验证中文和 shell 特殊文本按字面参数传递、参数类型拒绝、计划模式拒绝、异常响应触发未知结果，以及非法配置拒绝。真实示例 [real_extensions_demo.py](../../examples/real_extensions_demo.py) 让 MiniMax 调用外部源码分析器，修复函数，调用配置质量检查；保存加载后再次执行扩展和检查，核对测试文件未变。

组合示例 [real_engineering_demo.py](../../examples/real_engineering_demo.py) 在临时 Git 仓库中串联修复、计划、扩展分析、沙箱质量检查、预算拒绝、保存加载和撤销后重新修复。独立断言检查失败测试确实复现，修复后及恢复后 tests/diff 均退出 0；必须检查每个配置检查的结果，不能只看模型总结。