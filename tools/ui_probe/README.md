# AstroDeck UI probe

An end-to-end browser probe for the AstroDeck UI, meant to run after every
hub lands on `feat/ui-next`. It builds the UI, starts a fully isolated server
(own config dir, own captures dir, own port), drives a real Chromium browser
through a JSON-described set of routes at three viewport widths, and reports
PASS/FAIL with screenshots and a machine-readable report.

Owns `tools/ui_probe/**` only. Never commits anything itself.

## Requirements

- System `python` (3.12) with Playwright + Chromium installed. Verify with:
  `python -c "from playwright.sync_api import sync_playwright"`
- The server's own venv at `server/.venv/Scripts/python` -- used ONLY to
  launch the AstroDeck server subprocess. It does not need Playwright.
- The UI built (`ui/dist`), or let `run.ps1` build it for you.

## One command

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\ui_probe\run.ps1
```

This builds `ui/dist`, starts an isolated server on port 8801, walks
`routes_classic.json` at widths 390/820/1440 with no auth, stops the server,
and exits with the probe's exit code (0 = all routes passed at all widths).
Screenshots and `report.jsonl` land in `<repo>/.probe/out/`.

Flags:

- `-SkipBuild` -- skip `npm run build`, probe whatever is already in `ui/dist`.
- `-Auth` -- start the server with local auth bootstrapped
  (`probe_admin` / `probe_operator` / `probe_viewer`) and log the browser in
  as `-Role` before walking routes.
- `-Role viewer|operator|admin` -- which account to log in as under `-Auth`
  (default `admin`).
- `-Routes <file>` -- route list, resolved relative to `tools/ui_probe/`
  (default `routes_classic.json`).
- `-Widths "390,820,1440"` -- comma-separated CSS pixel widths.
- `-Port 8801` -- port for the isolated server.
- `-IncludePending` -- also attempt routes flagged `"_pending": true`
  (everything in `routes_next.json` today, until the new UI's hash routes
  actually exist).

## Manual (two-terminal) run

Useful when iterating on a route list without paying the build+boot cost
every time.

```powershell
# 1. Build once (skip if ui/dist is already current)
cd ui; npm run build; cd ..

# 2. Start an isolated server (default posture: open LAN, no auth)
python tools\ui_probe\server_ctl.py start --fresh
#   -> prints {"base": "http://127.0.0.1:8801", "pid": ..., ...}

# 3. Run the probe against it
python tools\ui_probe\probe.py --routes tools\ui_probe\routes_classic.json `
    --widths 390,820,1440 --out .probe\out

# 4. Stop it
python tools\ui_probe\server_ctl.py stop
```

Auth-gated run:

```powershell
python tools\ui_probe\server_ctl.py start --fresh --auth
python tools\ui_probe\probe.py --routes tools\ui_probe\routes_classic.json `
    --widths 390,820,1440 --out .probe\out `
    --auth --role operator --creds .probe\cfg\probe_users.json
python tools\ui_probe\server_ctl.py stop
```

## Files

- `server_ctl.py` -- starts/stops an isolated AstroDeck server. `start`
  wipes+recreates an isolated config dir and captures dir (with `--fresh`),
  launches `server/.venv/Scripts/python -m astrodeck run --port <P>`, waits
  for `/healthz`, optionally bootstraps local auth + 3 probe users, connects
  the simulator rig, and polls `/api/status` until the camera and telescope
  both report `connected`. `stop` reads the PID it wrote and runs
  `taskkill /PID <pid> /T /F` (kills the whole process tree -- necessary on
  Windows, since a bare kill of the launcher PID alone can leave the actual
  uvicorn process running).
- `probe.py` -- the Playwright walk. Takes a route list, a base URL, and a
  set of widths; for each (width, route) pair it navigates, runs a vacuity
  guard, performs the route's click steps, asserts a view-specific marker is
  VISIBLE, measures horizontal overflow, collects console errors and failed
  (>=400) network requests, screenshots, and records a JSON report line.
  Exits non-zero if anything failed anywhere.
- `routes_classic.json` -- route list for the CURRENT shipped UI (no URL
  routing -- views are React state). Proves the harness against a real,
  already-working app.
- `routes_next.json` -- placeholder route list for the NEW UI's hash routes
  (`#/sky`, `#/weather`, `#/session`, `#/rig/devices`, `#/rig/capture`,
  `#/monitor`, `#/settings`). Every entry is flagged `"_pending": true` and
  skipped by default (pass `--include-pending` / `-IncludePending` to force
  them) because none of these routes exist yet on `feat/ui-next`. The
  controller extends this file as each hub lands, dropping the flag once the
  hub actually renders under its hash route.
