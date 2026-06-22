#!/bin/sh
set -e
apt-get update -qq
apt-get install -y -qq libzbar0 git
pip install -q -U xmulogin
git clone -q --depth 1 https://github.com/l41036406-del/xmu-rollcall-wechat-bot.git /opt/bot
pip install -q -e /opt/bot/xmu-rollcall-cli
echo "SETUP_COMPLETE"
cd /opt/bot
python -m xmu_rollcall.wechat_bot --login-only
