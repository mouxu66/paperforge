# PaperForge 桌面版打包构建镜像
# 基础镜像：python:3.11-slim，内置 Node.js 20 + PyInstaller
# 用途：开发者执行 docker build -f Dockerfile.builder -t paperforge-builder . 后，
#       后端 /api/admin/package 接口会调用本镜像执行 pyinstaller 打包
FROM python:3.11-slim

# 安装 Node.js 20（nodesource 源）+ 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg \
        build-essential \
        libffi-dev \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 预装 Python 依赖（与 requirements.txt 保持一致）
COPY mock_api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir pyinstaller paper2md pypdf zhipuai openai requests

# 默认入口：pyinstaller（由后端调用时传入 paperforge.spec）
ENTRYPOINT ["pyinstaller"]