- `run.ps1` -- one-command build+start+probe+stop.

## Environment variables the server understands (read from the source, not
guessed)

- `ASTRODECK_CONFIG_DIR` -- config root (`server/astrodeck/config.py:40`).
  `server_ctl.py` sets this to `<repo>/.probe/cfg` by default so a probe run
  never touches a developer's real `server/config/`.
- `ASTRODECK_CAPTURE_DIR` -- captures root (`server/astrodeck/hub.py:216`).
  Defaults to `<repo>/.probe/captures`, same reasoning.
- `ASTRODECK_ALLOW_INSECURE_OPEN` / `ASTRODECK_REQUIRE_AUTH` -- deployment
  security toggles read by `server/astrodeck/__main__.py`. `server_ctl.py`
  explicitly unsets both and never binds anywhere but loopback
  (`127.0.0.1:<port>`, the CLI default), so neither toggle is needed: a
  loopback bind with the default `AuthConfig.methods == []` starts up with no
  auth configured and no warning (see below).

## Auth / first-run mechanism, and why

`AuthConfig.methods` defaults to `[]` -- "open/admin", byte-for-byte the
historical default (`server/astrodeck/config.py:291`). Under that posture a
loopback caller auto-resolves to admin (`trust_loopback` defaults `True`), so
the default probe run needs **no login at all**: `POST /api/connect/sim`,
`GET /api/status`, everything just works unauthenticated. This is the
reliable path for the mandatory verification run, and it is what `run.ps1`
does without `-Auth`.

For `--auth` runs, `server_ctl.py` seeds the first admin through the CLI
break-glass path -- `python -m astrodeck create-admin probe_admin --password
...` (`server/astrodeck/__main__.py:create_admin`) -- **before the server
ever starts**, rather than racing the unauthenticated
`POST /auth/setup/local` first-run window over the LAN. That CLI path writes
`methods: ["local"]` and `local_enabled_first_run: false` straight into the
config store and works with no browser and no running server, which is why
it is the reliable choice here (the task explicitly asks to "read the code
and pick the reliable path"). Once the server is up, `server_ctl.py` logs in
as that admin (`POST /auth/local`) and creates `probe_operator` and
`probe_viewer` over the admin-gated `POST /api/users`
(`server/astrodeck/auth/local_routes.py`), which requires email-shaped
usernames (`require_email=True`) -- hence `probe_operator@probe.local` /
`probe_viewer@probe.local`, while the CLI-seeded admin keeps a bare
`probe_admin` (the CLI path uses `require_email=False`, matching the
break-glass contract that it must work with nothing but a console). All
three sets of credentials are written to `<config-dir>/probe_users.json` for
`probe.py --auth --role <role>` to drive the actual login FORM in the
browser (`ui/src/views/Login.tsx`: fields labeled "Username"/"Password", a
"Sign in" button) -- the credentials are provisioned via the API for speed
and reliability, but the sign-IN itself is always exercised through the real
UI, never skipped.

## Vacuity / false-pass guards (see the traps memory this harness was built
against)

- **Hidden-duplicate nav labels.** The desktop rail (`ui/src/App.tsx`,
  `hidden sm:flex`) and the phone bottom nav
  (`ui/src/components/BottomNav.tsx`, `sm:hidden`) render the SAME labels
  (Equipment/Focus/Capture/Monitor/Settings), only one set visible at a
  given width. Every text lookup in `probe.py` -- clicks AND the marker
  assertion -- is filtered to visible-only matches (`_visible_matches`); a
  match that exists in the DOM but is `display:none` never counts as
  "found". This is also why click steps are best-effort/skippable: a step
  like `{"text": "More"}` is a real, required step on a 390px phone (opens
  the overflow sheet that holds Monitor/Settings) and correctly a no-op on
  820/1440px, where Monitor/Settings sit directly on the rail.
- **Marker collides with the nav label.** `routes_classic.json` deliberately
  does NOT assert "Equipment" or "Settings" as markers, even though those
  are the view titles -- both strings are ALSO nav labels that stay visible
  (and would false-pass) even if the click landed on the wrong view. It
  asserts the inner panel titles "Devices" and "Connection Status" instead,
  neither of which appears anywhere in the nav.
- **Expired session / sign-in false pass.** `_vacuity_guard` aborts the
  route (no clicks, no marker check, screenshot only) the instant the body
  contains "sign in to control" or "display disconnected"
  (case-insensitive), the body text is under 200 characters, or the
  `<header>` never renders the `ASTRODECK` wordmark.
