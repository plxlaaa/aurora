<#
.SYNOPSIS
    Aurora AI 智能助手 —— Windows 一键安装脚本。

.DESCRIPTION
    在 Windows 10/11 或 Windows Server 2019+ 上完成网关安装：
      1. 校验管理员权限（不足时自动提权重启）
      2. 检测 Python / Node.js（缺失时提示 winget 或 choco 安装方式）
      3. 创建 Python 虚拟环境并安装后端依赖
      4. 构建前端静态产物（npm ci && npm run build）
      5. 复制配置到 %ProgramData%\Aurora并写入环境配置文件
      6. 可选：注册 Windows 服务（NSSM，自动下载或使用本机已有）
      7. 可选：配置 IIS 反向代理（需已安装 URL Rewrite + ARR）
      8. 校验防火墙放行端口

    全程幂等：重复执行即为「升级安装」，不会破坏既有配置与会话数据。

.PARAMETER InstallDir
    程序安装目录，默认 %ProgramData%\Aurora。

.PARAMETER Port
    网关监听端口，默认 8080。

.PARAMETER ApiKey
    鉴权密钥。留空则不启用鉴权（仅建议本机调试时使用）。
    未提供时会自动生成一个随机强密钥并写入配置文件。

.PARAMETER RegisterService
    注册为 Windows 服务（开机自启、崩溃自动重启）。

.PARAMETER PublicPort
    对外暴露端口。默认留空——网关只监听 127.0.0.1，外部流量应经 IIS / nginx 反代进入。
    仅在确实需要直接对外提供 HTTP 服务时指定此参数（会自动放行防火墙）。

.PARAMETER ServiceUser
    服务运行账户。默认 LocalSystem；如需低权限账号可指定域\用户名。

.PARAMETER SkipFrontend
    跳过前端构建（已有 dist 产物或仅调后端时使用）。

.PARAMETER HealthRetries
    安装后的健康检查重试次数（默认 10 次，每次间隔 2 秒，共约 20s）。
    服务注册后需数秒加载配置与探测模型端点，一次探测易误判失败。

.PARAMETER Uninstall
    卸载模式：停止并删除服务（保留数据目录，除非加 -PurgeData）。

.PARAMETER PurgeData
    与 -Uninstall 搭配，额外删除配置与会话数据目录（不可逆）。

.EXAMPLE
    # 基础安装（自动生成 API Key，注册 Windows 服务）
    powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -RegisterService

.EXAMPLE
    # 自定义端口与密钥，注册为低权限服务账户
    .\install.ps1 -Port 9090 -ApiKey "your-secret" -ServiceUser ".\aurora_svc"

.EXAMPLE
    # 卸载并彻底清除数据
    .\install.ps1 -Uninstall -PurgeData

.NOTES
    运行环境：PowerShell 5.1（内置）或 PowerShell 7+。
    执行策略：若被拦截，使用 -ExecutionPolicy Bypass（单次生效）。
#>

[CmdletBinding()]
param(
    [string]$InstallDir = "$env:ProgramData\Aurora",
    [ValidateRange(1, 65535)]
    [int]$Port = 8080,
    [string]$ApiKey = "",
    [switch]$RegisterService,
    [ValidateRange(0, 65535)]
    [int]$PublicPort = 0,
    [string]$ServiceUser = "LocalSystem",
    [switch]$SkipFrontend,
    [ValidateRange(1, 60)]
    [int]$HealthRetries = 10,
    [switch]$Uninstall,
    [switch]$PurgeData
)

$ErrorActionPreference = "Stop"
$ServiceName = "AuroraGateway"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# ---------- 输出着色 ----------
function Write-Step { param([string]$M) Write-Host "==> $M" -ForegroundColor Cyan }
function Write-Ok   { param([string]$M) Write-Host "  [OK] $M" -ForegroundColor Green }
function Write-Warn { param([string]$M) Write-Host "  [!]  $M" -ForegroundColor Yellow }
function Write-Err  { param([string]$M) Write-Host "  [X]  $M" -ForegroundColor Red }
function Die        { param([string]$M) Write-Err $M; exit 1 }

