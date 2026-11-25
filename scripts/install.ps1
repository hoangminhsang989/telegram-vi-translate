param(
    [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$TgDir = Join-Path $env:APPDATA "Telegram Desktop"
$TdataDir = Join-Path $TgDir "tdata"
$Manifest = Join-Path $RepoRoot "shim_mscms\Cargo.toml"
$LauncherExe = Join-Path $RepoRoot "shim_mscms\target\release\vi_launcher.exe"
$OldShimDll = Join-Path $TgDir "mscms.dll"
$DefaultCfg = Join-Path $RepoRoot "translator_proxy\config.default.json"
$UserCfg = Join-Path $RepoRoot "translator_proxy\config.json"

if ($Rebuild -or -not (Test-Path $LauncherExe)) {
    Write-Host "[install] Dang bien dich vi_launcher.exe (release)..."
    cargo build --release --manifest-path $Manifest
    if ($LASTEXITCODE -ne 0) { throw "Build vi_launcher.exe that bai" }
}

if (-not (Test-Path $TgDir)) {
    throw "Khong tim thay thu muc Telegram Desktop tai $TgDir"
}

if (Test-Path $OldShimDll) {
    Remove-Item $OldShimDll -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path $UserCfg) -and (Test-Path $DefaultCfg)) {
    Copy-Item $DefaultCfg $UserCfg
    Write-Host "[install] Da tao config mac dinh tai $UserCfg"
}

Push-Location $RepoRoot
try {
    python -m scripts.config_telegram --install --tdata $TdataDir
    if ($LASTEXITCODE -ne 0) { throw "Cap nhat experimental_options.json that bai" }
} finally {
    Pop-Location
}

Write-Host "[install] Hoan tat. Chay .\scripts\start.ps1 -RestartTelegram de mo Telegram voi tinh nang dich tieng Viet."
