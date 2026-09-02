@echo off
chcp 65001 >nul
REM ============ EHS 知识库 每日备份（Windows）============
REM 手动执行： deploy\backup.bat
REM 定时执行： 已由 install_windows.ps1 注册为「EHS知识库每日备份」计划任务（每天 01:30）
setlocal enabledelayedexpansion

REM ---- 可修改的三个参数 ----
set "DOCS_ROOT=D:\ehs\docs"
set "BACKUP_DIR=\\NAS\backup\ehs"
set "KEEP_DAYS=30"

REM ---- 生成 yyyyMMdd-HHmmss 时间戳（不受系统区域设置影响）----
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%i"
set "TARGET=%BACKUP_DIR%\ehs-docs-%STAMP%.zip"

if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
echo [%date% %time%] 开始备份 %DOCS_ROOT% -^> %TARGET%

REM ---- 用 PowerShell 压缩（Windows Server 2012 R2 以上自带）----
powershell -NoProfile -Command ^
  "Compress-Archive -Path '%DOCS_ROOT%\*' -DestinationPath '%TARGET%' -CompressionLevel Optimal -Force"

if not exist "%TARGET%" (
    echo [错误] 备份失败，未生成 %TARGET%
    exit /b 1
)
for %%A in ("%TARGET%") do echo [%date% %time%] 备份成功，大小 %%~zA 字节

REM ---- 清理超过保留天数的旧备份 ----
forfiles /P "%BACKUP_DIR%" /M ehs-docs-*.zip /D -%KEEP_DAYS% /C "cmd /c echo 清理旧备份 @file && del /q @path" 2>nul

endlocal
exit /b 0
