param([Parameter(Mandatory = $true)][string] $InstallDir)

$ErrorActionPreference = "Stop"
$runtime = Join-Path $InstallDir "resources\python.exe"
$exe = Join-Path $InstallDir "Loom.exe"
if (-not (Test-Path $runtime)) { throw "Bundled 32-bit Python Agent is missing" }
if (-not (Test-Path $exe)) { throw "32-bit Electron desktop exe is missing" }

function Get-PeMachine([string] $path) {
  $stream = [IO.File]::OpenRead($path)
  $reader = [IO.BinaryReader]::new($stream)
  try {
    $stream.Position = 0x3c
    $offset = $reader.ReadInt32()
    $stream.Position = $offset
    if ($reader.ReadUInt32() -ne 0x4550) { throw "Invalid PE signature in $path" }
    return $reader.ReadUInt16()
  } finally { $reader.Dispose(); $stream.Dispose() }
}
foreach ($file in @($exe, $runtime)) {
  $machine = Get-PeMachine $file
  if ($machine -ne 0x014c) { throw "$file is not x86: PE machine 0x$($machine.ToString('x4'))" }
  Write-Host "Verified native x86 binary: $file"
}
# Playwright may install an x64 Node driver even into a Python x86 wheel.
# Verify the exact executable bundled in the final installer; imports alone
# do not prove the browser driver can start on a 32-bit Windows OS.
$driver = Join-Path $InstallDir "resources\\_internal\\playwright\\driver\\node.exe"
if (-not (Test-Path $driver)) { throw "Packaged x86 Playwright Node driver is missing" }
if ((Get-PeMachine $driver) -ne 0x014c) { throw "Packaged Playwright Node driver is not x86" }
& $driver --version
if ($LASTEXITCODE -ne 0) { throw "Packaged x86 Playwright Node driver will not run" }
Write-Host "Verified native x86 Playwright browser driver: $driver"
& $runtime -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); assert p.chromium.name == 'chromium'; p.stop(); print('loom-packaged-x86-playwright-driver-ok')"
if ($LASTEXITCODE -ne 0) { throw "Packaged Playwright x86 driver could not initialize" }
& $runtime -c "from app.agent_runtime.computer_windows import windows_computer_available; assert windows_computer_available(); print('loom-packaged-x86-computer-import-ok')"
if ($LASTEXITCODE -ne 0) { throw "Packaged Windows x86 Computer Use prerequisites unavailable" }
# A PTY request must not fail to launch ordinary Windows shells merely
# because winpty has no x86 native wheel. Validate actual process output.
& $runtime -c "from app.agent_runtime.process_runtime import _spawn_backend; from pathlib import Path; import os; b=_spawn_backend(argv=('cmd.exe','/d','/c','echo loom-win32-terminal-ok'),cwd=Path.cwd(),env=os.environ,pty=True,rows=24,cols=80); text=b.read_stdout(); assert b.name == 'windows-x86-pipe-terminal' and 'loom-win32-terminal-ok' in text, (b.name,text); b.close(); print('loom-packaged-x86-terminal-ok')"
if ($LASTEXITCODE -ne 0) { throw "Packaged Windows x86 interactive pipe terminal failed" }
# If this runner has Microsoft Edge, verify a real browser session. Missing
# Edge is a runtime prerequisite on user machines, not a reason to download
# an x64 Chromium build into the 32-bit installation.
$browserExe = @(
  "${env:ProgramFiles(x86)}\\Microsoft\\Edge\\Application\\msedge.exe",
  "$env:ProgramFiles\\Microsoft\\Edge\\Application\\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($browserExe) {
  & $runtime -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(channel='msedge', headless=True); page=b.new_page(); page.goto('data:text/html,<title>loom-win32</title>'); assert page.title() == 'loom-win32'; b.close(); p.stop(); print('loom-packaged-x86-browser-launch-ok')"
  if ($LASTEXITCODE -ne 0) { throw "Packaged x86 browser driver could not launch installed Microsoft Edge" }
} else {
  Write-Host "Microsoft Edge not installed on runner; browser launch is a separate hardware acceptance item."
}
$manifest = Get-Content (Join-Path $InstallDir "resources\manifest.json") -Raw | ConvertFrom-Json
if ($manifest.arch -ne "ia32" -or $manifest.platform -ne "win32") {
  throw "Wrong packaged Host architecture: $($manifest.arch)"
}
& $runtime self-test
if ($LASTEXITCODE -ne 0) { throw "Frozen x86 runtime self test failed" }

