$dirs=@('Program Files','Program Files (x86)','ProgramData','Users','Windows','PerfLogs','Recovery')
$rows=@()
foreach($d in $dirs){
  $p=Join-Path 'C:\' $d
  if(Test-Path $p){
    $s=0
    try{$s=(Get-ChildItem -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue | Where-Object { -not $_.PSIsContainer } | Measure-Object -Property Length -Sum).Sum}catch{$s=0}
    $rows+=[pscustomobject]@{Dir=$d;SizeGB=[math]::Round($s/1GB,2)}
  }
}
$rows | Format-Table -AutoSize
