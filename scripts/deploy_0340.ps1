# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
# Stage 0.3.40 on the astrotown rig. Run ON the rig, detached (see
# deploy_common.ps1 for the launch line), beside deploy_common.ps1 and
# deps_floor_check.py. The runtime dependency floors are unchanged since
# 0.3.39 (only a dev extra moved), so this deploy re-verifies and reuses
# wheelhouse-0.3.39\ already on the box.
#
# Owner, 2026-10-05 20:21: "once CI is green please cut a release."
# restart_gate.py answers GO at once for an idle rig and waits for a frame
# boundary otherwise.
#
# What 0.3.40 carries on top of 0.3.39: backlog waves 4-12 (rotator sync
# anchor and handedness, event-loop fault handling, focus carry-on, dither
# settle from Rig > Guider, max_run_min, the keep-out drift bound), the
# release-engineering and licence jobs, and the plain-wording pass over
# user-facing prose (no money metaphors). No native or vendor change, so
# the wheel on the box stays.
#
# A gate here is a marker that exists ONLY in the new code: it says the bytes
# on the box are the bytes that were built, which no version string can.
$ErrorActionPreference = "Stop"
$Root = "C:\Users\James\AstroDeck"
. (Join-Path $Root "deploy_common.ps1")
$Ver  = "0.3.40"
$Prev = "0.3.39"
$Rel  = Join-Path $Root "releases\$Ver"
$Py   = Join-Path $Root "venv\Scripts\python.exe"
$Wheel = Join-Path $Root "astrodeck_native-0.1.0-cp311-abi3-win_amd64.whl"
$WH   = Join-Path $Root "wheelhouse-$Prev"
$Dfc  = Join-Path $Root "deps_floor_check.py"

function Invoke-Step([string]$Name, [string]$FilePath, [string[]]$ArgumentList = @()) {
    $r = Invoke-DeployNative -FilePath $FilePath -ArgumentList $ArgumentList
    foreach ($l in $r.Lines) { Write-Output ("   | " + $l) }
    if ($r.ExitCode -ne 0) { throw "$Name failed (exit $($r.ExitCode))" }
}

