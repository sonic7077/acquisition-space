#!/bin/zsh
set -e
cd -- "$(dirname -- "$0")"
if ! command -v uv >/dev/null || ! command -v npm >/dev/null; then
  echo '需要安装 uv 与 Node.js 22+，见 README.md。'
  exit 1
fi
uv sync --project backend --python 3.12 --locked
if [ ! -d frontend/node_modules ]; then
  npm --prefix frontend ci --cache /tmp/acquisition-npm-cache --no-audit --no-fund
fi
npm --prefix frontend run build
exec uv run --project backend --python 3.12 python scripts/service.py
