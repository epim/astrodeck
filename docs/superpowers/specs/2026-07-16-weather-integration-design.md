# Sub-project C — Weather Integration Design

Date: 2026-07-16 (drafted night of 2026-07-15). Status: awaiting user review.
Builds on shipped A (sessions/ResumeArm with `resume_veto()` stub) and B (site privacy,
`view.site_precise`, SitePanel). Requirements authority for the C implementation plan.

## 0. User decisions (recorded)

- **Radar view:** mini slippy map (pan/zoom, from scratch — no map lib), WITH the scope's
  location and orientation indicated: compass direction and an indication of inclination.
  Overlay concept delegated to us (design in §11).
- **Panel home:** Monitor view mosaic + a cloud verdict in the health strip.
- **Thresholds:** ONE shared configurable threshold drives both the night warning popup and
  the auto-resume veto. Override = "ignore weather tonight" toggle.
- **V1 scope:** Open-Meteo (no-key) + IEM keyless tiles + Astrospheric BYO-key (optional,
  North-America-only seeing/transparency).
- Carried from A/B: high-cloud night warning popup is REQUIRED; `resume_veto()` gets its
  real implementation; ALL weather UI/data gates on `view.site_precise` (max-privacy).
- Prior research (recorded 2026-07-14/15): Open-Meteo 15-minutely `cloud_cover` +
  low/mid/high, no key; IEM tiles keyless (NEXRAD + GOES); RainViewer DEAD Jan-2026;
  Astrospheric optional BYO-key.

## 1. Ground truth (verified seams, 2026-07-15)

No weather/forecast/radar code exists anywhere in the repo — greenfield on these seams:

- **Opt-in online pattern:** `SurveyConfig.online_fetch` (`server/astrodeck/config.py:273-278`),
  AppConfig slot (`config.py:332-333`), setter `set_survey` (`config.py:635-639`), route +
  cap (`api/app.py:824-831`, `CAP_CONFIG_SITE_OPTICS` = `auth/capabilities.py:32`).
- **httpx fetch template:** `catalog/survey.py:170-191` (`_fetch_cutout`: own AsyncClient,
  timeout 6 s, 2 attempts, RuntimeError on exhaustion); gate read `survey.py:309-310`;
  zero-httpx-when-offline invariant test-enforced via `_Boom` client
  (`tests/test_survey.py:372-393`, `catalog/tiles.py:110-113`).
- **Veto hook:** `sequence/resume_arm.py:70-73` (`resume_veto() -> str | None`, doc comment
  names sub-project C); called at `resume_arm.py:120-125` — non-None logs a warning bus
  event and arms the 10-min retry latch, BEFORE any device is touched. Injected clock
  (`resume_arm.py:39`), cadence constants (`:34-35`), start/stop/never-die loop (`:47-68`).
- **"Tonight":** authoritative window = `sequence/schedule.py:242-267` (`resolve_window`)
  with `_night_dusk`/`_night_dawn` (`schedule.py:118-137`, backward-searching, polar-safe),
  using `cfg.safety.twilight_deg`. Display variant `catalog/coords.py:115-141`
  (`dark_window`, sun below −18°).
- **Server→UI alert lanes:** `bus.publish` → WS; UI `handleEvent` switch
  (`ui/src/store.ts:1009-1191`); `safety` event → sticky toast precedent (`store.ts:1068-1084`);
  `alert` slice key-bump pattern (`store.ts:437-439, 1085-1090`, `useAlert :1284`);
  `bus.log("warning", ...)` maps to outbound ntfy/webhook/telegram sinks
  (`alerting.py:271-278`) but does NOT toast in the UI (`store.ts:1180-1182`).
- **Popup machinery:** `confirmDialog(opts): Promise<boolean>` (`ui/src/components/ConfirmDialog.tsx:39`),
  `mode:"ok"` = acknowledge-only hard notice (`:140-144`); SessionsPanel no-monitor confirm +
  persistent chip precedent (`SessionsPanel.tsx:86-98, 177-182`).
