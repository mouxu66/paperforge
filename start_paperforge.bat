@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

set "LAUNCH_LOG=%~dp0paperforge_launch.log"
set "SERVER_LOG=%~dp0paperforge_server.log"
echo [%date% %time%] BAT started > "%LAUNCH_LOG%"
echo [%date% %time%] BAT cwd=%~dp0 >> "%LAUNCH_LOG%"

REM === detect project root (walk up from bat location) ===
set "PROJECT_ROOT=%~dp0"
:find_root
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
if exist "%PROJECT_ROOT%\mock_api\launcher.py" goto :root_found
for %%F in ("%PROJECT_ROOT%\..") do set "PARENT=%%~fF"
if "%PARENT:~-1%"=="\" set "PARENT=%PARENT:~0,-1%"
if /I "%PARENT%"=="%PROJECT_ROOT%" goto :root_not_found
set "PROJECT_ROOT=%PARENT%"
goto :find_root

:root_not_found
echo [%date% %time%] ROOT_NOT_FOUND >> "%LAUNCH_LOG%"
echo ============================================================
echo [ERROR] Cannot find PaperForge project root!
echo ============================================================
echo mock_api\launcher.py not found.
echo Put this bat in the PaperForge project root.
echo Log: %LAUNCH_LOG%
echo ============================================================
pause
exit /b 1

:root_found
echo [%date% %time%] PROJECT_ROOT=%PROJECT_ROOT% >> "%LAUNCH_LOG%"
cd /d "%PROJECT_ROOT%"

REM 确保 .env 存在（避免缺失导致启动失败；不会覆盖已有 .env）
if not exist ".env" (
    if exist ".env.dist" (
        copy .env.dist .env >nul 2>&1
        echo [%date% %time%] Copied .env.dist to .env >> "%LAUNCH_LOG%"
    )
)

REM === detect python: pick the FIRST that can fully import mock_api.launcher ===
REM NOTE: never `goto` inside a `for ... do (...)` block; it can silently
REM terminate the whole script. Use a flag + check after the loop instead.
set "PYEXE="
set "PYOK=0"
for /f "delims=" %%i in ('where python 2^>nul') do (
    if not "!PYOK!"=="1" (
        "%%i" -c "import mock_api.launcher" >nul 2>&1
        if not errorlevel 1 (
            set "PYEXE=%%i"
            set "PYOK=1"
            echo [%date% %time%] PYEXE=%%i import mock_api.launcher OK >> "%LAUNCH_LOG%"
        ) else (
            echo [%date% %time%] SKIP %%i cannot import mock_api.launcher >> "%LAUNCH_LOG%"
        )
    )
)
if "!PYOK!"=="1" goto :python_found

REM fallback: py launcher (Windows Python launcher)
REM Resolve py -> actual python.exe so we launch the same interpreter consistently.
for /f "delims=" %%i in ('where py 2^>nul') do (
    if not "!PYOK!"=="1" (
        for /f "delims=" %%j in ('"%%i" -c "import sys; print(sys.executable)"') do (
            "%%j" -c "import mock_api.launcher" >nul 2>&1
            if not errorlevel 1 (
                set "PYEXE=%%j"
                set "PYOK=1"
                echo [%date% %time%] PYEXE=%%j py launcher, import OK >> "%LAUNCH_LOG%"
            ) else (
                echo [%date% %time%] SKIP %%j py launcher, cannot import >> "%LAUNCH_LOG%"
            )
        )
    )
)
if "!PYOK!"=="1" goto :python_found

REM fallback: common virtualenv locations
for %%P in (
    "%PROJECT_ROOT%\.venv\Scripts\python.exe"
    "%PROJECT_ROOT%\.venv\python.exe"
    "%PROJECT_ROOT%\venv\Scripts\python.exe"
    "%~dp0.venv\Scripts\python.exe"
) do (
    if not "!PYOK!"=="1" (
        if exist %%P (
            %%P -c "import mock_api.launcher" >nul 2>&1
            if not errorlevel 1 (
                set "PYEXE=%%P"
                set "PYOK=1"
                echo [%date% %time%] PYEXE=%%P venv fallback, import OK >> "%LAUNCH_LOG%"
            ) else (
                echo [%date% %time%] SKIP %%P venv, cannot import >> "%LAUNCH_LOG%"
            )
        )
    )
)
if "!PYOK!"=="1" goto :python_found