Invoke-LoggedDeploy -Log (Join-Path $Root "deploy_$Ver.log") -Body {
    Write-Output ("== deploy $Ver started " + (Get-Date -Format o) + " ==")
    Write-Output "== the tarball is the one that was hashed =="
    $tarball = Join-Path $Root "astrodeck-$Ver.tar.gz"
    $want = ((Get-Content (Join-Path $Root "astrodeck-$Ver.tar.gz.sha256") -Raw) -split '\s+')[0].ToLower()
    $have = (Get-FileHash $tarball -Algorithm SHA256).Hash.ToLower()
    if ($want -ne $have) { throw "tarball sha256 mismatch: have $have want $want" }
    Write-Output ("   sha256 ok: " + $have.Substring(0, 16) + "...")
    if (-not (Test-Path $Wheel)) { throw "the native wheel is not on the box: $Wheel" }
    if (-not (Test-Path $Dfc)) { throw "deps_floor_check.py is not on the box: $Dfc" }
    $vendorSrc = Join-Path $Root "releases\$Prev\server\astrodeck\vendor"
    if (-not (Test-Path $vendorSrc)) { throw "the $Prev vendor tree is not on the box - carry-forward source missing" }

    Write-Output "== extract and verify BEFORE anything is stopped =="
    $staging = Join-Path $Root "stage$Ver"
    if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
    New-Item -ItemType Directory -Force $staging | Out-Null
    Invoke-Step "tar extract" "tar.exe" @("-xzf", $tarball, "-C", $staging)
    if (Test-Path $Rel) { Remove-Item -Recurse -Force $Rel }
    New-Item -ItemType Directory -Force (Join-Path $Root "releases") | Out-Null
    Move-Item (Join-Path $staging "astrodeck-$Ver") $Rel
    Remove-Item -Recurse -Force $staging

    Write-Output "== carry the manifest-pinned SDK libraries forward with the updater's own code =="
    $dst = Join-Path $Rel "server\astrodeck\vendor"
    Push-Location (Join-Path $Rel "server")
    try { Invoke-Step "carry-forward" $Py @("-m", "astrodeck.update.stage", "carry-forward", $vendorSrc, $dst) }
    finally { Pop-Location }
    if (-not (Test-Path (Join-Path $dst "playerone\PlayerOneCamera.dll"))) { throw "PlayerOneCamera.dll did not arrive in the staged tree" }
    Write-Output "   PlayerOneCamera.dll present in the staged tree"

    Write-Output "== GATE: the staged vendor tree verifies against the shipped manifest =="
    Push-Location (Join-Path $Rel "server")
    try { Invoke-Step "vendor integrity check" $Py @("-m", "astrodeck.devices.vendor_verify") }
    finally { Pop-Location }

    Write-Output "== refresh webui from ui/dist (a stale bundled copy shadows it) =="
    $webui = Join-Path $Rel "server\astrodeck\webui"
    if (Test-Path $webui) { Remove-Item -Recurse -Force $webui }
    New-Item -ItemType Directory -Force $webui | Out-Null
    Copy-Item -Recurse -Force (Join-Path $Rel "ui\dist\*") $webui
    if (-not (Test-Path (Join-Path $webui "bootstrap.js"))) { throw "webui/bootstrap.js missing" }

    Write-Output "== verify the STAGED tree is what we think it is =="
    $staged = Select-String -Path (Join-Path $Rel "server\astrodeck\__init__.py") -Pattern '__version__'
    Write-Output ("   staged: " + $staged.Line.Trim())
    if ($staged.Line -notmatch '0\.3\.40') { throw "staged tree is not 0.3.40" }
    $checks = @(
        # --- new in 0.3.40 (absent from release 0.3.39) --------------------
        @{ File = 'server/astrodeck/api/app.py'; Pattern = '_settle_field_override'; Name = 'W12 #560: a dither settle override reaches PHD2' },
        @{ File = 'server/astrodeck/hub.py'; Pattern = '_rotator_sync_anchor'; Name = 'W8: the rotator syncs to a solved anchor' },
        @{ File = 'server/astrodeck/hub.py'; Pattern = '_effective_rotator_sign'; Name = 'W8 #145: the rotator handedness is applied' },
        @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = 'jump limit reached'; Name = 'plain wording in engine messages' },
        @{ File = 'server/astrodeck/flows/doctor.py'; Pattern = 'will not have a saved report'; Name = 'plain wording in the flow doctor' },
        # --- carried from 0.3.39 -------------------------------------------
        @{ File = 'server/astrodeck/sequence/group_rules.py'; Pattern = 'HELD_PASS_ALERT_AT'; Name = 'W3 #563: held passes escalate (D-03)' },
        @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_alert_held_streak'; Name = 'W3 #563: the held-pass operator alert' },
        @{ File = 'server/astrodeck/hub.py'; Pattern = 'centring_solve_transient'; Name = 'W3 #576: centring and rotation solve transients split' },
        @{ File = 'server/astrodeck/hub.py'; Pattern = 'safety.horizon'; Name = 'W3 #132: pre-flight reads the obstruction horizon' },
        @{ File = 'server/astrodeck/hub.py'; Pattern = 'site_derived=True'; Name = 'W2 #166: the hub flip line is withheld from viewers' },
        @{ File = 'server/astrodeck/sequence/report.py'; Pattern = '_relay_drops_per_hour'; Name = 'W3 #521: the report counts relay drops per hour' },
        @{ File = 'server/astrodeck/sequence/report.py'; Pattern = '_retry_snapshot'; Name = 'W3 #579: a failed report snapshot is retried' },
        @{ File = 'server/astrodeck/remote/relay_client.py'; Pattern = '_note_drop'; Name = 'W3 #521: relay drops are recorded' },
        @{ File = 'server/astrodeck/polar/native.py'; Pattern = 'SOLVE_WRITE_ATTEMPTS'; Name = 'W3 #532: the polar solve writes a unique frame' },
        @{ File = 'server/astrodeck/flows/wizard.py'; Pattern = 'OverflowError'; Name = 'W3 #501: wizard numbers refuse an overflow' },
        @{ File = 'server/astrodeck/sequence/resume_arm.py'; Pattern = '_unsuperseded_stalled'; Name = 'W2: ResumeArm ignores a superseded stall' },
        @{ File = 'server/astrodeck/guide/native.py'; Pattern = '_prove_reuse_with_first_pulse'; Name = 'W2: a reused guider calibration proves itself on its first pulse' },
        @{ File = 'server/astrodeck/devices/backends/zwo_am5.py'; Pattern = '_pulse_park_lock'; Name = 'W2: the AM5 park waits out a guide pulse' },
        @{ File = 'server/astrodeck/cloudmap/service.py'; Pattern = 'telescope_payload'; Name = 'H4 #520: the telescope cloud map reads the mount on the server' },
        @{ File = 'server/astrodeck/api/app.py'; Pattern = '_refuse_site_query'; Name = 'H4 #520: no route takes site coordinates in a query string' },
        @{ File = 'server/astrodeck/hub.py'; Pattern = '_approach_rotator'; Name = 'H4 #526: the rotate loop approaches from one side' },
        @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = 'SOLVE_TRANSIENT'; Name = 'H4 #532: a solve that could not run is no centring strike' },
        @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_frame_was_guided'; Name = 'W1 #134: only a guided frame re-arms the guiding recovery' },
        @{ File = 'server/astrodeck/alerting.py'; Pattern = 'class _SinkLane'; Name = 'W1 #549: each alert sink sends from its own lane, by severity' },
        @{ File = 'server/astrodeck/logfmt.py'; Pattern = 'PathOnlyAccessFormatter'; Name = 'W1 #550: the rig server access log is path-only' },
        @{ File = 'server/astrodeck/sun_watch.py'; Pattern = 'BLIND_ERROR_AFTER'; Name = 'W1 #137: sun watch escalates an unreadable mount' },
        @{ File = 'server/astrodeck/devices/serial_link.py'; Pattern = '_mark_dead'; Name = 'W1 #133: a dead serial handle becomes a dead link' },
        @{ File = 'server/astrodeck/hub.py'; Pattern = '_cooling_restore_allowed'; Name = 'W1 #557: a daytime restart leaves the camera warm' },
        @{ File = 'server/astrodeck/api/redact.py'; Pattern = '_withhold_report_site_derived'; Name = 'W1 #567: a viewer report carries no site-derived numbers' },
        @{ File = 'server/astrodeck/sequence/models.py'; Pattern = 'twilight_deg'; Name = 'W1 #191: each DUSK start resolves its own sun altitude' },
        @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_hop_angle_within'; Name = '0.3.37 hotfix (ported): a short rotation inside the tolerance shoots' },
        @{ File = 'server/astrodeck/sequence/resume_arm.py'; Pattern = 'stop_recovery'; Name = 'H2 #220: Abort stops the recovery ladder' },
        @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_idle_park_hold'; Name = 'S0 #165: an idle mount is park-held' },
        @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_enforce_flip_owed'; Name = 'F3: no exposure while a flip is owed' },
        @{ File = 'server/astrodeck/dawn_park.py'; Pattern = 'issue #35'; Name = '#35: dawn warms the camera even with a session armed' }
    )
    foreach ($c in $checks) {
        $hit = Select-String -Path (Join-Path $Rel $c.File) -Pattern $c.Pattern -SimpleMatch
        if (-not $hit) { throw ($c.Name + " is NOT in the staged tree (" + $c.File + ")") }
        Write-Output ("   " + $c.Name + ": present")
    }
    $ui = Get-ChildItem (Join-Path $webui "assets") -Filter "index-*.js" | Select-Object -First 1
    if (-not (Select-String -Path $ui.FullName -Pattern '0\.3\.40' -Quiet)) { throw "the built UI does not carry the 0.3.40 version stamp" }
    Write-Output "   UI bundle carries 0.3.40"
    $uiChecks = @(
        @{ Pattern = 'The open this card asked for has not answered yet, so there is nothing here to show.'; Name = 'W3 #592: the sky flow card says why it is empty' },
        @{ Pattern = 'The saved FITS frames are untouched.'; Name = 'W3 #279: the delete confirm says what is kept' },
        @{ Pattern = 'IGNORE FORECAST RAIN TONIGHT'; Name = 'W2 #259: the Monitor weather override for tonight' },
        @{ Pattern = 'This flow has not opened yet.'; Name = 'W1 #553: #/next acts only on the flow it opened (carried)' },
        @{ Pattern = 'Waiting for the meridian, so the mosaic changes pier side once.'; Name = 'H4 #488: a meridian wait reads as one (carried)' },
        @{ Pattern = 'flow-loop-arc'; Name = 'S4: the panel loop arc on the canvas (carried)' },
        @{ Pattern = 'SEND TO FLOW WIZARD'; Name = 'S6 #196: the doors converge on the wizard (carried)' },
        @{ Pattern = 'link-next-ui'; Name = 'classic root with the door to #/next (carried)' }
    )
    foreach ($u in $uiChecks) {
        $hit = Get-ChildItem (Join-Path $webui "assets") -Filter "*.js" |
            Where-Object { Select-String -Path $_.FullName -Pattern $u.Pattern -SimpleMatch -Quiet } |
            Select-Object -First 1
        if (-not $hit) { throw ("the built UI is missing " + $u.Name + " (" + $u.Pattern + ")") }
        Write-Output ("   " + $u.Name + " in " + $hit.Name)
    }

    Write-Output "== the native wheel already in the venv is the right one (no reinstall) =="
    Invoke-Step "check_wheel" $Py @((Join-Path $Root "check_wheel.py"))

    Write-Output "== dependencies: the release's floors against this venv, report only (#609) =="
    $pyproj = Join-Path $Rel "server\pyproject.toml"
    Invoke-Step "floor report" $Py @($Dfc, $pyproj, "--report")

    Write-Output "== the wheelhouse is the one that was hashed =="
    $sums = Join-Path $WH "SHA256SUMS"
    if (-not (Test-Path $sums)) { throw "the wheelhouse manifest is missing: $sums" }
    $n = 0
    foreach ($line in (Get-Content $sums)) {
        if (-not $line.Trim()) { continue }
        $parts = $line.Trim() -split '\s+', 2
        $name = $parts[1].TrimStart('*')
        $f = Join-Path $WH $name
        if (-not (Test-Path $f)) { throw "wheelhouse file missing: $name" }
        $h = (Get-FileHash $f -Algorithm SHA256).Hash.ToLower()
        if ($h -ne $parts[0].ToLower()) { throw "wheelhouse hash mismatch: $name" }
        $n++
    }
    Write-Output ("   " + $n + " wheelhouse file(s) verified")
    $pins = Join-Path $WH "pins.txt"
    Invoke-Step "pip dry run" $Py @("-m", "pip", "install", "--dry-run", "--no-index", "--find-links", $WH, "-r", $pins)
    $freeze = Invoke-DeployNative -FilePath $Py -ArgumentList @("-m", "pip", "freeze", "--all")
    if ($freeze.ExitCode -ne 0) { throw "pip freeze failed (exit $($freeze.ExitCode))" }
    $freeze.Lines | Set-Content -Path (Join-Path $Root "deps_before_$Ver.txt") -Encoding ascii
    Write-Output ("   the venv before the install is recorded in deps_before_$Ver.txt (" + @($freeze.Lines).Count + " lines)")

    Write-Output "== GATE: restart only at a frame boundary, with the run armed to resume =="
    Invoke-Step "restart_gate" $Py @((Join-Path $Root "restart_gate.py"))

    Write-Output "== stop the task, then kill the tree by the PID THAT HOLDS THE PORT =="
    $before = $null
    try { $before = (Get-NetTCPConnection -LocalPort 8800 -State Listen -ErrorAction Stop).OwningProcess } catch { }
    Write-Output ("   listener before: " + $(if ($before) { $before } else { "none" }))
    try { Stop-ScheduledTask -TaskName 'AstroDeck' } catch { Write-Output "   task stop: $_" }
    Start-Sleep -Seconds 2
    if ($before) {
        foreach ($pid8800 in @($before)) {
            Write-Output ("   killing listener PID " + $pid8800)
            try { Stop-Process -Id $pid8800 -Force -ErrorAction Stop } catch { Write-Output "   already gone" }
        }
    }
    $procs = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'astrodeck' }
    foreach ($p in $procs) {
        Write-Output ("   killing PID " + $p.ProcessId)
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { Write-Output "   already gone" }
    }
    Start-Sleep -Seconds 3
    $still = $null
    try { $still = (Get-NetTCPConnection -LocalPort 8800 -State Listen -ErrorAction Stop).OwningProcess } catch { }
    if ($still) { throw "port 8800 is still held by PID $still - the old server survived" }
    Write-Output "   port 8800 is free"

    Write-Output "== install the release's dependency pins offline, with the server stopped (#609) =="
    try {
        Invoke-Step "pip install" $Py @("-m", "pip", "install", "--no-index", "--find-links", $WH, "-r", $pins)
        Invoke-Step "pip check" $Py @("-m", "pip", "check")
        Invoke-Step "floor gate" $Py @($Dfc, $pyproj)
    } catch {
        Write-Output ("   dependency step FAILED: " + $_ + " - restarting the previous release (pointer not flipped)")
        Start-ScheduledTask -TaskName 'AstroDeck'
        throw
    }

    Write-Output "== flip the pointer, keeping the rollback =="
    $cur = Join-Path $Root "current"
    if (Test-Path $cur) { Copy-Item $cur (Join-Path $Root "previous") -Force }
    Set-Content -Path $cur -Value $Ver -Encoding ascii -NoNewline
    Write-Output ("   current -> " + (Get-Content $cur) + "   previous -> " + (Get-Content (Join-Path $Root "previous")))

    Write-Output "== start (the SUPERVISOR reads 'current' once at startup) =="
    Start-ScheduledTask -TaskName 'AstroDeck'
    Start-Sleep -Seconds 25
    $h = $null
    for ($i = 0; $i -lt 12; $i++) {
        $r = Invoke-DeployNative -FilePath "curl.exe" -ArgumentList @("-s", "--max-time", "10", "http://127.0.0.1:8800/healthz")
        $h = ($r.Lines -join "")
        if ($h -match '"ok"') { break }
        Start-Sleep -Seconds 5
    }
    Write-Output ("   healthz: " + $h)
    if ($h -notmatch '0\.3\.40') { throw "the running server is not 0.3.40: $h" }
    $after = (Get-NetTCPConnection -LocalPort 8800 -State Listen).OwningProcess
    Write-Output ("   listener after: " + $after)
    if ($before -and ($after -eq $before)) { throw "the listener PID did not change - this is the old process" }

    Write-Output "== what the running server is actually serving =="
    $r = Invoke-DeployNative -FilePath "curl.exe" -ArgumentList @("-s", "--max-time", "15", "http://127.0.0.1:8800/")
    $idx = ($r.Lines -join [char]10)
    if ($idx -match 'assets/(index-[A-Za-z0-9_-]+\.js)') { Write-Output ("   served asset: " + $Matches[1]) } else { throw "could not read the served asset" }
    Write-Output ("DEPLOY_0339_DONE " + (Get-Date -Format o) + " (auto-resume is watched separately)")
}
