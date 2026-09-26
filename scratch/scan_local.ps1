$local=Join-Path $env:USERPROFILE 'AppData\Local'
$rows=@()
Get-ChildItem -LiteralPath $local -Directory -ErrorAction SilentlyContinue | ForEach-Object {
  $size=0
  try{
    $size=(Get-ChildItem -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue |
           Where-Object { -not $_.PSIsContainer } |
           Measure-Object -Property Length -Sum).Sum
  }catch{$size=0}
  $rows+=[pscustomobject]@{Name=$_.Name;SizeGB=[math]::Round($size/1GB,2)}
}
$rows | Sort-Object SizeGB -Descending | Select-Object -First 15 | Format-Table -AutoSize