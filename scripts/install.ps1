# Aurora Windows installer.
# Usage:
#   irm https://github.com/plxlaaa/aurora/releases/latest/download/install.ps1 | iex
#   powershell -ExecutionPolicy Bypass -File install.ps1

param(
    [string]$Repo = "plxlaaa/aurora",
    [string]$Version = "latest",
    [string]$InstallDir = "",
    [switch]$NoPath
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Get-Arch {
    if ([Environment]::Is64BitOperatingSystem) {
        $envArch = $env:PROCESSOR_ARCHITECTURE
        if ($envArch -eq "ARM64") { return "arm64" }
        return "amd64"
    }
    throw "Aurora requires 64-bit Windows"
}

function Get-InstallDir {
    if ($InstallDir) { return $InstallDir }
    $local = $env:LOCALAPPDATA
    if (-not $local) {
        $local = Join-Path $env:USERPROFILE "AppData\Local"
    }
    return Join-Path $local "Programs\Aurora"
}

$mirrors = @(
    @{ Name = "github"; Wrap = { param($u) $u } },
    @{ Name = "ghproxy_net"; Wrap = { param($u) "https://ghproxy.net/$u" } },
    @{ Name = "ghproxy"; Wrap = { param($u) "https://mirror.ghproxy.com/$u" } },
    @{ Name = "gitdl"; Wrap = { param($u) "https://gitdl.cn/$u" } },
    @{ Name = "gh_ddlc"; Wrap = { param($u) "https://gh.ddlc.top/$u" } }
)

function Save-Url {
    param([string]$Url, [string]$OutFile, [int]$TimeoutSec = 25)
    $ok = $false
    try {
        $client = New-Object System.Net.Http.HttpClient
        $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSec)
        $bytes = $client.GetByteArrayAsync($Url).GetAwaiter().GetResult()
        [IO.File]::WriteAllBytes($OutFile, $bytes)
        $ok = $true
    } catch { }
    if (-not $ok) {
        $wc = New-Object System.Net.WebClient
        $wc.Headers["User-Agent"] = "aurora-installer"
        $wc.DownloadFile($Url, $OutFile)
    }
    if (-not (Test-Path $OutFile) -or ((Get-Item $OutFile).Length -lt 100)) {
        throw "download too small: $Url"
    }
}

function Invoke-RaceDownload {
    param(
        [string[]]$Urls,
        [string]$OutFile,
        [int]$TimeoutSec = 25
    )
    $jobs = @()
    $tmpRoot = Join-Path $env:TEMP ("aurora-dl-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmpRoot | Out-Null
    try {
        $i = 0
        foreach ($url in $Urls) {
            $i++
            $dest = Join-Path $tmpRoot ("part-" + $i)
            $jobs += Start-Job -ScriptBlock {
                param($u, $d, $t)
                $ErrorActionPreference = "Stop"
                try {
                    $client = New-Object System.Net.Http.HttpClient
                    $client.Timeout = [TimeSpan]::FromSeconds($t)
                    $bytes = $client.GetByteArrayAsync($u).GetAwaiter().GetResult()
                    [IO.File]::WriteAllBytes($d, $bytes)
                } catch {
                    $wc = New-Object System.Net.WebClient
                    $wc.Headers["User-Agent"] = "aurora-installer"
                    $wc.DownloadFile($u, $d)
                }
                if (-not (Test-Path $d) -or ((Get-Item $d).Length -lt 100)) { throw "too small" }
                return $d
            } -ArgumentList $url, $dest, $TimeoutSec
        }
        $winner = $null
        $deadline = (Get-Date).AddSeconds($TimeoutSec)
        while ($jobs.Count -gt 0 -and -not $winner -and (Get-Date) -lt $deadline) {
            $done = Wait-Job -Job $jobs -Any -Timeout 2
            if (-not $done) { continue }
            foreach ($j in @($done)) {
                if ($j.State -eq "Completed") {
                    try {
                        $path = Receive-Job $j -ErrorAction Stop
                        if ($path -and (Test-Path $path)) {
                            $winner = $path
                        }
                    } catch { }
                }
                $jobs = @($jobs | Where-Object { $_.Id -ne $j.Id })
                Remove-Job $j -Force -ErrorAction SilentlyContinue
            }
        }
        $jobs | ForEach-Object { Stop-Job $_ -ErrorAction SilentlyContinue; Remove-Job $_ -Force -ErrorAction SilentlyContinue }
        if (-not $winner) {
            foreach ($url in $Urls) {
                try {
                    Write-Host "  fallback $url"
                    Save-Url -Url $url -OutFile $OutFile -TimeoutSec $TimeoutSec
                    return
                } catch {
                    Write-Host "  fail $($_.Exception.Message)"
                }
            }
            throw "all download channels failed"
        }
        Copy-Item -Force $winner $OutFile
    } finally {
        Remove-Item -Recurse -Force $tmpRoot -ErrorAction SilentlyContinue
    }
}

$arch = Get-Arch
$asset = "aurora-windows-$arch.zip"
$dir = Get-InstallDir
New-Item -ItemType Directory -Force -Path $dir | Out-Null

Write-Host "Aurora installer"
Write-Host "  repo    $Repo"
Write-Host "  asset   $asset"
Write-Host "  dest    $dir"

if ($Version -eq "latest") {
    $tagPath = "latest/download"
} else {
    $v = $Version.TrimStart("v")
    $tagPath = "download/v$v"
}
$raw = "https://github.com/$Repo/releases/$tagPath/$asset"
$urls = @()
foreach ($m in $mirrors) {
    $urls += & $m.Wrap $raw
}

$zip = Join-Path $env:TEMP $asset
Write-Host "Downloading via racing mirrors..."
Invoke-RaceDownload -Urls $urls -OutFile $zip

$extract = Join-Path $env:TEMP ("aurora-extract-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $extract | Out-Null
Expand-Archive -Force -Path $zip -DestinationPath $extract

$exeSrc = Get-ChildItem -Path $extract -Recurse -Filter "aurora.exe" | Select-Object -First 1
if (-not $exeSrc) { throw "zip does not contain aurora.exe" }

$exeDst = Join-Path $dir "aurora.exe"
Copy-Item -Force $exeSrc.FullName $exeDst
Remove-Item -Force $zip -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $extract -ErrorAction SilentlyContinue

if (-not $NoPath) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$dir*") {
        [Environment]::SetEnvironmentVariable("Path", "$userPath;$dir", "User")
        $env:Path = "$env:Path;$dir"
        Write-Host "Added $dir to user PATH"
    }
}

Write-Host ""
Write-Host "Installed $exeDst"
Write-Host "Open a new terminal and run: aurora"
Write-Host "Set AURORA_API_KEY before chatting."
