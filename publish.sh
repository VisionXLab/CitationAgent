#!/bin/bash
# 一键构建并发布到 PyPI，自动更新 README 更新日志，自动 commit + push
# 用法: bash publish.sh [version]
# 示例: bash publish.sh 1.0.3

set -e

if [ -n "$1" ]; then
  sed -i '' "s/^version = .*/version = \"$1\"/" pyproject.toml
  sed -i '' "s/__version__ = .*/__version__ = \"$1\"/" citationclaw/__init__.py
  echo "Version updated to $1"
fi

VERSION=$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
DATE=$(date +%Y-%m-%d)

# 更新 README.md 中唯一的 PyPI 更新日志行（匹配含 📦 的那行）
python3 - <<PYEOF
import re
with open('README.md', 'r', encoding='utf-8') as f:
    content = f.read()
content = re.sub(
    r'\| [0-9-]+ \| v[0-9\.]+ \| 📦[^\n]*\|',
    '| $DATE | v$VERSION | 📦 PyPI 最新版本：\`pip install citationclaw\` 一行安装，自动打开浏览器，支持跨平台（macOS / Linux / Windows） |',
    content
)
with open('README.md', 'w', encoding='utf-8') as f:
    f.write(content)
print(f'README changelog updated → v$VERSION ($DATE)')
PYEOF

# 清理旧构建
rm -rf dist/ build/ *.egg-info

# 安装构建工具（如未安装）
pip install --quiet build twine

# 构建
python -m build

# 上传到 PyPI
twine upload dist/*

# 提交并推送
git add pyproject.toml citationclaw/__init__.py README.md
git commit -m "Publish v$VERSION to PyPI"
git push origin main

echo ""
echo "✅ Published v$VERSION! Install with: pip install citationclaw"
