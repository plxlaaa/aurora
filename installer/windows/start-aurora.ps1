# 启动 Aurora 后端并记录 PID（供 aurora-stop.bat / 卸载程序精确终止）
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

$p = Start-Process -FilePath (Join-Path $root "runtime\python.exe") `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8080") `
    -WorkingDirectory (Join-Path $root "backend") `
    -WindowStyle Minimized -PassThru

if (-not $p) { throw "Start-Process 失败" }
Set-Content -Path (Join-Path $root "data\aurora.pid") -Value $p.Id -Encoding ascii
Write-Host "Aurora 后端已启动 (PID $($p.Id))"
