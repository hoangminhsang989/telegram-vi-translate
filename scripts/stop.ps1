$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $RepoRoot "logs\proxy.pid"
$stoppedAny = $false

if (Test-Path $PidFile) {
    $pidText = (Get-Content $PidFile -Raw).Trim()
    if ($pidText -match '^\d+$') {
        $targetPid = [int]$pidText
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $targetPid" -ErrorAction SilentlyContinue
        if ($cim -and $cim.CommandLine -match 'translator_proxy') {
            Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
            Write-Host "[stop] Da dung translator_proxy (PID=$targetPid)."
            $stoppedAny = $true
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$extraProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'translator_proxy' }
foreach ($p in $extraProcs) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "[stop] Da dung translator_proxy (PID=$($p.ProcessId))."
    $stoppedAny = $true
}

if (-not $stoppedAny) {
    Write-Host "[stop] translator_proxy khong chay."
}
