# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
# deploy_common.ps1 - the steps every per-version deploy script shares (#403).
#
# Each deploy_<ver>.ps1 is copied from the one before it, so a trap in one is
# inherited by every script after it. Two reached 0.3.35 (#403):
#
#   1. The native wheel was force-reinstalled BEFORE the server was stopped.
#      The running server has astrodeck_native's .pyd loaded, Windows will not
#      let pip replace a loaded DLL, and the step failed (2026-09-27 23:48).
#   2. The detached run's log ended at that step's header with no error text.
#      The script ran as `powershell -Command "& script *> log"`, and a
#      terminating error propagates OUT of that redirection rather than
#      through it, so it reached only a console nobody had.
#
# Future per-version scripts dot-source this file instead of copying those
# steps, so a fix made here reaches every deploy after it:
#
#     $ErrorActionPreference = "Stop"
#     . (Join-Path $PSScriptRoot "deploy_common.ps1")
#     Invoke-LoggedDeploy -Log (Join-Path $Root "deploy_$Ver.log") -Body {
#         # ... stage and verify, then stop the server ...
#         Install-NativeWheelIfChanged -Python $Py -Wheel $Wheel
#         # ... flip 'current', start, verify ...
#     }
#
# Copy this file to the rig beside the per-version script. Launch detached,
# because Start-Process does not survive an ssh disconnect (CLAUDE.md, "The
# rig"), and with NO redirection of your own, because Invoke-LoggedDeploy owns
# the log:
#
#     Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
#         CommandLine = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\James\AstroDeck\deploy_0336.ps1' }
#
# A launcher that ALSO redirects into the same file holds it open: the body's
# redirection then fails with "The process cannot access the file" and the
# body never runs (observed 2026-09-28). Redirecting to a different file is
# harmless.
#
# One Windows PowerShell 5.1 trait the body inherits from the old launcher:
# under a redirection and a Stop preference, the FIRST line a native command
# writes to stderr is a terminating NativeCommandError (observed 2026-09-28
# with `cmd /c "echo x 1>&2"`). The body stops there, and the log now says so.
# Run a native step whose stderr is chatter through Invoke-DeployNative, which
# folds stderr into the lines it returns and leaves the verdict to the exit
# code.

function Invoke-LoggedDeploy {
    <#  Run $Body with everything it writes appended to $Log. If anything in
        it throws, append that error to $Log and exit 1.

        The try/catch is the point. A terminating error leaves the `*>>`
        redirection as an exception, never as output, so without the catch it
        reaches only the console, and a detached run has none. `exit` rather
        than a rethrow, because a rethrow would carry the error out to that
        same missing console. #>
    param(
        [Parameter(Mandatory)][string]$Log,
        [Parameter(Mandatory)][scriptblock]$Body
    )
    # The first error stops the deploy, whatever the caller set: a
    # non-terminating one would otherwise be logged and stepped over on the
    # way to flipping 'current'. The body runs in a child of this scope, so
    # it inherits this.
    $ErrorActionPreference = 'Stop'
    try {
        & $Body *>> $Log
    } catch {
        # Out-File -Append with no -Encoding matches what `*>>` wrote
        # (UTF-16LE in 5.1, UTF-8 in 7), so the log stays in one encoding.
        # ScriptStackTrace names the per-version script's line that called
        # into this file, which the record's own position (the throw inside a
        # helper here) does not.
        ("== DEPLOY FAILED (exit 1) at " + (Get-Date -Format o) + " ==`r`n" +
            ($_ | Out-String) + "script stack:`r`n" + $_.ScriptStackTrace) |
            Out-File -LiteralPath $Log -Append
        exit 1
    }
}

function Invoke-DeployNative {
    <#  Run a native command and return @{ ExitCode; Lines }, with stderr
        folded into Lines. Nothing it writes to stderr stops the caller: the
        exit code is the verdict, and the caller decides what to throw.

        The local Continue is what makes that true. Under the caller's Stop,
        5.1 turns the first redirected stderr line into a terminating
        NativeCommandError, and pip's own report of why it failed would
        arrive as that error instead of as evidence. #>
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @()
    )
    $ErrorActionPreference = 'Continue'
    $lines = @(& $FilePath @ArgumentList 2>&1 | ForEach-Object {
        # A stderr line arrives as an ErrorRecord whose TargetObject is the
        # line itself. Its string form is the same text, except that an
        # empty line reads "System.Management.Automation.RemoteException".
        if ($_ -is [System.Management.Automation.ErrorRecord] -and
                $_.TargetObject -is [string]) { $_.TargetObject }
        else { "$_" }
    })
    [pscustomobject]@{ ExitCode = $LASTEXITCODE; Lines = $lines }
}

function Get-DeployListenerPid {
    <#  The PID listening on $Port, or $null when nothing is.

        The real cmdlet reports "nothing listens" as an ERROR, not an empty
        result: FullyQualifiedErrorId CmdletizationQuery_NotFound. Any other
        error (CIM unavailable, access denied) is no answer at all, and
        reading it as "stopped" would open the wheel gate on a guess, so it
        throws. The match is on that id, not on the ObjectNotFound category:
        a cmdlet that is missing or failed to load raises
        CommandNotFoundException, whose category is ObjectNotFound too
        (both checked on 2026-09-28). #>
    param([int]$Port = 8800)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1
    } catch {
        if ($_.FullyQualifiedErrorId -like 'CmdletizationQuery_NotFound*') { return $null }
        throw "could not tell whether port $Port is listening, so not assuming " +
              "it is free: $($_.Exception.Message)"
    }
    if ($conn) { return [int]$conn.OwningProcess }
    return $null
}

