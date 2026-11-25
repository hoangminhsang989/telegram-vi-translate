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
    $cfg = [IO.File]::ReadAllText($CfgPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
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
    $launchedPid = $null
    try {
        $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
        $cmdLine = "`"$pyExe`" -m translator_proxy --log-file `"$ProxyLog`""
        $res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
            CommandLine = $cmdLine
            CurrentDirectory = $RepoRoot
            ProcessStartupInformation = $startup
        }
        if ($res.ReturnValue -eq 0 -and $res.ProcessId) {
            $launchedPid = [int]$res.ProcessId
        }
    } catch {
        $launchedPid = $null
    }
    if (-not $launchedPid) {
        $proc = Start-Process -FilePath $pyExe `
            -ArgumentList "-m", "translator_proxy", "--log-file", $ProxyLog `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden `
            -PassThru
        $launchedPid = $proc.Id
    }
    $launchedPid | Set-Content -Path $PidFile -Encoding ASCII

    $ready = $false
    for ($i = 0; $i -lt 25; $i++) {
        Start-Sleep -Milliseconds 200
        if (Test-ProxyHealth) { $ready = $true; break }
    }
    if (-not $ready) {
        throw "translator_proxy khong phan hoi tai http://${HostAddr}:${Port}/health (xem $ProxyLog)"
    }
    Write-Host "[start] Proxy dang chay (PID=$launchedPid) tai http://${HostAddr}:${Port}"
} else {
    Write-Host "[start] Proxy da san sang tai http://${HostAddr}:${Port} (Dashboard: http://${HostAddr}:${Port}/)"
}

if (-not (Test-Path $LauncherExe)) {
    throw "Chua tim thay $LauncherExe. Hay chay .\scripts\install.ps1 truoc."
}
if (-not (Test-Path $TgExe)) {
    throw "Khong tim thay $TgExe"
}

$rawCheck = (& $LauncherExe --check --telegram $TgExe --no-proxy | Out-String).Trim()
$checkJson = $rawCheck | ConvertFrom-Json
if ($checkJson.running -and $RestartTelegram) {
    Write-Host "[start] Dang khoi dong lai Telegram Desktop voi patch..."
    & $LauncherExe --telegram $TgExe --log-file $ShimLog --repo-root $RepoRoot --restart
    if ($LASTEXITCODE -ne 0) { throw "vi_launcher.exe khoi chay Telegram that bai" }
} elseif (-not $checkJson.running) {
    & $LauncherExe --telegram $TgExe --log-file $ShimLog --repo-root $RepoRoot
    if ($LASTEXITCODE -ne 0) { throw "vi_launcher.exe khoi chay Telegram that bai" }
} elseif ($checkJson.patched) {
    Write-Host "[start] Telegram Desktop (PID=$($checkJson.pids -join ',')) da duoc mo khoa dich."
} else {
    Write-Host "[start] Telegram Desktop (PID=$($checkJson.pids -join ',')) dang chay ban chua patch. Hay chay lai voi -RestartTelegram."
}
