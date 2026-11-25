$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$TgDir = Join-Path $env:APPDATA "Telegram Desktop"
$OptFile = Join-Path $TgDir "tdata\experimental_options.json"
$LauncherExe = Join-Path $RepoRoot "shim_mscms\target\release\vi_launcher.exe"
$ShimLog = Join-Path $env:LOCALAPPDATA "TelegramViShim\shim.log"
$CfgPath = Join-Path $RepoRoot "translator_proxy\config.json"

$HostAddr = "127.0.0.1"
$Port = 18765
if (Test-Path $CfgPath) {
    $cfg = Get-Content $CfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($cfg.host) { $HostAddr = $cfg.host }
    if ($cfg.port) { $Port = [int]$cfg.port }
}

Write-Host "=== Telegram PC Vietnamese Auto-Translate Status ==="
try {
    $health = Invoke-RestMethod "http://${HostAddr}:${Port}/health" -TimeoutSec 2
    Write-Host ("Proxy: OK (provider={0}, target={1}, cached={2})" -f $health.provider, $health.target_lang, $health.cached)
} catch {
    Write-Host "Proxy: OFFLINE"
}

Write-Host ("Launcher built: {0} ({1})" -f (Test-Path $LauncherExe), $LauncherExe)
Write-Host ("Experimental options exists: {0} ({1})" -f (Test-Path $OptFile), $OptFile)

$tg = Get-Process -Name Telegram -ErrorAction SilentlyContinue | Select-Object -First 1
if ($tg) {
    $patched = (Test-Path $ShimLog) -and [bool](Select-String -Path $ShimLog -Pattern "patched pid=$($tg.Id)\b" -Quiet)
    Write-Host ("Telegram.exe: RUNNING (PID={0}, patched={1})" -f $tg.Id, $patched)
} else {
    Write-Host "Telegram.exe: NOT RUNNING"
}

if (Test-Path $ShimLog) {
    Write-Host "--- shim.log (last 5 lines) ---"
    Get-Content $ShimLog -Tail 5
}
