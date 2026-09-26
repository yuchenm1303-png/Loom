# Hunt for any leftover copies of the OpenAI/Claude session data.
$hints = @('OpenAI','Claude','Codex')
$roots = @('C:\Users','C:\ProgramData','C:\Windows\Temp')
$rows = @()
foreach($root in $roots){
  if(-not (Test-Path $root)){ continue }
  Get-ChildItem -LiteralPath $root -Recurse -Directory -Force -ErrorAction SilentlyContinue |
    Where-Object { $n = $_.Name; $hints | Where-Object { $n -like "*$_*" } | Select-Object -First 1 } |
    ForEach-Object {
      $size = 0
      try{ $size=(Get-ChildItem -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object { -not $_.PSIsContainer } | Measure-Object -Property Length -Sum).Sum }catch{}
      $rows += [pscustomobject]@{Path=$_.FullName;SizeMB=[math]::Round($size/1MB,1);LastWrite=$_.LastWriteTime}
    }
}
$rows | Sort-Object LastWrite -Descending | Format-Table -AutoSize