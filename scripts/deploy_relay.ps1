# deploy_relay.ps1 - ship the relay to Fly and prove what shipped (#462).
#
# On 2026-09-28 the relay had no working deploy path: the flyctl login on the
# workstation had expired, and the Actions workflow had been disabled with its
# minutes spent. Nothing on the running relay said which code it was either,
# so a deploy could not be verified and drift went unnoticed (the relay once
# ran July code for two months). /healthz now answers the version and commit
# baked into the image, and this script is the deploy that uses them. In
# order, it:
#
#   1. runs the relay suite, and deploys nothing unless it passes with no
#      test skipped (the on-wire tests skip when their deps are missing, and
#      pytest still exits 0);
#   2. runs `flyctl deploy --remote-only` from relay/, with the commit and the
#      AstroDeck version as build args;
#   3. polls /healthz until it reports that commit, and fails loudly if it
#      does not within -HealthTimeoutS.
#
# Before step 1 it refuses a relay/ tree with uncommitted changes: flyctl
# builds from the working tree, so such a change would ship under a commit
# that does not contain it, and /healthz would name the wrong code.
#
# Run it on the operator's workstation, from anywhere:
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy_relay.ps1
#
# flyctl must already be logged in (`fly auth login`). The script never
# handles a credential: it does not open flyctl's own config, reads no token
# variable, and withholds anything token-shaped in flyctl's output, which
# tends to get pasted into issues.
#
# It dot-sources deploy_common.ps1 for Invoke-DeployNative, which folds a
# native command's stderr into its output and leaves the verdict to the exit
# code (flyctl and pytest both write progress to stderr, and under 5.1 a Stop
# preference would otherwise turn their first stderr line into a terminating
# error). Invoke-LoggedDeploy does not fit: it sends everything to a log for a
# detached rig run, and this one is interactive. Each step's output is
# printed when the step finishes, so the remote build is quiet for a while.
#
# server/tests/test_deploy_relay_ps1.py drives this file with stubbed git,
# flyctl, suite and /healthz. No test deploys anything.

param(
    # The public /healthz of the relay being deployed (fly.toml's app).
    [string]$HealthUrl = 'https://astrodeck-relay.fly.dev/healthz',
    # The interpreter for the relay suite. The server venv has the relay's
    # wire deps; the system Python has no pytest.
    [string]$Python = '',
    [double]$PollIntervalS = 5,
    # A remote build plus a machine replacement takes a few minutes; flyctl
    # waits for most of that itself, so this covers the rollout's tail.
    [double]$HealthTimeoutS = 300
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'deploy_common.ps1')

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
$Relay = Join-Path $Root 'relay'
if (-not $Python) { $Python = Join-Path $Root 'server\.venv\Scripts\python.exe' }

function Resolve-RelayTool {
    <#  The path of a native tool on PATH, or a throw that names it. A
        missing command would otherwise leave $LASTEXITCODE at whatever the
        previous command set, and a missing flyctl could read as a deploy
        that exited 0. #>
    param([Parameter(Mandatory)][string]$Name)
    $c = Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $c) { throw "$Name is not on PATH" }
    return $c.Source
}

function Hide-FlyToken {
    <#  $Line with anything shaped like a Fly token replaced. flyctl has no
        reason to print one, but a token in plaintext has to be rotated, so
        this is cheaper than trusting that. #>
    param([string]$Line)
    return ($Line -replace '(FlyV1\s+)?\bf[a-z][0-9]_[A-Za-z0-9_+/=,-]{16,}', '[token withheld]')
}

function Get-ProjectVersion {
    <#  The [project] table's version from a pyproject.toml's lines, or
        $null. Only that table: a [tool.*] table can carry a version too. #>
    param([string[]]$Lines)
    $inProject = $false
    foreach ($l in $Lines) {
        if ($l -match '^\s*\[([^\]]+)\]\s*$') {
            $inProject = ($Matches[1].Trim() -eq 'project')
            continue
        }
        if ($inProject -and $l -match '^\s*version\s*=\s*"([^"]+)"') { return $Matches[1] }
    }
    return $null
}

function Write-StepLines {
    param([string[]]$Lines)
    foreach ($l in $Lines) { Write-Host ("   | " + (Hide-FlyToken $l)) }
}