- **Blank page must ABORT, not pass.** A vacuity failure is recorded as a
  FAILED route with the specific reason, never silently skipped or
  down-graded to a warning.

## Quirks hit while building this (both found by the FIRST end-to-end run,
before any fix -- exactly what this harness is for)

- **`ASTRODECK_UI_DIR` has to be set explicitly.** `_resolve_ui_dist()`
  (`server/astrodeck/api/app.py`) falls back to a REPO-RELATIVE path
  (`parents[3]` of `app.py`, i.e. `<repo>/ui/dist`) only when it finds no
  `<package>/webui` bundled copy. That fallback assumes an editable
  (`pip install -e .`) checkout. This repo's `server/.venv` is a NORMAL
  (non-editable) install, so `app.py`'s real path is under
  `.venv/Lib/site-packages/astrodeck/api/app.py` and `parents[3]` lands
  inside the venv itself (measured: `server/.venv/Lib/ui/dist`, which has no
  `index.html`). With neither candidate found, the SPA catch-all route
  never gets registered at all, and EVERY path 404s -- not just `/`. First
  probe run against the classic UI caught this immediately (`vacuity: body
  text only 22 chars`, `no <header> element at all`, `404 http://.../`) --
  it was not a probe bug, it was the harness doing its job. Fixed by having
  `server_ctl.py` always set `ASTRODECK_UI_DIR=<repo>/ui/dist` explicitly
  (the documented override for exactly this case), which also has the nice
  side effect of pinning the probe to the bundle THIS run just built,
  regardless of how the venv happens to be installed.
