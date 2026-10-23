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
cd fast-agent  # 进入独立项目目录
.venv/bin/python -m pip install -e '.[docs]'
.venv/bin/python -m mkdocs serve
```

打开 <http://127.0.0.1:8000/>。修改教程 Markdown 后页面自动刷新；在运行终端按 Ctrl+C 停止。

如端口被占用，可运行 `mkdocs serve -a 127.0.0.1:8001`。需要生成静态文档时运行 `mkdocs build --strict`，输出位于 `site/`。

文档服务仅监听本机，展示教程与 Python 源码参考页。它不会运行 Agent，也不需要加载模型密钥。

## 本地构建并发布到 GitHub Pages

站点地址为 <https://noogel.github.io/fast-agent/>。源码保存在 `main`，生成的静态文件保存在 GitHub 的 `gh-pages` 分支。

### 首次配置

在仓库 **Settings → Pages → Build and deployment** 中，将 **Source** 改为 **Deploy from a branch**，选择 **gh-pages** 和 **/(root)**，点击 **Save**。分支需要先通过下面的发布命令创建。不需要添加 Jekyll 或 Static HTML 工作流模板。

```bash
.venv/bin/python -m pip install -r requirements-docs.txt
bash scripts/publish-pages.sh --build-only
```

### 更新教程

先提交源码修改，再在本机运行：

```bash
bash scripts/publish-pages.sh
```

脚本检查工作区干净且 `github` 指向 `noogel/fast-agent`，使用 MkDocs 严格构建后推送到 `gh-pages`。它不会推送源码分支、不使用强制推送、不清空发布历史。需要同步源码时另行推送 `main`。从其他目录调用脚本时也会自动进入项目根目录；可用 `FAST_AGENT_DOCS_PYTHON` 指定文档 Python 环境。

只有文档静态产物和 `.nojekyll` 进入发布分支；该文件用于跳过 Jekyll 构建。文档包含公开的 Python 源码参考页，不需要读取 `.env` 或请求模型。不要在教程正文和代码示例中加入真实凭据。

### 失败与上线检查

构建警告或错误会停止推送。若远端发布分支领先而推送被拒绝，应先核对远端变更，不用强推覆盖。推送成功后仍需检查 Pages 部署状态，并访问首页、架构页及搜索，不能把 Git 推送成功当作网站上线成功。

这里取消的是自定义 Actions 构建流程。GitHub Pages 的最终部署仍由平台内部 Actions 工作流完成；账号账单锁定可能继续阻止部署，本地构建不能保证绕过该限制。详见 [GitHub 官方发布说明](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)。

`site_url` 是线上规范地址，`dev_addr` 是本机预览地址，两者用途不同。
