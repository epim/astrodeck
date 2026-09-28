# Stage 0.3.35 on the astrotown rig. Run ON the rig (ssh session).
#
# THIS DEPLOY RESTARTS THE SERVER MID-RUN ON PURPOSE (owner, 2026-09-27 23:29:
# "Deploy and confirm it auto resumes"). An ordinary deploy refuses under any
# running sequence (rig_precheck.py). This one runs restart_gate.py instead: it
# allows a run whose session is armed for auto-resume, still refuses a slew, a
# polar alignment, a loop, Bahtinov or a live stack, and waits for the next
# saved frame so the restart loses the least exposure. The run is NOT aborted:
# an abort disarms auto-resume, which is the very thing under test.
#
# What 0.3.35 carries on top of 0.3.34: the AstroFlows mosaic slices S0-S3 and
# the hardening rounds H1-H3 (#189 and the #147-#353 issues they closed), plus
# #399: the Monitor's LAST FRAME tile re-reads the snapshot on every reconnect
# and every saved frame and shows the newer of the live and snapshot frames.
# Built from a clean worktree of release/0.3.35 (812fcf9e plus the #399 fix and
# the version bump). No native or vendor changes since 0.3.34, so the 0.3.34
# wheel on the box stays.
#
# A gate here is a marker that exists ONLY in the new code: it says the bytes
# on the box are the bytes that were built, which no version string can.
$ErrorActionPreference = "Stop"
$Root = "C:\Users\James\AstroDeck"
$Ver  = "0.3.35"
$Prev = "0.3.34"
$Rel  = Join-Path $Root "releases\$Ver"
$Py   = Join-Path $Root "venv\Scripts\python.exe"
$Wheel = Join-Path $Root "astrodeck_native-0.1.0-cp311-abi3-win_amd64.whl"

Write-Output "== the tarball is the one that was hashed =="
$tarball = Join-Path $Root "astrodeck-$Ver.tar.gz"
$want = ((Get-Content (Join-Path $Root "astrodeck-$Ver.tar.gz.sha256") -Raw) -split '\s+')[0].ToLower()
$have = (Get-FileHash $tarball -Algorithm SHA256).Hash.ToLower()
if ($want -ne $have) { throw "tarball sha256 mismatch: have $have want $want" }
Write-Output ("   sha256 ok: " + $have.Substring(0, 16) + "...")
if (-not (Test-Path $Wheel)) { throw "the native wheel is not on the box: $Wheel" }
if (-not (Test-Path (Join-Path $Root "releases\$Prev\server\astrodeck\vendor"))) { throw "the $Prev vendor tree is not on the box - carry-forward source missing" }

Write-Output "== extract and verify BEFORE anything is stopped =="
$staging = Join-Path $Root "stage$Ver"
if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
New-Item -ItemType Directory -Force $staging | Out-Null
tar -xzf $tarball -C $staging
if (Test-Path $Rel) { Remove-Item -Recurse -Force $Rel }
New-Item -ItemType Directory -Force (Join-Path $Root "releases") | Out-Null
Move-Item (Join-Path $staging "astrodeck-$Ver") $Rel
Remove-Item -Recurse -Force $staging

Write-Output "== carry the manifest-pinned SDK libraries forward with the updater's own code =="
$src = Join-Path $Root "releases\$Prev\server\astrodeck\vendor"
$dst = Join-Path $Rel "server\astrodeck\vendor"
Push-Location (Join-Path $Rel "server")
& $Py -m astrodeck.update.stage carry-forward $src $dst
$carry = $LASTEXITCODE
Pop-Location
if ($carry -ne 0) { throw "carry-forward failed (exit $carry)" }
if (-not (Test-Path (Join-Path $dst "playerone\PlayerOneCamera.dll"))) { throw "PlayerOneCamera.dll did not arrive in the staged tree" }
Write-Output "   PlayerOneCamera.dll present in the staged tree"

Write-Output "== GATE: the staged vendor tree verifies against the shipped manifest =="
Push-Location (Join-Path $Rel "server")
& $Py -m astrodeck.devices.vendor_verify
$gate = $LASTEXITCODE
Pop-Location
if ($gate -ne 0) { throw "vendor integrity check FAILED on the staged tree - not flipping" }

Write-Output "== refresh webui from ui/dist (a stale bundled copy shadows it) =="
$webui = Join-Path $Rel "server\astrodeck\webui"
if (Test-Path $webui) { Remove-Item -Recurse -Force $webui }
New-Item -ItemType Directory -Force $webui | Out-Null
Copy-Item -Recurse -Force (Join-Path $Rel "ui\dist\*") $webui
if (-not (Test-Path (Join-Path $webui "bootstrap.js"))) { throw "webui/bootstrap.js missing" }

