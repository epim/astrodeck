<#
.SYNOPSIS
  One-command run of the AstroDeck UI probe: build the UI, start an isolated
  server, walk the classic-UI routes at three widths, stop the server.

.DESCRIPTION
  Mirrors the manual sequence in README.md. Exists so the controller (or a
  human) has exactly one command to run after a hub lands:

    powershell -NoProfile -ExecutionPolicy Bypass -File tools\ui_probe\run.ps1

  Exit code is the PROBE's exit code (0 = every route passed at every width),
  regardless of whether the stop step itself had trouble -- a probe failure
  must never be masked by a clean shutdown, and a shutdown hiccup must never
  be reported as a probe failure.

.PARAMETER SkipBuild
  Skip `npm run build` and probe whatever is already in ui/dist. Useful for
  re-running the probe alone after a build you already trust.

.PARAMETER Auth
  Start the server with --auth (bootstraps probe_admin/probe_operator/
  probe_viewer) and run the probe with --auth --role <Role> instead of the
  default open/no-auth posture.

.PARAMETER Role
  Role to log in as when -Auth is set. Default: admin.

.PARAMETER Routes
  Route list to walk. Default: routes_classic.json (the shipped, current UI).
  Pass routes_next.json -Include Pending once the new UI's hash routes exist.

.PARAMETER Widths
  Comma-separated CSS pixel widths. Default: 390,820,1440 (the spec'd trio).

.PARAMETER Port
  Port for the isolated server. Default: 8801.
#>
[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$Auth,
    [ValidateSet("viewer", "operator", "admin")]
    [string]$Role = "admin",
    [string]$Routes = "routes_classic.json",
    [string]$Widths = "390,820,1440",
    [int]$Port = 8801,
    [switch]$IncludePending
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$ToolDir = $PSScriptRoot
$ConfigDir = Join-Path $RepoRoot ".probe\cfg"
$OutDir = Join-Path $RepoRoot ".probe\out"

function Write-Step($msg) {
    Write-Host "==> $msg" -ForegroundColor Cyan
}

if (-not $SkipBuild) {
    Write-Step "Building UI (cd ui; npm run build)"
    Push-Location (Join-Path $RepoRoot "ui")
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed with exit code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
} else {
    Write-Step "Skipping build (-SkipBuild) -- probing whatever is in ui/dist"
}

Write-Step "Starting isolated server on port $Port (config: $ConfigDir)"
$startArgs = @(
    (Join-Path $ToolDir "server_ctl.py"), "start",
    "--config-dir", $ConfigDir,
    "--capture-dir", (Join-Path $RepoRoot ".probe\captures"),
    "--port", $Port,
    "--fresh"
)
if ($Auth) { $startArgs += "--auth" }
& python @startArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "server_ctl.py start FAILED (exit $LASTEXITCODE) -- see $ConfigDir\server.log" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Step "Running probe against http://127.0.0.1:$Port"
$probeArgs = @(
    (Join-Path $ToolDir "probe.py"),
    "--port", $Port,
    "--routes", (Join-Path $ToolDir $Routes),
    "--widths", $Widths,
    "--out", $OutDir
)
if ($Auth) {
    $probeArgs += "--auth"
    $probeArgs += "--role"
    $probeArgs += $Role
    $probeArgs += "--creds"
    $probeArgs += (Join-Path $ConfigDir "probe_users.json")
}
if ($IncludePending) { $probeArgs += "--include-pending" }

& python @probeArgs
$probeExit = $LASTEXITCODE

Write-Step "Stopping server"
$stopArgs = @((Join-Path $ToolDir "server_ctl.py"), "stop", "--config-dir", $ConfigDir)
& python @stopArgs

Write-Step "Done. Probe exit code: $probeExit  (screenshots + report: $OutDir)"
exit $probeExit
