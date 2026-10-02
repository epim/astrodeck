# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
# RigRestart.ps1 - restart the rig server and PROVE it happened (#103).
#
# Every deploy script up to 0.3.33 found the server to kill like this:
#
#     Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
#         Where-Object { $_.CommandLine -and $_.CommandLine -match 'astrodeck' }
#
# On astrotown `CommandLine` comes back EMPTY for those processes, so the
# Where-Object matched nothing, Stop-Process was never called, and the script
# printed its progress and carried on. The deploy then "verified" the release
# it had just staged against the server still running the OLD one. On
# 2026-09-19 that produced a reported measurement that was simply false, and
# the only reason it surfaced was that a later by-PID kill changed the numbers.
#
# Two rules here, and the second is the one that matters:
#
#   1. Find the process by the PORT IT IS LISTENING ON, which is the property
#      being restarted, rather than by a command line that may not be
#      readable. `Get-NetTCPConnection` needs no elevation for this.
#   2. REFUSE TO BE A NO-OP. Finding nothing to kill throws, and so does a
#      restart that leaves the same PID listening. A restart step that cannot
#      fail is worse than no restart step, because the verify that follows it
#      reports on the wrong process.

Set-StrictMode -Version Latest

# Progress goes to the HOST, never Write-Output. Write-Output inside a
# function puts its argument on the PIPELINE, so `$p = Restart-RigServer`
# would capture the progress lines AND the pid as an array and the caller
# would read a pid of '  stopping pid 184144 on port 56003 ... 215896'.
# Caught by the test below rather than in a deploy.

function Get-RigServerPid {
    <#  The PID listening on $Port, or $null. #>
    param([Parameter(Mandatory)][int]$Port)
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }
    return $null
}

function Wait-RigServerPid {
    <#  Wait until the listener on $Port satisfies $Until, returning the PID
        (or $null when $Until wants it gone). Throws on timeout. #>
    param(
        [Parameter(Mandatory)][int]$Port,
        [Parameter(Mandatory)][scriptblock]$Until,
        [int]$TimeoutSec = 60,
        [string]$What = "the listener to settle"
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $current = Get-RigServerPid -Port $Port
        if (& $Until $current) { return $current }
        Start-Sleep -Milliseconds 400
    }
    throw "timed out after ${TimeoutSec}s waiting for $What on port $Port"
}

function Restart-RigServer {
    <#  Stop whatever is listening on $Port, run $Start, and prove a DIFFERENT
        process is listening afterwards. Returns the new PID.

        Throws when nothing was listening to begin with: that is the silent
        no-op this exists to end. A caller that genuinely wants "start if
        absent" should pass -AllowAbsent and check the returned PID. #>
    param(
        [Parameter(Mandatory)][scriptblock]$Start,
        [int]$Port = 8800,
        [int]$TimeoutSec = 60,
        [switch]$AllowAbsent
    )
    $before = Get-RigServerPid -Port $Port
    if (-not $before) {
        if (-not $AllowAbsent) {
            throw "nothing is listening on port $Port, so there is nothing to " +
                  "restart - refusing to report a restart that did not happen"
        }
        Write-Host "  nothing on port $Port; starting fresh"
    } else {
        Write-Host "  stopping pid $before on port $Port"
        Stop-Process -Id $before -Force -ErrorAction Stop
        Wait-RigServerPid -Port $Port -TimeoutSec $TimeoutSec `
            -What "the old listener to exit" -Until { param($p) $null -eq $p } | Out-Null
    }

    & $Start

    $after = Wait-RigServerPid -Port $Port -TimeoutSec $TimeoutSec `
        -What "the new listener to appear" -Until { param($p) $null -ne $p }

    if ($before -and $after -eq $before) {
        throw "port $Port is still served by pid $before after the restart - " +
              "the process was never replaced"
    }
    Write-Host "  restarted: pid $before -> $after"
    return $after
}
