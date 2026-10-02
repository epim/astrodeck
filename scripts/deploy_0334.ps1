# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
# Stage 0.3.34 on the astrotown rig. Run ON the rig (ssh session).
#
# What 0.3.34 carries, on top of 0.3.33: seventy-eight commits, because the rig
# has been on 0.3.33 since 2026-09-19 while the issue backlog was worked. The
# server-side changes that matter to a night:
#
#   #24  no site, no flip, no polar alignment, no meridian numbers - and the
#        unset site is never written into the mount's firmware
#   #17  a TEC thirty degrees below ambient is not "at ambient", so the warm
#        ramp holds instead of cutting the cooler on a stalled sensor
#   #16  a camera that reports connected while producing nothing is dropped
#   #44  a run waits for the camera lane instead of losing three steps to it
#   #20  a whole-rig connect refuses mid-night unless forced
#   #72  guiding recovery is bounded and says so when it stands down
#   #15  a guide preview never takes the sensor from a running loop
#   #23  a saved frame records which way the telescope was facing (CENTAZ)
#   #113 two spellings of one Windows directory are one directory
#   #117 a starting run is no longer served as idle beside running true
#   #97/#109 bookkeeping writes and preview stats leave the event loop
#   #18/#111 a calibration walked far from square, or at a far declination,
#        is refused rather than reused
#
# plus the whole photosphere pass in the new UI (#62 through #107) and the
# guided first-run experience. previous -> 0.3.33 keeps the rollback.
#
# Built from a clean worktree of 3d6d68eb (trap 7: a dirty tree hides type
# errors - this build found two, TS2341 in photosphereStability.test.ts, now
# fixed and filed as #126). Tarball sha256 begins 6ca8000220b2fd35.
#
# All the 0.3.33 gates stay, and each of this release's load-bearing changes
# adds one. A gate here is a marker that exists ONLY in the new code: it says
# the bytes on the box are the bytes that were built, which no version string
# can (traps 5 and 9).
$ErrorActionPreference = "Stop"
$Root = "C:\Users\James\AstroDeck"
$Ver  = "0.3.34"
$Prev = "0.3.33"
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
Write-Output "   native wheel present"
if (-not (Test-Path (Join-Path $Root "releases\$Prev\server\astrodeck\vendor"))) { throw "the $Prev vendor tree is not on the box - carry-forward source missing" }

Write-Output "== refuse to restart under anything in flight (sequence, polar, slew, loop, busy lane) =="
& $Py (Join-Path $Root "rig_precheck.py")
if ($LASTEXITCODE -ne 0) { throw "rig is not idle (rig_precheck exit $LASTEXITCODE) - refusing to restart the server" }

Write-Output "== stop the task, then kill the tree by the PID THAT HOLDS THE PORT =="
# Win32_Process.CommandLine is empty under the account ssh lands in (#103), so
# a command-line match finds nothing and reports "survivors: 0" about its own
# empty query. The listener's owning PID is the one fact that cannot be empty.
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

Write-Output "== extract =="
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
if (-not (Test-Path (Join-Path $dst "playerone\PlayerOneCamera.dll"))) { throw "PlayerOneCamera.dll did not arrive in the staged tree - the imaging camera would be gone" }
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
Write-Output ("   webui index asset: " + ((Get-ChildItem (Join-Path $webui "assets") -Filter "index-*.js" | Select-Object -First 1).Name))
if (-not (Test-Path (Join-Path $webui "bootstrap.js"))) { throw "webui/bootstrap.js missing" }

