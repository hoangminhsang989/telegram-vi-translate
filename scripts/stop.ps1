$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $RepoRoot "logs\proxy.pid"

if (Test-Path $PidFile) {
    $pidText = (Get-Content $PidFile -Raw).Trim()
    if ($pidText -match '^\d+$') {
        $targetPid = [int]$pidText
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $targetPid" -ErrorAction SilentlyContinue
        if ($cim -and $cim.CommandLine -match 'translator_proxy') {
            Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
            Write-Host "[stop] Da dung translator_proxy (PID=$targetPid)."
        } else {
            Write-Host "[stop] PID=$targetPid khong con la translator_proxy."
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "[stop] Khong tim thay logs/proxy.pid."
}
