param(
    [string]$Profile = "loom-chatgpt",
    [string]$Workspace = (Get-Location).Path,
    [string]$TunnelClient = "tunnel-client",
    [string]$McpCommand = "",
    [switch]$Run
)

$ErrorActionPreference = "Stop"

function Require-EnvironmentValue([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Missing $Name. Create/export the OpenAI tunnel runtime value before running this helper."
    }
    return $value.Trim()
}

$tunnelId = Require-EnvironmentValue "CONTROL_PLANE_TUNNEL_ID"
$null = Require-EnvironmentValue "CONTROL_PLANE_API_KEY"

if ($tunnelId -notmatch '^tunnel_[0-9a-f]{32}$') {
    throw "CONTROL_PLANE_TUNNEL_ID does not match the OpenAI tunnel_id format."
}

$resolvedWorkspace = [System.IO.Path]::GetFullPath($Workspace)
if (-not (Test-Path -LiteralPath $resolvedWorkspace -PathType Container)) {
    throw "Workspace does not exist: $resolvedWorkspace"
}

if ([string]::IsNullOrWhiteSpace($McpCommand)) {
    $escapedWorkspace = $resolvedWorkspace.Replace('"', '\"')
    $McpCommand = "loom-chatgpt-mcp --workspace `"$escapedWorkspace`""
}

Write-Host "Initializing Secure MCP Tunnel profile '$Profile' for Loom."
Write-Host "MCP command: $McpCommand"
Write-Host "No OpenAI API key is written by this script; tunnel-client reads CONTROL_PLANE_API_KEY from the environment."

& $TunnelClient init `
    --sample sample_mcp_stdio_local `
    --profile $Profile `
    --tunnel-id $tunnelId `
    --mcp-command $McpCommand

if ($LASTEXITCODE -ne 0) {
    throw "tunnel-client init failed with exit code $LASTEXITCODE"
}

& $TunnelClient doctor --profile $Profile --explain
if ($LASTEXITCODE -ne 0) {
    throw "tunnel-client doctor failed with exit code $LASTEXITCODE"
}

if ($Run) {
    Write-Host "Starting tunnel-client in the foreground. Keep exactly one stdio runner active for this tunnel id."
    & $TunnelClient run --profile $Profile
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Profile is ready. Start it with:"
Write-Host "  tunnel-client run --profile $Profile"
Write-Host ""
Write-Host "Then add/select this tunnel from ChatGPT connector/app settings while the runner is healthy."