- **Tile proxy precedent:** `catalog/tiles.py:59-127` (hardcoded origin, injectable transport,
  JPEG validation, atomic write, disk-free guard, single-flight `survey.py:111-135`,
  never cache failures). Route shape `GET /api/survey/tile/{slug}/{order}/{npix}.jpg`.
- **Redaction seam:** `api/redact.py:41` strip keys; contract comment `redact.py:79-82`
  (future site-bearing payloads must ride the ONE seam); both WS lanes share it (LAN
  `app.py:~2814-2887`, relay `relay_client.py:~515-604`); secrets scrub `config.py:791-861`.
- **Lifespan:** start `app.py:147-149` (dispatcher task, `resume_arm.start()`), conditional
  poller precedent `app.py:165-172`, stop in finally `app.py:191-208`.
- **UI cap gate:** `useCanViewSitePrecise()` (`ui/src/lib/caps.ts:134`, fail-closed; its doc
  comment reserves it for this panel). Conditional-mount idiom `SettingsView.tsx:62-77, 140`.
- **Charts:** no chart/map/date libs (`ui/package.json` — react, react-dom, zustand only).
  Hand-rolled SVG precedents: `graphs.tsx` (`GuideGraph :202` = closest analog),
  `monitor.tsx:268` Sparkline. Night rule: series by dash/width/inline label, NEVER hue
  (`monitor.tsx:266-267`); full-color imagery gets `var(--img-filter)` redshift
  (`index.css:114`; TileEngine applies filter at `TileEngine.tsx:235`).
- **Gestures:** `SkyCanvas.tsx:355-443` drag-pan/wheel-trap/keyboard layer is
  projection-agnostic and portable. The WebGL HiPS TileEngine is sky-projection only —
  NOT reusable for Web-Mercator Earth tiles; a separate simple `<img>`-grid widget is required.
- **Monitor mosaic:** `MonitorView.tsx:284-287` 12-col grid, `Panel className="col-span-full
  lg:col-span-N"` cells; health fold `lib/health.ts:76-172` (`deriveHealthIssues`), strip
  `monitor.tsx:692-732` ("Night looks OK").

## 2. Server: configuration

New `WeatherConfig` in `server/astrodeck/config.py`, placed beside `SurveyConfig`:

```python
class WeatherConfig(BaseModel):
    """Weather forecast + radar integration (sub-project C). enabled gates ALL
    weather upstream calls (Open-Meteo, Astrospheric, IEM tile proxy): False (default)
    = zero outbound weather traffic. One shared threshold drives both the night
    warning and the auto-resume veto."""
    enabled: bool = False
    cloud_threshold_pct: int = 50      # ge=0 le=100; breach metric = total cloud_cover
    sustain_minutes: int = 30          # ge=15 le=240; breach must persist this long
    astrospheric_api_key: str | None = None  # SECRET; None = feature absent
```

- AppConfig slot after `survey:` (`config.py:332-333` pattern); append-tolerant load needs
  no migration.
- Setter `set_weather(body)` mirroring `set_survey` (`config.py:635-639`).
- Route `POST /api/config/weather`, gated `CAP_CONFIG_SITE_OPTICS`, mirroring
  `app.py:824-831` (asyncio.to_thread write, `bus.publish("config", config=redacted(cfg))`,
  return `_config_payload(principal)`).
- **Secret handling:** `astrospheric_api_key` joins the `redacted()` scrub set
  (`config.py:791-861`) exactly like `google_client_secret` — masked on every read;
  never logged; never echoed in config payloads or 409 bodies (B seams already route those).
- Breach metric decision: threshold applies to TOTAL `cloud_cover` (15-minutely). High
  cirrus raises total, so high-cloud-only nights still trigger; warning copy names the
  dominant layer (§5). One knob, one mental model.

## 3. Server: forecast service

New module `server/astrodeck/weather.py` — one service class owning fetch, cache, alert
latch, and veto logic. Module-level singleton wired in `app.py` like `resume_arm`.

