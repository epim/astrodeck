# Roll astrotown back from 0.3.29 to 0.3.28. Run ON the rig (elevated ssh session).
# The 0.3.28 release directory is untouched on the box; this points `current`
# at it and restarts the supervisor, which reads `current` once at startup.
# The config file 0.3.29 wrote loads under 0.3.28 (unknown keys are ignored).
$ErrorActionPreference = "Stop"
$Root = "C:\Users\James\AstroDeck"
$Ver  = "0.3.28"
$From = "0.3.29"
$Rel  = Join-Path $Root "releases\$Ver"
$Py   = Join-Path $Root "venv\Scripts\python.exe"

Write-Output "== the rollback target exists and is what it says =="
if (-not (Test-Path $Rel)) { throw "release directory missing: $Rel" }
$v = Select-String -Path (Join-Path $Rel "server\astrodeck\__init__.py") -Pattern '__version__'
Write-Output ("   target: " + $v.Line.Trim())
if ($v.Line -notmatch '0\.3\.28') { throw "the 0.3.28 release directory does not carry 0.3.28" }
if (-not (Test-Path (Join-Path $Rel "server\astrodeck\vendor\playerone\PlayerOneCamera.dll"))) { throw "PlayerOneCamera.dll missing from the 0.3.28 tree" }
Write-Output ("   running now: " + (& curl.exe -s --max-time 10 "http://127.0.0.1:8800/healthz"))

Write-Output "== refuse to restart under anything in flight =="
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

Write-Output "== flip the pointer back, keeping the way forward =="
$cur = Join-Path $Root "current"
Copy-Item $cur (Join-Path $Root "previous") -Force
Set-Content -Path $cur -Value $Ver -Encoding ascii -NoNewline
Write-Output ("   current -> " + (Get-Content $cur) + "   previous -> " + (Get-Content (Join-Path $Root "previous")))

Write-Output "== start =="
Start-ScheduledTask -TaskName 'AstroDeck'
Start-Sleep -Seconds 25
$h = $null
for ($i = 0; $i -lt 12; $i++) {
    try { $h = (& curl.exe -s --max-time 10 "http://127.0.0.1:8800/healthz"); if ($h) { break } } catch { }
    Start-Sleep -Seconds 5
}
Write-Output ("   healthz: " + $h)
if ($h -notmatch '0\.3\.28') { throw "the running server is not 0.3.28: $h" }
$idx = ((& curl.exe -s --max-time 15 "http://127.0.0.1:8800/") -join [char]10)
if ($idx -match 'assets/(index-[A-Za-z0-9_-]+\.js)') { Write-Output ("   served asset: " + $Matches[1]) } else { throw "could not read the served asset" }

Write-Output "== rig status on the rolled-back build =="
Start-Sleep -Seconds 20
& $Py (Join-Path $Root "rig_precheck.py") --report
Write-Output "NOTE: the dawn-park daemon restarted; an idle unparked mount parks within a minute."
Write-Output "ROLLBACK_TO_0328_DONE"
