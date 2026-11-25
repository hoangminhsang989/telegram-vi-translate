param(
    [switch]$RestartTelegram
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$TgDir = Join-Path $env:APPDATA "Telegram Desktop"
$TgExe = Join-Path $TgDir "Telegram.exe"
$LauncherExe = Join-Path $RepoRoot "shim_mscms\target\release\vi_launcher.exe"
$CfgPath = Join-Path $RepoRoot "translator_proxy\config.json"
$LogDir = Join-Path $RepoRoot "logs"
$PidFile = Join-Path $LogDir "proxy.pid"
$ProxyLog = Join-Path $LogDir "proxy.log"
$ShimLog = Join-Path $env:LOCALAPPDATA "TelegramViShim\shim.log"

$HostAddr = "127.0.0.1"
$Port = 18765
if (Test-Path $CfgPath) {
    $cfg = Get-Content $CfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($cfg.host) { $HostAddr = $cfg.host }
    if ($cfg.port) { $Port = [int]$cfg.port }
}

function Test-ProxyHealth {
    try {
        $resp = Invoke-RestMethod "http://${HostAddr}:${Port}/health" -TimeoutSec 2
        return [bool]$resp.ok
    } catch {
        return $false
    }
}

if (-not (Test-ProxyHealth)) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $pyw = Get-Command pythonw -ErrorAction SilentlyContinue
    $pyExe = if ($pyw) { $pyw.Source } else { (Get-Command python).Source }
    $proc = Start-Process -FilePath $pyExe `
        -ArgumentList "-m", "translator_proxy", "--log-file", $ProxyLog `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -PassThru
    $proc.Id | Set-Content -Path $PidFile -Encoding ASCII

    $ready = $false
    for ($i = 0; $i -lt 25; $i++) {
        Start-Sleep -Milliseconds 200
        if (Test-ProxyHealth) { $ready = $true; break }
    }
    if (-not $ready) {
        throw "translator_proxy khong phan hoi tai http://${HostAddr}:${Port}/health (xem $ProxyLog)"
    }
    Write-Host "[start] Proxy dang chay (PID=$($proc.Id)) tai http://${HostAddr}:${Port}"
} else {
    Write-Host "[start] Proxy da san sang tai http://${HostAddr}:${Port}"
}

if (-not (Test-Path $LauncherExe)) {
    throw "Chua tim thay $LauncherExe. Hay chay .\scripts\install.ps1 truoc."
}

$runningTg = Get-Process -Name Telegram -ErrorAction SilentlyContinue
if ($runningTg -and $RestartTelegram) {
    Write-Host "[start] Dang dung Telegram Desktop de khoi chay lai voi patch..."
    $runningTg | Stop-Process -Force
    $runningTg | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 400
    $runningTg = $null
}

if (-not $runningTg) {
    if (-not (Test-Path $TgExe)) { throw "Khong tim thay $TgExe" }
    & $LauncherExe --telegram $TgExe --log-file $ShimLog
    if ($LASTEXITCODE -ne 0) { throw "vi_launcher.exe khoi chay Telegram that bai" }
    Start-Sleep -Seconds 2
    $launched = Get-Process -Name Telegram -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launched -and $launched.MainWindowHandle -eq 0) {
        Start-Process -FilePath $TgExe -ArgumentList "-noupdate" -WorkingDirectory $TgDir
    }
} else {
    $tgPid = ($runningTg | Select-Object -First 1).Id
    $patched = (Test-Path $ShimLog) -and [bool](Select-String -Path $ShimLog -Pattern "patched pid=$tgPid\b" -Quiet)
    if ($patched) {
        Write-Host "[start] Telegram Desktop (PID=$tgPid) da duoc mo khoa dich."
    } else {
        Write-Host "[start] Telegram Desktop (PID=$tgPid) dang chay ban chua patch. Hay chay lai voi -RestartTelegram."
    }
}
