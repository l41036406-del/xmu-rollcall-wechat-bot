# Azure 部署脚本
param(
    [string]$ResourceGroup = "xmu-rollcall-bot",
    [string]$VmName = "xmu-wechat-bot",
    [string]$Location = "eastasia",
    [string]$VmSize = "Standard_B1s"
)

Write-Host "=== 部署 XMU WeChat Bot 到 Azure ===" -ForegroundColor Cyan

# 清理旧资源（如果存在）
az vm delete --resource-group $ResourceGroup --name $VmName --yes 2>$null
Write-Host "[1/4] 旧 VM 已清理" -ForegroundColor Green

# 重试逻辑：尝试不同区域和大小
$regions = @("eastasia", "southeastasia", "australiaeast", "westus", "eastus")
$sizes = @("Standard_B1s", "Standard_B1ms", "Standard_DS1_v2")

$deployed = $false
foreach ($region in $regions) {
    foreach ($size in $sizes) {
        Write-Host "  尝试 $region / $size ..." -ForegroundColor Yellow
        $result = az vm create `
            --resource-group $ResourceGroup `
            --name $VmName `
            --image "Canonical:0001-com-ubuntu-server-jammy:22_04-lts:latest" `
            --size $size `
            --admin-username azureuser `
            --generate-ssh-keys `
            --public-ip-sku Standard `
            --security-type Standard `
            --location $region `
            --output json 2>$null

        if ($LASTEXITCODE -eq 0) {
            $deployed = $true
            Write-Host "  => 成功! Region=$region, Size=$size" -ForegroundColor Green
            break
        }
    }
    if ($deployed) { break }
}

if (-not $deployed) {
    Write-Host "ERROR: 无法创建 VM，请联系支持" -ForegroundColor Red
    exit 1
}

# 获取公网 IP
Write-Host "[2/4] 获取公网 IP..." -ForegroundColor Green
$ip = az vm show --resource-group $ResourceGroup --name $VmName --show-details --query publicIps -o tsv
Write-Host "  VM IP: $ip" -ForegroundColor Cyan

# 等待 VM 就绪
Write-Host "[3/4] 等待 VM 就绪 (60s)..." -ForegroundColor Green
Start-Sleep -Seconds 60

# 通过 SSH 执行部署脚本
Write-Host "[4/4] 在 VM 上部署签到机器人..." -ForegroundColor Green
$sshCmd = @"
set -e
echo '=== 开始部署 ==='
cd /opt
sudo git clone https://github.com/l41036406-del/xmu-rollcall-wechat-bot.git xmu-rollcall-wechat-bot 2>/dev/null || true
sudo chown -R azureuser:azureuser /opt/xmu-rollcall-wechat-bot
cd /opt/xmu-rollcall-wechat-bot
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e xmu-rollcall-cli/
pip install -U xmulogin

# systemd 服务
sudo tee /etc/systemd/system/xmu-wechatbot.service > /dev/null << 'EOF'
[Unit]
Description=XMU Rollcall WeChat Bot
After=network.target

[Service]
Type=simple
User=azureuser
WorkingDirectory=/opt/xmu-rollcall-wechat-bot
ExecStart=/opt/xmu-rollcall-wechat-bot/.venv/bin/python -m xmu_rollcall.wechat_bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo 'DEPLOYMENT_COMPLETE'
"@

$sshOutput = ssh -o StrictHostKeyChecking=no azureuser@$ip $sshCmd 2>&1
Write-Host $sshOutput

# 输出连接信息
Write-Host "" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  部署完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "VM IP: $ip" -ForegroundColor Cyan
Write-Host "SSH: ssh azureuser@$ip" -ForegroundColor Cyan
Write-Host "首次扫码登录: ssh azureuser@$ip 'cd /opt/xmu-rollcall-wechat-bot && source .venv/bin/activate && python -m xmu_rollcall.wechat_bot --login-only'" -ForegroundColor Cyan
Write-Host "启动服务: ssh azureuser@$ip 'sudo systemctl start xmu-wechatbot'" -ForegroundColor Cyan
Write-Host "查看状态: ssh azureuser@$ip 'sudo journalctl -u xmu-wechatbot -f'" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
