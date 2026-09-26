$userProfile=$env:USERPROFILE
$targets=@('Desktop','Documents','Downloads','Videos','Music','Pictures','AppData\Local','AppData\Roaming')
$rows=@()
foreach($t in $targets){
  $p=Join-Path $userProfile $t
  if(Test-Path $p){
    $s=0
    try{
      $s=(Get-ChildItem -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue |
          Where-Object { -not $_.PSIsContainer } |
          Measure-Object -Property Length -Sum).Sum
    }catch{$s=0}
    $rows+=[pscustomobject]@{Path=$p;SizeGB=[math]::Round($s/1GB,2)}
  }
}
$rows | Format-Table -AutoSize