try {
    $git = Resolve-RelayTool 'git'
    $fly = Resolve-RelayTool 'flyctl'
    if (-not (Test-Path -LiteralPath $Python)) {
        throw "the suite's interpreter is not there: $Python (pass -Python)"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Relay 'fly.toml'))) {
        throw "no fly.toml in $Relay"
    }

    # -- what is being deployed ------------------------------------------------
    $r = Invoke-DeployNative -FilePath $git -ArgumentList @('-C', $Root, 'rev-parse', 'HEAD')
    $Sha = "$($r.Lines | Select-Object -Last 1)".Trim().ToLower()
    if ($r.ExitCode -ne 0 -or $Sha -notmatch '^[0-9a-f]{40}$') {
        throw "git rev-parse HEAD did not name a commit (exit $($r.ExitCode)): $($r.Lines -join ' | ')"
    }

    $r = Invoke-DeployNative -FilePath $git -ArgumentList @(
        '-C', $Root, 'status', '--porcelain', '--untracked-files=all', '--', 'relay')
    if ($r.ExitCode -ne 0) {
        throw "git status exited $($r.ExitCode): $($r.Lines -join ' | ')"
    }
    $dirty = @($r.Lines | Where-Object { "$_".Trim() })
    if ($dirty.Count -gt 0) {
        throw ("relay/ has changes that are not in commit $Sha, and flyctl builds " +
               "from the working tree, so /healthz would name code the image does " +
               "not match. Commit or set them aside first: " + ($dirty -join '; '))
    }

    # The AstroDeck version at that commit, as the home's own /healthz reports
    # it. Read from the commit rather than the working tree, so a version bump
    # in progress elsewhere in the tree cannot label this build.
    $r = Invoke-DeployNative -FilePath $git -ArgumentList @(
        '-C', $Root, 'show', "${Sha}:server/pyproject.toml")
    $Version = Get-ProjectVersion -Lines $r.Lines
    if ($r.ExitCode -ne 0 -or -not $Version -or $Version -notmatch '^[0-9A-Za-z.+_-]{1,64}$') {
        throw "could not read the [project] version from server/pyproject.toml at $Sha (git exit $($r.ExitCode))"
    }
    Write-Host "deploying the relay at commit $Sha (AstroDeck $Version)"

    # -- 1. the suite ----------------------------------------------------------
    Write-Host "== 1/3 the relay suite: $Python -m pytest -q (in relay/)"
    Push-Location -LiteralPath $Relay
    try {
        $suite = Invoke-DeployNative -FilePath $Python -ArgumentList @('-m', 'pytest', '-q')
    } finally {
        Pop-Location
    }
    Write-StepLines $suite.Lines
    if ($suite.ExitCode -ne 0) {
        throw "the relay suite failed (pytest exit $($suite.ExitCode)); nothing was deployed"
    }
    $summary = $suite.Lines | Where-Object { $_ -match '\b\d+ passed\b' } | Select-Object -Last 1
    if (-not $summary) {
        throw "pytest exited 0 but reported no passed tests; nothing was deployed"
    }
    if ($summary -match '\b\d+ skipped\b') {
        throw ("the relay suite skipped tests ($summary). The on-wire tests skip " +
               "when starlette, uvicorn, websockets or httpx is missing from " +
               "$Python; nothing was deployed")
    }

    # -- 2. the deploy ---------------------------------------------------------
    Write-Host "== 2/3 flyctl deploy --remote-only (in relay/); output follows when it finishes"
    Push-Location -LiteralPath $Relay
    try {
        $deploy = Invoke-DeployNative -FilePath $fly -ArgumentList @(
            'deploy', '--remote-only',
            '--build-arg', "RELAY_BUILD_COMMIT=$Sha",
            '--build-arg', "RELAY_BUILD_VERSION=$Version")
    } finally {
        Pop-Location
    }
    Write-StepLines $deploy.Lines
    if ($deploy.ExitCode -ne 0) {
        throw "flyctl deploy exited $($deploy.ExitCode); see its output above"
    }

    # -- 3. the proof ----------------------------------------------------------
    # flyctl's exit code says the rollout finished, not which code is serving:
    # only the relay can say that, and it says it here.
    Write-Host "== 3/3 waiting for $HealthUrl to report commit $Sha"
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $deadline = (Get-Date).AddSeconds($HealthTimeoutS)
    $last = 'no answer yet'
    while ($true) {
        $now = $null
        try {
            $h = Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec 15 `
                -Headers @{ 'Cache-Control' = 'no-cache' }
            $got = "$($h.commit)".Trim().ToLower()
            if ($got -eq $Sha) {
                Write-Host "relay deployed: /healthz reports commit $($h.commit), version $($h.version)"
                break
            }
            $now = "commit '$($h.commit)', version '$($h.version)'"
        } catch {
            $now = "no answer ($($_.Exception.Message))"
        }
        if ($now -ne $last) { Write-Host "   /healthz: $now"; $last = $now }
        if ((Get-Date) -ge $deadline) {
            throw ("the relay's /healthz did not report commit $Sha within " +
                   "$HealthTimeoutS s; it last answered $last. flyctl reported the " +
                   "deploy finished, so the relay serving is not the code just built.")
        }
        Start-Sleep -Milliseconds ([int]($PollIntervalS * 1000))
    }
} catch {
    Write-Host ("RELAY DEPLOY FAILED: " + $_.Exception.Message)
    exit 1
}
exit 0
