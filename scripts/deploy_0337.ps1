# Stage 0.3.37 on the astrotown rig. Run ON the rig, detached (see
# deploy_common.ps1 for the launch line), beside deploy_common.ps1.
#
# A DAYTIME deploy (owner, 2026-09-29 09:42: "make any deployments necessary.
# Make sure that tonight we'll grab 7331 data ... as well as the mosaic"). No
# run is in flight; restart_gate.py still runs and answers GO at once for an
# idle rig. The NGC 1499 mosaic session stays dormant and ARMED across the
# restart (owner: "don't disarm the mosaic").
#
# What 0.3.37 carries on top of 0.3.36: S7 item 1 and the S5/S6 carry-over
# (e477de68, 39689e94), plus two mosaic hotfixes on release/0.3.37 (9433343a):
# a rotation solves through the solve filter (#531), and a stopped-short
# rotation inside the group's angle tolerance is not a failed visit (#526,
# #534). No native or vendor change, so the wheel on the box stays.
#
# A gate here is a marker that exists ONLY in the new code: it says the bytes
# on the box are the bytes that were built, which no version string can.
$ErrorActionPreference = "Stop"
$Root = "C:\Users\James\AstroDeck"
. (Join-Path $Root "deploy_common.ps1")
$Ver  = "0.3.37"
$Prev = "0.3.36"
$VendorFrom = "0.3.34"
$Rel  = Join-Path $Root "releases\$Ver"
$Py   = Join-Path $Root "venv\Scripts\python.exe"
$Wheel = Join-Path $Root "astrodeck_native-0.1.0-cp311-abi3-win_amd64.whl"

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
    if ($staged.Line -notmatch '0\.3\.37') { throw "staged tree is not 0.3.37" }
    $checks = @(
        # --- new in 0.3.37 (absent from release/0.3.36) ---------------------
        @{ File = 'server/astrodeck/hub.py'; Pattern = '#222, #531'; Name = 'hotfix #531: a rotation solves through the solve filter' },
        @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_hop_angle_within'; Name = 'hotfix #526/#534: a short rotation inside the tolerance shoots' },
        @{ File = 'server/astrodeck/flows/progress.py'; Pattern = 'plan_saved_ts'; Name = 'S7 #473: the progress route says what an armed session replays' },
        @{ File = 'server/astrodeck/sequence/report.py'; Pattern = '_retry_final'; Name = 'S7 #477: the final report retry is off the event loop' },
        # --- carried from 0.3.36 ------------------------------------------------
        @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_flip_retry_past_s'; Name = 'S5 #366: the flip retry waits past the crossing' },
        @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_completion_owed'; Name = 'S5 #373: the frame that fires a jump is banked first' },
        @{ File = 'server/astrodeck/sequence/group_rules.py'; Pattern = 'VisitBound'; Name = 'S2: the group driver rules' },
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
    if (-not (Select-String -Path $ui.FullName -Pattern '0\.3\.37' -Quiet)) { throw "the built UI does not carry the 0.3.37 version stamp" }
    Write-Output "   UI bundle carries 0.3.37"
    $uiChecks = @(
        @{ Pattern = 'will replay the version from'; Name = 'S7 #473: the editors say what an armed session replays' },
        @{ Pattern = 'flow-loop-arc'; Name = 'S4: the panel loop arc on the canvas (carried)' },
        @{ Pattern = 'flow-node-frame'; Name = 'S4: the TARGET card opens the Target modal' },
        @{ Pattern = 'framing-order-note'; Name = 'S5 #412: the Target modal order note' },
        @{ Pattern = 'hops not yet costed'; Name = 'S5 5.10: the phone readouts' },
        @{ Pattern = 'SEND TO FLOW WIZARD'; Name = 'S6 #196: the doors converge on the wizard' },
        @{ Pattern = 'relay_gap'; Name = 'S5 #399: the client re-reads on a relay gap' },
        @{ Pattern = 'timeout(8e3)'; Name = '#399: the classic Monitor re-read (carried)' },
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
    if ($h -notmatch '0\.3\.37') { throw "the running server is not 0.3.37: $h" }
    $after = (Get-NetTCPConnection -LocalPort 8800 -State Listen).OwningProcess
    Write-Output ("   listener after: " + $after)
    if ($before -and ($after -eq $before)) { throw "the listener PID did not change - this is the old process" }

    Write-Output "== what the running server is actually serving =="
    $r = Invoke-DeployNative -FilePath "curl.exe" -ArgumentList @("-s", "--max-time", "15", "http://127.0.0.1:8800/")
    $idx = ($r.Lines -join [char]10)
    if ($idx -match 'assets/(index-[A-Za-z0-9_-]+\.js)') { Write-Output ("   served asset: " + $Matches[1]) } else { throw "could not read the served asset" }
    Write-Output ("DEPLOY_0337_DONE " + (Get-Date -Format o) + " (auto-resume is watched separately)")
}
