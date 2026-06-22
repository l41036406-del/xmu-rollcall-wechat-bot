#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "请用 sudo 运行：sudo bash scripts/install-systemd.sh" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/xmu-rollcall-cli"
RUNTIME_DIR="$ROOT_DIR/.lazybot"
ENV_FILE="$RUNTIME_DIR/xmu-wechatbot.env"
SERVICE_FILE="/etc/systemd/system/xmu-wechatbot.service"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-$(logname 2>/dev/null || echo root)}}"

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "服务用户不存在：$SERVICE_USER" >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "还没初始化，请先运行：bash scripts/bootstrap.sh" >&2
  exit 1
fi

install -d -m 755 "$RUNTIME_DIR"
install -d -m 755 "$RUNTIME_DIR/data"
chown -R "$SERVICE_USER":"$(id -gn "$SERVICE_USER")" "$RUNTIME_DIR"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=XMU Rollcall WeChat Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
Environment=TZ=Asia/Shanghai
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python -m xmu_rollcall.wechat_bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now xmu-wechatbot

cat <<EOF

systemd 安装完成。

常用命令：
- 查看状态：sudo systemctl status xmu-wechatbot --no-pager -l
- 查看日志：sudo journalctl -u xmu-wechatbot -f
- 重启服务：sudo systemctl restart xmu-wechatbot

EOF