function Get-InstalledWheelSha256 {
    <#  The sha256 pip recorded for the installed $Distribution, lower-case,
        or '' when it is not installed or no hash was recorded.

        pip writes the archive's hash into the dist-info's direct_url.json
        when it installs from a file, as "hash": "sha256=..." and as
        "hashes": {"sha256": ...}. pip 25.0.1 and 26.2.1 were both seen
        writing both keys, even for a plain path (2026-09-28); the snippet
        takes either. pip replaces that file on every install, so it cannot
        go stale the way a stamp file of our own could. python finds the
        dist-info the server actually imports, so no venv layout is assumed.
        The snippet holds no double quote: 5.1 drops embedded ones from a
        native argument. #>
    param(
        [Parameter(Mandatory)][string]$Python,
        [string]$Distribution = 'astrodeck_native'
    )
    $code = "import json,sys;from importlib import metadata as m;" +
            "d=next(iter(m.distributions(name=sys.argv[1])),None);" +
            "a=(json.loads(d.read_text('direct_url.json') or '{}') if d else {}).get('archive_info') or {};" +
            "h=(a.get('hashes') or {}).get('sha256') or str(a.get('hash') or '').partition('sha256=')[2];" +
            "print('SHA256='+h.lower())"
    $r = Invoke-DeployNative -FilePath $Python -ArgumentList @('-c', $code, $Distribution)
    $line = $r.Lines | Where-Object { $_ -like 'SHA256=*' } | Select-Object -Last 1
    if ($r.ExitCode -ne 0 -or -not $line) {
        throw "could not read the sha256 recorded for the installed $Distribution " +
              "(python exit $($r.ExitCode)): $($r.Lines -join ' | ')"
    }
    return $line.Substring(7).Trim().ToLower()
}

function Test-NativeWheelCurrent {
    <#  $true when the venv records exactly this wheel's sha256. #>
    param(
        [Parameter(Mandatory)][string]$Python,
        [Parameter(Mandatory)][string]$Wheel,
        [string]$Distribution = 'astrodeck_native'
    )
    $want = (Get-FileHash -LiteralPath $Wheel -Algorithm SHA256 -ErrorAction Stop).Hash.ToLower()
    return ((Get-InstalledWheelSha256 -Python $Python -Distribution $Distribution) -eq $want)
}

function Install-NativeWheelIfChanged {
    <#  Reinstall $Wheel only when its sha256 differs from the one the venv
        records, and only once nothing listens on $Port.

        The same wheel is left alone, server up or down: that was 0.3.35,
        where a force-reinstall of the wheel already loaded was the whole
        failure. A changed wheel while the port listens is refused in words:
        the step belongs after the stop. An unknown installed hash (not
        installed, or none recorded) is not "the same" and reinstalls.

        pip is pinned to this wheel's hash with a #sha256= fragment, so it
        verifies the file and records the hash, and the hash is read back
        afterwards: a pip that exits 0 and replaces nothing does not pass. #>
    param(
        [Parameter(Mandatory)][string]$Python,
        [Parameter(Mandatory)][string]$Wheel,
        [string]$Distribution = 'astrodeck_native',
        [int]$Port = 8800
    )
    $want = (Get-FileHash -LiteralPath $Wheel -Algorithm SHA256 -ErrorAction Stop).Hash.ToLower()
    $have = Get-InstalledWheelSha256 -Python $Python -Distribution $Distribution
    if ($have -eq $want) {
        Write-Host ("   native wheel unchanged (sha256 " + $want.Substring(0, 16) +
                    "...): not reinstalled")
        return
    }
    $had = if ($have) { $have.Substring(0, 16) + "..." } else { "no hash" }
    Write-Host ("   native wheel changed: the venv records " + $had +
                ", the wheel is " + $want.Substring(0, 16) + "...")

    $listener = Get-DeployListenerPid -Port $Port
    if ($listener) {
        throw "refusing to reinstall the native wheel while port $Port is listening " +
              "(pid $listener): the running server has $Distribution's .pyd loaded " +
              "and Windows will not let pip replace a loaded DLL. Stop the server " +
              "first; this step belongs after the stop (#403)."
    }

    $url = ([Uri](Resolve-Path -LiteralPath $Wheel).ProviderPath).AbsoluteUri + "#sha256=$want"
    $pip = Invoke-DeployNative -FilePath $Python -ArgumentList @(
        '-m', 'pip', 'install', '--force-reinstall', '--no-deps', '--no-index',
        '--disable-pip-version-check', '--no-input', "$Distribution @ $url")
    foreach ($l in $pip.Lines) { Write-Host "     | $l" }
    if ($pip.ExitCode -ne 0) {
        throw "pip install exited $($pip.ExitCode) for $Wheel`: $($pip.Lines -join ' | ')"
    }
    $now = Get-InstalledWheelSha256 -Python $Python -Distribution $Distribution
    if ($now -ne $want) {
        throw "pip exited 0 but the venv still records sha256 '$now', not the " +
              "wheel's $want - the wheel was not installed"
    }
    Write-Host ("   native wheel reinstalled: the venv records sha256 " +
                $want.Substring(0, 16) + "...")
}
