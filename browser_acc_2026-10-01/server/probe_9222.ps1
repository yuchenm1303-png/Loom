$p = Get-Process -Id 1736 -ErrorAction SilentlyContinue
if ($p) {
    Write-Host "PID=$($p.Id) Name=$($p.ProcessName)"
    $cl = (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)" -EA SilentlyContinue).CommandLine
    if ($cl) { Write-Host "CMD=$cl" }
}
Write-Host "DONE"