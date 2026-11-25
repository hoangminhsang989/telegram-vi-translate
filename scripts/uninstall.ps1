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

$shortcuts = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Telegram (VI).lnk"),
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Telegram Desktop\Telegram (VI).lnk")
)
foreach ($sc in $shortcuts) {
    if (Test-Path $sc) {
        Remove-Item $sc -Force -ErrorAction SilentlyContinue
        Write-Host "[uninstall] Da xoa shortcut: $sc"
    }
}

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (Get-ItemProperty -Path $runKey -Name "TelegramViTranslate" -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path $runKey -Name "TelegramViTranslate" -ErrorAction SilentlyContinue
    Write-Host "[uninstall] Da tat tu dong khoi dong TelegramViTranslate"
}

Push-Location $RepoRoot
try {
    python -m scripts.config_telegram --uninstall --tdata $TdataDir
    if ($LASTEXITCODE -ne 0) { throw "Khoi phuc experimental_options.json that bai" }
} finally {
    Pop-Location
}

Write-Host "[uninstall] Da go bo sach se."
