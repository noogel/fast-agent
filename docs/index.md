# Fast Agent 中文教程

从一个失败测试开始，理解模型怎样调用工具、怎样管理消息，以及怎样可靠地修改代码。

## 第一次阅读

进入[由浅入深理解 agent](deep-dive/index.md)，按八章顺序学习：认识 Agent → 工具请求 → 多轮消息 → 上下文 → 安全修改 → 中断恢复 → 扩展分工 → 分层架构。

这是一条连续学习路线，不需要先读完所有实践指南。第一章会链接到[修复测试的完整例子](start-with-a-task.md)。

## 想动手或查具体功能

侧栏的“实践指南”按问题分类，不要求从头读到尾：

| 你要做什么 | 从这里开始 |
| --- | --- |
| 写出最小模型调用和工具 | [单次调用](tutorial/01-model.md)、[自定义工具](tutorial/06-custom-tool.md) |
| 检索代码、修改并测试 | [仓库开发](tutorial/08-repository.md)、[质量检查](tutorial/27-quality-checks.md) |
| 保存对话、处理长上下文 | [会话保存](tutorial/07-context.md)、[手动压缩](tutorial/19-manual-compaction.md) |
| 控制命令、权限和任务进度 | [任务与权限](tutorial/10-tasks-jobs-permissions.md)、[继续执行](tutorial/21-continue-steps.md) |
| 增加 Skill 或连接外部工具 | [Skill](tutorial/32-local-skills.md)、[MCP](tutorial/29-mcp-tools.md) |
| 隔离工作目录、委派只读分析 | [worktree](tutorial/37-worktree-isolation.md)、[子 Agent](tutorial/38-readonly-delegation.md) |

实践指南按任务组织，可按需查阅；页面地址保持稳定，便于对应代码和验证记录。源码链接可以打开只读参考页，搜索支持中文关键词。

## 先跑一次离线演示

在已安装项目依赖的目录执行：

```bash
source .venv/bin/activate
fast-agent --demo
```

演示在临时目录完成读取、修改和复读验证，无需模型密钥。接着回到学习主线理解每一步。

## 启动这个文档服务

```bash
cd fast-agent  # 从 awesome 仓库根目录进入项目
.venv/bin/python -m pip install -e '.[docs]'
.venv/bin/python -m mkdocs serve
```

打开 <http://127.0.0.1:8000/>。修改教程 Markdown 后页面自动刷新；在运行终端按 Ctrl+C 停止。

如端口被占用，可运行 `mkdocs serve -a 127.0.0.1:8001`。需要生成静态文档时运行 `mkdocs build --strict`，输出位于 `site/`。

文档服务仅监听本机，展示教程与 Python 源码参考页。它不会运行 Agent，也不需要加载模型密钥。

## 发布到 GitHub Pages

线上地址为 <https://nuniok.github.io/awesome/>。GitHub 仓库的 Pages 发布来源需设置为 **GitHub Actions**，`github-pages` 环境需允许 `codex/fast-agent` 分支部署。

选择 GitHub Actions 后，无需再创建页面推荐的 Jekyll 或 Static HTML 模板。若首次运行因未启用 Pages 而失败，启用后可在 Actions 中重新运行失败任务，或推送新的教程变更触发发布。

仓库根目录的 `.github/workflows/fast-agent-pages.yml` 在该分支的教程、源码参考或发布配置变更推送到 GitHub 时触发。它安装 `requirements-docs.txt` 中的文档依赖，执行严格构建，仅上传 `fast-agent/site/`。不安装模型 SDK，不读取 `.env`，不运行 Agent，也不需要模型密钥。源码参考页会公开展示项目 Python 模块和示例。

发布前可在项目目录本地检查：

```bash
.venv/bin/python -m pip install -r requirements-docs.txt
.venv/bin/python -m mkdocs build --strict
```

推送后在 GitHub 的 Actions 页面检查 **Publish Fast Agent tutorial**。构建失败不会进入部署步骤；部署失败时检查 Pages 来源、环境允许分支和工作流权限。只有部署成功且线上页面可访问，才算发布完成。`workflow_dispatch` 手动入口需要工作流存在于默认分支；当前分支可以通过新的相关文件提交触发。

`site_url` 是线上规范地址，`dev_addr` 仍为本机预览地址，两者用途不同。切换发布分支时须同时修改工作流触发条件、作业分支检查和 Pages 环境规则。
