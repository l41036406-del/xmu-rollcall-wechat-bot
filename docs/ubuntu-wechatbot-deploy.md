# Ubuntu 手动部署

这份文档是高级手动版。

如果你只是想尽快跑起来，优先使用仓库根目录的懒人脚本：

```bash
bash scripts/bootstrap.sh
bash scripts/start-local.sh --login-only
sudo bash scripts/install-systemd.sh
```

下面这些步骤适合你想自己掌控路径、用户和 `systemd` 配置的时候再看。

## 1. 准备环境

推荐 Ubuntu 22.04/24.04 + Python 3.11。

```bash
sudo apt update
sudo apt install -y git python3.11 python3.11-venv
```

## 2. 拉代码

```bash
git clone <你的仓库地址>
cd xmu-rollcall-helper/xmu-rollcall-cli
```

## 3. 安装依赖

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

## 4. 配环境变量

你至少需要两个环境变量：

- `XMU_ROLLCALL_CONFIG_DIR`
- `XMU_WECHAT_BOT_CRED_PATH`

示例：

```bash
export XMU_ROLLCALL_CONFIG_DIR=$HOME/.xmu-wechatbot/data
export XMU_WECHAT_BOT_CRED_PATH=$HOME/.xmu-wechatbot/data/wechatbot-credentials.json
mkdir -p "$XMU_ROLLCALL_CONFIG_DIR"
```

## 5. 首次扫码

```bash
python -m xmu_rollcall.wechat_bot --login-only
```

看到登录链接后，用机器人微信号扫码。

## 6. systemd 服务

可以使用仓库内模板 [deploy/xmu-wechatbot.service](../deploy/xmu-wechatbot.service)，也可以直接写成下面这样：

```ini
[Unit]
Description=XMU Rollcall WeChat Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=你的用户名
WorkingDirectory=/你的仓库路径/xmu-rollcall-cli
EnvironmentFile=/你的环境变量文件路径/xmu-wechatbot.env
ExecStart=/你的仓库路径/xmu-rollcall-cli/.venv/bin/python -m xmu_rollcall.wechat_bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

环境变量文件内容示例：

```bash
PYTHONUNBUFFERED=1
TZ=Asia/Shanghai
XMU_ROLLCALL_CONFIG_DIR=/你的数据目录
XMU_WECHAT_BOT_CRED_PATH=/你的数据目录/wechatbot-credentials.json
```

## 7. 常用命令

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now xmu-wechatbot
sudo systemctl status xmu-wechatbot --no-pager -l
sudo journalctl -u xmu-wechatbot -f
```

## 8. 微信侧操作

首次配置：

1. 发送 `/conf`
2. 按提示发送学号
3. 按提示发送密码
4. 发送 `/answer`

常用命令：

- `/accounts`
- `/switch 账号ID`
- `/cron add 4 8:00`
- `/cron del 2`
- `/cron off`
- `/refresh`
- `/cancel`

## 9. 机器人头像

机器人头像来自登录它的微信号本身。

你可以直接把 [pfp.jpg](../pfp.jpg) 设成机器人微信号头像，再扫码登录。
