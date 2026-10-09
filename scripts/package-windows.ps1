<#
.SYNOPSIS
    构建 Windows 离线安装包（zip），用于 winget / scoop 分发或内网部署。

.DESCRIPTION
    产出 dist\aurora-<version>-windows-<arch>.zip，内含：
       backend\            后端源码（不含 venv）
       frontend\dist\      前端构建产物
       config\             配置模板（不含任何密钥）
       deploy\             systemd / nginx / 环境变量模板
       install.ps1         一键安装脚本（随包分发，目标机无需 git / npm）
       README.md / INSTALL.md

    与直接运行 deploy\windows\install.ps1 的差异：本脚本只「打包」不「安装」，
    便于上传到内网镜像仓库或GitHub Releases 后由各机器自行安装。

.PARAMETER Version
    版本号，默认读取 deploy\windows\winget 清单中的版本；缺省为 1.3.0。

.PARAMETER Arch
    目标架构：x64（默认）或 arm64。

.PARAMETER OutDir
    输出目录，默认仓库根目录下的 dist。

.PARAMETER WithFrontend
    先执行 npm run build 生成前端产物（默认复用已存在的 frontend\dist）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\package-windows.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\package-windows.ps1 -Version 1.2.0 -WithFrontend

.NOTES
    运行环境：PowerShell 5.1+（Windows）。zip 打包无需管理员权限。
#>

[CmdletBinding()]
param(
    [string]$Version = "",
    [ValidateSet("x64", "arm64")]
    [string]$Arch = "x64",
    [string]$OutDir = "",
    [switch]$WithFrontend
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Write-Step { param([string]$M) Write-Host "==> $M" -ForegroundColor Cyan }
function Write-Ok   { param([string]$M) Write-Host "  [OK] $M" -ForegroundColor Green }
function Write-Warn { param([string]$M) Write-Host "  [!]  $M" -ForegroundColor Yellow }
function Die        { param([string]$M) Write-Host "  [X]  $M" -ForegroundColor Red; exit 1 }

# ---------- 版本号 ----------
if ([string]::IsNullOrWhiteSpace($Version)) {
    $manifestPath = Join-Path $RepoRoot "deploy\windows\winget\plxlaaa.aurora.yaml"
    if (Test-Path $manifestPath) {
        $match = Select-String -Path $manifestPath -Pattern 'PackageVersion:\s*([\d.]+)' | Select-Object -First 1
        if ($match) { $Version = $match.Matches[0].Groups[1].Value }
    }
}
if ([string]::IsNullOrWhiteSpace($Version)) { $Version = "1.3.0" }

if ([string]::IsNullOrWhiteSpace($OutDir)) { $OutDir = Join-Path $RepoRoot "dist" }
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

$StageName = "aurora-$Version-windows-$Arch"
$Stage     = Join-Path $OutDir $StageName
$ZipFile   = Join-Path $OutDir "$StageName.zip"

Write-Step "构建 Windows 离线安装包"
Write-Host "  版本: $Version"
Write-Host "  架构: $Arch"
Write-Host "  暂存: $Stage"

# ---------- 前端产物 ----------
$distPath = Join-Path $RepoRoot "frontend\dist"
if ($WithFrontend) {
    Write-Step "构建前端 (npm ci && npm run build)"
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Die "未找到 npm，无法构建前端（可先手动 npm run build，或去掉 -WithFrontend）"
    }
    Push-Location (Join-Path $RepoRoot "frontend")
    try {
        if (Test-Path "package-lock.json") { npm ci --no-audit --no-fund }
        else { npm install --no-audit --no-fund }
        if ($LASTEXITCODE -ne 0) { Die "npm 安装依赖失败。" }
        npm run build
        if ($LASTEXITCODE -ne 0) { Die "前端构建失败。" }
    } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $distPath "index.html"))) {
    Die "未找到 frontend\dist\index.html（请先 npm run build，或加 -WithFrontend）"
}

# ---------- 清理旧暂存 ----------
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
if (Test-Path $ZipFile) { Remove-Item -Force $ZipFile }

# ---------- 收集文件 ----------
Write-Step "收集文件"
$backendDst = New-Item -ItemType Directory -Path (Join-Path $Stage "backend") -Force
# 复制 app 目录并排除缓存与 venv
$appSrc = Join-Path $RepoRoot "backend\app"
Copy-Item -Recurse -Force $appSrc (Join-Path $Stage "backend")
Get-ChildItem -Recurse -Force (Join-Path $Stage "backend") -Include "__pycache__", "*.pyc" -Directory |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item -Force (Join-Path $RepoRoot "backend\requirements.txt") (Join-Path $Stage "backend\requirements.txt")

New-Item -ItemType Directory -Path (Join-Path $Stage "frontend") -Force | Out-Null
Copy-Item -Recurse -Force $distPath (Join-Path $Stage "frontend\dist")

# 配置：跳过本机密钥层
New-Item -ItemType Directory -Path (Join-Path $Stage "config") -Force | Out-Null
Get-ChildItem -Recurse -File (Join-Path $RepoRoot "config") | Where-Object { $_.Name -notlike "*.local.yaml" } | ForEach-Object {
    $rel = $_.FullName.Substring((Join-Path $RepoRoot "config").Length).TrimStart('\')
    $dst = Join-Path (Join-Path $Stage "config") $rel
    New-Item -ItemType Directory -Path (Split-Path $dst -Parent) -Force | Out-Null
    Copy-Item -Force $_.FullName $dst
}

# 部署模板
$deployDst = Join-Path $Stage "deploy"
New-Item -ItemType Directory -Path $deployDst -Force | Out-Null
foreach ($item in @("nginx.conf", "docker-compose.yml", ".env.example", "gateway.env.example")) {
    $src = Join-Path $RepoRoot "deploy\$item"
    if (Test-Path $src) { Copy-Item -Force $src $deployDst }
}
foreach ($sub in @("systemd", "docker", "windows", "wsl2")) {
    $src = Join-Path $RepoRoot "deploy\$sub"
    if (Test-Path $src) { Copy-Item -Recurse -Force $src (Join-Path $deployDst $sub) }
}

# 随包分发安装脚本 + 说明
Copy-Item -Force (Join-Path $RepoRoot "deploy\windows\install.ps1") (Join-Path $Stage "install.ps1")
foreach ($doc in @("README.md", "INSTALL.md", "CHANGELOG.md")) {
    $src = Join-Path $RepoRoot $doc
    if (Test-Path $src) { Copy-Item -Force $src (Join-Path $Stage $doc) }
}
Write-Ok "文件收集完成"

# ---------- 压缩 ----------
Write-Step "生成 zip"
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $ZipFile -CompressionLevel Optimal
$sizeMB = [math]::Round((Get-Item $ZipFile).Length / 1MB, 2)

# ---------- 校验和 ----------
$hash = (Get-FileHash -Path $ZipFile -Algorithm SHA256).Hash.ToLower()
$shaPath = "$ZipFile.sha256"
"$hash  $(Split-Path $ZipFile -Leaf)" | Set-Content -Path $shaPath -Encoding ASCII

Write-Host ""
Write-Host "======================================================" -ForegroundColor Green
Write-Host " Windows 离线包构建完成" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Green
Write-Host " 文件    : $ZipFile ($sizeMB MB)"
Write-Host " 校验    : $shaPath"
Write-Host " 安装    : 解压后执行 powershell -ExecutionPolicy Bypass -File .\install.ps1 -RegisterService"
Write-Host " 分发    : 上传至 GitHub Releases 后即可被 winget / scoop 引用"
Write-Host "======================================================" -ForegroundColor Green
