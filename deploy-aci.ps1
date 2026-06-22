$ErrorActionPreference = "Stop"

Write-Host "=== XMU WeChat Bot - Azure Container Instances 部署 ===" -ForegroundColor Cyan

$scriptBlock = @'
apt-get update && apt-get install -y libzbar0 git && pip install -U xmulogin && cd /tmp && git clone https://github.com/l41036406-del/xmu-rollcall-wechat-bot.git && cd /tmp/xmu-rollcall-wechat-bot && pip install -e xmu-rollcall-cli/ && echo "BOT_READY" && python -m xmu_rollcall.wechat_bot --login-only
'@

Write-Host "[1/3] 创建 Container Instance..." -ForegroundColor Green
$result = az container create `
    --resource-group xmu-rollcall-bot `
    --name xmu-wechat-bot `
    --image python:3.12-slim `
    --os-type Linux `
    --cpu 1 --memory 1 `
    --location southeastasia `
    --command-line "sh -c `"$scriptBlock`"" `
    --restart-policy Never `
    --output table 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: $result" -ForegroundColor Red
    exit 1
}

Write-Host "  $result" -ForegroundColor Green

Write-Host "[2/3] 等待容器启动 (30s)..." -ForegroundColor Green
Start-Sleep -Seconds 30

Write-Host "[3/3] 查看容器日志（扫码登录 URL 会出现在这里）:" -ForegroundColor Yellow
az container logs --resource-group xmu-rollcall-bot --name xmu-wechat-bot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "首次扫码登录已完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "容器停止后，删除重新创建以启动机器人:" -ForegroundColor Yellow
Write-Host "  az container delete -g xmu-rollcall-bot -n xmu-wechat-bot --yes" -ForegroundColor Yellow
Write-Host "  az container create ... --command-line 'python -m xmu_rollcall.wechat_bot' --restart-policy Always" -ForegroundColor Yellow
