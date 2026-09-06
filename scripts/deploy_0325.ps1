# Deploy 0.3.25 - the guider-night fixes (spec GN-01..GN-09, 2026-09-06):
# recalibrate after a pier change, the AM5 pulse on a worker thread with a
# 1000 ms cap, re-lock counting with a hold, the eccentricity gate on by
# default (config schema 2 migrates a 0 to 0.65 once), the autofocus metric
# on the grader's HFR, the folded orthogonality report, solved-pointing
# headers, and the relative HFR watchdog. Adapted from the 0.3.24 deploy;
# every trap that script names still applies (rig_precheck gate, kill by PID,
# vendor carry-forward with the updater's own code, webui refresh, the
# supervisor reads 'current' once).
$ErrorActionPreference = "Stop"
$Root = "C:\Users\James\AstroDeck"
$Ver  = "0.3.25"
$Prev = "0.3.24"
$Rel  = Join-Path $Root "releases\$Ver"
$Py   = Join-Path $Root "venv\Scripts\python.exe"

Write-Output "== the tarball is the one the release signed =="
$tarball = Join-Path $Root "astrodeck-$Ver.tar.gz"
$want = ((Get-Content (Join-Path $Root "astrodeck-$Ver.tar.gz.sha256") -Raw) -split '\s+')[0].ToLower()
$have = (Get-FileHash $tarball -Algorithm SHA256).Hash.ToLower()
if ($want -ne $have) { throw "tarball sha256 mismatch: have $have want $want" }
Write-Output ("   sha256 ok: " + $have.Substring(0, 16) + "...")

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
if ($staged.Line -notmatch '0\.3\.25') { throw "staged tree is not 0.3.25" }
$checks = @(
    @{ File = 'server\astrodeck\guide\native.py';              Pattern = 'def _pier_changed_since';         Name = 'GN-01 pier-change recalibration' },
    @{ File = 'server\astrodeck\devices\backends\zwo_am5.py';  Pattern = 'class _Pulse';                    Name = 'GN-02 pulse on a worker thread' },
    @{ File = 'server\astrodeck\devices\serial_link.py';       Pattern = 'def request_sync';                Name = 'GN-02 serial thread door' },
    @{ File = 'server\astrodeck\guide\native.py';              Pattern = 'relock';                          Name = 'GN-03 re-lock counting' },
    @{ File = 'server\astrodeck\config.py';                    Pattern = 'CONFIG_SCHEMA = 2';               Name = 'GN-04 schema 2 (eccentricity default)' },
    @{ File = 'server\astrodeck\imaging\stars.py';             Pattern = 'SIZE_FINE_MAX_TRUNCATION';        Name = 'GN-05 fine-first size metric' },
    @{ File = 'server\astrodeck\imaging\fitsio.py';            Pattern = 'PNTGSRC';                         Name = 'GN-07 solved-pointing headers' },
    @{ File = 'server\astrodeck\sequence\instructions.py';     Pattern = 'focus_baseline_hfr';              Name = 'GN-08 relative watchdog' }
)
foreach ($c in $checks) {
    $hit = Select-String -Path (Join-Path $Rel $c.File) -Pattern $c.Pattern -SimpleMatch
    if (-not $hit) { throw ($c.Name + " is NOT in the staged tree (" + $c.File + ")") }
    Write-Output ("   " + $c.Name + ": present")
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
    try { $h = (& curl.exe -s --max-time 10 "http://127.0.0.1:8800/healthz"); if ($h) { break } } catch { }
    Start-Sleep -Seconds 5
}
Write-Output ("   healthz: " + $h)
if ($h -notmatch '0\.3\.25') { throw "the running server is not 0.3.25: $h" }

Write-Output "== what the running server is actually serving =="
$idx = ((& curl.exe -s --max-time 15 "http://127.0.0.1:8800/") -join [char]10)
if ($idx -match 'assets/(index-[A-Za-z0-9_-]+\.js)') { Write-Output ("   served asset: " + $Matches[1]) } else { throw "could not read the served asset" }
$bs = (& curl.exe -s -o NUL -w "%{http_code}" --max-time 10 "http://127.0.0.1:8800/bootstrap.js")
if ($bs -ne "200") { throw "bootstrap.js not served ($bs)" }
Write-Output "   bootstrap.js served"

Write-Output "== the config migrated to schema 2 with the eccentricity standard on =="
$cfgPath = Join-Path $Root "config\astrodeck.json"
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
Write-Output ("   schema_version: " + $cfg.schema_version + "   standards.max_eccentricity: " + $cfg.standards.max_eccentricity)
if ($cfg.schema_version -lt 2) { throw "config did not migrate to schema 2" }

Write-Output "== rig status on the new build (devices must all be back, incl. the Player One camera) =="
Start-Sleep -Seconds 20
& $Py (Join-Path $Root "rig_precheck.py") --report
Write-Output "DEPLOY_0325_DONE"
