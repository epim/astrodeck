# Site Location UI + Strip-Entirely Privacy — Design (Sub-project B)

**Program context:** Sub-project B of the multi-night program (A sessions/quotas SHIPPED 2026-07-15 → **B this spec** → C weather → D cloud-dodging). B delivers the site-location settings UI and closes the location-privacy surface so C can build weather/radar features on top of a trustworthy gate.

**Carry-outs to sub-project C (recorded here, implemented there):** the weather/forecast/radar UI and any weather API endpoints MUST gate on `view.site_precise` (the same capability this spec hardens); high-cloud-cover night warning popup; ResumeArm `resume_veto()` weather implementation.

## User decisions (recorded)

- **Strip entirely** (program decomposition round, 2026-07-14): principals without site privilege see NO coordinates anywhere; weather/radar UI hidden for them; admin sees everything.
- **Keep ephemeris, strip geolocators** (2026-07-15): non-holders keep dark windows, transit times, and altitude curves (remote Atlas/planning keeps working) but lose all direct geolocators: coordinates, elevation, site name, `place_hint`, LST. Residual coarse inference from ephemeris (~city/region scale: dusk+dawn pins longitude to ~1°, altitude curves imply latitude) is **accepted and documented**.
- **Capability-based scope, everyone** (2026-07-15): the strip applies to ANY principal lacking `view.site_precise`, regardless of connection origin (LAN or relay). One uniform rule; no origin-keyed second path. LAN viewers/operators lose coordinates too; grant the cap to a custom role if that is ever wanted.

**Review rounds (recorded):** bridge review by antigravity 2026-07-15 — verdict SHIP with three advisories, all folded in: no-coords-in-logs constraint (§7; `/api/logs` is viewer-visible), mount read-back rejects non-finite/out-of-range sentinels (§3), breaking-change release note for non-admin API consumers (§2). Runtime-validation concern evaluated, no change: the UI uses plain TS casts, no Zod/io-ts schemas exist.

## 1. Ground truth (existing code this spec builds on)

- **`Site` model** `server/astrodeck/config.py:55-63`: `name` (default "[SITE-LABEL]"), `latitude` (signed, +N, ±90), `longitude` (**signed East-positive**, ±180 — load-bearing convention per config.py:11-16), `elevation_m` (−430..9000), `is_default`, `horizon_min_deg` (default 15.0). Persisted in `AppConfig` via `ConfigStore` (atomic JSON, `.bak` recovery). `ConfigStore.set_site(site, expected_version)` (config.py:489-495) flips `is_default=False` and bumps `version`; `ConfigVersionConflict` → 409.
- **`PUT /api/site`** (alias `POST`) `server/astrodeck/api/app.py:1321-1363`: body `SiteSaveBody {site, version, horizon_min_deg}`; field-level RBAC `_require_site_field_caps` (coords need `config.site_optics`; a present `horizon_min_deg` additionally needs `config.safety`); preserves stored `horizon_min_deg` when omitted; pushes site to mount (`hub.push_site_to_mount()` hub.py:865-882, push-only); publishes `config` event; returns redacted `_config_payload()`.
- **Redaction seam** `server/astrodeck/api/redact.py`: `_redact_site_for` (REST/hello) and `_redact_ws_event` (copy-on-write per WS frame) currently **coarsen** lat/lon to 0.1° (~11 km) for principals lacking `CAP_VIEW_SITE_PRECISE = "view.site_precise"`. Both the LAN WS lane (app.py:2814-2887) and the relay lane (`remote/relay_client.py:515-604`) call these two functions at hello and per frame, with 60 s re-auth and mid-stream downgrade re-tightening. Because `relay_client.py` runs home-side, redacted frames are what transit the relay.
- **Capability vocabulary** `server/astrodeck/auth/capabilities.py:19-35`; `view.site_precise` is held by `admin` only (viewer = `{view.status, view.preview}`; operator adds `control.capture/guide`). Remote + `none` provider is hard-denied (`resolve_principal(..., remote=True)` deps.py:149-159), so the open-default admin never reaches the relay.
- **Known leak surface (what this spec closes):**
  1. `elevation_m` and site `name` are NOT in the coarsen set — they pass through unredacted today.
  2. `GET /api/site/sky` app.py:1386-1404 (gated only `view.status`, no redaction) returns `place_hint` (names the region) and `lst_str` (LST ≡ longitude) — direct geolocators.
  3. `GET /api/visibility` and `GET /api/visibility/order` (catalog/visibility.py:510-568) have **no auth gate at all** (explicitly exempted from the boot RBAC assertion, app.py:2917-2931).
