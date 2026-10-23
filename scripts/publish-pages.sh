#!/usr/bin/env bash
# 本地严格构建并发布静态教程；不会提交源码或强制覆盖远端历史。
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${FAST_AGENT_DOCS_PYTHON:-.venv/bin/python}"
case "${1:-}" in
  --build-only) "$python_bin" -m mkdocs build --strict; exit 0 ;;
  "") ;;
  *) echo "用法：bash scripts/publish-pages.sh [--build-only]" >&2; exit 2 ;;
esac
remote_url=$(git remote get-url --push github)
case "$remote_url" in
  git@github.com:noogel/fast-agent.git|https://github.com/noogel/fast-agent.git) ;;
  *) echo "github 远端不是预期的 noogel/fast-agent，停止发布。" >&2; exit 1 ;;
esac
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "请先提交源码变更，确保产物能够追溯到确定的提交。" >&2
  exit 1
fi
"$python_bin" -m mkdocs gh-deploy --strict --remote-name github --remote-branch gh-pages
echo "静态文件已推送到 gh-pages；仍需检查 GitHub Pages 部署和线上网址。"
