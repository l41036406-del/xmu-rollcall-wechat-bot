FROM python:3.12-slim

WORKDIR /app

# 安装 QR 解码依赖（pyzbar 需要 libzbar0；opencv-python-headless 需要 glib/gomp）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libzbar0 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 复制项目
COPY . .

# 安装 Python 依赖
RUN pip install --no-cache-dir -e xmu-rollcall-cli/

# 默认启动（可在 Azure 配置中覆盖为 wechat_bot）
CMD ["python", "-m", "xmu_rollcall.wechat_bot"]
