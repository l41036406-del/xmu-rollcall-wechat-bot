"""Azure Container Instances deployment for XMU WeChat Bot"""
import subprocess, time, sys

RG = "xmu-rollcall-bot"
NAME = "xmu-wechat-bot"

def run_az(*args):
    cmd = ["az.bat"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result

# Delete old container if exists
print("[1/3] Cleaning up old container...")
run_az("container", "delete", "-g", RG, "-n", NAME, "--yes")

# Startup script as a single-line Python script passed to `python -c`
# This avoids shell quoting issues entirely
SETUP_PY = r'''
import subprocess, sys
subprocess.run(["apt-get", "update", "-qq"], check=False)
subprocess.run(["apt-get", "install", "-y", "-qq", "libzbar0", "git"], check=False)
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "xmulogin"], check=False)
subprocess.run(["git", "clone", "-q", "--depth", "1",
    "https://github.com/l41036406-del/xmu-rollcall-wechat-bot.git", "/tmp/bot"], check=False)
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", "/tmp/bot/xmu-rollcall-cli"], check=False)
print("=== SETUP COMPLETE ===", flush=True)
sys.stdout.flush()
import xmu_rollcall.wechat_bot
xmu_rollcall.wechat_bot.main(["--login-only"])
'''

print("[2/3] Creating container...")
result = run_az("container", "create",
    "--resource-group", RG,
    "--name", NAME,
    "--image", "python:3.12-slim",
    "--os-type", "Linux",
    "--cpu", "2", "--memory", "2",
    "--location", "southeastasia",
    "--command-line",
    'python3', '-c', SETUP_PY,
    "--restart-policy", "Never")

if result.returncode != 0:
    print(f"ERROR: {result.stderr}")
    sys.exit(1)
print("  Container created successfully")

print("[3/3] Waiting for QR code...")
time.sleep(5)
for i in range(60):
    result = run_az("container", "logs", "-g", RG, "-n", NAME)
    out = (result.stdout or "") + (result.stderr or "")
    if "请扫码" in out or "https://login.weixin" in out.lower() or "login.weixin" in out.lower():
        print("\n=== QR CODE URL DETECTED ===")
        # Extract QR URL
        for line in out.split('\n'):
            if 'login.weixin' in line.lower() or '请扫码' in line:
                print(line)
        print("\nOpen this URL on your phone to scan with WeChat!")
        break
    if "登录成功" in out or "LOGIN" in out:
        print("\n=== LOGIN SUCCESSFUL ===")
        print(result.stdout)
        break
    if "SETUP COMPLETE" in out:
        print("  Setup complete, waiting for WeChat QR...")
    time.sleep(2)

print("\nFull logs:")
result = run_az("container", "logs", "-g", RG, "-n", NAME)
print((result.stdout or "")[-2000:])
