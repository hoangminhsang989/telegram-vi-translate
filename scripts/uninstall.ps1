param(
    [switch]$StopTelegram
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$TgDir = Join-Path $env:APPDATA "Telegram Desktop"
$TdataDir = Join-Path $TgDir "tdata"
$OldShimDll = Join-Path $TgDir "mscms.dll"

& (Join-Path $PSScriptRoot "stop.ps1")

if ($StopTelegram) {
    $tg = Get-Process -Name Telegram -ErrorAction SilentlyContinue
    if ($tg) {
        $tg | Stop-Process -Force
        $tg | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 400
    }
}

if (Test-Path $OldShimDll) {
    Remove-Item $OldShimDll -Force -ErrorAction SilentlyContinue
}

Push-Location $RepoRoot
try {
    python -m scripts.config_telegram --uninstall --tdata $TdataDir
    if ($LASTEXITCODE -ne 0) { throw "Khoi phuc experimental_options.json that bai" }
} finally {
    Pop-Location
}

Write-Host "[uninstall] Da go bo sach se."
