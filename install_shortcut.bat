@echo off
REM 安装 PaperForge 桌面快捷方式
REM 双击本文件即可在桌面创建 PaperForge.lnk

setlocal
set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"

echo ========================================
echo  创建 PaperForge 桌面快捷方式
echo ========================================
echo.
echo 项目目录: %PROJECT_ROOT%

REM Create a temporary VBScript to create the shortcut
set "VBS=%TEMP%\create_paperforge_shortcut.vbs"
> "%VBS%" echo Set ws = CreateObject("WScript.Shell")
>> "%VBS%" echo desktop = ws.SpecialFolders("Desktop")
>> "%VBS%" echo Set fso = CreateObject("Scripting.FileSystemObject")
>> "%VBS%" echo oldBat = desktop ^& "\PaperForge-run.bat"
>> "%VBS%" echo If fso.FileExists(oldBat) Then
>> "%VBS%" echo   fso.DeleteFile oldBat, True
>> "%VBS%" echo End If
>> "%VBS%" echo Set shortcut = ws.CreateShortcut(desktop ^& "\PaperForge.lnk")
>> "%VBS%" echo shortcut.TargetPath = "%PROJECT_ROOT:\=\\%\\start_paperforge.bat"
>> "%VBS%" echo shortcut.WorkingDirectory = "%PROJECT_ROOT%"
>> "%VBS%" echo shortcut.Description = "PaperForge - Academic Writing Assistant"
>> "%VBS%" echo shortcut.Save()
>> "%VBS%" echo WScript.Echo "OK: " ^& desktop ^& "\PaperForge.lnk"

echo.
echo 正在创建快捷方式...
cscript //nologo "%VBS%"
if errorlevel 1 (
    echo [ERROR] 创建失败。
    pause
    exit /b 1
)

del "%VBS%"
echo.
echo ✅ 桌面快捷方式已创建成功！
echo    文件名: PaperForge.lnk
echo    指向:   %PROJECT_ROOT%\start_paperforge.bat
echo.
echo 现在到桌面上双击 PaperForge.lnk 就可以启动了！
echo.
pause