**Poller.** `WeatherService.start()/stop()` (ResumeArm shape, `resume_arm.py:47-68`):
one asyncio task, tick every 60 s, broad-except never-die loop, injected `clock=time.time`.
Started unconditionally in lifespan next to `resume_arm.start()` (`app.py:149`); stopped in
the finally block. Each tick no-ops unless `cfg.weather.enabled` AND the config site has
finite lat/lon — so runtime config toggles take effect within one tick, no service restart.
Cadences (due-when-older-than, monotonic-latch pattern `alerting.py:202-226`):
- Open-Meteo: every **15 min** (~96 calls/day, far under their 10k/day free tier).
- Astrospheric: every **6 h**, only when `astrospheric_api_key` is set. VERIFIED
  2026-07-16 against astrospheric.com/DynamicContent/api_info.html: each call costs
  **5 API credits** and Pro accounts get **100 credits/day** (refresh at midnight UTC) —
  6-hourly = 4 calls/day = 20 credits, comfortable headroom; their model only updates
  every 6 h, so faster polling buys nothing. (The key requires an Astrospheric Pro
  membership.)

**Open-Meteo fetch.** `https://api.open-meteo.com/v1/forecast` with
`latitude`, `longitude`, `minutely_15=cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high`,
`forecast_days=2`, `timezone=UTC`. Fetch shape copies `_fetch_cutout`
(`survey.py:170-191`): own `httpx.AsyncClient(timeout=6.0, headers={"User-Agent": "AstroDeck/0.1"})`,
2 attempts, 0.4 s backoff, RuntimeError on exhaustion. Keep ≤192 samples/series (48 h).

**Astrospheric fetch.** VERIFIED 2026-07-16 against their live API docs: HTTP POST to
`https://astrosphericpublicaccess.azurewebsites.net/api/GetForecastData_V1`, JSON body
exactly `{"Latitude": float, "Longitude": float, "APIKey": string}` (that casing).
Response `ReturnObject`: `UTCStartTime` (forecast start), `ModelTime` (`YYYYMMDDHH`),
`APICreditUsedToday` (int), and hourly `Array<HourValue>` fields — we extract
`Astrospheric_Seeing` and `Astrospheric_Transparency` (raw value per hour; ignore the
bundled map-color). Sample times = `UTCStartTime + index` hours (81 h horizon). Errors:
400/403/500 with `ErrorInfo` JSON; locations outside the RDPS (North America) domain are
a 400 — fail-soft covers all of these. Independent fail-soft from Open-Meteo: one failing
never blocks the other. The key appears only in the request body — never in logs, never
in any payload. Surface `APICreditUsedToday` as `astrospheric.credits_used_today` in the
weather payload so the user can watch their daily budget.

**Cache + staleness.** In-memory only (no disk): latest forecast + `fetched_ts` per source.
Failures NEVER cached (survey rule, `survey.py:116-117`); on failure keep the previous
forecast and mark staleness by age. Stale thresholds: Open-Meteo > 45 min,
Astrospheric > 12 h (two 6-hourly model cycles). Failure logging is outcome-only — `type(exc).__name__`, never
coordinates, never the URL with lat/lon query (B log rule; `hub.py:882` precedent).

**Publish.** After each successful refresh (and once, with cleared payload, on the first
tick after `enabled` flips false): `bus.publish("weather", **payload)` where payload =
the `GET /api/weather` shape (§7). WS fan-out redaction per §8.

## 4. Server: resume_veto + ignore-tonight override

**Veto logic lives in WeatherService** (`veto_reason(now) -> str | None`); ResumeArm stays
thin: constructor gains `weather=None` (injected like `clock`), and `resume_veto()` becomes
`return self._weather.veto_reason(self._clock()) if self._weather else None`. Wired in
`app.py:88` (`ResumeArm(engine, hub, weather=weather_service)`).

`veto_reason` returns a reason string when ALL hold:
- `cfg.weather.enabled` is true, and ignore-tonight (below) is NOT set;
- Open-Meteo forecast is fresh (age ≤ 45 min) — stale/missing forecast → **None (fail-open)**:
  weather is advisory; the safety monitor remains the hard guard;