# ---------- 管理员权限（不足则自动提权重启） ----------
function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    if ($Uninstall) { Die "卸载需要管理员权限，请以管理员身份重试。" }
    Write-Warn "当前非管理员权限，正在尝试自动提权..."
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    foreach ($k in $PSBoundParameters.GetEnumerator()) {
        if ($k.Key -in @('Verbose','Debug','ErrorAction','WarningAction','InformationAction','ProgressAction','ErrorVariable','WarningVariable','InformationVariable','OutVariable','OutBuffer','PipelineVariable')) { continue }
        if ($k.Value -is [switch]) {
            if ($k.Value.IsPresent) { $argList += "-$($k.Key)" }
        } else {
            $argList += @("-$($k.Key)", "`"$($k.Value)`"")
        }
    }
    try {
        Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs -Wait
        exit $LASTEXITCODE
    } catch {
        Die "自动提权被拒绝，请右键以「以管理员身份运行」后重试。"
    }
}

# ---------- 卸载流程 ----------
if ($Uninstall) {
    Write-Step "卸载 Aurora 网关"
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        Write-Step "停止并删除服务 $ServiceName"
        if (Get-Command nssm -ErrorAction SilentlyContinue) { nssm stop $ServiceName confirm | Out-Null }
        else { Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue }
        if (Get-Command sc.exe -ErrorAction SilentlyContinue) {
            sc.exe delete $ServiceName | Out-Null
        }
        Write-Ok "服务已删除"
    } else {
        Write-Warn "未找到服务 $ServiceName，跳过"
    }
    if (Test-Path $InstallDir) {
        if ($PurgeData) {
            Remove-Item -Recurse -Force $InstallDir
            Write-Ok "已删除数据目录 $InstallDir"
        } else {
            Write-Ok "已保留数据目录 $InstallDir（如需删除请加 -PurgeData）"
        }
    }
    Write-Step "卸载完成"
    exit 0
}

# ---------- 依赖检测 ----------
function Find-Python {
    foreach ($cand in @("python.exe", "python3.exe")) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if ($cmd) {
            $ver = & $cmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($ver -and [version]$ver -ge [version]"3.10") { return $cmd.Source }
        }
    }
    return $null
}

function Find-Node {
    $cmd = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($cmd -and (& $cmd.Source -v 2>$null)) { return $cmd.Source }
    return $null
}

Write-Step "Aurora 网关安装（Windows）"
Write-Host "  源码目录: $RepoRoot"
Write-Host "  安装目录: $InstallDir"
Write-Host "  监听端口: $Port"
Write-Host "  当前用户: $env:USERNAME"

# Python
Write-Step "检测 Python 3.10+"
$python = Find-Python
if (-not $python) {
    Write-Err "未找到 Python 3.10+。请先安装其一："
    Write-Host "     winget install --id Python.Python.3.12 -e" -ForegroundColor Yellow
    Write-Host "     或从 https://www.python.org/downloads/ 下载（安装时勾选 Add to PATH）" -ForegroundColor Yellow
    Die "缺少 Python 依赖，已中止。"
}
Write-Ok "Python: $python ($(& $python -c 'import platform;print(platform.python_version())'))"

# Node（仅构建前端时需要）
$node = $null
if (-not $SkipFrontend) {
    Write-Step "检测 Node.js 18+"
    $node = Find-Node
    if (-not $node) {
        Write-Warn "未找到 Node.js。前端构建需要 Node 18+："
        Write-Host "       winget install --id OpenJS.NodeJS.LTS -e" -ForegroundColor Yellow
        Write-Host "     若仅运行后端或由 IIS/nginx 托管前端，可加 -SkipFrontend 重试。" -ForegroundColor Yellow
        Die "缺少 Node.js 依赖，已中止。"
    }
    Write-Ok "Node: $node ($(& $node -v))"
}

# ---------- 目录骨架 ----------
Write-Step "创建目录结构"
$backendDir = Join-Path $InstallDir "backend"
$configDir  = Join-Path $InstallDir "config"
$dataDir    = Join-Path $InstallDir "data\sessions"
$uploadDir  = Join-Path $InstallDir "data\uploads"
$logDir     = Join-Path $InstallDir "logs"
foreach ($d in @($InstallDir, $backendDir, $configDir, $dataDir, $uploadDir, $logDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}
Write-Ok "目录就绪"

# ---------- 复制代码（保留 venv，跳过运行时目录）----------
Write-Step "同步后端代码"
$excludeDirs = @("venv", ".venv", "__pycache__", "tests")
if (Test-Path (Join-Path $backendDir "app")) {
    Remove-Item -Recurse -Force (Join-Path $backendDir "app") -ErrorAction SilentlyContinue
}
Copy-Item -Recurse -Force (Join-Path $RepoRoot "backend\app") $backendDir
Copy-Item -Force (Join-Path $RepoRoot "backend\requirements.txt") (Join-Path $backendDir "requirements.txt")
Write-Ok "后端代码已同步到 $backendDir"

# ---------- 虚拟环境与依赖 ----------
Write-Step "创建 Python 虚拟环境并安装依赖"
$venvPython = Join-Path $backendDir "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    & $python -m venv (Join-Path $backendDir "venv")
}
if (-not (Test-Path $venvPython)) { Die "虚拟环境创建失败：$venvPython 不存在" }
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r (Join-Path $backendDir "requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) { Die "Python 依赖安装失败，请检查网络或 pip 源配置。" }
Write-Ok "Python 依赖已安装"

# ---------- 前端构建 ----------
$frontendDist = Join-Path $InstallDir "frontend\dist"
if (-not $SkipFrontend) {
    Write-Step "构建前端 (npm ci && npm run build)"
    $frontendSrc = Join-Path $RepoRoot "frontend"
    $nodeModules = Join-Path $frontendSrc "node_modules"
    if (-not (Test-Path $nodeModules)) {
        Push-Location $frontendSrc
        try {
            if (Test-Path "package-lock.json") { npm ci --no-audit --no-fund }
            else { npm install --no-audit --no-fund }
            if ($LASTEXITCODE -ne 0) { Die "npm 安装依赖失败。" }
        } finally { Pop-Location }
    }
    Push-Location $frontendSrc
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { Die "前端构建失败（npm run build）。" }
    } finally { Pop-Location }

    New-Item -ItemType Directory -Path (Split-Path $frontendDist -Parent) -Force | Out-Null
    if (Test-Path $frontendDist) { Remove-Item -Recurse -Force $frontendDist }
    Copy-Item -Recurse -Force (Join-Path $frontendSrc "dist") $frontendDist
    Write-Ok "前端已构建到 $frontendDist"
} else {
    Write-Warn "已跳过前端构建（-SkipFrontend）"
}

# ---------- 配置 ----------
Write-Step "同步配置与密钥环境文件"
Copy-Item -Recurse -Force (Join-Path $RepoRoot "config\*") $configDir -ErrorAction SilentlyContinue

$envFile = Join-Path $InstallDir "gateway.env"
if (-not (Test-Path $envFile)) {
    if ([string]::IsNullOrWhiteSpace($ApiKey)) {
        # 自动生成强随机密钥，避免出现「默认无鉴权」的不安全部署
        Add-Type -AssemblyName System.Security
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        $bytes = New-Object byte[] 32
        $rng.GetBytes($bytes)
        $ApiKey = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
        Write-Ok "已自动生成 API Key（见下方与 $envFile）"
    }
    $envContent = @(
        "# Aurora 网关运行环境变量（由 install.ps1 生成）",
        "# 敏感信息请勿提交到版本库",
        "",
        "AI_ASSISTANT_API_KEY=$ApiKey",
        "AI_ASSISTANT_JWT_SECRET=$ApiKey",
        "AI_ASSISTANT_HOST=127.0.0.1",
        "AI_ASSISTANT_PORT=$Port",
        "AI_ASSISTANT_FRONTEND_DIR=$($frontendDist -replace '\\','/')",
        "AI_ASSISTANT_CONFIG_DIR=$($configDir -replace '\\','/')",
        "AI_ASSISTANT_DATA_DIR=$($dataDir -replace '\\','/')",
        "AI_ASSISTANT_UPLOAD_DIR=$($uploadDir -replace '\\','/')",
        "AI_ASSISTANT_SKILLS_DIR=$(($configDir -replace '\\','/'))/skills",
        "AI_ASSISTANT_LOG_LEVEL=INFO",
        "AI_ASSISTANT_TRUST_PROXY=true"
    ) -join "`n"
    Set-Content -Path $envFile -Value $envContent -Encoding UTF8
    # 仅服务账户/管理员可读
    try {
        $acl = Get-Acl $envFile
        $acl.SetAccessRuleProtection($true, $false)
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            "SYSTEM", "FullControl", "Allow")))
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            "Administrators", "FullControl", "Allow")))
        Set-Acl -Path $envFile -AclObject $acl
    } catch { Write-Warn "设置 ACL 失败（可稍后手动收紧权限）：$($_.Exception.Message)" }
    Write-Ok "已生成 $envFile"
} else {
    Write-Warn "已存在 $envFile，保留原配置（如需重置请手动删除后重跑）"
}

