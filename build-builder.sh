#!/usr/bin/env bash
# PaperForge 教师版打包镜像一键构建脚本（Mac/Linux）
# 用法：./build-builder.sh
set -e

echo "========================================"
echo "  PaperForge Builder 镜像构建"
echo "========================================"
echo

# 检查 Docker 是否安装并运行
if ! command -v docker >/dev/null 2>&1; then
    echo "[错误] 未检测到 Docker，请先安装 Docker。"
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "[错误] Docker 未运行，请先启动 Docker。"
    exit 1
fi

echo "[1/2] 正在构建 paperforge-builder 镜像（首次约 3-5 分钟）..."
docker build -f Dockerfile.builder -t paperforge-builder .

echo
echo "[2/2] 构建完成！"
echo
echo "后续可在网页右上角点击「导出教师版」按钮，自动生成 .exe。"
echo
