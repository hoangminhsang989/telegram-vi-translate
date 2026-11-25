param(
    [switch]$Rebuild,
    [switch]$NoShortcut,
    [switch]$Autostart
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$TgDir = Join-Path $env:APPDATA "Telegram Desktop"
$TgExe = Join-Path $TgDir "Telegram.exe"
$TdataDir = Join-Path $TgDir "tdata"
$Manifest = Join-Path $RepoRoot "shim_mscms\Cargo.toml"
$LauncherExe = Join-Path $RepoRoot "shim_mscms\target\release\vi_launcher.exe"
$OldShimDll = Join-Path $TgDir "mscms.dll"
$DefaultCfg = Join-Path $RepoRoot "translator_proxy\config.default.json"
$UserCfg = Join-Path $RepoRoot "translator_proxy\config.json"

$needsBuild = $Rebuild -or -not (Test-Path $LauncherExe)
if (-not $needsBuild) {
    $exeTime = (Get-Item $LauncherExe).LastWriteTimeUtc
    $newerSrc = Get-ChildItem -Path (Join-Path $RepoRoot "shim_mscms") -Recurse -File |
        Where-Object { $_.Extension -in ".rs", ".toml" -and $_.LastWriteTimeUtc -gt $exeTime } |
        Select-Object -First 1
    if ($newerSrc) { $needsBuild = $true }
}

if ($needsBuild) {
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

if (-not $NoShortcut) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnkPaths = @(Join-Path $desktop "Telegram (VI).lnk")
    $startMenuTg = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Telegram Desktop"
    if (Test-Path $startMenuTg) {
        $lnkPaths += Join-Path $startMenuTg "Telegram (VI).lnk"
    }

    $wsh = New-Object -ComObject WScript.Shell
    foreach ($lnkPath in $lnkPaths) {
        $sc = $wsh.CreateShortcut($lnkPath)
        $sc.TargetPath = $LauncherExe
        $sc.Arguments = "--restart-if-unpatched"
        $sc.WorkingDirectory = $RepoRoot
        $sc.Description = "Mo Telegram Desktop kem tu dong dich tieng Viet"
        if (Test-Path $TgExe) {
            $sc.IconLocation = "$TgExe, 0"
        }
        $sc.Save()
        Write-Host "[install] Da tao shortcut: $lnkPath"
    }
}

if ($Autostart) {
    $runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    $cmd = "`"$LauncherExe`" --restart-if-unpatched -startintray"
    Set-ItemProperty -Path $runKey -Name "TelegramViTranslate" -Value $cmd
    Write-Host "[install] Da bat tu dong khoi dong cung Windows (TelegramViTranslate)"
}

Write-Host "[install] Hoan tat. Mo Telegram bang shortcut 'Telegram (VI)' tren Desktop hoac chay .\scripts\start.ps1 -RestartTelegram."