# Verify the real Python App Server can initialize and list threads, using
# Loom's existing packaged-runtime gate (from the source directory).
node desktop-react/scripts/verify-packaged-runtime.mjs (Join-Path $InstallDir "resources")
if ($LASTEXITCODE -ne 0) { throw "Actual x86 local App Server startup gate failed" }

# Now launch the same binary users will open: the Host must publish the
# local discovery endpoint without needing a different remote x64 PC.
$originalHome = $env:LOOM_HOME
$originalHostData = $env:LOOM_HOST_DATA_DIR
$probeHome = Join-Path $env:RUNNER_TEMP "loom-win32-local-smoke"
New-Item -ItemType Directory -Force -Path $probeHome | Out-Null
$env:LOOM_HOME = $probeHome
$env:LOOM_HOST_DATA_DIR = (Join-Path $probeHome "host")
$child = $null
$stdoutPath = Join-Path $probeHome "loom-host-stdout.log"
$stderrPath = Join-Path $probeHome "loom-host-stderr.log"
try {
  $env:ELECTRON_ENABLE_LOGGING = "1"
  $child = Start-Process -FilePath $exe -ArgumentList @("--loom-background-host", "--enable-logging") -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
  $verified = $false
  for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    if ($child.HasExited) { break }
    try {
      $status = Invoke-RestMethod -Uri "http://127.0.0.1:39223/loom/status" -TimeoutSec 2
      if ($status.ok -and $status.deviceId -and $status.hostProtocol -ge 1) {
        Write-Host "Live local IA32 Host online: $($status.deviceName), protocol $($status.hostProtocol)"
        $verified = $true
        break
      }
    } catch { }
  }
  if (-not $verified) {
    Write-Host "IA32 Host process exited: $($child.HasExited)"
    if ($child.HasExited) { Write-Host "Exit code: $($child.ExitCode)" }
    foreach ($diagnostic in @($stdoutPath, $stderrPath)) {
      if (Test-Path $diagnostic) {
        Write-Host "=== $diagnostic ==="
        Get-Content $diagnostic -Tail 100
      }
    }
    throw "IA32 Loom Host did not start its localhost discovery endpoint"
  }

  # Host-only success is not enough for a desktop release: the original Loom
  # UI process must launch and attach to its own local Host through IPC.
  $desktopOut = Join-Path $probeHome "loom-desktop-stdout.log"
  $desktopErr = Join-Path $probeHome "loom-desktop-stderr.log"
  $desktop = Start-Process -FilePath $exe -PassThru -RedirectStandardOutput $desktopOut -RedirectStandardError $desktopErr
  try {
    $desktopAttached = $false
    $pidFile = Join-Path $env:LOOM_HOST_DATA_DIR "host-ipc\desktop-pids.json"
    for ($i = 0; $i -lt 40; $i++) {
      Start-Sleep -Seconds 1
      if ($desktop.HasExited) { break }
      if (-not (Test-Path $pidFile)) { continue }
      try {
        $clientPids = @(Get-Content $pidFile -Raw | ConvertFrom-Json)
        if ($clientPids.Count -gt 0) {
          Start-Sleep -Seconds 2
          if (-not $desktop.HasExited) {
            $desktopAttached = $true
            Write-Host "Real IA32 Loom Desktop launched and attached to its own local Host: $($clientPids -join ',')"
            break
          }
        }
      } catch { }
    }
    if (-not $desktopAttached) {
      foreach ($log in @($desktopOut, $desktopErr)) {
        if (Test-Path $log) { Write-Host "=== $log ==="; Get-Content $log -Tail 100 }
      }
      throw "IA32 Loom Desktop did not attach to its own local Host"
    }
  } finally {
    if ($desktop -and -not $desktop.HasExited) { Stop-Process -Id $desktop.Id -Force -ErrorAction SilentlyContinue }
  }
} finally {
  Remove-Item Env:ELECTRON_ENABLE_LOGGING -ErrorAction SilentlyContinue
  if ($child -and -not $child.HasExited) { Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue }
  if ($null -eq $originalHome) { Remove-Item Env:LOOM_HOME -ErrorAction SilentlyContinue }
  else { $env:LOOM_HOME = $originalHome }
  if ($null -eq $originalHostData) { Remove-Item Env:LOOM_HOST_DATA_DIR -ErrorAction SilentlyContinue }
  else { $env:LOOM_HOST_DATA_DIR = $originalHostData }
}
