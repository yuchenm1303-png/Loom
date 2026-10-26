<#
.SYNOPSIS
One-time administrator preparation for Loom's Windows sandbox.

.DESCRIPTION
Loom runs workspace commands in a Microsoft MXC AppContainer. On Windows builds
without the BaseContainer API (Windows 11 23H2 and GitHub's windows-latest are
two), MXC falls back to the "AppContainer + DACL" tier, and three things stay
out of the sandbox's reach until an administrator opens them:

  1. The NUL device. Its security descriptor names no AppContainer SID, and the
     kernel resets it at every boot, so git dies with
     "could not open '/dev/null'". This script applies MXC's documented
     descriptor now and registers a boot task (SYSTEM) that reapplies it.
  2. Metadata of the system-drive root. Node's realpath fails on "lstat C:\".
     MXC's prepare-system-drive adds the minimal ACE once; it survives reboots.
  3. Tool folders with a protected DACL, such as C:\Program Files\nodejs. MXC
     cannot edit them, so node and npm cannot start. This script grants read and
     execute on them to Loom's own capability SID (loomSandboxTools) only;
     other AppContainer apps gain nothing.

Writes outside the workspace, reads of the user profile (~/.ssh and the like)
and network access stay blocked: none of these changes widen them.

wxc-host-prep.exe is Microsoft's tool from @microsoft/mxc-sdk. Its Authenticode
signature is checked before it runs elevated, and the boot task runs a copy
under %ProgramFiles%\Loom\SandboxPrep, which only administrators can replace.

Run without arguments from any terminal; it asks for elevation (UAC) itself.
  -Status  report what is prepared; needs no administrator rights.
  -DryRun  print every change it would make, change nothing; needs no administrator rights.
  -Undo    remove the boot task, the system-drive ACE and the tool-folder grants.

.PARAMETER ToolDir
Extra tool folders to open to Loom's sandbox (read and execute only).
#>
[CmdletBinding()]
param(
    [switch]$Status,
    [switch]$DryRun,
    [switch]$Undo,
    [string[]]$ToolDir = @(),
    [string]$MxcBin = "",
    [switch]$NoElevate,
    # Internal: elevated relaunch arguments.
    [string]$ToolDirList = "",
    [switch]$PauseAtEnd
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$CapabilitySid = "S-1-15-3-1024-3969206430-1258273362-3479422002-2855359522-1761291178-1853497174-2915481160-3664497781"
$AllAppPackagesSid = "S-1-15-2-1"
$InstallDir = Join-Path $env:ProgramFiles "Loom\SandboxPrep"
$HostPrepCopy = Join-Path $InstallDir "wxc-host-prep.exe"
$GrantLog = Join-Path $InstallDir "granted-tool-dirs.txt"
$TaskPath = "\Loom\"
$TaskName = "Sandbox NUL device"
$ToolNames = @("git", "node", "npm", "npx", "pnpm", "yarn", "bun", "deno", "pwsh", "python", "py", "uv",
    "cargo", "rustc", "go", "java", "dotnet", "gh", "rg")
$ReadAndExecute = 0x1200A9

function Test-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-MxcArch {
    if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { return "arm64" }
    return "x64"
}

function Find-HostPrepSource {
    $candidates = @()
    if ($MxcBin) { $candidates += (Join-Path $MxcBin "wxc-host-prep.exe") }
    $candidates += (Join-Path $PSScriptRoot "wxc-host-prep.exe")
    if ($env:LOOM_WINDOWS_SANDBOX_EXECUTABLE) {
        $candidates += (Join-Path (Split-Path -Parent $env:LOOM_WINDOWS_SANDBOX_EXECUTABLE) "wxc-host-prep.exe")
    }
    $candidates += (Join-Path $PSScriptRoot ("..\desktop-react\node_modules\@microsoft\mxc-sdk\bin\{0}\wxc-host-prep.exe" -f (Get-MxcArch)))
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return (Resolve-Path -LiteralPath $candidate).Path }
    }
    throw "wxc-host-prep.exe was not found. Pass -MxcBin <folder that contains it> (it ships in @microsoft/mxc-sdk\bin\<arch>)."
}

