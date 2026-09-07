# xmu-wechat-bot

这是项目的 Python 包目录，当前已经收成 bot-only：

- 不再包含旧的 CLI 轮询监控链路
- 只保留微信机器人、账号存储、会话缓存和签到服务

## 安装

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

## 启动

首次只做扫码登录：

```bash
python -m xmu_rollcall.wechat_bot --login-only
```

正常启动：

```bash
python -m xmu_rollcall.wechat_bot
```

## 微信命令

- `/conf`
- `/switch 账号ID`
- `/accounts`
- `/answer`
- `/qr`：进入待命，随后发送二维码照片即可快速签到
- `/qrcode 二维码内容`：用二维码内容签到；也可 `/qrcode rollcall_id 内容`
- `/cron add 4 8:00`
- `/refresh`
- `/cancel`
- `/help`

直接把“二维码点名”的二维码照片发给机器人，也会自动解码并签到；用 `/qr` 先进入待命再发照片速度更快。

## 更简单的方式

如果你是直接从仓库根目录使用，优先走懒人脚本：

- [../scripts/bootstrap.sh](../scripts/bootstrap.sh)
- [../scripts/start-local.sh](../scripts/start-local.sh)
- [../scripts/install-systemd.sh](../scripts/install-systemd.sh)

## 手动部署文档

- [../docs/ubuntu-wechatbot-deploy.md](../docs/ubuntu-wechatbot-deploy.md)
