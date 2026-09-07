# XMU 微信签到机器人（阿里云 VPS）

这是一个常驻在 Linux VPS 上的微信机器人，用于通过微信指令查询并完成 TronClass 签到。

支持的指令包括：

- `/conf`：配置学号和密码
- `/accounts`、`/switch 账号ID`：管理多个账号
- `/answer`：立即检查并自动完成数字或雷达签到
- `/qr`：等待二维码照片后识别并签到；也可使用 `/qr 二维码内容`
- `/cron`：管理定时签到任务
- `/refresh`、`/cancel`、`/help`

## 阿里云 VPS 部署

适用于 Ubuntu 22.04 或其他具有 Python 3.9+、systemd 的 Linux 发行版。

```bash
git clone <你的仓库地址> xmu-rollcall-wechat-bot
cd xmu-rollcall-wechat-bot
bash scripts/bootstrap.sh
bash scripts/start-local.sh --login-only
sudo bash scripts/install-systemd.sh
```

首次运行 `--login-only` 后，按输出的链接用机器人微信号扫码。成功后，服务会自动以后台方式运行。

## 日常维护

```bash
# 服务状态
sudo systemctl status xmu-wechatbot --no-pager -l

# 实时日志
sudo journalctl -u xmu-wechatbot -f

# 更新已拉取的代码后，重新安装并重启
bash scripts/bootstrap.sh
sudo systemctl restart xmu-wechatbot
```

运行数据不在 Git 中：

- `.lazybot/xmu-wechatbot.env`：服务环境变量
- `.lazybot/data/` 或 `~/.xmu_rollcall/`：微信登录凭证、账号配置和缓存

更新代码时务必保留这些目录。

## 项目结构

- `xmu-rollcall-cli/`：机器人程序
- `scripts/`：VPS 初始化、前台登录与 systemd 安装脚本