function Assert-MicrosoftSigned([string]$Path) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    $subject = ""
    if ($signature.SignerCertificate) { $subject = $signature.SignerCertificate.Subject }
    if ($signature.Status -ne "Valid" -or $subject -notmatch "(^|,\s*)O=Microsoft Corporation(,|$)") {
        throw "Refusing to run $Path elevated: its Authenticode signature is '$($signature.Status)' from '$subject', not a valid Microsoft signature."
    }
}

function Test-SandboxReadable([string]$Directory) {
    try {
        $acl = Get-Acl -LiteralPath $Directory
    } catch {
        return $false
    }
    $granted = 0
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        $sid = $rule.IdentityReference.Value
        if ($sid -ne $AllAppPackagesSid -and $sid -ne $CapabilitySid) { continue }
        if (($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0) { continue }
        $rights = [int64]$rule.FileSystemRights -band 0xFFFFFFFF
        if ($rule.AccessControlType -eq "Deny") {
            if (($rights -band $ReadAndExecute) -ne 0) { return $false }
            continue
        }
        $granted = $granted -bor $rights
    }
    return (($granted -band $ReadAndExecute) -eq $ReadAndExecute)
}

function Test-TooBroad([string]$Directory) {
    $full = [IO.Path]::GetFullPath($Directory).TrimEnd("\")
    if ($full.Length -le 3) { return $true }
    $broad = @($env:SystemRoot, $env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:ProgramData,
        (Join-Path $env:SystemDrive "Users"), $env:USERPROFILE)
    foreach ($item in $broad) {
        if ($item -and $full -ieq ([IO.Path]::GetFullPath($item).TrimEnd("\"))) { return $true }
    }
    return $false
}

function Test-UnderUserProfile([string]$Directory) {
    # The signed-in user owns these; MXC opens them per command by itself.
    if (-not $env:USERPROFILE) { return $false }
    $profileRoot = [IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd("\") + "\"
    return ([IO.Path]::GetFullPath($Directory).TrimEnd("\") + "\").StartsWith($profileRoot, [StringComparison]::OrdinalIgnoreCase)
}

function Find-ToolDirs {
    $found = New-Object System.Collections.Generic.List[string]
    foreach ($name in $ToolNames) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $command) { continue }
        $directory = Split-Path -Parent $command.Source
        # bin\java.exe reads ..\lib, cmd\git.exe launches ..\mingw64: open the install root.
        if (@("bin", "cmd", "scripts", "sbin") -contains (Split-Path -Leaf $directory).ToLowerInvariant()) {
            $directory = Split-Path -Parent $directory
        }
        $found.Add($directory)
    }
    foreach ($item in $ToolDir) { if ($item) { $found.Add($item) } }
    foreach ($item in ($ToolDirList -split ";")) { if ($item) { $found.Add($item) } }
    $unique = @{}
    $result = @()
    foreach ($item in $found) {
        if (-not (Test-Path -LiteralPath $item -PathType Container)) { continue }
        $full = (Resolve-Path -LiteralPath $item).Path.TrimEnd("\")
        if ($unique.ContainsKey($full.ToLowerInvariant())) { continue }
        $unique[$full.ToLowerInvariant()] = $true
        $result += $full
    }
    return $result
}

function Get-NullDeviceSddl {
    if (-not ("LoomNullDevice" -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class LoomNullDevice {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr memory);
    [DllImport("advapi32.dll")] static extern uint GetSecurityInfo(IntPtr handle, int type, uint info, IntPtr owner, IntPtr group, IntPtr dacl, IntPtr sacl, out IntPtr descriptor);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool ConvertSecurityDescriptorToStringSecurityDescriptorW(IntPtr descriptor, uint revision, uint info, out IntPtr text, IntPtr length);
    public static string Dacl() {
        IntPtr handle = CreateFileW(@"\\.\NUL", 0x00020000, 3, IntPtr.Zero, 3, 0x80, IntPtr.Zero);
        if (handle == new IntPtr(-1)) return null;
        try {
            IntPtr descriptor;
            if (GetSecurityInfo(handle, 6, 4, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, out descriptor) != 0) return null;
            try {
                IntPtr text;
                if (!ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, out text, IntPtr.Zero)) return null;
                try { return Marshal.PtrToStringUni(text); } finally { LocalFree(text); }
            } finally { LocalFree(descriptor); }
        } finally { CloseHandle(handle); }
    }
}
"@
    }
    return [LoomNullDevice]::Dacl()
}

function Test-NullDeviceReady {
    $sddl = Get-NullDeviceSddl
    return [bool]($sddl -and $sddl -match "\(A;[^;]*;[^;]*;;;AC\)")
}

function Test-SystemDriveReady {
    $root = $env:SystemDrive + "\"
    $acl = Get-Acl -LiteralPath $root
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.IdentityReference.Value -ne $AllAppPackagesSid -or $rule.AccessControlType -ne "Allow") { continue }
        if ((([int64]$rule.FileSystemRights) -band 0x80) -ne 0) { return $true }
    }
    return $false
}

function Show-Status {
    $task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-Host ("NUL device open to AppContainers : {0}" -f (Test-NullDeviceReady))
    Write-Host ("System drive root metadata       : {0}" -f (Test-SystemDriveReady))
    Write-Host ("Boot task reapplying NUL         : {0}" -f [bool]$task)
    Write-Host ("Signed wxc-host-prep copy        : {0}" -f (Test-Path -LiteralPath $HostPrepCopy))
    foreach ($directory in (Find-ToolDirs)) {
        if (Test-UnderUserProfile $directory) {
            Write-Host ("Tool folder (yours, opened per command): {0}" -f $directory)
            continue
        }
        Write-Host ("Tool folder readable by sandbox  : {0}  {1}" -f (Test-SandboxReadable $directory), $directory)
    }
}

function Invoke-HostPrep([string[]]$Arguments) {
    & $HostPrepCopy @Arguments
    if ($LASTEXITCODE -ne 0) { throw "wxc-host-prep $($Arguments -join ' ') exited with $LASTEXITCODE" }
}

# Every change goes through here, so -DryRun can say what it would do and do none of it.
function Step([string]$Description, [scriptblock]$Action) {
    if ($DryRun) {
        Write-Host "[dry run] would: $Description"
        return
    }
    Write-Host $Description
    & $Action
}

function Install-Prep {
    $source = Find-HostPrepSource
    Assert-MicrosoftSigned $source
    Write-Host "Using Microsoft-signed $source"
    Step "create $InstallDir and copy wxc-host-prep.exe there (administrators only can change that folder)" {
        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
        if ((Resolve-Path -LiteralPath $source).Path -ine $HostPrepCopy) {
            Copy-Item -LiteralPath $source -Destination $HostPrepCopy -Force
        }
        Assert-MicrosoftSigned $HostPrepCopy
    }

    Step "run 'wxc-host-prep prepare-system-drive': let AppContainers read the metadata of $env:SystemDrive\ (survives reboots)" {
        Invoke-HostPrep @("prepare-system-drive")
    }
    Step "run 'wxc-host-prep prepare-null-device': let AppContainers open the NUL device (the kernel resets it at every boot)" {
        Invoke-HostPrep @("prepare-null-device")
    }
    Step "register scheduled task $TaskPath$TaskName (runs as SYSTEM at startup: '$HostPrepCopy prepare-null-device')" {
        $action = New-ScheduledTaskAction -Execute $HostPrepCopy -Argument "prepare-null-device"
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
        Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Trigger $trigger `
            -Principal $principal -Settings $settings -Description "Loom: reapply the NUL device descriptor MXC sandboxes need (reset by the kernel at boot)." -Force | Out-Null
    }

    $granted = @()
    if (Test-Path -LiteralPath $GrantLog) { $granted = @(Get-Content -LiteralPath $GrantLog) }
    $newlyGranted = @()
    foreach ($directory in (Find-ToolDirs)) {
        if (Test-TooBroad $directory) {
            Write-Host "Skipping $directory (too broad to open)."
            continue
        }
        if (Test-UnderUserProfile $directory) {
            Write-Host "Skipping $directory (yours; Loom opens it per command)."
            continue
        }
        if (Test-SandboxReadable $directory) {
            Write-Host "Already readable by the sandbox: $directory"
            continue
        }
        $newlyGranted += $directory
        Step "grant read+execute on $directory to Loom's sandbox capability only (icacls, inheritable)" {
            & icacls.exe $directory /grant ("*{0}:(OI)(CI)(RX)" -f $CapabilitySid) /C /Q | Out-Null
            if ($LASTEXITCODE -ne 0) { Write-Warning "icacls reported errors for $directory (exit $LASTEXITCODE)." }
        }
    }
    if (-not $DryRun) {
        foreach ($directory in $newlyGranted) { if ($granted -notcontains $directory) { $granted += $directory } }
        Set-Content -LiteralPath $GrantLog -Value $granted -Encoding UTF8
    }
    Write-Host ""
    if ($DryRun) {
        Write-Host "Dry run only: nothing was changed. Run without -DryRun to apply."
    } else {
        Write-Host "Done. Loom's Windows sandbox can now start git, node, npm and PowerShell."
    }
}

function Remove-Prep {
    $task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Step "remove scheduled task $TaskPath$TaskName" {
            Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Confirm:$false
        }
    }
    if (Test-Path -LiteralPath $HostPrepCopy) {
        Step "run 'wxc-host-prep unprepare-system-drive' (note: other MXC-based apps lose that ACE too)" {
            Invoke-HostPrep @("unprepare-system-drive")
        }
    }
    if (Test-Path -LiteralPath $GrantLog) {
        foreach ($directory in (Get-Content -LiteralPath $GrantLog)) {
            if (-not $directory -or -not (Test-Path -LiteralPath $directory)) { continue }
            Step "remove Loom's sandbox capability from $directory" {
                & icacls.exe $directory /remove:g ("*{0}" -f $CapabilitySid) /C /Q | Out-Null
            }
        }
    }
    if (Test-Path -LiteralPath $InstallDir) {
        Step "delete $InstallDir" { Remove-Item -LiteralPath $InstallDir -Recurse -Force }
    }
    if (-not $DryRun) {
        Write-Host "The NUL device returns to the kernel default at the next boot."
    }
}

try {
    if ($Status) {
        Show-Status
        return
    }
    if (-not $DryRun -and -not (Test-Administrator)) {
        if ($NoElevate) { throw "Run this from an administrator terminal, or drop -NoElevate to get a UAC prompt." }
        # Discover tools here, as the signed-in user: an elevated process can
        # belong to another account with another PATH.
        $relaunch = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-PauseAtEnd")
        if ($Undo) {
            $relaunch += "-Undo"
        } else {
            $source = Find-HostPrepSource
            $relaunch += @("-MxcBin", "`"$(Split-Path -Parent $source)`"")
            $dirs = (Find-ToolDirs | Where-Object { -not (Test-UnderUserProfile $_) }) -join ";"
            if ($dirs) { $relaunch += @("-ToolDirList", "`"$dirs`"") }
        }
        $shell = (Get-Process -Id $PID).Path
        $process = Start-Process -FilePath $shell -Verb RunAs -ArgumentList $relaunch -Wait -PassThru
        exit $process.ExitCode
    }
    if ($Undo) { Remove-Prep } else { Install-Prep }
} catch {
    Write-Host ""
    Write-Host ("Loom sandbox preparation failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
    if ($PauseAtEnd) { Read-Host "Press Enter to close" | Out-Null }
    exit 1
}
if ($PauseAtEnd) { Read-Host "Press Enter to close" | Out-Null }