echo [%date% %time%] NO_SUITABLE_PYTHON >> "%LAUNCH_LOG%"
echo ============================================================
echo [ERROR] No Python interpreter can run PaperForge!
echo ============================================================
echo None of the pythons in PATH can import mock_api.launcher.
echo Install deps: pip install -r mock_api/requirements.txt
echo or create a .venv in the project root.
echo Log: %LAUNCH_LOG%
echo ============================================================
pause
exit /b 1

:python_found
echo ========================================
echo   PaperForge
echo ========================================
echo Python:  !PYEXE!
echo Project: %PROJECT_ROOT%
echo.

REM ---- Python dependency check ----
"!PYEXE!" -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
    echo [1/2] Installing Python dependencies...
    "!PYEXE!" -m pip install -r mock_api\requirements.txt --quiet
    if errorlevel 1 (
        echo [ERROR] pip install failed.
        pause
        exit /b 1
    )
) else (
    echo [1/2] Python dependencies OK.
)

REM ---- llama-server (8080) hosting note (Plan B) ----
netstat -an 2>nul | findstr ":8080" | findstr "LISTEN" >nul
if errorlevel 1 (
    if "%PAPERFORGE_QWEN_AUTOSTART%"=="false" (
        echo [PRE] Starting llama-server via desktop bat [QWEN_AUTOSTART=false]...
        start "" /MIN cmd /c "%USERPROFILE%\Desktop\start-llama-dflash-wsl.bat"
    ) else (
        echo [PRE] llama-server will be auto-managed by PaperForge.
    )
) else (
    echo [PRE] llama-server already listening on :8080.
)

REM ---- Web dependency check (prod served from web/dist) ----
if not exist "%PROJECT_ROOT%\web\node_modules" (
    if not exist "%PROJECT_ROOT%\web\dist" (
        echo [2/2] Installing web/node_modules...
        where npm >nul 2>&1
        if errorlevel 1 (
            echo [ERROR] npm not found. Please install Node.js 18+ and add it to PATH.
            pause
            exit /b 1
        )
        cd /d "%PROJECT_ROOT%\web"
        call npm install
        if errorlevel 1 (
            echo [%date% %time%] npm install failed >> "%LAUNCH_LOG%"
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

echo [%date% %time%] Launching launcher.py ... >> "%LAUNCH_LOG%"
echo PaperForge is starting. The browser will open automatically.
echo Diagnostics log: %LAUNCH_LOG%
echo Server log:      %SERVER_LOG%
echo (Keep this window open while using PaperForge)
echo.

REM Force UTF-8 for Python stdout/stderr so non-GBK characters (e.g. checkmark) do not crash.
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

REM ---- Browser open safety net ----
REM launcher.py writes the URL to .paperforge_url once the service is ready.
REM In case Python's webbrowser module fails (common when stdout is redirected
REM to a file), this tiny monitor opens the URL via the OS default browser.
if exist ".paperforge_url" del /f /q ".paperforge_url" >nul 2>&1
start "" /MIN cmd /c "chcp 65001 >nul & cd /d \"%PROJECT_ROOT%\" & for /L %%I in (1,1,60) do ( if exist \".paperforge_url\" ( for /f \"delims=\" %%U in (.paperforge_url) do ( start \"\" \"%%U\" ) & exit ) else ( ping 127.0.0.1 -n 2 >nul ) )"

"!PYEXE!" mock_api\launcher.py >> "%SERVER_LOG%" 2>&1
set "LAUNCH_RC=%errorlevel%"

REM Cleanup browser open signal file
if exist ".paperforge_url" del /f /q ".paperforge_url" >nul 2>&1
echo [%date% %time%] Launcher exited with code %LAUNCH_RC% >> "%LAUNCH_LOG%"

echo.
echo ========================================
echo  Launcher exit code: %LAUNCH_RC%
echo  Diagnostics log: %LAUNCH_LOG%
echo  Server log:      %SERVER_LOG%
echo ========================================
echo.
echo ---- diagnostics log tail ----
type "%LAUNCH_LOG%"
echo.
echo ---- server log tail ----
type "%SERVER_LOG%"
echo.
endlocal

echo Press any key to close this window...
pause
