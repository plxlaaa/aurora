<#
.SYNOPSIS
    Aurora AI 智能助手 —— WSL2 部署脚本（Windows 10/11 内置 WSL2）。

.DESCRIPTION
    在 WSL2（Ubuntu/Debian 发行版）内完成网关安装，复用 Linux 安装流程，
    使Windows 侧也能享受 systemd + nginx 的托管能力。

    与原生 Windows 安装（deploy\windows\install.ps1）的差异：
      - 走Linux 原生 systemd 服务，无需 NSSM / IIS；
      - 可通过 localhost 转发从Windows 宿主机直接访问。

    典型用法（在 Windows PowerShell 中）：
      # 1. 确认 WSL2 可用并查看发行版
      wsl --list --verbose

      # 2. 从 WSL 文件系统调用本脚本（需先放入仓库副本）
      wsl -d Ubuntu -- bash -lc "cd /mnt/c/path/to/aurora && sudo bash deploy/wsl2/install-wsl2.sh"

      # 3. 安装完成后从 Windows 浏览器访问
      #    http://localhost:8080

.PARAMETER InstallDir
    WSL 内的安装目录，默认 /opt/aiassistant。

.PARAMETER Port
    网关监听端口，默认 8080。

.PARAMETER Distro
    仅用于提示信息，默认 Ubuntu。

.PARAMETER SkipFrontend
    跳过前端构建（已有 dist 产物时使用）。

.PARAMETER Uninstall
    卸载模式：停止并删除 systemd 服务（保留数据，--purge 可一并删除）。

.PARAMETER Purge
    与 -Uninstall 搭配，额外删除配置与会话数据目录（不可逆）。

.EXAMPLE
    wsl -d Ubuntu -- bash -lc "sudo bash deploy/wsl2/install-wsl2.sh"

.EXAMPLE
    wsl -d Ubuntu -- bash -lc "sudo bash deploy/wsl2/install-wsl2.sh --skip-frontend --port 9090"

.NOTES
    前置要求：
      1. Windows 10 2004+ / Windows 11 已启用 WSL2（wsl --install）；
      2. 发行版内已具备 python3.10+ 与 sudo；
      3. 前端构建需Node.js 18+（可用 --skip-frontend 跳过）。
#>

[CmdletBinding()]
param(
    [string]$InstallDir = "/opt/aiassistant",
    [ValidateRange(1, 65535)]
    [int]$Port = 8080,
    [string]$Distro = "Ubuntu",
    [switch]$SkipFrontend,
    [switch]$Uninstall,
    [switch]$Purge
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$WslRepoPath = "/mnt/$($RepoRoot.Substring(0,1).ToLower())/$($RepoRoot.Substring(3) -replace '\\','/')"

function Write-Step { param([string]$M) Write-Host "==> $M" -ForegroundColor Cyan }
function Write-Ok   { param([string]$M) Write-Host "  [OK] $M" -ForegroundColor Green }
function Write-Warn { param([string]$M) Write-Host "  [!]  $M" -ForegroundColor Yellow }
function Write-Err  { param([string]$M) Write-Host "  [X]  $M" -ForegroundColor Red }
function Die        { param([string]$M) Write-Err $M; exit 1 }

# ---------- 确认 WSL2 可用 ----------
$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wsl) { Die "未找到 wsl.exe，请先执行 wsl --install 后重启系统。" }

$distros = & wsl.exe --list --quiet 2>$null | Where-Object { $_ -and $_.Trim() }
if (-not $distros) { Die "未检测到 WSL 发行版，请先运行：wsl --install -d Ubuntu" }
if ($Distro -notin $distros) {
    Write-Warn "未找到发行版 '$Distro'，可用发行版：$($distros -join ', ')"
    Die "请用 -Distro 指定正确的发行版名。"
}

Write-Step "在 WSL2 发行版 $Distro 中部署 Aurora"
Write-Host "  仓库路径: $WslRepoPath"
Write-Host "  安装目录: $InstallDir"
Write-Host "  监听端口: $Port"

# ---------- 组装 Linux 侧命令 ----------
$linuxArgs = @()
if ($SkipFrontend) { $linuxArgs += "--skip-frontend" }
if ($Uninstall)     { $linuxArgs += "--uninstall" }
if ($Purge)         { $linuxArgs += "--purge" }
$linuxArgs += @("--install-dir", $InstallDir, "--port", $Port)

# WSL 侧脚本；本文件位于 deploy/wsl2/，对应仓库内deploy/wsl2/install-wsl2-linux.sh
$linuxScript = Join-Path $WslRepoPath "deploy/wsl2/install-wsl2-linux.sh"
$quoted = ($linuxArgs | ForEach-Object { "'" + ($_ -replace "'", "'\''") + "'" }) -join " "
$cmd = "test -f '$linuxScript' && sudo bash '$linuxScript' $quoted || { echo 'ERROR: 未找到安装脚本 $linuxScript（请在 WSL 内先克隆仓库）'; exit 1; }"

Write-Host ""
& wsl.exe -d $Distro -- bash -lc $cmd
$exit = $LASTEXITCODE

if ($exit -ne 0) {
    Write-Host ""
    Die "WSL 内安装失败（退出码 $exit），请在该发行版内手动执行以查看详细日志：sudo bash $linuxScript"
}

Write-Host ""
Write-Host "======================================================" -ForegroundColor Green
Write-Host " WSL2 部署完成" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Green
Write-Host " 访问地址   : http://localhost:$Port （WSL2 localhost 转发）"
Write-Host " 健康检查   : wsl -d $Distro -- curl http://127.0.0.1:$Port/api/health"
Write-Host " 服务管理   : wsl -d $Distro -- sudo systemctl status aiassistant-gateway"
Write-Host " 查看日志   : wsl -d $Distro -- sudo journalctl -u aiassistant-gateway -f"
Write-Host " 彻底卸载   : wsl -d $Distro -- sudo systemctl disable --now aiassistant-gateway"
Write-Host "======================================================" -ForegroundColor Green
