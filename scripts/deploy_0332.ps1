# Deploy 0.3.32 to the astrotown rig. Run ON the rig (elevated ssh session).
#
# What 0.3.32 carries, on top of 0.3.31: the classic app's unified sky atlas
# and refined dome (dc09a27c), and the web root opening on the CLASSIC app
# again - #/ and every legacy view name mount classic, the new six-hub UI
# stays at its hub routes and at #/next, and the classic Settings view links
# to it (93f33ab9). No server behaviour changes. previous -> 0.3.31 keeps the
# rollback. Deployed at night on the user's word after the rig session stood
# the clouded-out run down; the cooler stays cold for the darks that follow.
#
# All 0.3.31 gates stay.
$ErrorActionPreference = "Stop"
$Root = "C:\Users\James\AstroDeck"
$Ver  = "0.3.32"
$Prev = "0.3.31"
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

Write-Output "== stop the task, then kill the tree by EXPLICIT PID =="
try { Stop-ScheduledTask -TaskName 'AstroDeck' } catch { Write-Output "task stop: $_" }
Start-Sleep -Seconds 2
$procs = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'astrodeck' }
foreach ($p in $procs) {
    Write-Output ("   killing PID " + $p.ProcessId)
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { Write-Output "   already gone" }
}
Start-Sleep -Seconds 3
$left = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'astrodeck' }
Write-Output ("   survivors: " + ($left | Measure-Object).Count)

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
if ($staged.Line -notmatch '0\.3\.32') { throw "staged tree is not 0.3.32" }
$checks = @(
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = 'FLIP_CALIBRATE_TIMEOUT_S'; Name = 'flip bound holds a calibration (0.3.28)' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_shift_temp_comp_reference'; Name = 'temp comp re-anchors on a filter offset' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = 'dither_settle_fail_limit'; Name = 'dither settle-fail hold' },
    @{ File = 'server/astrodeck/focus/tempcomp.py'; Pattern = 'def decide'; Name = 'temperature compensation decide()' },
    @{ File = 'server/astrodeck/dew.py'; Pattern = 'class DewController'; Name = 'dew-margin heater loop' },
    @{ File = 'server/astrodeck/api/redact.py'; Pattern = '_strip_dew'; Name = 'dew readings redacted for a viewer' },
    @{ File = 'server/astrodeck/api/app.py'; Pattern = '_refuse_if_camera_owned'; Name = 'one camera-ownership refusal on every exposure route' },
    @{ File = 'server/astrodeck/api/app.py'; Pattern = 'allow_inf_nan=False'; Name = 'NaN slew rate refused' },
    @{ File = 'server/astrodeck/catalog/ephemeris/elements.py'; Pattern = 'def merge_satellite_rows'; Name = 'ephemeris: a bad fetch cannot shrink a good cache' },
    @{ File = 'server/astrodeck/power_guard.py'; Pattern = 'def annotate'; Name = 'per-port run protection' },
    @{ File = 'server/astrodeck/planning.py'; Pattern = '/api/planning'; Name = 'planning store route' },
    @{ File = 'server/astrodeck/imaging/video_routes.py'; Pattern = '/api/capture/video'; Name = 'SER video routes' },
    @{ File = 'server/astrodeck/mount_offset.py'; Pattern = 'def parse_nudge'; Name = 'mount nudge bounds' },
    @{ File = 'server/astrodeck/guide/native.py'; Pattern = 'relock_jump_arcsec'; Name = 'guider stops itself on re-lock displacement' },
    @{ File = 'server/astrodeck/config.py'; Pattern = 'relock_arcsec_limit'; Name = 'guide re-lock limits in config' },
    @{ File = 'server/astrodeck/dawn_park.py'; Pattern = 'getattr(eng, "paused", False)'; Name = 'dawn park ignores a PAUSED run''s veto' },
    @{ File = 'server/astrodeck/polar/native.py'; Pattern = 'MAX_ROTATION_DISAGREEMENT_DEG'; Name = 'polar: the rotation-agreement guard (0.3.27)' },
    @{ File = 'server/astrodeck/hub.py'; Pattern = 'async def pier_side_now'; Name = 'flip checks the pier side before paying (0.3.28)' },
    @{ File = 'server/astrodeck/dawn_park.py'; Pattern = '_release_cooler'; Name = 'dawn releases the cooler' },
    @{ File = 'server/astrodeck/flows/to_plan.py'; Pattern = '"carried"'; Name = 'compiler notes carry what is honoured' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_enforce_flip_owed'; Name = 'F3: no exposure while a flip is owed' },
    @{ File = 'server/astrodeck/dawn_park.py'; Pattern = '_check_unattended_guiding'; Name = 'F4: unattended guiding is stopped' },
    @{ File = 'server/astrodeck/catalog/coords.py'; Pattern = 'pier_side_for_hour_angle'; Name = 'F6: pier side from hour angle' },
    @{ File = 'server/astrodeck/devices/backends/zwo_am5.py'; Pattern = 'reports_destination_pier_side = True'; Name = 'F6: the AM5 reports the destination pier side' },
    @{ File = 'server/astrodeck/hub.py'; Pattern = 'PIER_SIDE_STALE_S'; Name = 'F6: pier side cached with an age' },
    @{ File = 'server/astrodeck/config.py'; Pattern = 'unattended_guide_min'; Name = 'F3/F4 config keys' }
)
foreach ($c in $checks) {
    $hit = Select-String -Path (Join-Path $Rel $c.File) -Pattern $c.Pattern -SimpleMatch
    if (-not $hit) { throw ($c.Name + " is NOT in the staged tree (" + $c.File + ")") }
    Write-Output ("   " + $c.Name + ": present")
}
$ui = Get-ChildItem (Join-Path $webui "assets") -Filter "index-*.js" | Select-Object -First 1
if (-not (Select-String -Path $ui.FullName -Pattern '0\.3\.32' -Quiet)) { throw "the built UI does not carry the 0.3.32 version stamp" }
Write-Output "   UI bundle carries 0.3.32"
$atlas = Get-ChildItem (Join-Path $webui "assets") -Filter "*.js" | Where-Object { Select-String -Path $_.FullName -Pattern 'atlas-canvas' -SimpleMatch -Quiet } | Select-Object -First 1
if (-not $atlas) { throw "the built UI carries no ATLAS mode (atlas-canvas marker missing)" }
Write-Output ("   ATLAS mode in " + $atlas.Name)
$markers = Get-ChildItem (Join-Path $webui "assets") -Filter "*.js" | Where-Object { Select-String -Path $_.FullName -Pattern 'atlas-markers' -SimpleMatch -Quiet } | Select-Object -First 1
if (-not $markers) { throw "the built UI carries no target markers on the atlas (atlas-markers missing)" }
Write-Output ("   targets on the atlas in " + $markers.Name)
$door = Get-ChildItem (Join-Path $webui "assets") -Filter "*.js" | Where-Object { Select-String -Path $_.FullName -Pattern 'link-next-ui' -SimpleMatch -Quiet } | Select-Object -First 1
if (-not $door) { throw "the built UI has no door from classic to the new UI (root switch not in this bundle)" }
Write-Output ("   classic root with the door to #/next in " + $door.Name)

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
if ($h -notmatch '0\.3\.32') { throw "the running server is not 0.3.32: $h" }

Write-Output "== what the running server is actually serving =="
$idx = ((& curl.exe -s --max-time 15 "http://127.0.0.1:8800/") -join [char]10)
if ($idx -match 'assets/(index-[A-Za-z0-9_-]+\.js)') { Write-Output ("   served asset: " + $Matches[1]) } else { throw "could not read the served asset" }
$bs = (& curl.exe -s -o NUL -w "%{http_code}" --max-time 10 "http://127.0.0.1:8800/bootstrap.js")
if ($bs -ne "200") { throw "bootstrap.js not served ($bs)" }
Write-Output "   bootstrap.js served"

Write-Output "== rig status on the new build (devices must all be back, incl. the Player One camera) =="
Start-Sleep -Seconds 20
& $Py (Join-Path $Root "rig_precheck.py") --report
Write-Output "NOTE: the dawn-park daemon restarted and will park an idle unparked mount within a minute; if the mount was rotated to shade the PC, rotate it again AFTER this (rotate2.py) - the daemon latches after its first park and leaves the operator's unpark alone."
Write-Output "DEPLOY_0332_DONE"