Write-Output "== verify the STAGED tree is what we think it is =="
$staged = Select-String -Path (Join-Path $Rel "server\astrodeck\__init__.py") -Pattern '__version__'
Write-Output ("   staged: " + $staged.Line.Trim())
if ($staged.Line -notmatch '0\.3\.35') { throw "staged tree is not 0.3.35" }
$checks = @(
    # --- new in 0.3.35 -----------------------------------------------------
    @{ File = 'server/astrodeck/flows/models.py'; Pattern = 'FLOW_LOOP_REFUSAL'; Name = 'S0 #149: a flow lane runs once' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_idle_park_hold'; Name = 'S0 #165: an idle mount is park-held on its own clock' },
    @{ File = 'server/astrodeck/flows/identity.py'; Pattern = 'def step_id'; Name = 'S1: deterministic flow identity' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = 'IDLE_STOP_RETRY_S'; Name = 'H1: the idle-stop retry on its own clock' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_hold_watch'; Name = 'H1 #203/#205: the cloud hold watches the mount' },
    @{ File = 'server/astrodeck/guide/native.py'; Pattern = '_relock_radius_px'; Name = 'H1 #204: re-lock measured against the lock' },
    @{ File = 'server/astrodeck/sequence/resume_arm.py'; Pattern = 'stop_recovery'; Name = 'H2 #220: Abort stops the recovery ladder' },
    @{ File = 'server/astrodeck/solve/light.py'; Pattern = 'NOT_A_NUMBER_WHY'; Name = 'H3 #251: a failed solve is classified by its light' },
    @{ File = 'server/astrodeck/api/redact.py'; Pattern = 'is_site_derived'; Name = 'H3/S2: site-timed lines withheld from a viewer' },
    @{ File = 'server/astrodeck/sequence/group_rules.py'; Pattern = 'VisitBound'; Name = 'S2: the group driver rules' },
    @{ File = 'server/astrodeck/flows/rig.py'; Pattern = 'class '; Name = 'S3: rig facts for the compile' },
    # --- carried from 0.3.34 -------------------------------------------------
    @{ File = 'server/astrodeck/site_gate.py'; Pattern = 'def site_is_set'; Name = '#24: one predicate for "is there a real site"' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_MAX_GUIDING_RECOVERIES'; Name = '#72: guiding recovery is bounded' },
    @{ File = 'server/astrodeck/api/app.py'; Pattern = '_refuse_if_camera_owned'; Name = 'one camera-ownership refusal on every exposure route' },
    @{ File = 'server/astrodeck/dawn_park.py'; Pattern = 'issue #35'; Name = '#35: dawn warms the camera even with a session armed' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_enforce_flip_owed'; Name = 'F3: no exposure while a flip is owed' }
)
foreach ($c in $checks) {
    $hit = Select-String -Path (Join-Path $Rel $c.File) -Pattern $c.Pattern -SimpleMatch
    if (-not $hit) { throw ($c.Name + " is NOT in the staged tree (" + $c.File + ")") }
    Write-Output ("   " + $c.Name + ": present")
}
$ui = Get-ChildItem (Join-Path $webui "assets") -Filter "index-*.js" | Select-Object -First 1
if (-not (Select-String -Path $ui.FullName -Pattern '0\.3\.35' -Quiet)) { throw "the built UI does not carry the 0.3.35 version stamp" }
Write-Output "   UI bundle carries 0.3.35"
$uiChecks = @(
    @{ Pattern = 'monitor-resume-recovering'; Name = 'H3 #246: the Monitor draws the recovery ladder' },
    @{ Pattern = 'monitor-hold-deferred'; Name = 'H3 #244: the Monitor draws a deferred hold' },
    @{ Pattern = 'timeout(8e3)'; Name = '#399: the Monitor re-reads the snapshot (8 s bound)' },
    @{ Pattern = 'link-next-ui'; Name = 'classic root with the door to #/next' }
)
foreach ($u in $uiChecks) {
    $hit = Get-ChildItem (Join-Path $webui "assets") -Filter "*.js" |
        Where-Object { Select-String -Path $_.FullName -Pattern $u.Pattern -SimpleMatch -Quiet } |
        Select-Object -First 1
    if (-not $hit) { throw ("the built UI is missing " + $u.Name + " (" + $u.Pattern + ")") }
    Write-Output ("   " + $u.Name + " in " + $hit.Name)
}

Write-Output "== the native wheel stays in the venv (idempotent) =="
& $Py -m pip install --quiet --force-reinstall --no-deps $Wheel
if ($LASTEXITCODE -ne 0) { throw "wheel install failed (exit $LASTEXITCODE)" }
& $Py (Join-Path $Root "check_wheel.py")
if ($LASTEXITCODE -ne 0) { throw "the installed astrodeck_native has no peek_next (exit $LASTEXITCODE)" }

Write-Output "== GATE: restart only at a frame boundary, with the run armed to resume =="
& $Py (Join-Path $Root "restart_gate.py")
if ($LASTEXITCODE -ne 0) { throw "restart_gate refused (exit $LASTEXITCODE) - not restarting" }

Write-Output "== stop the task, then kill the tree by the PID THAT HOLDS THE PORT =="
$before = $null
try { $before = (Get-NetTCPConnection -LocalPort 8800 -State Listen -ErrorAction Stop).OwningProcess } catch { }
Write-Output ("   listener before: " + $(if ($before) { $before } else { "none" }))
try { Stop-ScheduledTask -TaskName 'AstroDeck' } catch { Write-Output "task stop: $_" }
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
    try { $h = (& curl.exe -s --max-time 10 "http://127.0.0.1:8800/healthz"); if ($h) { break } } catch { }
    Start-Sleep -Seconds 5
}
Write-Output ("   healthz: " + $h)
if ($h -notmatch '0\.3\.35') { throw "the running server is not 0.3.35: $h" }
$after = (Get-NetTCPConnection -LocalPort 8800 -State Listen).OwningProcess
Write-Output ("   listener after: " + $after)
if ($before -and ($after -eq $before)) { throw "the listener PID did not change - this is the old process" }

Write-Output "== what the running server is actually serving =="
$idx = ((& curl.exe -s --max-time 15 "http://127.0.0.1:8800/") -join [char]10)
if ($idx -match 'assets/(index-[A-Za-z0-9_-]+\.js)') { Write-Output ("   served asset: " + $Matches[1]) } else { throw "could not read the served asset" }
Write-Output "DEPLOY_0335_DONE (auto-resume is watched separately)"
