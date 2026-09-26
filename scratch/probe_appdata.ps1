$local = Join-Path $env:USERPROFILE 'AppData\Local'
$roam  = Join-Path $env:USERPROFILE 'AppData\Roaming'

$rows = @()

function Probe($root, $depth){
  if(-not (Test-Path $root)){ return }
  Get-ChildItem -LiteralPath $root -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
    $name = $_.Name.ToLower()
    $match = ''
    foreach($kw in @('claude','openai','codex','anthropic','chatgpt')){
      if($name -like "*$kw*"){ $match = $kw; break }
    }
    if($match){
      $size = 0
      try{
        $size = (Get-ChildItem -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue |
                 Where-Object { -not $_.PSIsContainer } |
                 Measure-Object -Property Length -Sum).Sum
      }catch{}
      $script:rows += [pscustomobject]@{
        Path    = $_.FullName
        Match   = $match
        SizeMB  = [math]::Round($size/1MB,1)
        MTime   = $_.LastWriteTime
      }
    }
  }
}

Probe $local 2
Probe $roam  2

$rows | Sort-Object MTime -Descending | Format-Table -AutoSize -Wrap