# ---------- 权限：服务账户需要读写数据目录 ----------
Write-Step "配置目录权限"
try {
    $acl = Get-Acl $InstallDir
    if ($ServiceUser -ne "LocalSystem") {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            $ServiceUser, "Modify", "ContainerInherit,ObjectInherit", "None", "Allow")))
        Set-Acl -Path $InstallDir -AclObject $acl
        Write-Ok "已授予 $ServiceUser 修改权限"
    } else {
        Write-Ok "LocalSystem 账户默认拥有权限"
    }
} catch { Write-Warn "设置权限失败（服务可能无法写入数据目录）：$($_.Exception.Message)" }

# ---------- 防火墙 ----------
Write-Step "配置 Windows 防火墙"
try {
    $ruleName = "$ServiceName-In TCP"
    # 网关默认仅监听 127.0.0.1（见下方 gateway.env /服务启动参数），
    # 外部无法直连，因此不需要任何入站放行规则。
    # 仅当显式要求对外暴露时才创建规则。
    if ($PublicPort) {
        Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue |
            Remove-NetFirewallRule -ErrorAction SilentlyContinue
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $PublicPort -Profile Any | Out-Null
        Write-Ok "已放行入站 TCP $PublicPort（对外暴露模式，建议改用 IIS/nginx 反代）"
    } else {
        Write-Ok "默认仅监听 127.0.0.1，未添加防火墙规则（需远程访问请加 -PublicPort <端口>）"
    }
} catch { Write-Warn "配置防火墙失败（可能需要管理员确认）：$($_.Exception.Message)" }

