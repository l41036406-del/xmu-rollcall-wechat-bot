#!/bin/bash
# Azure VM 一键部署脚本
# 用法: bash deploy/azure-deploy.sh

set -e

echo "=== XMU Rollcall WeChat Bot - Azure 部署 ==="

# 1. 系统更新 + 安装依赖
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv python3-dev zbar-tools libzbar0 git

# 2. 克隆仓库
cd /opt
sudo git clone https://github.com/l41036406-del/xmu-rollcall-wechat-bot.git xmu-rollcall-wechat-bot
cd xmu-rollcall-wechat-bot
sudo chown -R $USER:$USER .

# 3. 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e xmu-rollcall-cli/

# 4. 自动检测 xmulogin 版本
pip install -U xmulogin

echo ""
echo "=== 安装完成！接下来需要：==="
echo ""
echo "1. 启动扫码登录："
echo "   cd /opt/xmu-rollcall-wechat-bot"
echo "   source .venv/bin/activate"
echo "   python -m xmu_rollcall.wechat_bot --login-only"
echo ""
echo "2. 扫码成功后安装 systemd 服务："
echo "   bash scripts/install-systemd.sh"
echo ""
echo "3. 查看状态："
echo "   sudo journalctl -u xmu-wechatbot -f"
echo ""