- **UI**: no site form exists anywhere. `view.site_precise` exists in `ui/src/types.ts:1053` but is consumed nowhere client-side. Settings → Connect tab hosts `DriversPanel` + `SkyAtlasPanel` (`SettingsView.tsx:145-150`) — the pattern to follow. `store.site: SiteInfo | null` + `useSite()` already exist; Atlas shows a default-site nudge on `site.is_default`; observer location is consumed server-side only (clients send RA/Dec, server supplies coords).

## 2. Privacy model: strip entirely

**Definition.** For any principal lacking `view.site_precise`, every site payload has the keys `name`, `latitude`, `longitude`, `elevation_m` **removed** (absent, not nulled). Retained: `is_default` and `horizon_min_deg` — the UI needs both (default-site nudge, alt-limit display) and neither reveals location.

**Mechanics.**
- Replace `_coarsen_latlon` in redact.py with `_strip_site(site: dict) -> dict` that deletes the four keys. Coarsening is retired entirely; no caller or test keeps it. `_redact_site_for` and `_redact_ws_event` keep their signatures and call sites (REST: `/api/status`, `/api/summary`, `/api/config`; WS: hello + every event in BOTH lanes; both the `site` and `config.site` copies). Copy-on-write behavior in `_redact_ws_event` is preserved (bus `Event.data` is shared across subscribers).
- Mid-stream downgrade (60 s re-auth) re-tightens to stripped, exactly as coarsening does today.
- `GET /api/site/sky`: for non-holders, omit `place_hint` and `lst_str` from the response; keep `sun_alt_deg` and `dark_window` (per the ephemeris decision). Holder response unchanged.
- `GET /api/visibility` and `GET /api/visibility/order`: add the standard `require(CAP_VIEW_STATUS)` gate + `@declare(CAP_VIEW_STATUS)`, and remove both routes from the boot-assertion exemption list at app.py:2917-2931. Response bodies unchanged (ephemeris is kept for all status viewers by decision).
- `PUT /api/site` response (`_config_payload()`) is already redacted per-principal; a writer holding `config.site_optics` but not `view.site_precise` would get a stripped echo of what they just wrote. Accepted edge (realistic writers are admins); document with a comment, do not special-case.
- **Residual leak (documented, accepted):** dark windows, transit times, and altitude curves permit coarse location inference. The user chose to keep remote planning functional over closing this channel.
- **Breaking change (document, don't soften):** third-party scripts polling `/api/status`/`/api/summary` with a non-admin token stop receiving `latitude`/`longitude` (today they get coarsened values). This is the intended privacy outcome; record it in the release notes / changelog entry for the shipping commit.

**Why the site name is stripped:** user-chosen names routinely contain addresses or place names ("Barn at 12 Oak Lane").

## 3. Mount GPS read-back

New endpoint `GET /api/site/mount-gps`, gated `require(CAP_CONFIG_SITE_OPTICS)` + `@declare(CAP_CONFIG_SITE_OPTICS)` (it exposes precise coordinates; `config.site_optics` is the cap that may write them, and admin holds it).

- Reads `sitelatitude` / `sitelongitude` / `siteelevation` from the connected Alpaca mount via the same best-effort raw client used by `push_site_to_mount()` (hub.py:865-882) — a new `hub.read_site_from_mount()` alongside it. Today the site↔mount channel is push-only; this is the first read-back.
- Response (always 200): `{available: bool, latitude?: float, longitude?: float, elevation_m?: float, detail?: str}`. `available: false` with a human `detail` when: no mount connected, mount is not Alpaca-backed, any property read fails, the mount reports exactly `(0.0, 0.0)` (unset-GPS sentinel on common mounts — `detail: "Mount reports 0,0 — GPS likely unset"`), or **any returned value is non-finite (NaN/inf) or out of the Site model's ranges** (lat ±90, lon ±180, elevation −430..9000 — some mounts return junk sentinels like `99.0/181.0` when unset). Genuine Null-Island observers can still type `0, 0` manually; the read-back is only an assist.
- **Assist only — never persisted.** The UI fills the form draft; the user reviews and saves explicitly through the normal `PUT /api/site` path.

## 4. UI: Settings → Site panel

New `ui/src/components/settings/SitePanel.tsx`, mounted in the Connect tab's left column between `DriversPanel` and `SkyAtlasPanel` (`SettingsView.tsx:145-150`). Follows the DriversPanel pattern exactly: `Panel`/`Field`/`.field` inputs, `btn btn-accent` actions, `run(fn, okMsg)` busy+toast helper, 403 special-cased to a capability message, lock-note footer when read-only.

**Fields and conversion.** `name` (text); latitude as magnitude 0–90 + N/S select; longitude as magnitude 0–180 + E/W select; `elevation_m` (number, −430..9000). Signed storage convention (lat +N, lon +E) is converted at the boundary by pure helpers in a new `ui/src/lib/site.ts` (`toSigned`/`fromSigned`, `validateLat`/`validateLon`/`validateElevation`, `formatCoord` to 6 dp). Inputs disabled without `config.site_optics` (via existing `useCan("config.site_optics")`), with the standard lock note.

**Reading state.** The authoritative copy is `config.site` (`useConfig()`); `store.site` remains the live-status projection. For principals receiving stripped payloads, coordinate fields render a "Hidden" placeholder — driven by a new `useCanViewSitePrecise = () => useCapability("view.site_precise")` hook added to `lib/caps.ts` (the convention at caps.ts:123-131). This hook is also the gate sub-project C's weather panel will use. A warn chip shows when `config.site.is_default` ("Using default location (0, 0) — sequencing windows and Atlas visibility are wrong until set").

**Save.** `PUT /api/site` with `{site, version: config?.version ?? null}` via a new typed `ui/src/api/site.ts` (`saveSite`, `getMountGps` — mirroring `api/backends.ts` one-function-per-route). On success: `loadConfig()` re-GET (never partial-merge). On 409 version conflict: `loadConfig()` + error toast "Config changed elsewhere — reloaded, re-apply your edit". `horizon_min_deg` is never sent — the Safety panel owns it, and omission preserves the stored value (P2-1 guard at app.py:1331-1337).

**"Use my location" button.** Rendered only when `isSecureContext && "geolocation" in navigator` (no prior geolocation use exists in the codebase; the UI is served over plain LAN http where the API is unavailable outside localhost). When unavailable, a hint line replaces it: "Browser location needs HTTPS or localhost — enter manually or use mount GPS." When available: `getCurrentPosition` with `enableHighAccuracy: true`, 10 s timeout; success fills the drafts (6 dp); failure → error toast with the browser's message. Fills drafts only — user still saves explicitly.

**"Use mount GPS" button.** Calls `getMountGps()`; `available: false` → info toast with `detail`; otherwise fills the drafts. Always enabled for `config.site_optics` holders (the endpoint itself reports unavailability).

**Types.** In `ui/src/types.ts`, the strippable fields on the site shapes that arrive over the wire (`SiteInfo` at :890-895, `RigStatus.site` at :89-96, and `AppConfig`'s `Site` at :454-459) become optional: `name?`, `latitude?`, `longitude?`, `elevation_m?` (`is_default` and `horizon_min_deg` stay required). Strip-aware consumers to adjust: `PreflightStrip.tsx:123-131` site signature uses `site.latitude ?? "hidden"` (behavior unchanged — it already skips fetching when `is_default`); `AtlasView` uses only `horizon_min_deg`/`is_default` (kept fields). The build is strict (`tsc -b`), so the compiler enumerates every consumer.

## 5. Testing

**Server** (conventions of `server/tests/test_rbac_enforcement.py` — in-process fakes via monkeypatch + TestClient, no unittest.mock, `_principal_with(*caps)` / `principal_for_role`):
- Rework T-RBAC-13 (lines 361-415) from coarsen-asserts to strip-asserts: seed precise site (`40.123456, -74.654321`, elevation 123.4, name "Secret Barn"); viewer responses on `/api/status`, `/api/config`, `/api/summary` (both `site` and `config.site`) and the WS hello have NO `latitude`/`longitude`/`elevation_m`/`name` keys and DO have `is_default` + `horizon_min_deg`; admin sees exact values.
- `/api/site/sky`: viewer response lacks `place_hint` and `lst_str`, keeps `sun_alt_deg` + `dark_window`; admin gets all fields.
- `/api/visibility` + `/api/visibility/order`: unauthenticated → 401/403 (fail-closed); viewer (`view.status`) → 200; boot RBAC assertion passes with the exemption removed.
- `/api/site/mount-gps`: viewer → 403; `config.site_optics` holder with no mount → `{available: false}`; with sim/fake Alpaca mount reporting coords → values echoed; `(0.0, 0.0)` → `available: false` with the GPS-unset detail.
- Relay (`test_remote_relay.py:586-796` pattern): tunneled hello + streamed frames stripped for viewer, full for holder, and mid-stream downgrade re-tightens to stripped.
- Log hygiene: after a `/api/site` save and a `/api/site/mount-gps` read against the seeded precise site, `bus.log_history` contains no occurrence of the precise coordinate strings.
- Run: `cd server && ./.venv/Scripts/python.exe -m pytest -q` (currently 1037 passed; all green required).

**UI:**
- New `ui/src/lib/__tests__/site.test.ts` (self-executing `npx tsx`, inline pass/fail harness like `caps.test.ts`): validation ranges (±90/±180/−430..9000, NaN/empty rejected), signed↔magnitude+hemisphere round-trips including 0 and boundary values, `formatCoord` precision.
- `npm run build` (`tsc -b && vite build`, strict `noUnusedLocals`/`noUnusedParameters`) green — this is also the compile-time sweep for the optional-field change.
- UI tsx tests never run in CI; they run locally and must pass before commit.

## 6. Out of scope

- Weather fetching, forecast/cloud panels, radar tiles, high-cloud warning popup, ResumeArm `resume_veto()` weather gate (all sub-project C — but C's UI gates on `view.site_precise` per carry-out).
- Dynamic cloud-dodging (D).
- `horizon_min_deg` editing (Safety panel owns it; this panel never sends it).
- Relay server changes (redaction is home-side; the relay stays untrusted forward-only; for non-holders, precise coordinates never leave the home network at all).
- Changing role→capability assignments (viewer/operator/admin keep their current cap sets).

## 7. Constraints (project-wide, binding on the plan)

- Longitude is stored **signed East-positive**; latitude signed +N. UI collects magnitude + hemisphere and converts at the boundary (`lib/site.ts`). Exact convention per config.py:11-16.
- Strip set is exactly `{name, latitude, longitude, elevation_m}`; retain set is exactly `{is_default, horizon_min_deg}`.
- `view.site_precise` remains admin-only in the role map; `config.site_optics` remains the write cap; no new capabilities are introduced.
- All redaction changes live in `redact.py` (single seam shared by both WS lanes); no redaction logic in route handlers beyond calling the seam. Any FUTURE event or payload that embeds site data must place it at `site` / `config.site` so the seam catches it — a code comment at `_redact_ws_event` states this contract.
- **Precise coordinates must never enter `bus.log`** — `GET /api/logs` (app.py:2687-2690) returns `bus.log_history` to any `view.status` holder, which would bypass redaction entirely. `push_site_to_mount` already logs outcome-only (hub.py:880-882); the new `read_site_from_mount()` and the `/api/site` save path must do the same (log presence/success/failure, never values).
- Night-mode: the panel uses semantic tokens only (`text-warn`/`text-bad`/`.field`/`btn*`); status never encoded by hue alone.
- Commit trailer on every commit: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` + `Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL`.
- Do not touch: the catalog HiPS/tile engine and survey-pack code, `native/`, sessions/quota code shipped in A, weather (C). Exception: adding the auth gate to the `/api/visibility[/order]` routes in `catalog/visibility.py` IS in scope (§2).