- breach in the next 60 min: ≥ `max(1, sustain_minutes // 15)` CONSECUTIVE 15-min samples
  with total cloud ≥ `cloud_threshold_pct`.

Reason string: `f"cloud cover {peak}% forecast within the next hour (threshold {threshold}%)"`.
ResumeArm's existing plumbing (`resume_arm.py:120-125`) handles logging + 10-min retry.

**Ignore-tonight.** Runtime flag on WeatherService, NOT persisted config:
`POST /api/weather/ignore-tonight` body `{"ignore": bool}`, gated `CAP_CONTROL_CAPTURE`
(operator — it affects sequencing, like other control caps). The flag is keyed to tonight's
dusk timestamp (via `_night_dusk`, `schedule.py:125-137`, with `cfg.safety.twilight_deg`)
and auto-expires when a new night begins; setting it while no site/night resolves is a 409
`{detail:{code:"no_night"}}`. When set: `veto_reason` returns None. It does NOT retract an
already-shown warning (the user has seen it; the flag records "proceed anyway").
Flag state rides the weather payload (§7) so all clients see it.

## 5. Server: high-cloud night warning (REQUIRED)

Evaluated inside WeatherService on each successful Open-Meteo refresh:

- Resolve tonight: `[dusk, dawn]` via `_night_dusk`/`_night_dawn`
  (`schedule.py:118-137`) with `cfg.safety.twilight_deg` — same definition ResumeArm uses,
  so the warning and the veto agree about what "tonight" means. No resolvable night
  (polar day) → no evaluation.
- Breach: same sustained rule as §4 applied over the dark window (≥ sustain_minutes of
  consecutive samples ≥ threshold, anywhere in [dusk, dawn]).
- **Once-per-night latch** keyed by `int(dusk_ts)` (the `_gave_up_for` idiom,
  `resume_arm.py:45`): on first breach detection for a given night, emit BOTH:
  1. `bus.publish("weather", ...)` with `alert` populated (§7) → drives the UI popup;
  2. `bus.log("warning", f"high cloud forecast tonight: peak {peak}% ({dominant} layer) "
     f"{start_hhmm}–{end_hhmm}", "weather")` → log drawer + existing ntfy/webhook/telegram
     sinks (`alerting.py:271-278`) reach phones with zero new alerting code. Message
     contains times and percentages only — NEVER coordinates (B log rule:
     `/api/logs` is readable by any `view.status` holder).
- `alert` payload: `{"kind": "high_cloud", "start_iso": .., "end_iso": .., "peak_pct": int,
  "dominant_layer": "low"|"mid"|"high"}` — dominant = layer with highest mean over the
  breach window. The alert stays in the weather payload until dawn (UI shows chip), latch
  resets when the night key changes.

## 6. Server: IEM tile proxy

New routes in `app.py` (weather section), modeled on `catalog/tiles.py:59-127`:

```
GET /api/weather/tile/{layer}/{z}/{x}/{y}.png    layer: Literal["radar", "satellite"]
```

- Gated `require(CAP_VIEW_SITE_PRECISE)` + `@declare` — radar tiles centered on the site
  reveal the site area; holders only (§8).
- Upstream hardcoded server-side (never caller-supplied): IEM tile cache
  `https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/{slug}/{z}/{x}/{y}.png` with
  `radar` → `nexrad-n0q-900913` (CONUS composite reflectivity) and `satellite` →
  `goes_east_fulldisk_ch13` (GOES East channel-13 longwave IR, full disk — cloud-top
  temps, works at night). BOTH SLUGS VERIFIED LIVE 2026-07-16: real PNG tiles returned
  with `Cache-Control: public, max-age=300` (actively updating). The plan copies these
  verbatim; no further verification step needed.
- Zoom clamp `3 ≤ z ≤ 11`; x/y validated to the z range; out-of-range → 422 (Literal/
  bounds at the route boundary, survey precedent `survey.py:280-282`).
- `cfg.weather.enabled` false → 404 `no-store` with ZERO httpx construction (the
  `tiles.py:110-113` invariant, test-enforced the same way).