Write-Output "== verify the STAGED tree is what we think it is =="
$staged = Select-String -Path (Join-Path $Rel "server\astrodeck\__init__.py") -Pattern '__version__'
Write-Output ("   staged: " + $staged.Line.Trim())
if ($staged.Line -notmatch '0\.3\.34') { throw "staged tree is not 0.3.34" }
$checks = @(
    # --- new in 0.3.34 -----------------------------------------------------
    @{ File = 'server/astrodeck/site_gate.py'; Pattern = 'def site_is_set'; Name = '#24: one predicate for "is there a real site"' },
    @{ File = 'server/astrodeck/polar/native.py'; Pattern = 'site_is_set'; Name = '#24: polar refuses the whole alignment with no site' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_flip_no_site_logged'; Name = '#24: no site, no flip, and it says so once' },
    @{ File = 'server/astrodeck/cooling.py'; Pattern = 'WARM_NOT_AMBIENT_C'; Name = '#17: a sensor far below ambient is not at ambient' },
    @{ File = 'server/astrodeck/hub.py'; Pattern = 'stalled_cold'; Name = '#17: the warm ramp holds rather than cutting a stalled TEC' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_camera_is_silent'; Name = '#16: a camera producing nothing is dropped' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_CAMERA_LANE_WAIT_S'; Name = '#44: a run waits for the camera lane' },
    @{ File = 'server/astrodeck/api/app.py'; Pattern = 'body.force'; Name = '#20: a whole-rig connect refuses mid-night unless forced' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_MAX_GUIDING_RECOVERIES'; Name = '#72: guiding recovery is bounded' },
    @{ File = 'server/astrodeck/hub.py'; Pattern = '_guider_is_guiding'; Name = '#15: a preview never takes the sensor from a running loop' },
    @{ File = 'server/astrodeck/imaging/fitsio.py'; Pattern = 'obj_az_deg'; Name = '#23: a frame records which way the scope faced' },
    @{ File = 'server/astrodeck/persist.py'; Pattern = 'def path_key'; Name = '#113: two spellings of one directory are one directory' },
    @{ File = 'server/astrodeck/api/app.py'; Pattern = '_sequence_envelope'; Name = '#117: a starting run is not served as idle' },
    # --- carried from 0.3.33 and earlier ------------------------------------
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = 'FLIP_CALIBRATE_TIMEOUT_S'; Name = 'flip bound holds a calibration (0.3.28)' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_shift_temp_comp_reference'; Name = 'temp comp re-anchors on a filter offset' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = 'dither_settle_fail_limit'; Name = 'dither settle-fail hold' },
    @{ File = 'server/astrodeck/dew.py'; Pattern = 'class DewController'; Name = 'dew-margin heater loop' },
    @{ File = 'server/astrodeck/api/redact.py'; Pattern = '_strip_dew'; Name = 'dew readings redacted for a viewer' },
    @{ File = 'server/astrodeck/api/app.py'; Pattern = '_refuse_if_camera_owned'; Name = 'one camera-ownership refusal on every exposure route' },
    @{ File = 'server/astrodeck/api/app.py'; Pattern = 'allow_inf_nan=False'; Name = 'NaN slew rate refused' },
    @{ File = 'server/astrodeck/power_guard.py'; Pattern = 'def annotate'; Name = 'per-port run protection' },
    @{ File = 'server/astrodeck/guide/native.py'; Pattern = 'relock_jump_arcsec'; Name = 'guider stops itself on re-lock displacement' },
    @{ File = 'server/astrodeck/dawn_park.py'; Pattern = 'getattr(eng, "paused", False)'; Name = 'dawn park ignores a PAUSED run''s veto' },
    @{ File = 'server/astrodeck/polar/native.py'; Pattern = 'MAX_ROTATION_DISAGREEMENT_DEG'; Name = 'polar: the rotation-agreement guard (0.3.27)' },
    @{ File = 'server/astrodeck/hub.py'; Pattern = 'async def pier_side_now'; Name = 'flip checks the pier side before paying (0.3.28)' },
    @{ File = 'server/astrodeck/dawn_park.py'; Pattern = '_release_cooler'; Name = 'dawn releases the cooler' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_enforce_flip_owed'; Name = 'F3: no exposure while a flip is owed' },
    @{ File = 'server/astrodeck/dawn_park.py'; Pattern = '_check_unattended_guiding'; Name = 'F4: unattended guiding is stopped' },
    @{ File = 'server/astrodeck/catalog/coords.py'; Pattern = 'pier_side_for_hour_angle'; Name = 'F6: pier side from hour angle' },
    @{ File = 'server/astrodeck/hub.py'; Pattern = 'PIER_SIDE_STALE_S'; Name = 'F6: pier side cached with an age' },
    @{ File = 'server/astrodeck/dawn_park.py'; Pattern = 'issue #35'; Name = '#35: dawn warms the camera even with a session armed' }
)
foreach ($c in $checks) {
    $hit = Select-String -Path (Join-Path $Rel $c.File) -Pattern $c.Pattern -SimpleMatch
    if (-not $hit) { throw ($c.Name + " is NOT in the staged tree (" + $c.File + ")") }
    Write-Output ("   " + $c.Name + ": present")
}
$ui = Get-ChildItem (Join-Path $webui "assets") -Filter "index-*.js" | Select-Object -First 1
if (-not (Select-String -Path $ui.FullName -Pattern '0\.3\.34' -Quiet)) { throw "the built UI does not carry the 0.3.34 version stamp" }
Write-Output "   UI bundle carries 0.3.34"
$uiChecks = @(
    @{ Pattern = 'atlas-canvas'; Name = 'ATLAS mode' },
    @{ Pattern = 'atlas-markers'; Name = 'targets on the atlas' },
    @{ Pattern = 'link-next-ui'; Name = 'classic root with the door to #/next' },
    @{ Pattern = 'below-overhead'; Name = '#76: a no-pose record says which source was missing' }
)
foreach ($u in $uiChecks) {
    $hit = Get-ChildItem (Join-Path $webui "assets") -Filter "*.js" |
        Where-Object { Select-String -Path $_.FullName -Pattern $u.Pattern -SimpleMatch -Quiet } |
        Select-Object -First 1
    if (-not $hit) { throw ("the built UI is missing " + $u.Name + " (" + $u.Pattern + ")") }
    Write-Output ("   " + $u.Name + " in " + $hit.Name)
}

Write-Output "== the native wheel with peek_next stays in the venv (idempotent) =="
& $Py -m pip install --quiet --force-reinstall --no-deps $Wheel
if ($LASTEXITCODE -ne 0) { throw "wheel install failed (exit $LASTEXITCODE)" }
& $Py (Join-Path $Root "check_wheel.py")
if ($LASTEXITCODE -ne 0) { throw "the installed astrodeck_native has no peek_next (exit $LASTEXITCODE)" }

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
if ($h -notmatch '0\.3\.34') { throw "the running server is not 0.3.34: $h" }
$after = (Get-NetTCPConnection -LocalPort 8800 -State Listen).OwningProcess
Write-Output ("   listener after: " + $after)
if ($before -and ($after -eq $before)) { throw "the listener PID did not change - this is the old process" }

Write-Output "== what the running server is actually serving =="
$idx = ((& curl.exe -s --max-time 15 "http://127.0.0.1:8800/") -join [char]10)
if ($idx -match 'assets/(index-[A-Za-z0-9_-]+\.js)') { Write-Output ("   served asset: " + $Matches[1]) } else { throw "could not read the served asset" }
$bs = (& curl.exe -s -o NUL -w "%{http_code}" --max-time 10 "http://127.0.0.1:8800/bootstrap.js")
if ($bs -ne "200") { throw "bootstrap.js not served ($bs)" }
Write-Output "   bootstrap.js served"

Write-Output "== rig status on the new build (devices must all be back, incl. the Player One camera) =="
Start-Sleep -Seconds 20
& $Py (Join-Path $Root "rig_precheck.py") --report
Write-Output "NOTE: the dawn-park daemon restarted and will park an idle unparked mount within a minute."
Write-Output "DEPLOY_0334_DONE"
