# Deploy 0.3.28 to the astrotown rig. Run ON the rig (elevated ssh session).
#
# What 0.3.28 carries, on top of 0.3.27's polar guard: the meridian flip's
# bound now contains the calibration the flip itself forces. It was 420 s
# covering a re-slew, two plate solves AND a fresh calibration whose own
# budget is 600 s, which aborted a 105-frame run at 34 frames on 2026-09-09
# and was marginal even on a clear night. It is now composed: 540 s of base
# plus either the 180 s guide-start allowance or the 660 s calibration one,
# and the extension is bought only by asking the guider, after the base
# expires, whether the flip's own discard left it needing a walk.
#
# All 0.3.27 gates stay, and the wheel and config steps remain idempotent
# no-ops that re-prove themselves.
$ErrorActionPreference = "Stop"
$Root = "C:\Users\James\AstroDeck"
$Ver  = "0.3.28"
$Prev = "0.3.27"
$Rel  = Join-Path $Root "releases\$Ver"
$Py   = Join-Path $Root "venv\Scripts\python.exe"
$Wheel = Join-Path $Root "astrodeck_native-0.1.0-cp311-abi3-win_amd64.whl"

Write-Output "== the tarball is the one the release signed =="
$tarball = Join-Path $Root "astrodeck-$Ver.tar.gz"
$want = ((Get-Content (Join-Path $Root "astrodeck-$Ver.tar.gz.sha256") -Raw) -split '\s+')[0].ToLower()
$have = (Get-FileHash $tarball -Algorithm SHA256).Hash.ToLower()
if ($want -ne $have) { throw "tarball sha256 mismatch: have $have want $want" }
Write-Output ("   sha256 ok: " + $have.Substring(0, 16) + "...")
if (-not (Test-Path $Wheel)) { throw "the native wheel is not on the box: $Wheel" }
Write-Output "   native wheel present"

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
if ($staged.Line -notmatch '0\.3\.28') { throw "staged tree is not 0.3.28" }
$checks = @(
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = 'FLIP_CALIBRATE_TIMEOUT_S'; Name = 'flip bound holds a calibration' },
    @{ File = 'server/astrodeck/sequence/engine.py'; Pattern = '_flip_bounded'; Name = 'flip bound is split in time' },
    @{ File = 'server/astrodeck/polar/native.py'; Pattern = 'MAX_ROTATION_DISAGREEMENT_DEG'; Name = 'polar: the rotation-agreement guard' },
    @{ File = 'server/astrodeck/polar/native.py'; Pattern = '_SETTLE_AFTER_SLEW_S'; Name = 'polar: settle after each slew' },
    @{ File = 'server/astrodeck/focus/window.py'; Pattern = 'def measure_window'; Name = 'autofocus measures a centred window' },
    @{ File = 'server/astrodeck/focus/approach.py'; Pattern = 'async def approach'; Name = 'one-side approach rule' },
    @{ File = 'server/astrodeck/hub.py'; Pattern = 'async def pier_side_now'; Name = 'flip checks the pier side before paying' },
    @{ File = 'server/astrodeck/dawn_park.py'; Pattern = '_release_cooler'; Name = 'dawn releases the cooler' }
)
foreach ($c in $checks) {
    $hit = Select-String -Path (Join-Path $Rel $c.File) -Pattern $c.Pattern -SimpleMatch
    if (-not $hit) { throw ($c.Name + " is NOT in the staged tree (" + $c.File + ")") }
    Write-Output ("   " + $c.Name + ": present")
}

Write-Output "== the native wheel with peek_next goes into the venv while nothing has it loaded =="
& $Py -m pip install --quiet --force-reinstall --no-deps $Wheel
if ($LASTEXITCODE -ne 0) { throw "wheel install failed (exit $LASTEXITCODE)" }
& $Py (Join-Path $Root "check_wheel.py")
if ($LASTEXITCODE -ne 0) { throw "the installed astrodeck_native has no peek_next (exit $LASTEXITCODE)" }

Write-Output "== config: sky_fallback_hold back on, by a Python round-trip =="
& $Py (Join-Path $Root "rig_set_config.py")
if ($LASTEXITCODE -ne 0) { throw "config edit failed (exit $LASTEXITCODE)" }

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
if ($h -notmatch '0\.3\.28') { throw "the running server is not 0.3.28: $h" }

Write-Output "== what the running server is actually serving =="
$idx = ((& curl.exe -s --max-time 15 "http://127.0.0.1:8800/") -join [char]10)
if ($idx -match 'assets/(index-[A-Za-z0-9_-]+\.js)') { Write-Output ("   served asset: " + $Matches[1]) } else { throw "could not read the served asset" }
$bs = (& curl.exe -s -o NUL -w "%{http_code}" --max-time 10 "http://127.0.0.1:8800/bootstrap.js")
if ($bs -ne "200") { throw "bootstrap.js not served ($bs)" }
Write-Output "   bootstrap.js served"

Write-Output "== rig status on the new build (devices must all be back, incl. the Player One camera) =="
Start-Sleep -Seconds 20
& $Py (Join-Path $Root "rig_precheck.py") --report
Write-Output "NOTE: the dawn-park daemon restarted with the Sun up and will park an idle unparked mount within a minute; if the mount was rotated to shade the PC, rotate it again AFTER this (rotate2.py) - the daemon latches after its first park and leaves the operator's unpark alone."
Write-Output "DEPLOY_0328_DONE"
