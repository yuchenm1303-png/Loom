$ports = 9222,9229,18800,18801,18802,20000,30000,33333,39223,49000,50000
foreach ($p in $ports) {
    $conn = Test-NetConnection -ComputerName 127.0.0.1 -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue
    if ($conn) { Write-Host "PORT $p OPEN" }
}
Write-Host "DONE"