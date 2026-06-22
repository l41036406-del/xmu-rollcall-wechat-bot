FROM python:3.12-slim

WORKDIR /app

# 安装 QR 解码依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libzbar0 \
    && rm -rf /var/lib/apt/lists/*

# 复制项目
COPY . .

# 安装 Python 依赖
RUN pip install --no-cache-dir -e xmu-rollcall-cli/

# 默认启动（可在 Azure 配置中覆盖为 wechat_bot）
CMD ["python", "-m", "xmu_rollcall.wechat_bot"]
