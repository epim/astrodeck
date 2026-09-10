# AstroDeck next front-end - deviations from the design

This is the reviewer-facing record of every place the shipped code (branch
`feat/ui-next`, `fec53970`..`a34338cf`, 39 commits ahead of `main`) departs
from `ASTRODECK-UPDATE/design_handoff_astrodeck_mobile/README.md` and
`GAP-ANALYSIS.md`. Every row was checked against the working tree, not just
against a plan document - a deviation a later commit already closed is not
listed here (see the report that accompanies this document for the ones that
were dropped for exactly that reason). Sources: `ARCHITECTURE.md`, each hub's
own plan (`<scratch>/plan/hub-*.md`, section H or E or F "Deviations and
resolved questions"), `<scratch>/plan/INTEGRATION-BACKLOG.md`, and
`<scratch>/plan/REVIEW-FINDINGS.md`.

Status is binary: **deliberate** (the design asked for something the engine
cannot do, or asked for it dishonestly, and this is the permanent shape) or
**follow-up** (known, scoped, not done - tracked in the Follow-ups list, not
re-litigated here).

## Sky

| Design says | What shipped | Why | Status |
|---|---|---|---|
| README 1 (line 67): the lens dial holds 7 target kinds | 5 kinds (galaxies, nebulae, clusters, planets, moon). Satellites and comets are absent - no ring, no chip, no lens slot, not even greyed | No ISS or comet ephemeris anywhere in the engine (`server/astrodeck/catalog/solar_system.py:71-81` has Sun/Moon/Mercury..Neptune only, ERFA `plan94`/`moon98`); a kind with no data source is not a filter a user can turn on. `hub-sky.md` H.1 | deliberate |
| README 1 (line 71): "a MOSAIC stage enters the flow" | DONE on a multi-panel framing writes N plan targets sharing a `mosaic_group` via `panelsToTargets`/`addTargetsToPlan`; the flow card shows a synthetic MOSAIC row with the footnote naming the shape. The quick-session route (`GENERATE FLOW`) itself still takes one target | `nodeDefs` has 21 node types and none is `mosaic` - the engine's mosaic mechanism IS N targets sharing a group, not a flow stage (`hub-sky.md` H.6). `SkyHub.tsx` (comment at the `panelsToTargets` call site) and `hubs/sky/sheets/quick.tsx:322-350` | deliberate (previously broken - review #3 found `panelsToTargets` called by one test and nothing else; fixed in `b83751f8`, see Legacy defects below) |
| README 3 (line 83-84): quick-session HOW LONG is a duration dial, subs computed from it | `subs` is PER FILTER and shared across every checked filter (one sub per filter per pass); the phone's duration arithmetic is advisory display math over the engine's real allocation | `FlowQuickBody.subs` is one count, not per-filter (`app.py:1382`, `wizard.py:575`); `cycle.params.plan`/`cycles` cannot express a distinct count per filter at all. `hubs/sky/sheets/quick.tsx:204` (`passesFor`), `hub-sky.md` H.7 | deliberate |
| README screenshot 09/design: manual VIDEO capture for a locked planet/moon target | The lock card's `RECORD <NAME> · VIDEO` CTA still routes to Rig - Capture in video mode (matches `proto/logic.js:360`), where the control is honest-locked (see the Rig table's VIDEO row) | `hub-sky.md` H.2; `hubs/rig/capture/CaptureScreen.tsx:10-13` | deliberate |
| README 1: a full 3D skydome card sits on the Sky finder (prototype only) | No SKYDOME card on Sky; a `SKYDOME ›` pill in the status row links to the Weather hub's dome with `?target=<lockId>` | README's own screen-1 layout list and screenshot 01 do not show one; ARCHITECTURE section 11 assigns `SkyDome` to Weather. Two hemisphere renderers in two hubs would be a second truth about the sky. `hub-sky.md` H.3, `hubs/sky/cards/StatusRow.tsx:10,77` | deliberate |
| README 1 (line 71): "camera-rotation dial 0-165 deg in 15 deg steps" | Kept as specified (0-165, 15 deg steps) on the Sky hub's FRAME dial; the Atlas's own manual stepper (0-360, step 5, tablet/desktop) is untouched | A sensor at 180 deg frames identically to 0, so 12 stops cover every distinct framing; the two dials serve different surfaces and both write the same `framing.rotation_deg`. `hub-sky.md` H.4 | deliberate |
| README 1: empty-lock card titled "NOTHING IN THE RETICLE" | Card reads "NO CATALOGUE TARGET HERE" (the prototype's later wording), with `IMAGE THIS PATCH` | The two titles contradict each other on one card - a button that images what is in the reticle cannot sit under a title saying there is nothing there. Copy rule "every string must carry information the pixels do not" agrees. `hub-sky.md` H.5 | deliberate |
| README ground rule (line 8): "Hyphens, never em-dashes, in UI copy" | `components/atlas/CatalogSearch`'s placeholder, `"Search catalog — e.g. M 31"`, is mounted verbatim with its em-dash intact, because the server matches that exact string for the beginner example | Re-typing the widget to convert the dash would lose the zero-result copy, the outside-tap dismissal and the stale-response guard that come with reusing the component as-is. `hubs/sky/sheets/targets.tsx:21-24` | deliberate |
| README "Formulas to lift": wind direction as a bearing | `weather.now.wind_dir_deg` (Open-Meteo `winddirection_10m`, the direction wind comes FROM) is converted `+180 % 360` before it reaches `advect()`, which wants a TOWARD bearing | Getting this backwards points every drift arrow the wrong way while still looking plausible; `hub-sky.md` H.8 pins a seeded DOM test (225 deg -> NE arrow) against it | deliberate |
| GAP-ANALYSIS 6: "Either the engine gains ephemerides... or those kinds are hidden until it does" | Resolved as above (5 kinds); additionally the useSolarSystem hook itself had a real bug, now fixed | `hubs/sky/finder/model.ts:298-306` - reading `rows` instead of `results` off `GET /api/catalog?q=` meant no planet or the Moon ever reached the finder; `sheets/targetsModel.ts` already read `results` correctly. Fixed - see Legacy defects below | deliberate (post-fix) |

## Session

| Design says | What shipped | Why | Status |
|---|---|---|---|
| README 4 (line 92): six phase-pill words (SLEWING/FOCUSING/GUIDING/CAPTURING/PAUSED/DONE) plus the incident states | Eleven pills: the six, plus SOLVING, COOLING, WAITING, STOPPING, ERROR, NINA IS DRIVING | The engine publishes those states (`engine.py:2432,4525,1810,971,1457`; `SequenceState.state` includes `aborting`, `nina_native`) - a pill reading CAPTURING through a cooling gate is the UI lying. `hub-session-capture.md` H, D1 | deliberate |
| README 4 (line 91): campaign `night n of ~total` | `total` carries a tilde and is a projection (3.5 clear-hours/night from the prototype), not a server value | `server-routes.md` 4.13: "there is no persisted goal-per-filter object returned by the server." D2 | deliberate |
| README 4: night-strip clouded nights marked amber | Marked with the word "held", not only a color/glyph | Night mode collapses warn/bad toward the same red; status must never be hue- or glyph-only. D3 | deliberate |
| README 4 (line 95): channel strip lets you "view that channel alone" | Single-channel view is a CSS tint of the composite image plus an explicit text line, not a re-rendered single-channel frame | `sessionStackImageUrl` / `GET /api/sequence/stack/preview.jpg` take only `size` and `seq` - no channel parameter exists on the wire. D4 | deliberate |
| README 4 (line 95): SOFT/AUTO/HARD stretch segmented control | Writes `localStorage["astrodeck-next-stretch"]`, not `store.stretch` | `store.stretch` is the live-preview B/M/W transfer function (`types.ts:506-514`); hijacking it would change the Inspect histogram too. D5 | deliberate |
| README 5 (line 105): "DOWNLOAD N SUBS · size with Wi-Fi vs relay ETA" | A `~38 MB/s` fixture is not shipped; a measured EMA throughput (`filesData.ts` `noteThroughput`, `EMA_ALPHA`, keyed by `via`) drives the ETA, falling back to bytes-only until a transfer has been measured on this connection | A plain `<a download>` exposes no live progress to simulate against; the fixture number would be a lie the first time it ran on a real link. `hubs/session/sheets/files.tsx:332-365`, `filesData.ts:460-538`. D6, D9 | deliberate |
| ARCHITECTURE 8 / `GAP-ANALYSIS` 1: FITS gated on admin | Files sheet locks FITS behind "FITS originals need syncer or admin access" | `accessPhrase("view.media")` (`ui/src/lib/caps.ts:79-119`) - a syncer genuinely holds `view.media`; hand-written role copy is the exact defect class `accessPhrase` exists to prevent. `hubs/session/sheets/files.tsx:354`, pinned by `__tests__/filesDom.test.tsx:15-18`. **Contract amendment**: `ARCHITECTURE.md` section 8 said "admin access" and has been corrected in place. D7 | deliberate |
| README 5: download the selected filters as a zip | A partial filter selection falls back to a `picked` selection policed by `downloadPlan`, with an "ALL N INSTEAD" escape when the pick is too large | iOS serves one file at a time in the foreground, so N separate zips is not an option; `PICKED_URL_BUDGET = 6000` refuses rather than silently truncating. D8 | deliberate |
| README 6 (line 109): Gallery card footer, legacy wording included "you clear them from the tablet" | That sentence is dropped | The phone now has its own DELETE on the card, so the sentence would be false. D10 | deliberate |
| README (platform, line 25): "the Flows canvas becomes a full surface" at tablet/desktop, in the new language | `components/flows/FlowsView` is mounted **whole**, its own `FlowHeader` included, at >= 768 px, duplicating shell chrome | `FlowHeader` carries LIBRARY / PLAN / provider badge / validation chip / ETA / TONIGHT / RUN, none of which the next shell has yet; forking it now would fork logic the reviewer would then have to keep in sync. D11 | follow-up (named for a later "hub-8" cleanup) |
| README 4: "Suggest settings" action on Capture | Stays on the Rig - Capture screen; its copy names the Camera sheet as the destination for the photometry inputs it used to hold inline | Sub-length is a capture-time decision; the underlying photometry inputs moved to hub 4's Camera sheet, so the string has to say where they went rather than pretend they are still here. D13 | deliberate |
| README 9 (line 131): result card offers "SAVE TO GALLERY" for a captured-not-saved frame | Button reads "RE-SHOOT AND SAVE" | `POST /api/capture` writes to disk at capture time or not at all - there is no "promote an already-taken preview" route, so the only honest action is to shoot it again with saving on. D14 | deliberate |
| README: the `view.media` note belongs to the preview toolbar area | The note sits **under** `PreviewToolbar`, not inside it | The toolbar takes no capability prop; forking it to add one would fork the download-gating logic that is otherwise kept in lockstep with the server's retention constants. D15 | deliberate |
| README: Inspect mode shows histogram/stars/stats for any frame | Inspect on `src=stack` (the live composite) shows neither - the composite JPEG carries no `PreviewInfo` | Fabricating stats for a JPEG that never had per-pixel data would be the "constant-where-it-should-vary" defect this repo already has a name for. D16 | deliberate |

## Rig

| Design says | What shipped | Why | Status |
|---|---|---|---|
| README 8 / GAP-3: Camera BINNING `1x1 / 2x2 / 3x3` | Powers of two, `1x1 / 2x2 / 4x4` | `status.camera.max_bin` defaults to 4 (`types.ts:147`); GAP-ANALYSIS 3 itself: "4x4 exists in AstroDeck - design offers 1/2/3; use 1/2/4". E1 | deliberate |
| README 8: Camera `USB` readout | Fourth tile is `E-GAIN` (`status.camera.egain` / `egain_learned`) | No `usb_limit`/`usb_bandwidth`/`BandWidth` field anywhere under `server/astrodeck`; E-GAIN is real and gives the e-/ADU learn loop a home. `hubs/rig/sheets/camera.tsx:22-23,357-360`. E2 | deliberate |
| README 8: Focuser `TEMPERATURE COMPENSATION` toggle, "-14 steps per C" | Omitted entirely; the TUBE tile and a `REFOCUS AFTER n C` stepper carry what is real | No temperature-compensation field, coefficient or loop anywhere server-side; the only temperature-driven behavior is `standards.refocus_on_temp_delta_c` (`config.py:907`), a refocus TRIGGER, not a per-degree offset. `hubs/rig/sheets/focuser.tsx:50-53,689-697`. E3 | deliberate |
| README 8: `AUTOFOCUS RUNS WHEN` chips (filter change / every 60 min / HFR +15%) | Filter change -> `SHIFT BY FILTER OFFSET ON A CHANGE` switch (the engine shifts by the stored offset instead of refocusing). Every-N-min -> read-only `REFOCUS EVERY n FRAMES`, sourced from the plan. HFR+15% -> read-only `HFR GATE xn -> <action>`, tapping to Safety | `standards.apply_filter_offsets` (`config.py:902-904`); `SequencePlan.autofocus_every` counts FRAMES per-plan (`sequence/models.py:282`); `escalation.hfr_reject_factor`/`hfr_reject_action` is a frame-quality gate, not a refocus trigger. E4-E6 | deliberate |
| README 8: Mount `TRACKING: sidereal / lunar / solar / king / off` | Four stops - `king` dropped | `TRACKING_RATES` is `("sidereal", "lunar", "solar")` with the comment `ASCOM's "King" rate is out of scope (YAGNI)` (`devices/base.py:236-239`). E7 | deliberate |
| README 8: Mount `SLEW RATE 0.5x...800x` tile | No separate tile; the rate lives in the `SlewPad`'s own three-stop centre control (GUIDE / 8x SID / 0.5 deg/s) | `/api/mount/move` clamps `rate_deg_s` to `TOUCH_MAX_RATE_DEG_S = 0.6` (`lib/slewController.ts:36`); 800x would be ~3.34 deg/s, five times the enforced ceiling. E8 | deliberate |
| README 8: Mount `RA STEP / DEC STEP 1'...10 deg` per pad tap | Dropped; the pad's existing tap model (fixed pulse or brief rate move) is kept, including the NINA relative-goto branch | No per-tap arcminute verb exists (`lib/slewController.ts:150-259`). E9 | deliberate |
| README 8: Mount `offset from target - RA 0 - Dec 0` | Shows `mount.pointing.error_arcmin` ("measured at the last solve") or "offset unknown - run SOLVE + SYNC" | The engine publishes no cumulative pad-offset state; the prototype's field is fixture-only. E10 | deliberate |
| README 8 (line 126): five safety bars (rain/wind/cloud/power/humidity) with red limit ticks, always drawn | One bar per key actually present in `reading.detail`; ticks only where config supplies a limit; a single status row when `detail` is absent | `SafetyReading` is `{is_safe, reason, source, detail?, stale, ts}` with no fixed key set, and `SafetyConfig` carries no wind/cloud/humidity thresholds - the monitor device owns them. `hubs/rig/sheets/safety.tsx:9-12`. E11 | deliberate |
| README 8: chain `STOP CAPTURE -> PARK -> CLOSE -> WARM COOLER -> NOTIFY`, always drawn in full | Nodes built from config (`on_unsafe`, `close_dome_on_unsafe`, `cooling.warm_ramp`, whether a sink exists); unlit nodes dim with their own reason | The chain is config-conditional, not a fixed pipeline. E12 | deliberate |
| README 8: Power `mount/camera/USB locked while a session runs` | A name-heuristic (`/mount\|camera\|usb/i` on the port label) drives the lock, disclosed in a footer note | `SwitchPort` carries no lock flag; the prototype hard-codes it (`logic.js:112`). E13 | deliberate |
| README 8: Guider `DITHER BETWEEN SUBS` on/off, "every N subs" | A `DITHER n PX` stepper (0 = off) plus a read-only "every n subs - set per night in the plan" line and a `DITHER NOW` action | Cadence is per-plan (`SequencePlan.dither_every`); distance is rig-level (`config.guide.dither_pixels`, `config.py:486`) - two different scopes the design drew as one toggle. E14 | deliberate |
| README 8: Guider `AGGRESSION` / `MIN MOVE` as single dials | The dial writes BOTH axes through `validateGuideSettings`/`toSnake`, with the note "Sets both axes. Per-axis values are in the tuning editor" | They are per-axis params (`ra_params`/`dec_params`) under the hood. E15 | deliberate |
| README 8: Guider STAR tile `SNR 48 - mag 8.1 - 3.1 px` | Renders `snr` only; magnitude and size are dropped | Only `snr` is on the stats bus - the rest would be invented. E16 | deliberate |
| README 8: `CameraDial` fan-out over the polar reticle | `PolarQuickBar` pickers carry the same `solve`-scope writes | There is no preview stage in the Polar sheet to hang a `CameraDial` over. E17 | deliberate |
| README 8: polar summary "checkmark aligned to X'"; rotator "warning PA n deg is outside..." | Glyphs dropped, text unchanged | House rule: no glyphs, plain text only (global no-emoji rule extends to status glyphs). E18 | deliberate |
| README 8: filter wheel drawn with exactly 7 slots | Radius `clamp(floor(74*sin(PI/n)) - 3, 12, 22)`; a list fallback above 12 slots | Wheels report `names.length`, which is not always 7. E19 | deliberate |
| README 8: "Offsets are steps relative to L" | "relative to `<reference slot name>`" | The reference slot is `ref_slot`, a configurable field, not always L. E20 | deliberate |
| GAP-3: filter wheel row needs a `type` column, including "blackout" | Derived: `opaque -> blackout`, `narrowband -> narrowband`, else `broadband` | No type enum exists on the wire (`server-routes.md` 4.2); this also lands GAP-3's missing blackout type. E21 | deliberate |
| README 8: "ADD A DEVICE scans for INDI - Alpaca - ASCOM" | Copy reads "scan this computer for USB, Alpaca and ASCOM drivers" | There is no INDI backend anywhere in the server. E22 | deliberate |
| README 8/GAP-9: sensor window heater and Power dew heaters "follow the dew margin from Weather" | Copy reads "set the power by hand" / "hold their power until you change them" | The engine does not drive either from the dew margin - no such loop exists. E23-E24 | deliberate |
| README 8: camera cooling curve as a smooth exponential | Plots the sensor temperatures this client has actually received; fewer than 3 points shows the setpoint line plus "building the curve" | The smooth curve is a prototype fabrication. E25 | deliberate |
| README 14 (fixture): camera spec line naming a ZWO ASI2600MM | Built from the driver's own `describe()` fields; any field the driver did not report is dropped rather than guessed | The design's line is a fixture, not read from a real driver. E26 | deliberate |
| README 8: polar home/park row "set - counterweights down" | `mount.parked` + `can_find_home`, or "no home sensor" | The design's line is a fixture with no engine backing. E27 | deliberate |
| GAP-2: "Missing - rotator" sheet | `RotatorCard` (the existing component) mounted inside the sheet chrome as-is | A ground-up rebuild is a second implementation of logic `RotatorCard` already has. E28 | follow-up (a full re-skin to the design's own vocabulary is named, not done) |
| README 8/11: Profiles as a popover holding rename/update-from-rig/import/export | Popover = quick switch + save + delete; a `profiles` sheet mounts the existing `ProfileList` for the rest | Those actions do not fit a popover's interaction shape. E29 | deliberate |
| Legacy `NotConnectedInterstitial` per view | Folded into the FIRST NIGHT card (four rows) on Rig - Devices; other hubs get the design's dashed browse banner instead | The new IA has one Rig screen where the legacy UI had several equipment views. E30 | deliberate |
| Legacy `BackendLinkGrid` table | Its per-role connection reason is preserved as the device row's third line | `BackendLinkGrid` is a Settings/Connection-shaped surface, not part of the new Rig - Devices IA. E31 | deliberate |
| N/A (cross-cutting) | `lib/humanize.ts`'s busy-lane sentences still name "the Mount page" / "the Focus page" / "the Rig page" at two call sites | Out of this plan's directory ownership (`ARCHITECTURE.md` section 1's file-ownership rule); the plan explicitly forbids editing it from inside a hub task. `ui/src/lib/humanize.ts:109,135`. E32 | follow-up (partially done: the busy-lane/sequence-error sentences were repointed in `2beb42f9`; these two diagnostic lines were not, and are listed by name in `INTEGRATION-BACKLOG.md`'s "Page-naming copy remaining") |
| README 9/20 (line 131): `VIDEO · PLANETS` capture - readouts, RECORD, QUICK STACK, DOWNLOAD SER | `VIDEO · PLANETS` renders honest-disabled; only STILL - FRAMES is live | Verified three ways that no video/SER capture route exists server-side (`/api/capture`, `/api/capture/loop`, `live_stack_active` and nothing video-shaped). A future route would need `POST /api/capture/video {roi, fps, exposure_ms, gain, duration_s, format}` plus a `video` busy lane and `.ser` file serving. `hubs/rig/capture/CaptureScreen.tsx:10-13,466-470`. D12 | deliberate |

## Weather

| Design says | What shipped | Why | Status |
|---|---|---|---|
| Legacy: `"Weather is off — enable it in Settings → Connect."` | `"Weather is off." "Turn it on in Weather settings."` with a CTA to the Weather settings sheet | The old copy names a route ("Settings -> Connect") that no longer exists in the new IA. `hubs/weather/conditions/verdict.ts:123-124`, used by both `ConditionsScreen.tsx:210` and `RadarScreen.tsx:52`. `hub-weather-monitor-settings.md` F.4 | deliberate |
| README 7 (line 113): the skydome draws "target path to dawn... horizon fill... +30 min ghost tiles" when dragged | `domeOverlay.tsx` draws a wind compass rose and a legend only; horizon fill, ghost tiles and the path to dawn are explicitly NOT drawn on the dome canvas, stated on screen via `NOT_DRAWN_NOTE` | `SkyDomePanel` (`components/cloudmap/SkyDomePanel.tsx:57-60`) takes only `{pointing, target}`; its yaw is a private `useState` with no hook out, and its canvas radius/center come from padding constants private to `SkyDome.tsx:120-127`. An overlay drawn at yaw 0 over a dome the operator turned 90 deg would mark the wrong part of the sky - worse than not drawing it. `hubs/weather/dome/domeOverlay.tsx:1-48`. `hub-weather-monitor-settings.md` F.6 | follow-up (named: "add an overlay slot + yaw callback to SkyDomePanel/SkyDome" - out of this wave) |

## Monitor

| Design says | What shipped | Why | Status |
|---|---|---|---|
| README 10: Monitor's Live screen carries a weather block unconditionally | Monitor's weather panels (Sky Conditions + Radar) render only at tablet/desktop | On a phone the WEATHER hub is one tab away and the design gives Monitor no weather block there; the `view.weather` caller-gate is unchanged, the breakpoint gate is additive. `hubs/monitor/live/LiveScreen.tsx:505` (`bp !== "phone" && canSeeWeather`). `hub-weather-monitor-settings.md` F.10 | deliberate |
| Legacy: `LogDrawer` owns `openLog()`/`closeLog()` as a mount/unmount side effect of the drawer opening | The Log screen (`#/monitor/log`) itself calls `openLog()` on mount and `closeLog()` on unmount | `LogDrawer` is not mounted under `NextApp`; `logOpen` is otherwise only an `unseenError`-reset flag, which this preserves without a drawer. `hub-weather-monitor-settings.md` F.11 | deliberate |
| README 10: the radar/weather panel is always live when mounted | `LiveScreen` mounts `RadarMap` only when `weather.enabled` (`radarOff = !weather \|\| !weather.enabled`), matching the gate `RadarScreen.tsx` already used | Mounting the radar behind a dead weather feed fired a grid of `/api/weather/tile/...` requests every one of which 404s, on a timer, for as long as the screen stayed mounted. Fixed in `3f10681c` - see Legacy defects below | deliberate (post-fix) |

## Settings

| Design says | What shipped | Why | Status |
|---|---|---|---|
| README 11: Optics sheet's APERTURE and REDUCER dials persist with the rig's optics config | Both stored under `localStorage["astrodeck-next-optics-aux"]`, phone-local, used only for the f-ratio label and the reducer preview; the screen says so | `ui/src/types.ts:918-929` has no `aperture_mm` and no `reducer` field, and the server wave (ARCHITECTURE section 12, fixed at S1-S6) does not add them. Silently multiplying `focal_length_mm` by the reducer on save was rejected - that would change what the rig frames from a control the user thinks is a label. `USE THE REDUCED FOCAL LENGTH` is the button that makes a reducer real. `hub-weather-monitor-settings.md` F.1 | deliberate |
| README 11 (line 139): connection radio `DIRECT · RIG WI-FI / RELAY · ANYWHERE / HOME LAN` | Two cards (DIRECT, RELAY), not three, and not radio buttons - they are links/status cards, not a selectable control | "The one honesty fix on this screen": a radio implies the app applies the setting; it cannot - DIRECT vs RELAY is which network the browser itself is already on. `HOME LAN . ETHERNET` is dropped as a third option because nothing distinguishes it from DIRECT (same origin, different cable) and "a radio that cannot be wrong is not a control". `hubs/settings/sheets/ConnectionSheet.tsx:3-13`. F.2 | deliberate |
| Legacy: `astrodeck-autolock` (`setTouch({autoLockMs})`) is read by `TouchGuard.tsx` but no screen anywhere writes it | Settings > PHONE > AUTO-LOCK gives it a UI for the first time | A persisted setting the engine honors with no screen to change it is exactly the broken-promise shape this codebase has a taxonomy for. `hubs/settings/general/PhoneGroup.tsx:249-257`. F.3 | deliberate (new capability, not a loss) |
| Legacy: `StandardsPanel`'s footer link calls `useStore().setView("sequence")` | Still calls the same store action; `legacyBridge.ts` maps `store.view` changes to routes, landing it on the Session hub's `planEditor` sheet | The store action is ungated and still works, but the legacy root it used to navigate is not mounted - `legacyBridge.ts:54` (`sequence: "/session/flows/planEditor"`) is the bridge that makes it land correctly instead of silently doing nothing. F.5 | deliberate |
| README 11 (line 139): "PAIR ANOTHER RIG · QR" | `PAIR ANOTHER RIG · LINK` with a copy button; QR encoding is not shipped | No QR encoder is available in this stack, and adding one is not worth a wrong-bytes risk on a pairing flow. F.7 | deliberate |
| README 11: `AUTO-SWITCH` toggle between DIRECT and RELAY | A 30 s down-link banner with a switch-CTA; the app does not silently move itself between origins | The prototype has no switching algorithm, and the client cannot silently move the browser between origins without risking in-flight work or the session itself. F.9 | deliberate |

## Cross-hub / shell

| Design says | What shipped | Why | Status |
|---|---|---|---|
| README (design tokens, line 192): toasts are 2.8 s, uniformly | Sticky (`ttl 0`) toasts - specifically the sequence-fatal error - stay until dismissed; success/info keep 2.8 s | An UNSAFE/fatal toast must not vanish on its own; `shell/Toasts.tsx:7-8` states this as a deliberate deviation. ARCHITECTURE section 5 already names it | deliberate |
| README (design tokens, line 193): night mode is a `mix-blend-mode: color` red layer over the whole screen | The app's existing `:root.night` token swap (`index.css`) is reused; every primitive reads correctly under it, with shape/text carrying state (never hue alone) | The house mechanism already exists and a second night-mode implementation would be two sources of truth for the same toggle. ARCHITECTURE section 6 | deliberate |
| README (platform, line 25/27): "rebuilt in the new language" at tablet/desktop for the flows canvas, plate-solve view and limit editing | The Flows canvas (`FlowsView`), the plan editor (`views/SequenceView`), and the tuning editors (guide algorithms, quotas, safety limits, calibration library/tolerances, standards, naming, WCS stamp, sync, restricted assets, update, credits) are mounted **as-is**, wrapped in the new chrome, not restyled | Rebuilding ~20 legacy panels from scratch was out of scope for this branch; ARCHITECTURE section 11 designates them "Rebuilt... acceptable for tuning editors (tablet/desktop)" with restyle deferred | follow-up (restyle named per-panel in section 11; none restyled this wave) |

## Server

| Design says | What shipped | Why | Status |
|---|---|---|---|
| `ARCHITECTURE.md` section 12 (original): S5's frame row carries `path` | The frame row omits `path` entirely (`{id, ts, bytes, accepted, override, hfr, stars, guide_rms, thumb}`) | `SessionFrame.path` is redacted below `config.backend` (admin-only) - shipping it would render blank for the Files sheet's real audience (operator/admin), and nothing needs it as a join key. `server/astrodeck/sequence/session_files.py:146-158`. **Contract amendment**: section 12 has been corrected in place; REVIEW-FINDINGS #67 says explicitly not to "fix" this back in | deliberate |
| Original interim state (H.9 in `hub-sky.md`): a single global `config.safety.horizon` polyline stands in for per-site horizon until Wave S lands | S1 landed; the Sites and Horizon sheets read/write real per-site `horizon_points` via `PUT /api/locations/{id}` and `POST /api/locations/{id}/apply` | `hubs/sky/sheets/sites.tsx:126-369`, `horizon.tsx:76-121` | resolved (not a current deviation - noted here only because the plan called it out as temporary) |

## Follow-ups

Known and not done on this branch, in no particular priority order:

- **UI version unbumped.** `ui/package.json`'s `version` stays `0.1.0`, so `__APP_VERSION__` (wired, S6) prints "app 0.1.0" instead of a real release number beside the engine's version. Bump as part of a release process, not a code task (REVIEW-FINDINGS #42).
- **No code-splitting.** All six hubs and every sheet are static imports through `hubs/index.ts`; the bundle is ~1.7 MB (546 KB gzip) before first paint, against the legacy UI's lazy-loaded views (`lib/lazyViews.ts`). At minimum split the sheet registry (REVIEW-FINDINGS #43).
- **Gallery chip carries no count.** `hubs/index.ts:122` renders a bare `{ id: "gallery", label: "GALLERY" }`; the design's own screenshot 07 and its "every chip is worth its height only with a number" rule call for `GALLERY 8`. `flowCount` and `alertsUndelivered` are already wired into `subContext.ts`; gallery's is not (REVIEW-FINDINGS #53, `INTEGRATION-BACKLOG.md`).
- **RotatorCard restyle.** Mounted as-is in the sheet chrome (Rig table, E28); a full re-skin to the design's own vocabulary is named but not scheduled.
- **Inline styles not yet moved into `next.css`.** Several hub tasks reported inline-styled surfaces (capture, inspect, files/gallery) that `next.css` does not yet have `nx-*` classes for; `INTEGRATION-BACKLOG.md`'s "Must do in the integration wave" names the specific files.
- **SessionStack overlay slot / dome overlay yaw hook.** The dome overlay's missing horizon fill/ghost tiles/path (Weather table) needs `SkyDomePanel`/`SkyDome` to expose a yaw callback and an overlay slot before it can be closed - explicitly out of this wave.
- **The phone path to start a plan run.** RUN for a saved plan/flow lives on Session - Flows; there is no shortcut from a phone-width screen that does not pass through the Flows list first (GAP-ANALYSIS 7's "Missing - plan library/schedule/... Run - on a phone", `hub-session-capture.md` D11's territory).
- **`health.ts` has no tier-2 read for a viewer.** Named in the backlog as a gap in the health-check surface's role coverage; not reached by this branch's task list.
- **Page-naming copy remaining** in a handful of places the integration wave chose not to touch because they are outside a hub's directory ownership: `lib/troubleshoot.ts:42,52,57,195,200,245` (Guide/Mount/Capture page - it feeds the new Help sheet and needs rewording to action/hub-neutral language), `views/FocusView.tsx:913` ("Plan screen"), `components/ConnectionBanner.tsx:99`, `views/GuideView.tsx:336`, `views/PowerView.tsx:153`, and `ui/src/lib/humanize.ts:109,135` (see the Rig table's E32 row - the busy-lane sentences were repointed in `2beb42f9`, these diagnostic lines were not).
- **Two known-good horizontal-overflow bugs**, found by the end-to-end probe and reported for the controller to trace (not fixed here - probe tooling does not touch `ui/src`): `#/rig/devices/addDevice` (a fixed 2454px `scrollWidth` regardless of viewport) and `#/rig/devices/mount/polar`'s sibling `#/rig/devices/safety` (100px overflow at 820px only). See `tools/ui_probe/README.md`'s "2026-09-10 run notes".
- **Latent, non-exploitable capability under-declarations** (REVIEW-FINDINGS "Latent" section): `ConditionsScreen.tsx:101`/`incidentActions.ts:83` declare only `control.capture` for ignore-tonight, though the route also needs `CAP_VIEW_WEATHER`; `sky/sheets/horizon.tsx:48` declares only `config.safety`, though SAVE also hits `config.site_optics`. Both pairs are operator+admin / admin-only in the shipped four-role model, so no role currently sees a live control it cannot use - becomes real the moment custom/split roles land (`caps.ts:56`'s deferred item).
- **REVIEW-FINDINGS #72-74** (P2, not yet verified fixed as of this document): REBUILD PREVIEWS on the Archive/Frames screen declares no capability and 403s on tap for a viewer (`hubs/session/sheets/archive.tsx:413-421` - should use the same `useCanControlCapture()` hook the DELETE action on the same screen already uses); the "JPEG previews otherwise" half of the FITS deviation is not fully delivered (a locked DOWNLOAD button still prints a byte count nobody forced onto JPEG can act on, and the full-screen JPEG viewer is gated on `view.media` though the server serves it at `view.preview`).

## Legacy defects found and fixed on this branch

Pre-existing defects the branch's review found and this branch's own commits
fixed (not design deviations - genuine bugs, several predating this branch):

- **Guide drawer whole-block PUT.** The classic `GuideView.tsx` drawer (and the
  guide-assistant's Apply) sent six fields to `PUT /api/guide/settings`, which
  the server persists wholesale - resetting `dither_pixels`,
  `recover_guiding`, exposure/gain/binning/offset and
  `recalibrate_after_pier_change` to defaults on every save. Both now read the
  live block and spread the edit over it. Fixed in `2beb42f9` (17 tests,
  including a 385-line save-path test).
- **Quick flows commanding a fixture position angle.** `wizard.quick` left the
  node vocabulary's shipped `rotation: 23.4` in place on the TARGET node and
  only replaced name/ra/dec; `compile.py` read that 23.4 as a real position
  angle, so every quick-session flow quietly commanded a connected rotator to
  PA 23.4 regardless of the framing the user actually chose. Fixed in
  `b83751f8` (also the commit that wired the mosaic panels into the plan; `-1`
  is `to_plan`'s own "no angle constraint" sentinel, used when there is no
  real framing).
- **Forecast-hold false claim.** Multiple surfaces (`App.tsx`, `NextApp.tsx`,
  `SkyConditionsPanel.tsx`, `SessionsPanel.tsx`) said "Auto-resume will hold
  unless ignore weather tonight is set." The engine's auto-resume veto is
  RAIN-only and fail-open on a stale forecast (`server/astrodeck/weather.py`
  `veto_reason`); cloud holds are a separate, in-run mechanism. All surfaces
  now carry the rain-only, fail-open wording. Fixed across `50a2c4a4` (the
  last two surfaces + page-naming reasons) and earlier work on the same
  string.
- **Permissions-Policy blocking the app's own origin.** The server sent
  `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(),
  usb=()` - an empty allowlist that blocked the API for every origin,
  including same-origin, so the Sky finder's AR camera mode, the photosphere
  and "fill from phone" geolocation could never work in any browser regardless
  of device permissions. Now scoped to allow this origin. Fixed in `3f10681c`.
- **Monitor snapshot leaking the site to viewers.** The monitor snapshot (and
  the REST/WS status payloads) carried the meridian-flip block unredacted to
  every role. Fixed alongside the longitude leak below, in `a34338cf`.
- **`meridian.hours_to_flip` leaking the rig's longitude to a viewer.**
  `_compute_meridian` derives the flip countdown from the site's longitude
  (`lst_hours(site["longitude"])`); `meridian` sits at the top level as a
  sibling of `mount`, not inside it, so the key-name filter that correctly
  stripped `mount.alt`/`az` never reached it - a viewer's `GET /api/status` or
  the `/ws` `status` event recovered the rig's longitude to within ~120 m from
  one authorized request, and the flip status leaked latitude coarsely too
  (`flip_unnecessary_over_pole`). `server/astrodeck/api/redact.py` now strips
  `hours_to_flip` to null and collapses site-derived statuses to "unknown" for
  principals without `view.site_derived`, on REST, WS, and the monitor
  snapshot alike. Fixed in `a34338cf` (40 new tests).
- **Relay fence for locations.** Location mutations (apply, and the
  write-through into `config.safety.horizon`) reached the site config over the
  relay tunnel, past the LAN-only fence every other site-mutating route
  already enforced. Locations now join that fence. Fixed in `a34338cf`.

Commit hashes are from `git log --oneline main..HEAD` on `feat/ui-next` as of
this document.