- Disk cache under the survey-cache pattern: TTL **240 s** for radar, **600 s** for
  satellite (radar composites update ~5 min); `Cache-Control: private, max-age=240|600`
  (NOT immutable — these tiles change); atomic `.tmp`→replace writes; disk-free guard;
  single-flight per tile key (`survey.py:111-135` util); failures return 502 `no-store`
  and are never cached. PNG magic (`\x89PNG`) validated instead of JPEG SOI.
- Privacy note (accepted): the HOME SERVER's IP fetches site-area tiles from IEM — same
  exposure class as the Open-Meteo fetch itself; the point of the proxy is that BROWSERS
  (possibly on foreign networks, over the relay) never contact IEM.

## 7. Server: API surface + RBAC summary

| Route | Method | Cap | Notes |
|---|---|---|---|
| `/api/weather` | GET | `CAP_VIEW_SITE_PRECISE` | full payload below |
| `/api/weather/ignore-tonight` | POST | `CAP_CONTROL_CAPTURE` | `{"ignore": bool}`; 409 `no_night` |
| `/api/weather/tile/{layer}/{z}/{x}/{y}.png` | GET | `CAP_VIEW_SITE_PRECISE` | §6 |
| `/api/config/weather` | POST | `CAP_CONFIG_SITE_OPTICS` | §2 |

All four use `@declare` so the boot assertion covers them (B rule: no exempt paths).

`GET /api/weather` (and the `weather` WS event `data`) payload:

```json
{
  "enabled": true,
  "fetched_ts": 1789000000.0,
  "stale": false,
  "ignore_tonight": false,
  "threshold_pct": 50,
  "sustain_minutes": 30,
  "forecast": {"times": ["...iso Z..."], "cloud": [..], "cloud_low": [..],
               "cloud_mid": [..], "cloud_high": [..]},
  "astrospheric": {"times": ["..."], "seeing": [..], "transparency": [..],
                   "fetched_ts": 1789000000.0, "stale": false,
                   "credits_used_today": 20},
  "alert": {"kind": "high_cloud", "start_iso": "..", "end_iso": "..",
            "peak_pct": 78, "dominant_layer": "high"}
}
```

`forecast`/`astrospheric`/`alert` are null when absent. `enabled:false` payload carries
nulls for all three. NO coordinates anywhere in this payload — forecast series are
site-derived but location-free; the payload needs no key-stripping, only the event-drop
rule (§8).

## 8. Privacy & redaction

- **WS `weather` events are DROPPED entirely (not stripped) for principals lacking
  `view.site_precise`.** Implementation: `_redact_ws_event` (`api/redact.py:72-106`) gains
  an event-type rule — type `weather` + non-holder → return `None`; BOTH WS lanes (LAN
  `app.py:~2814-2887`, relay `relay_client.py:~515-604`) must skip sending on None.
  The plan verifies both lanes' None-handling explicitly. Rationale: max-privacy (B
  decision) — even location-free forecast numbers describe conditions at the site.
- The 60 s re-auth downgrade (B) applies unchanged: a downgraded principal stops
  receiving weather events within a minute.
- REST: both weather GET routes require the cap outright (no partial payloads).
- `astrospheric_api_key` scrubbed in `redacted()` (§2); a secret-absence assertion joins
  the config-echo tests (T-RBAC-13b family).
- Logs: warning text carries times/percentages only; fetch errors log exception type
  only (§3, §5).
- UI: nothing weather-related mounts for non-holders (§10-12); no weather request is
  ever issued by a non-holder client.

## 9. UI: types, API, store

