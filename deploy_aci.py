import subprocess, json, sys, time, os

def run(*args, **kwargs):
    cmd = ["az.bat"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    return result

def wait_for_logs():
    """Wait for QR URL to appear in container logs"""
    for i in range(30):
        result = run("container", "logs",
                     "--resource-group", "xmu-rollcall-bot",
                     "--name", "xmu-wechat-bot")
        out = (result.stdout or "") + (result.stderr or "")
        if "请扫码" in out or "扫码" in out or "https://login.weixin" in out.lower():
            print("\n[FOUND] QR Code URL detected!")
            print(out)
            return out
        if "登录成功" in out:
            print("\n[OK] Login already completed!")
            print(out)
            return out
        time.sleep(2)
    # Print whatever we have
    result = run("container", "logs",
                 "--resource-group", "xmu-rollcall-bot",
                 "--name", "xmu-wechat-bot")
    print(result.stdout or "")
    print(result.stderr or "")
    return result.stdout or result.stderr or ""

# Step 1: Create container that runs the bot
print("=== Creating Azure Container Instance for XMU WeChat Bot ===")
print("[1/3] Creating container (this takes 1-2 minutes)...")

cmd = ("apt-get update -qq && apt-get install -y -qq libzbar0 git && "
       "pip install -q -U xmulogin && "
       "cd /tmp && git clone -q --depth 1 https://github.com/l41036406-del/xmu-rollcall-wechat-bot.git && "
       "cd /tmp/xmu-rollcall-wechat-bot && pip install -q -e xmu-rollcall-cli/ && "
       "echo '=== SETUP COMPLETE ===' && "
       "python -m xmu_rollcall.wechat_bot --login-only")

result = run("container", "create",
             "--resource-group", "xmu-rollcall-bot",
             "--name", "xmu-wechat-bot",
             "--image", "python:3.12-slim",
             "--os-type", "Linux",
             "--cpu", "2", "--memory", "2",
             "--location", "southeastasia",
             "--command-line", f"sh -c '{cmd}'",
             "--restart-policy", "Never")

if result.returncode != 0:
    print(f"ERROR: {result.stderr}")
    sys.exit(1)
print(f"  Container created: {result.stdout.strip()}")

# Step 2: Wait and watch logs
print("[2/3] Waiting for setup and QR code...")
time.sleep(25)
logs = wait_for_logs()

# Step 3: Print instructions
print("\n" + "=" * 60)
print("DEPLOYMENT INSTRUCTIONS")
print("=" * 60)
print()
print("If you see a QR code URL above:")
print("  1. Open that URL in a browser on your phone")
print("  2. Scan the QR code with your WeChat")
print("  3. The bot will save the credentials")
print()
print("Once login is complete, re-run this script")
print("with --production to start the actual bot.")
print()
print("Or manually run in a new container:")
print("  az container create -g xmu-rollcall-bot -n xmu-wechat-bot-prod")
print("    --image python:3.12-slim --cpu 1 --memory 1")
print("    --location southeastasia")
print("    --command-line 'sh -c \"...install...&& python -m xmu_rollcall.wechat_bot\"'")
print("    --restart-policy Always")
