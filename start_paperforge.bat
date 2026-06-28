@echo off
REM PaperForge 一键启动：后端 + 前端
REM 双击桌面 PaperForge.lnk 即可
REM launcher.py 会自动探测端口、启动前端（生产/开发模式）、打开浏览器

setlocal
chcp 65001 >nul
set PYEXE=C:\Users\mouxu\AppData\Local\Programs\Python\Python311\python.exe
set PROJECT_ROOT=C:\Users\mouxu\WorkBuddy\2026-06-13-21-30-08\paperforge

echo ========================================
echo   PaperForge
echo ========================================
echo Python:  %PYEXE%
echo Project: %PROJECT_ROOT%
echo.

cd /d "%PROJECT_ROOT%"

REM ---- 1. Python 依赖检查 ----
%PYEXE% -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
    echo [1/2] Installing Python dependencies...
    set PYTHONUTF8=1
    set PYTHONIOENCODING=utf-8
    %PYEXE% -m pip install -r mock_api\requirements.txt --quiet
    if errorlevel 1 (
        echo [ERROR] pip install failed.
        pause
        exit /b 1
    )
) else (
    echo [1/2] Python dependencies OK.
)

REM ---- 2. 前端依赖检查（仅开发模式需要，生产模式由 web/dist 托管）----
if not exist "%PROJECT_ROOT%\web\node_modules" (
    if not exist "%PROJECT_ROOT%\web\dist" (
        echo [2/2] Installing web/node_modules...
        cd /d "%PROJECT_ROOT%\web"
        call npm install
        if errorlevel 1 (
            echo [ERROR] npm install failed.
            pause
            exit /b 1
        )
        cd /d "%PROJECT_ROOT%"
    ) else (
        echo [2/2] Web dist exists, skip npm install.
    )
) else (
    echo [2/2] Web dependencies OK.
)

echo.
echo Starting PaperForge launcher...
echo.

REM ---- 启动统一启动器（自动探测端口 + 启动前端 + 打开浏览器）----
REM 生产模式（web/dist 存在）：FastAPI 托管静态文件
REM 开发模式（web/dist 不存在）：自动启动 Vite dev server
%PYEXE% "%PROJECT_ROOT%\mock_api\launcher.py"

endlocal