- `ui/src/types.ts`: `WeatherState` mirroring §7 payload (all-optional/nullable style of
  B's Site types).
- `ui/src/api/weather.ts`: `getWeather()`, `setIgnoreTonight(ignore)`, `saveWeatherConfig(cfg, version)`
  (version-checked like `saveSite`, 409 → reload-and-retoast idiom).
- `ui/src/lib/weather.ts`: `normalizeWeather(raw, nowTs)` — pure, npx-tsx-testable
  (`lib/safety.ts` precedent): derives `stale` fail-closed from `fetched_ts` age (>45 min),
  clamps percentages 0-100, tolerates missing series. Also the breach-window helper the
  panel uses to shade "bad" spans (same sustained rule as the server, display-only).
- `store.ts`: slice `weather: WeatherState | null` (beside `safety` `:436`);
  `case "weather":` in `handleEvent` (`:1009-1191`) — normalize + set, and bump a
  monotonic `weatherAlertKey` when `alert` transitions null→non-null (the `alert` slice
  key idiom `:1085-1090`) so the popup effect fires once per latch;
  selectors `useWeather`, `useWeatherAlertKey` (beside `:1283-1284`).
- Cold load: the Sky conditions panel fetches `GET /api/weather` on mount and on WS
  reconnect (holders only); WS events keep it fresh thereafter.

## 10. UI: Sky conditions panel (Monitor) + health strip

- `ui/src/components/weather/SkyConditionsPanel.tsx`, mounted in MonitorView's mosaic
  (`MonitorView.tsx:284-287`) as `col-span-full lg:col-span-6`, ONLY when
  `useCanViewSitePrecise()` (conditional-mount idiom, `SettingsView.tsx:140`). Non-holders:
  nothing renders. Holders with `enabled:false`: empty state "Weather is off — enable it in
  Settings → Connect."
- **Cloud chart:** hand-rolled SVG time-series (GuideGraph/Sparkline precedent): x = next
  ~24 h, y = 0-100 %. Series low/mid/high differentiated by dash pattern + stroke width +
  inline text labels at line ends — NEVER hue alone (night rule `monitor.tsx:266-267`).
  Tonight's dark window drawn as a shaded band (`var(--text-faint)` at low opacity);
  threshold drawn as a faint horizontal rule; "now" cursor line; breach spans shaded via
  the `lib/weather.ts` helper. Tick labels via native `Date`/`Intl` (no date lib).
- **Chips row:** staleness note ("updated 12 min ago" / "STALE — last fetch 2 h ago"),
  Astrospheric seeing + transparency chips when present, active-alert chip (word + icon),
  ignore-tonight toggle (operator cap; disabled otherwise with the B lock-note idiom).
- **Health strip:** `deriveHealthIssues` (`lib/health.ts:76-87`) gains a `weather` input;
  active alert → Tier-1 AMBER chip `high cloud forecast tonight (peak 78%)`. Never Tier-2
  (weather is advisory; red is for safety/disk/link). "Night looks OK" therefore requires
  no active cloud alert.
- `ui/src/components/settings/WeatherPanel.tsx` in Settings → Connect, below SitePanel
  (`SettingsView.tsx:150` region), SitePanel idiom: enabled toggle, threshold + sustain
  inputs (bounded, 422-safe), Astrospheric key input (password field, write-only: shows
  "set/not set" from the masked config, never the value), save = `POST /api/config/weather`
  with version-conflict handling.

## 11. UI: radar map + scope-pointing overlay

`ui/src/components/weather/RadarMap.tsx` + `ui/src/lib/mercator.ts` — a second Monitor
mosaic cell (`col-span-full lg:col-span-6`, same cap gate + enabled gate).

**Slippy widget (from scratch, no lib):**
- `lib/mercator.ts` (pure, tsx-tested): lon/lat ↔ Web-Mercator tile/pixel math for
  z 3-11; `destPoint(lat, lon, bearing_deg, dist_km)` spherical destination formula;
  `pierceDistanceKm(alt_deg, deck_km) = deck_km / tan(alt)`.
- Rendering: absolutely-positioned `<img>` grid (ceil(viewport/256)+1 overscan) inside an
  `overflow-hidden` panel; tile src = `/api/weather/tile/{layer}/{z}/{x}/{y}.png` (proxy
  only — the browser NEVER contacts IEM). Broken-tile `onError` → hide img (B thumb
  fallback idiom). Night mode: `filter: var(--img-filter)` on the tile layer
  (`index.css:114`), exactly as TileEngine dims survey imagery.
- Gestures: port SkyCanvas's pointer/wheel/keyboard layer (`SkyCanvas.tsx:355-443`) —
  drag-pan, non-passive wheel-zoom with page-scroll trap, keyboard nudge/zoom, zoom clamp
  3-11. Center defaults to the site; a "recenter" button returns to it.
- Controls: layer toggle radar/satellite (word-labeled buttons, not hue), refresh button
  (cache-busting query `?t=` floor(now/240 s) so tiles roll over with the server TTL),
  attribution line "Radar/satellite: Iowa Environmental Mesonet", and a "radar is ~5 min
  delayed" note.

**Scope location + orientation overlay (the user-required part):**
- **Site marker:** scope glyph at the site's lat/lon (SVG overlay above the tile layer).
- **Azimuth wedge:** a narrow wedge/arrow from the marker along the mount's current
  azimuth — compass orientation at a glance (map is north-up).
- **Sight-line pierce points — the inclination indicator:** the map locations where the
  telescope's line of sight crosses each cloud deck, at
  `destPoint(site, azimuth, deck_km / tan(altitude))` for decks low=2 km, mid=5 km,
  high=9 km. The HIGH-deck point is the primary marker (crosshair glyph + "high cloud"
  label) — it is the exact radar/satellite pixel that decides whether subs survive; low
  and mid render as smaller dots along the same ray. Pointing at zenith the markers sit on
  the scope; at 20° altitude the high-deck point is ~25 km downrange — length of the ray
  IS the inclination readout, physically. Clamp: hide pierce markers below 3° altitude or
  beyond 150 km; a dashed ray connects marker → farthest visible point.
- **Readout chip:** `Az 214° · Alt 43°` beside the layer toggle (word+number, hue-free).
- **Degrade:** no mount connected (or no az/alt in status) → site marker only, chip reads
  "no mount". Az/alt source: the mount block already present in `store.status` (the plan
  pins the exact field names during seam extraction; MountPanel already displays alt/az).
- All overlay geometry is pure `lib/mercator.ts` math → self-executing tsx tests (golden
  vectors: zenith → zero offset; alt 45°, deck 9 km → 9 km; alt 20° → ~24.7 km; bearing
  0/90/180/270 destination sanity at mid-latitudes).

## 12. UI: warning popup + chips

- Popup effect (in the always-mounted shell, beside ConfirmHost usage): watches
  `useWeatherAlertKey`; on bump → `confirmDialog({ title: "High cloud forecast tonight",
  body: <peak %, dominant layer, HH:MM–HH:MM window, threshold>, tone: "warn",
  mode: "ok" })` — acknowledge-only popup (the user-required behavior; `ConfirmDialog.tsx:140-144`).
  Holders only (the event never reaches non-holders anyway, §8).
- Persistent chip while `alert` is non-null: on the Sky conditions panel and the
  SessionsPanel automation card (beside the no-monitor chip, `SessionsPanel.tsx:177-182`):
  `high cloud tonight — auto-resume will hold unless overridden` with the
  ignore-tonight toggle adjacent (operator cap).
- When ignore-tonight is set: chip text flips to `weather override active — resume will
  ignore clouds tonight` (warn tone, icon + words, hue-free).

## 13. Error handling summary

- Fetch failures: keep last forecast, mark stale by age, log outcome-only, never cache
  failures, never die (loop guard). Stale/missing → veto fail-open (§4), warning simply
  not evaluated (no data = no alert), panel shows STALE chip.
- Tile proxy: upstream failure → 502 `no-store`; disabled → 404 `no-store` zero-httpx;
  invalid layer/z/x/y → 422.
- Ignore-tonight with no resolvable night → 409 `{detail:{code:"no_night"}}`; UI toasts
  the parsed detail (B ApiError plumbing).
- Config writes: bounded fields → 422 at the boundary; version conflict → 409 through the
  B-hardened redaction seams (secrets + site both covered structurally).
- UI: all weather fetches no-op for non-holders; panel never renders partial data without
  the staleness chip.

## 14. Testing

Server (`cd server && ./.venv/Scripts/python.exe -m pytest -q`):
- `tests/test_weather.py`: service tick with injected clock + monkeypatched httpx
  (`test_survey.py` `_FakeClient`/`_Boom` + `test_resume_arm.py` clock patterns):
  cadence due/not-due; enabled-flip within one tick; zero-httpx-when-disabled (`_Boom`);
  failure keeps last forecast + stale marking; Astrospheric independent fail-soft; breach
  math (consecutive-sample rule incl. sustain > window edge cases); warning latch
  once-per-night + reset on night change; alert payload shape; `bus.log` warning contains
  no coordinates.
- `tests/test_resume_arm.py` additions: veto matrix — breach → vetoed (assert `_retry_at`
  latch, engine not started, log written); stale → starts; disabled → starts;
  ignore-tonight → starts; no-weather-service → starts (back-compat).
- `tests/test_rbac_enforcement.py` additions: all four routes gated (viewer 403 on GET
  weather + tiles; operator can POST ignore-tonight but not config; boot assertion covers
  them via `@declare`); WS `weather` event dropped for non-holders on BOTH lanes
  (`test_remote_relay.py` strip-test pattern); `astrospheric_api_key` absent from
  redacted config, config echoes, and 409 bodies.
- Tile proxy tests: Literal 422, zoom clamp, TTL cache hit skips network, PNG validation,
  failure not cached, disabled = 404 + zero-httpx.

UI (self-executing, `npx tsx ui/src/lib/__tests__/<name>.test.ts`; never in CI):
- `weather.test.ts`: normalize (stale derivation, clamping, missing series), breach-window
  helper golden cases.
- `mercator.test.ts`: tile math round-trips, destPoint golden vectors, pierce distances
  (zenith/45°/20°/clamps).
- `cd ui && npm run build` strict after every UI task.

## 15. Out of scope (v1)

- Sub-project D (dynamic cloud-dodging, forecast-driven target reordering, dormancy).
- Radar animation loops / multi-frame playback (single latest frame only).
- Per-target cloud evaluation; pierce-point-driven automation (display only in C).
- Astrospheric providers beyond seeing/transparency; non-NA fallbacks.
- Push-notification changes (existing warning-level sink mapping is reused as-is).
- Any coarse-weather view for non-`view.site_precise` principals.

## 16. Global constraints (copy into the plan verbatim)

- `weather.enabled` default FALSE; zero outbound weather httpx when disabled —
  test-enforced with the `_Boom` pattern for BOTH the poller and the tile proxy.
- ALL weather data/UI is `view.site_precise`-gated; WS weather events DROP (not strip)
  for non-holders on BOTH WS lanes; no new redaction lane — extend `_redact_ws_event`.
- `astrospheric_api_key` is a secret: scrubbed in `redacted()`, absent from config echoes
  and 409 bodies, never logged, write-only in the UI.
- No coordinates in ANY log line or alert text (times + percentages only).
- One shared threshold (`cloud_threshold_pct` + `sustain_minutes`) drives warning AND
  veto; veto fail-open on stale/missing data; safety monitor remains the hard guard.
- "Tonight" = `_night_dusk`/`_night_dawn` with `cfg.safety.twilight_deg` — the ResumeArm
  definition, everywhere in C.
- IEM/Open-Meteo/Astrospheric origins hardcoded server-side; browsers only ever hit
  `/api/weather/*`. Upstreams verified live 2026-07-16 (§3, §6) — the plan copies them
  verbatim. Astrospheric cadence is 6 h and MUST NOT be increased (5 credits/call,
  100/day Pro budget).
- No new npm dependencies; charts hand-rolled SVG; series differentiated by dash/width/
  label, never hue; imagery gets `var(--img-filter)` at night.
- Server tests via `cd server && ./.venv/Scripts/python.exe -m pytest -q`; UI tsx tests
  self-executing, never in CI; `npm run build` strict gate.
- Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` +
  `Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL`; CI claims
  only via `gh run watch`/`gh run view`.
