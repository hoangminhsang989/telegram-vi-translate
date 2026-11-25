$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$TgDir = Join-Path $env:APPDATA "Telegram Desktop"
$TgExe = Join-Path $TgDir "Telegram.exe"
$OptFile = Join-Path $TgDir "tdata\experimental_options.json"
$LauncherExe = Join-Path $RepoRoot "shim_mscms\target\release\vi_launcher.exe"
$ShimLog = Join-Path $env:LOCALAPPDATA "TelegramViShim\shim.log"
$ProxyLog = Join-Path $RepoRoot "logs\proxy.log"
$CfgPath = Join-Path $RepoRoot "translator_proxy\config.json"
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Telegram (VI).lnk"

$HostAddr = "127.0.0.1"
$Port = 18765
if (Test-Path $CfgPath) {
    $cfg = [IO.File]::ReadAllText($CfgPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($cfg.host) { $HostAddr = $cfg.host }
    if ($cfg.port) { $Port = [int]$cfg.port }
}

Write-Host "=== Telegram PC Vietnamese Auto-Translate Status ==="
try {
    $health = Invoke-RestMethod "http://${HostAddr}:${Port}/health" -TimeoutSec 2
    Write-Host ("Proxy: OK (provider={0}, target={1}, cached={2})" -f $health.provider, $health.target_lang, $health.cached)
    Write-Host ("Dashboard: http://{0}:{1}/" -f $HostAddr, $Port)
} catch {
    Write-Host "Proxy: OFFLINE"
}

Write-Host ("Launcher built: {0} ({1})" -f (Test-Path $LauncherExe), $LauncherExe)
Write-Host ("Desktop shortcut: {0} ({1})" -f (Test-Path $DesktopShortcut), $DesktopShortcut)
Write-Host ("Experimental options exists: {0} ({1})" -f (Test-Path $OptFile), $OptFile)

if (Test-Path $LauncherExe) {
    $rawCheck = (& $LauncherExe --check --telegram $TgExe --no-proxy | Out-String).Trim()
    if ($rawCheck -match '^\{') {
        $chk = $rawCheck | ConvertFrom-Json
        if ($chk.running) {
            Write-Host ("Telegram.exe: RUNNING (PID={0}, live_patched={1})" -f ($chk.pids -join ","), $chk.patched)
        } else {
            Write-Host "Telegram.exe: NOT RUNNING"
        }
    } else {
        Write-Host "Telegram.exe: CHECK FAILED ($rawCheck)"
    }
}

if (Test-Path $ShimLog) {
    Write-Host "--- shim.log (last 5 lines) ---"
    Get-Content $ShimLog -Tail 5
}
if (Test-Path $ProxyLog) {
    Write-Host "--- proxy.log (last 5 lines) ---"
    Get-Content $ProxyLog -Tail 5
}
