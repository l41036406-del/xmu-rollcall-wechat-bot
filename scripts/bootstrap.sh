#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/xmu-rollcall-cli"
RUNTIME_DIR="$ROOT_DIR/.lazybot"
DATA_DIR="$RUNTIME_DIR/data"
ENV_FILE="$RUNTIME_DIR/xmu-wechatbot.env"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "找不到 Python，请先安装 python3.11 或设置 PYTHON_BIN。" >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Python 版本过低，请使用 Python 3.9 及以上。" >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR" "$DATA_DIR"

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
fi

"$APP_DIR/.venv/bin/pip" install setuptools wheel
"$APP_DIR/.venv/bin/pip" install --no-build-isolation -e "$APP_DIR"

cat > "$ENV_FILE" <<EOF
PYTHONUNBUFFERED=1
TZ=Asia/Shanghai
XMU_ROLLCALL_CONFIG_DIR=$DATA_DIR
XMU_WECHAT_BOT_CRED_PATH=$DATA_DIR/wechatbot-credentials.json
EOF

cat <<EOF

懒人包初始化完成。

已生成：
- 虚拟环境：$APP_DIR/.venv
- 运行时目录：$RUNTIME_DIR
- 环境变量文件：$ENV_FILE

下一步：
1. 本地先扫码登录
   bash scripts/start-local.sh --login-only

2. 想后台常驻
   sudo bash scripts/install-systemd.sh

3. 看运行日志
   sudo journalctl -u xmu-wechatbot -f

EOF
