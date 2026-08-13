# Deploy 0.2.77 - a flow-driven night now ends parked and warm.
# Modelled line for line on deploy_0274.ps1, including the traps it names.
$ErrorActionPreference = "Stop"
$Root = "C:\Users\James\AstroDeck"
$Ver  = "0.2.77"
$Prev = "0.2.76"
$Rel  = Join-Path $Root "releases\$Ver"

Write-Output "== refuse to deploy over a running sequence =="
$tok = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "mint-token.ps1")
$tok = ($tok | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 1).Trim()
$hdr = "Cookie: ad_session=" + $tok
$q = (& curl.exe -s --max-time 20 -H $hdr "http://127.0.0.1:8800/api/sequence/state") | ConvertFrom-Json
Write-Output ("   sequence state: " + $q.state)
if ($q.state -eq "running" -or $q.state -eq "paused" -or $q.state -eq "holding") {
    throw "a sequence is $($q.state) - refusing to restart the server under it"
}

Write-Output "== stop the task, then kill the tree by EXPLICIT PID (trap 3/8) =="
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
tar -xzf (Join-Path $Root "astrodeck-$Ver.tar.gz") -C $staging
if (Test-Path $Rel) { Remove-Item -Recurse -Force $Rel }
New-Item -ItemType Directory -Force (Join-Path $Root "releases") | Out-Null
Move-Item (Join-Path $staging "astrodeck-$Ver") $Rel
Remove-Item -Recurse -Force $staging

Write-Output "== carry vendor + astap forward (build_release excludes them) =="
foreach ($sub in @("vendor")) {
    $src = Join-Path $Root "releases\$Prev\server\astrodeck\$sub"
    $dst = Join-Path $Rel "server\astrodeck\$sub"
    if (Test-Path $src) {
        New-Item -ItemType Directory -Force $dst | Out-Null
        Copy-Item -Recurse -Force (Join-Path $src "*") $dst
        Write-Output ("   $sub entries: " + (Get-ChildItem $dst | Measure-Object).Count)
    }
}

Write-Output "== refresh webui from ui/dist (trap 2/7: a stale bundled copy shadows it) =="
$webui = Join-Path $Rel "server\astrodeck\webui"
if (Test-Path $webui) { Remove-Item -Recurse -Force $webui }
New-Item -ItemType Directory -Force $webui | Out-Null
Copy-Item -Recurse -Force (Join-Path $Rel "ui\dist\*") $webui

Write-Output "== verify the STAGED version, not the repo's (trap 9) =="
$staged = Select-String -Path (Join-Path $Rel "server\astrodeck\__init__.py") -Pattern '__version__'
Write-Output ("   staged: " + $staged.Line.Trim())

Write-Output "== flip the pointer, keeping the rollback =="
$cur = Join-Path $Root "current"
if (Test-Path $cur) { Copy-Item $cur (Join-Path $Root "previous") -Force }
Set-Content -Path $cur -Value $Ver -Encoding ascii -NoNewline
Write-Output ("   current -> " + (Get-Content $cur))
Write-Output ("   previous -> " + (Get-Content (Join-Path $Root "previous") -ErrorAction SilentlyContinue))

Write-Output "== start (the SUPERVISOR reads `current` once at startup - trap 1) =="
Start-ScheduledTask -TaskName 'AstroDeck'
Start-Sleep -Seconds 25
for ($i = 0; $i -lt 12; $i++) {
    try {
        $h = (& curl.exe -s --max-time 10 "http://127.0.0.1:8800/healthz")
        if ($h) { Write-Output ("   healthz: " + $h); break }
    } catch { }
    Start-Sleep -Seconds 5
}