- **A click's text can match a visible-but-covered element underneath an
  overlay.** `is_visible()` reports `True` for an element that is rendered,
  on-screen and non-zero-size even when a modal/sheet overlay sits visually
  on top of it -- occlusion is a CLICK-time actionability check in
  Playwright, not a visibility one. First probe run: at 390px, clicking
  "Monitor" inside the open More sheet instead matched (and tried to click)
  a "Safety monitor" device-role label still mounted on the underlying
  Equipment view (substring match: "Monitor" is contained in "Safety
  monitor", case-insensitively). Playwright's own actionability retries
  eventually gave up with "`<span>Safety monitor</span>` ... subtree
  intercepts pointer events", so the click never silently mis-fired --
  it correctly timed out and failed the route -- but the fix is to not rely
  on substring matching for clicks at all: `_run_clicks` now defaults every
  click step to `exact=True` (case-sensitive, whole-string), since every nav
  label this harness drives is a short, exact, known string straight from
  the source (`BottomNav.PRIMARY`, `App.tsx NAV`,
  `NavMoreSheet.OVERFLOW_VIEWS`). Markers stay `exact=False`
  (substring/case-insensitive) -- they are panel titles chosen specifically
  to not collide with anything else on screen (see the marker-collision note
  above), so the more forgiving match is safe there and was not the source
  of this failure.
- The server's UI-bundle resolution also prefers `<package>/webui` over
  `ui/dist` if BOTH exist, tie-broken by `index.html` mtime -- there is no
  `server/astrodeck/webui/` in this checkout, so this tie-break never
  triggers here, but a release/binary build staged from this tree would
  need `ASTRODECK_UI_DIR` (or a fresh `webui/` copy) for the same reason.
- `POST /api/users` requires `require_email=True`, so `probe_operator` /
  `probe_viewer` must be email-shaped usernames; a bare username there 422s.
- Playwright's `get_by_text(text, exact=False)` is already case- and
  whitespace-insensitive, so markers are written in natural case ("Focuser",
  "Connection Status") even though the CSS renders them uppercase via
  `text-transform` -- the actual DOM text is mixed-case.
- `taskkill /PID <pid> /T /F` is load-bearing, not belt-and-braces: the
  server subprocess `server_ctl.py` launches is a *launcher* whose actual
  uvicorn process is a CHILD pid (confirmed via the server log: "Started
  server process [45580]" under launcher pid 32508). Killing only the
  launcher pid would leave uvicorn running and bound to the port.

## `routes_next.json` -- the real route list (2026-09-10, `feat/ui-next`)

Replaces the old placeholder (every entry `_pending`, none of it real). Built
by reading the actual source, not by guessing: `ui/src/next/router.ts` (the
hash grammar and the `SUBS` table, which is where the real sub-nav names for
weather/session/rig/monitor/settings come from -- e.g. weather's subs are
`conditions`/`sky`/`radar`, not `sky`/`radar`), `ui/src/next/hubs/index.ts`
(`HUB_ORDER`, `HUB_META`, and the composed global `SHEETS` registry), every
hub's own `sheets/index.ts` + its `reg-*.ts` fragments (the per-task sheet
contributions six agents wrote in parallel for the Rig hub, four for
Settings), and a full `data-testid="..."` grep across `ui/src/next/hubs/**`
and `ui/src/next/shell/**` to get the REAL marker for every screen rather
than inventing one.

60 routes, none `_pending` -- every hub in `hubs/index.ts` is wired to a real
component by this point in the branch:

- 15 hub-root / sub-nav routes (one per entry in every hub's `SUBS[hub]`,
  using the router's own default-resolution for a bare hub hash, e.g. `#/settings`
  resolves to `general` because that is `SUBS.settings[0]`).
- 44 sheet routes -- one per name actually present in a hub's composed
  `sheets` registry object, reached through the sub-nav screen that actually
  links to it (so Settings' MORE-group sheets sit under
  `#/settings/general/<name>`, matching where `GeneralScreen`'s own rows
  open them from). Left out on purpose: `driver` and `demo` (both in the Rig
  hub's registry) -- `driver` is reachable only mid-flow through `addDevice`
  (there is no nav path that lands on it cold) and `demo` is `SheetHost`
  test scaffolding by its own doc comment, not a screen a real user reaches.
- `#/classic` -- proves the legacy root (`ui/src/App.tsx`) still mounts.
  Marker is the nav label `"Equipment"`, not the header's `STATUS` strip:
  `STATUS` is `hidden md:flex` (App.tsx:599) and would false-FAIL the probe's
  visible-only text match at 390px for a reason that has nothing to do with
  whether classic actually mounted. `Equipment`'s nav-label collision risk
  (see the trap notes above) does not apply here because this route clicks
  nowhere -- it only has to prove SOME legacy default view rendered.

Every route carries `"testid"` (a `data-testid` value, asserted via the new
`probe.py` support below) as its primary, load-bearing check; a handful also
carry a text `"marker"` as a cheap independent second check where the
visible label is unambiguous. `marker` stays fully optional per-route (see
the `probe.py` section below) -- most `routes_next.json` entries do not set
one.

One param correction worth flagging: `#/session/files` reads `params.src`,
not `params.source` (`ui/src/next/hubs/session/sheets/files.tsx:177`), and
the value the UI's own TONIGHT chip uses is `src=current`
(`files.tsx:511`), not `src=tonight` -- used the real names.

No `expected_failures` entries were pre-guessed, and after running the full
list twice (see "2026-09-10 run notes" below) none were added: every failure
the run produced traced back to a real gap (a stale server install, one
screen missing a gate its sibling screen already has, a genuine overflow
bug, or the server's own security header blocking a capability the new code
calls) rather than a simulator limitation the UI is correctly reporting
around. `expected_failures` stays empty on purpose -- adding one for any of
these would have hidden a real finding rather than documented a legitimate
gap.

## 2026-09-10 run notes

First full run against `routes_next.json` (60 routes x 3 widths = 180) was
144/180. Almost all of that was environment, not the UI: `server/.venv` was
a NON-editable install frozen at Sep 8 (`astrodeck==0.3.26`), five commits
behind the Sep 10 repo source it was supposed to be serving -- missing `GET
/api/site` and `GET /api/remote/status` entirely (confirmed both ways: `curl`
straight to the running port returned `{"detail":"Not Found"}` for both, and
a line-count diff against `server/astrodeck/api/app.py` showed the installed
copy 259 lines short). Reinstalled editable (`pip install -e . --no-deps`
from `server/`, no repo file touched -- `.venv/` is gitignored) and reran:
166/180. The remaining 14 are real and are NOT probe bugs:

- **`monitor-live` / `monitor-log` / `monitor-alerts` fetch radar tiles even
  when weather is off**, 404ing on every tile (`{"detail":"weather
  disabled"}`) and re-firing on `RadarMap`'s own refresh timer for as long as
  the component stays mounted. `ui/src/next/hubs/monitor/live/LiveScreen.tsx:481-486`
  mounts `<RadarMap />` behind `bp !== "phone" && canSeeWeather` only -- no
  `weather.enabled` check. `ui/src/next/hubs/weather/radar/RadarScreen.tsx:44-68`
  mounts the SAME component behind an explicit `off = !weather ||
  !weather.enabled` gate, with its own comment explaining exactly why: "It
  does NOT mount the map when weather is switched off. Mounting it would
  fire a grid of tile requests through the server-side IEM proxy for a
  feature the operator has turned off." LiveScreen is missing that gate.
  Because every route in one width's run shares a single browser tab (a
  hash-only `page.goto` is a same-document navigation, so the SPA never
  reloads between routes), RadarMap's interval keeps firing after the probe
  has moved on, which is why the 404s also landed on `settings` and
  `settings-users-screen` at 1440px in one run -- those screens never
  request tiles themselves, they were just the active route when a stale
  timer tick resolved.
- **Permissions-Policy blocks camera (`sky`, 390/820px) and geolocation
  (`sky-sites`, all 3 widths).** The server's own security middleware
  (`server/astrodeck/api/app.py:2026-2028`) sends
  `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(),
  usb=()` on every response -- an empty allowlist, which blocks the API for
  every origin including same-origin, not just third-party embeds. The new
  UI calls both anyway (`ui/src/next/hubs/sky/finder/camera.ts`,
  `ui/src/next/hubs/sky/sheets/photosphere.ts` for camera;
  `ui/src/next/hubs/sky/sheets/sites.tsx`'s "fill from phone" for
  geolocation), so those features cannot work in ANY browser against this
  server as configured now, regardless of device permissions -- this predates
  the new UI (the header looks written when the classic UI had no
  camera/geolocation-calling code) and is a genuine cross-cutting
  conflict, not a probe artifact.
- **Two real horizontal-overflow bugs**, both under Rig > Devices:
  `#/rig/devices/addDevice` (`scrollWidth` a FIXED 2454px regardless of
  viewport -- 1634px overflow at 820px, 1014px at 1440px, confirmed visually
  in the 820px screenshot: driver rows and the assignment table run off the
  right edge with no scrollbar) and `#/rig/devices/mount/polar`'s sibling
  `#/rig/devices/safety` (100px overflow, 820px only). Neither reproduces at
  390px. Reported for the controller to trace; not fixed here per the
  brief (probe tooling does not touch `ui/src`).

Fixed here, in `probe.py`, because both were the harness's own fault, not
the app's:

- `_login()`'s post-login success check waited for the text "Devices" to
  become visible -- the classic Equipment view's inner panel title. Every
  `--auth` run against `routes_next.json` failed at the login step, not
  because login was broken, but because the new UI's default post-login
  screen is the Sky hub, which never shows that string. Replaced with a
  UI-agnostic check: wait for the Username field to close, then run the
  same `_vacuity_guard` every route already passes through. The
  `--auth --role viewer` subset (`sky`, `session-now`, `rig-devices`,
  `rig-devices-camera`, `settings`) is 15/15 after the fix.
- Added `"testid"` route support and `--only` (see above).

Full reports: `.probe/out-next/report.jsonl` (no-auth, all 60 routes x 3
widths), `.probe/out-next-viewer/report.jsonl` (`--auth --role viewer`
subset). Both directories are gitignored (`.probe/`), regenerated by
`server_ctl.py` / `probe.py`, not committed.

## `probe.py` extension: `"testid"` routes + `--only`

Two additions, both needed to drive `routes_next.json`:

- **`"testid": "some-id"`** on a route now asserts
  `[data-testid="some-id"]` is VISIBLE (same visible-only discipline as text
  markers, via a new `_visible_css_matches` / `_wait_for_visible_testid`,
  mirroring `_visible_matches` / `_wait_for_visible_text`) before the route
  can pass. `"marker"` is now optional (`route.get("marker")` instead of the
  old `route["marker"]`, which would `KeyError` on any route that omits it).
  A route must supply at least one of `testid` / `marker` or it fails with
  an explicit reason ("route defines neither 'testid' nor 'marker'") rather
  than silently passing on vacuity alone -- an empty gate is exactly the
  false-pass shape this harness exists to catch. The JSON report line for
  each route now also carries `"testid"` / `"testid_ok"` alongside the
  existing `"marker"` / `"marker_ok"`.
- **`--only name1,name2,...`** filters the loaded route list down to those
  `"name"` values before walking widths. Added so the brief's
  `--auth --role viewer` read-only smoke pass could run over a named
  SUBSET of `routes_next.json` (sky, session/now, rig/devices,
  rig/devices/camera, settings) without a second route file -- this tool's
  file ownership is `routes_next.json` + this README + `probe.py` /
  `server_ctl.py`, not arbitrary new JSON files. Unknown names match
  nothing rather than erroring, since a caller may reuse `--only` against a
  route file that does not have every name.
