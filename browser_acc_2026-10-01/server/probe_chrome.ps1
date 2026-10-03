$ErrorActionPreference = 'SilentlyContinue'
$procs = Get-Process -Name chrome
foreach ($p in $procs) {
    $cl = (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)").CommandLine
    if ($cl) {
        $short = $cl.Substring(0, [Math]::Min(300, $cl.Length))
        Write-Host ("PID=$($p.Id) CMD=$short")
    }
}