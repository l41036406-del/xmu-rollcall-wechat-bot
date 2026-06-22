# XMU 微信签到机器人

[使用效果图](docs/screenshot.jpg)

机器人支持这些微信命令：

- `/conf`：分步配置账号
- `/switch 账号ID`
- `/accounts`
- `/answer`
- `/cron add 4 8:00`
- `/refresh`
- `/cancel`
- `/help`

`/answer` 不会持续轮询 TronClass，而是在你发命令时即时查一次；如果成功，会把签到码或经纬度发回微信。机器人消息里的时间按中国时区 `UTC+8` 显示。

## 三步上手

### 0. 服务器

租用一个 Linux 服务器（推荐 Ubuntu 22.04），可以选择阿里云、腾讯云、Google Cloud 等。

### 1. 初始化

```bash
git clone https://github.com/KrsMt-0113/xmu-rollcall-wechat-bot.git
cd xmu-rollcall-helper
bash scripts/bootstrap.sh
```

### 2. 先扫码登录一次

```bash
bash scripts/start-local.sh --login-only
```

看到登录链接后，用机器人微信号扫码。

### 3. 装成后台服务

```bash
sudo bash scripts/install-systemd.sh
```

看日志：

```bash
sudo journalctl -u xmu-wechatbot -f
```

## 微信里怎么用

首次配置：

1. 发送 `/conf`
2. 按提示发送学号
3. 按提示发送密码
4. 发送 `/answer`

如果配置多个账号：

- 发送 `/accounts` 看账号列表
- 发送 `/switch 2` 切换到 `ID=2`

定时执行：

- 发送 `/cron add 4 8:00` 新增一条任务，也兼容简写 `/cron 4 8:00`
- 发送 `/cron` 查看当前全部计划
- 发送 `/cron del 2` 删除 `ID=2` 的任务
- 发送 `/cron off` 清空全部定时

## 仓库里给你准备好的东西

- [scripts/bootstrap.sh](scripts/bootstrap.sh)：一键建虚拟环境并安装
- [scripts/start-local.sh](scripts/start-local.sh)：本地启动或首次扫码
- [scripts/install-systemd.sh](scripts/install-systemd.sh)：一键安装 `systemd`

## 目录说明

运行后会自动生成：

- `.lazybot/xmu-wechatbot.env`：运行环境变量
- `.lazybot/data/`：微信凭证、XMU 登录缓存和账号映射
- `xmu-rollcall-cli/.venv/`：项目虚拟环境

## 高级手动部署

如果你不想用懒人脚本，也可以看手动版：

- [docs/ubuntu-wechatbot-deploy.md](docs/ubuntu-wechatbot-deploy.md)