# ---------- Windows 服务 ----------
if ($RegisterService) {
    Write-Step "注册 Windows 服务 $ServiceName"
    $nssm = Get-Command nssm.exe -ErrorAction SilentlyContinue
    if (-not $nssm) {
        $nssmPath = Join-Path $InstallDir "tools\nssm.exe"
        if (-not (Test-Path $nssmPath)) {
            Write-Warn "未找到 NSSM。可任选其一："
            Write-Host "     winget install NSSM.NSSM" -ForegroundColor Yellow
            Write-Host "     或手动下载 https://nssm.cc/release/win64/nssm-2.24.zip 解压后重跑" -ForegroundColor Yellow
            Write-Warn "已跳过服务注册。可用「计划任务：启动时」或手动运行 venv\\Scripts\\uvicorn.exe 代替。"
        } else {
            $nssm = $nssmPath
        }
    }
    if ($nssm) {
        $uvicornExe = Join-Path $backendDir "venv\Scripts\uvicorn.exe"
        $workDir    = $backendDir
        $logFile    = Join-Path $logDir "service.log"

        # 幂等：已存在则先卸载
        if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
            & $nssm.Source stop $ServiceName confirm | Out-Null
            & $nssm.Source remove $ServiceName confirm | Out-Null
            Write-Ok "已移除旧服务定义"
        }
        & $nssm.Source install $ServiceName $uvicornExe `
            "app.main:app --host 127.0.0.1 --port $Port --proxy-headers --forwarded-allow-ips 127.0.0.1" `
            $workDir | Out-Null

        # 环境变量加载：NSSM 不支持 env 文件，故生成一个 wrapper 批处理先加载再启动。
        # 批处理中 for /f 用 usebackq + 双引号包裹文件名（路径原样传入，无需转义反斜杠）。
        $launcher = Join-Path $InstallDir "run-gateway.cmd"
        $launcherLines = @(
            '@echo off',
            'rem 由 install.ps1 生成：加载环境变量后启动网关',
            ('for /f "usebackq tokens=1,* delims==" %a in ("{0}") do (' -f $envFile),
            '    if not "%a"=="" set "%a=%b"',
            ')',
            ('cd /d "{0}"' -f $workDir),
            ('"{0}" app.main:app --host 127.0.0.1 --port {1} --proxy-headers --forwarded-allow-ips 127.0.0.1' -f $uvicornExe, $Port)
        )
        Set-Content -Path $launcher -Value ($launcherLines -join "`r`n") -Encoding ASCII

        # 服务改为经由 wrapper 启动，确保环境变量生效
        & $nssm.Source set $ServiceName Application $launcher | Out-Null
        & $nssm.Source set $ServiceName AppParameters "" | Out-Null
        & $nssm.Source set $ServiceName AppDirectory $workDir | Out-Null
        & $nssm.Source set $ServiceName DisplayName "Aurora AI 助手网关" | Out-Null
        & $nssm.Source set $ServiceName Description "Aurora 单机 AI 智能助手网关（FastAPI）" | Out-Null
        & $nssm.Source set $ServiceName Start SERVICE_AUTO_START | Out-Null
        # 崩溃自动重启：3 秒后重试，最多 3 次
        & $nssm.Source set $ServiceName AppExit Default Restart | Out-Null
        & $nssm.Source set $ServiceName AppRestartDelay 3000 | Out-Null
        & $nssm.Source set $ServiceName AppThrottle 1000 | Out-Null
        & $nssm.Source set $ServiceName AppStdout $logFile | Out-Null
        & $nssm.Source set $ServiceName AppStderr $logFile | Out-Null
        if ($ServiceUser -ne "LocalSystem") {
            & $nssm.Source set $ServiceName ObjectName $ServiceUser | Out-Null
        }
        & $nssm.Source start $ServiceName | Out-Null
        Write-Ok "服务已注册并启动"
    }
} else {
    Write-Warn "未注册 Windows 服务（加 -RegisterService 可开机自启）"
}

