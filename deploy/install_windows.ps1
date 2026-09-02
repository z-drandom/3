# ============ EHS 知识库 Windows Server 一键安装（PowerShell，管理员运行）============
# 用法： 右键「以管理员身份运行 PowerShell」，执行：
#   Set-ExecutionPolicy Bypass -Scope Process -Force
#   .\deploy\install_windows.ps1
# 前置：已安装 Python 3.10+（勾选 Add to PATH）与 nssm（https://nssm.cc，解压后把 nssm.exe 放进 PATH 或与本脚本同目录）

$ErrorActionPreference = "Stop"

$AppDir      = "D:\ehs\app"          # 程序目录
$DocsRoot    = "D:\ehs\docs"         # 知识库根目录（数据）
$Port        = 8080
$AdminToken  = "Change-Me-2026-EHS"  # 管理口令，请修改
$ServiceName = "EHS-KB"

Write-Host "[1/7] 创建目录" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $AppDir, $DocsRoot, "$DocsRoot\.trash" | Out-Null
foreach ($c in @("危化品","特种设备","危废","应急","法规","SOP")) {
    New-Item -ItemType Directory -Force -Path "$DocsRoot\$c" | Out-Null
}

Write-Host "[2/7] 复制程序文件" -ForegroundColor Cyan
$Src = Split-Path -Parent $PSScriptRoot
robocopy $Src $AppDir /E /XD .git .venv docs /NFL /NDL /NJH /NJS | Out-Null
# 首次安装拷入示例文档（已有内容则跳过）
if (-not (Get-ChildItem $DocsRoot -Exclude ".trash")) {
    robocopy "$Src\docs" $DocsRoot /E /NFL /NDL /NJH /NJS | Out-Null
}

Write-Host "[3/7] 创建虚拟环境并安装依赖" -ForegroundColor Cyan
python -m venv "$AppDir\.venv"
& "$AppDir\.venv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel -q
# 内网离线安装改用： --no-index --find-links=D:\ehs\pkgs
& "$AppDir\.venv\Scripts\pip.exe" install -q -r "$AppDir\requirements.txt"

Write-Host "[4/7] 用 nssm 注册 Windows 服务（开机自启）" -ForegroundColor Cyan
$nssm = (Get-Command nssm.exe -ErrorAction SilentlyContinue).Source
if (-not $nssm) { $nssm = Join-Path $PSScriptRoot "nssm.exe" }
if (-not (Test-Path $nssm)) { throw "找不到 nssm.exe，请先从 https://nssm.cc 下载并放入 PATH 或 deploy 目录" }

& $nssm stop    $ServiceName 2>$null | Out-Null
& $nssm remove  $ServiceName confirm 2>$null | Out-Null
& $nssm install $ServiceName "$AppDir\.venv\Scripts\python.exe" "-m uvicorn app.main:app --host 0.0.0.0 --port $Port --workers 1"
& $nssm set $ServiceName AppDirectory  $AppDir
& $nssm set $ServiceName DisplayName   "EHS 知识库"
& $nssm set $ServiceName Description   "EHS 知识库（FastAPI，文件即数据库，Markdown 热重载）"
& $nssm set $ServiceName Start         SERVICE_AUTO_START
& $nssm set $ServiceName AppStdout     "$AppDir\logs\service.log"
& $nssm set $ServiceName AppStderr     "$AppDir\logs\service.log"
& $nssm set $ServiceName AppRotateFiles 1
& $nssm set $ServiceName AppRotateBytes 10485760
# 环境变量（多条用回车分隔）
& $nssm set $ServiceName AppEnvironmentExtra `
    "EHS_DOCS_ROOT=$DocsRoot" `
    "EHS_ADMIN_TOKEN=$AdminToken" `
    "EHS_HOST=0.0.0.0" `
    "EHS_PORT=$Port" `
    "EHS_TRASH_RETAIN_DAYS=90" `
    "EHS_REVIEW_WARN_DAYS=30"
New-Item -ItemType Directory -Force -Path "$AppDir\logs" | Out-Null

Write-Host "[5/7] 放行防火墙端口 $Port" -ForegroundColor Cyan
New-NetFirewallRule -DisplayName "EHS-KB $Port" -Direction Inbound -Protocol TCP `
    -LocalPort $Port -Action Allow -Profile Any -ErrorAction SilentlyContinue | Out-Null

Write-Host "[6/7] 启动服务" -ForegroundColor Cyan
& $nssm start $ServiceName
Start-Sleep -Seconds 5
& $nssm status $ServiceName

Write-Host "[7/7] 注册每日备份计划任务（每天 01:30）" -ForegroundColor Cyan
schtasks /Create /TN "EHS知识库每日备份" /TR "`"$AppDir\deploy\backup.bat`"" /SC DAILY /ST 01:30 /RU SYSTEM /F | Out-Null

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
       Select-Object -First 1).IPAddress
Write-Host "====================================================" -ForegroundColor Green
Write-Host " 部署完成： http://${ip}:$Port"
Write-Host " 知识库目录： $DocsRoot"
Write-Host " 管理口令： $AdminToken （请修改后执行： nssm restart $ServiceName）"
Write-Host "====================================================" -ForegroundColor Green
