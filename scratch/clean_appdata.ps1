$local=Join-Path $env:USERPROFILE 'AppData\Local'
function Clean-SubDir($parent,$child){
  $p=Join-Path $parent $child
  if(-not (Test-Path $p)){ Write-Host ("Skip (missing): " + $p); return }
  $before=0
  try{
    $before=(Get-ChildItem -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue |
             Where-Object { -not $_.PSIsContainer } |
             Measure-Object -Property Length -Sum).Sum
  }catch{}
  Get-ChildItem -LiteralPath $p -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  [pscustomobject]@{Target=$child;BeforeGB=[math]::Round($before/1GB,2)}
}
$rows=@()
$rows+= Clean-SubDir $local 'Packages'
$rows+= Clean-SubDir $local 'Microsoft'
$rows+= Clean-SubDir $local 'Kingsoft'
$rows+= Clean-SubDir $local 'OpenAI'
$rows+= Clean-SubDir $local 'Google'
foreach($u in @('@genieworkbuddy-desktop-updater','@phoenixdesktop-updater','@loomdesktop-react-updater','ingredient-date-generator-updater')){
  $rows+= Clean-SubDir $local $u
}
$rows | Format-Table -AutoSize
Write-Host '--- After cleanup ---'
Get-PSDrive C | Select-Object @{n='FreeGB';e={[math]::Round($_.Free/1GB,2)}},@{n='UsedGB';e={[math]::Round($_.Used/1GB,2)}},@{n='FreePercent';e={[math]::Round(($_.Free/($_.Used+$_.Free))*100,1)}} | Format-List