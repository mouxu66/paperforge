@echo off
REM PaperForge 教师版打包镜像一键构建脚本（Windows）
REM 双击运行一次即可，后续 /api/admin/package 会自动调用本镜像
echo ========================================
echo   PaperForge Builder 镜像构建
echo ========================================
echo.

REM 检查 Docker 是否安装
where docker >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Docker，请先安装 Docker Desktop。
    echo        下载地址：https://www.docker.com/products/docker-desktop
    pause
    exit /b 1
)

REM 检查 Docker 是否运行
docker info >nul 2>nul
if errorlevel 1 (
    echo [错误] Docker 未运行，请先启动 Docker Desktop。
    pause
    exit /b 1
)

REM 确保 .env 存在（避免缺失导致启动失败；不会覆盖已有 .env）
if not exist ".env" (
    if exist ".env.dist" (
        copy .env.dist .env >nul 2>&1
        echo [1/2] 已复制 .env.dist 到 .env
    )
)

echo [1/2] 正在构建 paperforge-builder 镜像（首次约 3-5 分钟）...
docker build -f Dockerfile.builder -t paperforge-builder .
if errorlevel 1 (
    echo [错误] 镜像构建失败。
    pause
    exit /b 1
)

echo.
echo [2/2] 构建完成！
echo.
echo 后续可在网页右上角点击「导出教师版」按钮，自动生成 .exe。
echo.
pause