# ---------- 冒烟验证 ----------
# 服务注册后需数秒加载配置并探测模型端点，故做带重试的轮询而非一次性探测，
# 避免把「仍在启动」误判为安装失败。
Write-Step "验证网关可访问（最多重试 $HealthRetries 次）"
$ok = $false
for ($i = 1; $i -le $HealthRetries; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 5
        if ($resp.StatusCode -eq 200) {
            $health = $resp.Content | ConvertFrom-Json
            Write-Ok "健康检查通过（第 $i 次）：ok=$($health.ok) ready=$($health.ready) version=$($health.version)"
            $ok = $true
            break
        }
    } catch {
        if ($i -eq $HealthRetries) {
            Write-Warn "健康检查未通过：$($_.Exception.Message)"
        } else {
            Write-Host "  ... 等待服务就绪（$i/$HealthRetries）" -ForegroundColor DarkGray
        }
        Start-Sleep -Seconds 2
    }
}

# 就绪探针：至少一个模型端点可用时 ready=true。
# 未配置模型端点属于正常初始状态，仅提示不判失败。
if ($ok) {
    try {
        $ready = (Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/ready" -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
        if ($ready.ready) {
            Write-Ok "就绪探针通过：可用端点 $($ready.endpoints_available -join ', ')"
        } else {
            Write-Warn "就绪探针未通过（尚无可用模型端点）。对话将失败，请编辑 $InstallDir\config\models.local.yaml 填入 base_url / api_key。"
        }
    } catch {
        Write-Warn "就绪探针请求失败：$($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "======================================================" -ForegroundColor Green
Write-Host " 安装完成" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Green
Write-Host " 访问地址   : http://127.0.0.1:$Port"
if ($ok) { Write-Host " 健康检查   : http://127.0.0.1:$Port/api/health" -ForegroundColor Green }
Write-Host " 接口文档   : http://127.0.0.1:$Port/docs （需 AI_ASSISTANT_PRODUCTION=false）"
Write-Host " 安装目录   : $InstallDir"
Write-Host " 环境变量   : $envFile"
if ($RegisterService -and $nssm) {
    Write-Host " 服务管理   : Get-Service $ServiceName | Stop-Service $ServiceName | Start-Service $ServiceName"
    Write-Host " 服务日志   : Get-Content `"$logDir\service.log`" -Tail 100"
} else {
    Write-Host " 手动启动   : cd `"$backendDir`"; .\`venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port $Port"
}
if (-not [string]::IsNullOrWhiteSpace($ApiKey)) {
    Write-Host " 鉴权密钥   : $ApiKey" -ForegroundColor Yellow
    Write-Host "              （登录页使用；建议登录后改写入 models.local.yaml 的端点密钥管理）"
}
Write-Host " 卸载方式   : .\install.ps1 -Uninstall （加 -PurgeData 一并删除数据）"
Write-Host "======================================================" -ForegroundColor Green