#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/xmu-rollcall-cli"
ENV_FILE="$ROOT_DIR/.lazybot/xmu-wechatbot.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "还没初始化，请先运行：bash scripts/bootstrap.sh" >&2
  exit 1
fi

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  echo "找不到虚拟环境，请先运行：bash scripts/bootstrap.sh" >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

exec "$APP_DIR/.venv/bin/python" -m xmu_rollcall.wechat_bot "$@"
