# AstroDeck UI Rebuild — 01 · Screen & Information-Architecture Map

> **Purpose.** The thing all four persona reviewers said they couldn't proceed without: a single authoritative map of *what screens exist today, what each one shows and controls, how they connect, and which concept / state / API each surface is wired to.* This is reverse-engineered from the current code (`ui/src/`, `server/astrodeck/api/app.py`) so a rebuild starts from ground truth, not guesswork.
>
> **Companion docs:** [`02-state-automation-model.md`](02-state-automation-model.md) (the states each surface renders), [`03-personas-roles-attention.md`](03-personas-roles-attention.md) (who uses what), [`04-failure-2am-ux-spec.md`](04-failure-2am-ux-spec.md) (what each surface does when things break).

---

## 0. Architecture in one breath

- **Stack:** React + a single Zustand store (`ui/src/store.ts`). **No router** — view switching is `store.view` + `setView()` over a `ViewName` union.
- **One read channel:** a single send-only WebSocket `/ws` pushes `{type, data, ts}` events. The server emits a full `status` snapshot every **~2 s**; previews/guide/focus/sequence/polar/safety arrive on their own topics. The store's `handleEvent` dispatches by `type`.
- **One write channel:** all commands are REST `POST /api/*`. Long actions (slew, autofocus, sequence) return `{"started": …}` immediately and report progress/failure over the `log` + topic streams — **not** in the HTTP response.
- **Auth/role:** `GET /api/me` → `{role, email, caps[]}`. UI gates control visibility off `caps` (fail-closed to viewer). See doc 03.

This shape matters for the rebuild: **the UI is a projection of a 2 s server snapshot plus event topics.** Optimistic UI, staleness handling, and "is this number live or stale?" are first-class concerns, not afterthoughts (the store already flips `telemetryStale` after 20 s of silence).

---

## 1. View inventory (12 views)

`ViewName` (`types.ts`) and the `VIEWS` map (`App.tsx`). Nav order from `NAV` (`App.tsx`).

| `ViewName` | Nav label | File | Purpose | Connect-gated? |
|---|---|---|---|---|
| `connect` | **Rig** | `views/ConnectView.tsx` | Connect equipment (sim / Alpaca / NINA), per-role LEDs | no |
| `polar` | **Align** | `views/PolarView.tsx` | TPPA polar alignment reticle + az/alt error | **yes** |
| `mount` | **Mount** | `views/MountView.tsx` | Pointing, slew pad, GoTo catalog, solve & sync | **yes** |
| `focus` | **Focus** | `views/FocusView.tsx` | Live preview, autofocus, V-curve | **yes** |
| `capture` | **Capture** | `views/CaptureView.tsx` | Exposures, live preview, filter, cooler | **yes** |
| `guide` | **Guide** | `views/GuideView.tsx` | Guide error graph, RMS, dither | **yes** |
| `atlas` | **Atlas** | `views/AtlasView.tsx` | Sky atlas, framing, mosaic, visibility | no |
| `sequence` | **Plan** | `views/SequenceView.tsx` | Build/run the night's plan + automation | no (build offline; Run gated) |
| `power` | **Power** | `views/PowerView.tsx` | Switch outputs, dew heaters, telemetry | **yes** |
| `monitor` | **Monitor** | `views/MonitorView.tsx` | Glanceable unattended-run dashboard | no |
| `settings` | **Settings** | `components/settings/SettingsView.tsx` | Backends, profiles, safety, updates, account, users, auth | no |
| `report` | — | placeholder (`App.tsx`) | Session report — **not wired** | — |

"Connect-gated" views render a `<NotConnectedInterstitial/>` until equipment is connected (`GATED` in `App.tsx`).

---

## 2. Per-view breakdown (panels → controls → readouts)

Exact panel titles and control labels are quoted from the source so they can be searched.

### Rig (`connect`)
- **"Equipment Rig"** — per-role LED rows (camera / telescope / focuser / filterwheel / switch + synthesized guide-cam & guider). Buttons: **"▶ Connect Simulator Rig"**, **"Disconnect All"**.
- **"Alpaca Network Discovery"** — **"⟳ Scan"**; discovered devices as assign buttons; manual host/port/**"Query"**; **"PHD2 guiding host"** + **"Connect PHD2"**.
- **"NINA Bridge — Transition Mode"** — **"⟳ Scan Network"**, **"◈ Bridge"** per instance; manual host/port.

### Align (`polar`)
- **"Polar Alignment"** — `<PolarReticle/>` + message line.
- **"Error"** — hero **total** arcmin readout; **"Azimuth"** row (turn AZ knob, direction arrow, E/W); **"Altitude"** row (raise/lower); progress bar.
- **"Control"** — **"⊕ Start Alignment"**, **"Pause"/"Resume"**, **"Stop"** (danger). Copy differs NINA vs sim.

### Mount (`mount`)
- **"Pointing"** (read-only badge when `!canMount`) — Stats **RA / Dec / Altitude / Azimuth / State** (PARKED·SLEWING·TRACKING·IDLE); **"sidereal tracking"** toggle; **"Park"/"Unpark"**.
- **"Slew Pad"** — `<SlewPad/>` (N/S/E/W, rate selector, full-width **STOP**, reverse-RA/Dec); **"✛ Solve & Sync"** ("plate-solves current frame, syncs mount model").
- **"Target Catalog"** — search; table **ID/Name/Type/Mag/Alt**; per-row Frame (→Atlas) + **"GOTO"** (runs altitude preflight confirm). Header toggle **"center after slew"**.

### Focus (`focus`)
- **"Live Preview"** + `<FocusVerdict/>` — `<PreviewStage/>`, zoom toolbar (−/+/**Fit**/**100%**), `<FrameStats/>`.
- **"V-Curve · HFR vs Position"** — `<VCurve/>`; "✓ best focus …" / "✗ autofocus failed".
- **"Focuser"** — Stats **position / max / temp**; jog ±10/100/1000; **"Go to position"** + **"Go"** + **"Halt"** (danger).
- **"Autofocus"** — **"Exposure (s)"**, **"Step size"**, **"◎ Run Autofocus"**.

### Capture (`capture`)
- `<LivePreview/>` (full preview surface — see §5).
- **"Exposure"** (read-only badge when `!canCapture`) — **"Exposure (s)" / "Gain" / "Offset" / "Binning"**; **"save FITS to library"** + **"Target name"**; **"Single"/"Loop"/"Stop"**; per-frame progress (exposing vs "downloading…").
- **"Filter Wheel"** — filter-name buttons, active highlighted.
- **"Cooler"** (when `cam.can_cool`) — LED + Cooling/Off, power %, "at target" chip; Stats **sensor / target**; **"Target °C"**; **"Cool"/"Warm"**; **"dew heater"** slider.

### Guide (`guide`)
- **"Guide Error · arcsec"** + "● guiding" — `<GuideGraph/>`; Stats **RMS RA / RMS Dec / RMS Total / SNR**.
- **"Scatter"** — `<GuideScatter/>`.
- **"Control"** — **"❖ Start Guiding"**, **"Stop"**, **"Dither (px)"** + **"Dither"**.

### Atlas (`atlas`)
- Empty: **"Frame a target"** / **"Free-roam the sky"**.
- Active: object header + frame FOV; **"Focal length"** (mm); **"Calibrate from last solve"**; `<SkyCanvas/>` (HiPS survey + `<FovOverlay/>` mosaic grid, pan/zoom/rotate).
- **"Survey & framing"** — survey select, stretch **Linear/Asinh**, FOV stepper, **"Lock zoom to camera FOV"**, **"Camera angle (manual)"**, brightness slider.
- **"Mosaic"** — **Rows / Cols / Overlap %**; Stats **Total field / Panels**; **"Send N panels to Plan"** / **"Add target to Plan"**.
- **"Tonight"** — `<VisibilityPanel/>` (visibility curve, transit, dark window, moon).

### Plan (`sequence`)
- **"Resume Interrupted Run"** (conditional).
- **"Sequence · {plan_name}"** run panel — `<SeqStateBadge/>` (RUNNING/PAUSED/COMPLETE/ERROR/ABORTED); progress "X/Y frames · N rejected · Nm elapsed"; **"Pause"/"Resume"/"Abort"** (hold-to-confirm), **"Re-run"**, **"View log"**, **"Resume from frame N"**.
- **"Targets"** — per-target cards: RA/Dec, **"PA N°"**, toggles **center/AF/Cal**, **"+ step"**, delete (5 s undo). Step rows: filter / exp s / gain / bin / count / minutes. Mosaic-group blocks.
- **"Plan"** — **"Plan name"**; Stats **frames / integration**.
- **"Automation"** — see doc 02 §3 for the full list (guide, dither, refocus, filter offsets, meridian flip, recover guiding, cool-to, HFR reject, park/warm when done).
- `<PreflightStrip/>` + **"≡ Run Sequence"** (gated by preflight + role); `<PreflightModal/>`.

### Power (`power`)
- **"Power Outputs"** (ON/OFF LED buttons), **"Dew Heaters · PWM"** (sliders), **"Telemetry"** (read-only sensors). Polls `/api/switch/ports` every 5 s.

### Monitor (`monitor`) — the unattended dashboard
- Header strip: `<StateBadge/>`, "LIVE" chip, target/plan/filter, `<LiveTimer/>` finish clock, `<PauseButton/>` + `<HoldButton face="Abort"/>`.
- **"Progress"** — bar, `<SubFrameBar/>`, frames X/Y %, elapsed, "CAPTURE STALLED?" line, flagged count, HFR-trend `<Sparkline/>`.
- **"Countdowns"** — `<MeridianCountdown/>` + `<CoolingCountdown/>`.
- **"Last frame"** — `<PreviewTile/>` (night brightness slider).
- **"Guiding"** — guide sparkline, `<RmsVerdict/>`.
- **"Thermal"** — Stats sensor/target, `<ThermometerBar/>`.
- "BELOW HORIZON" chip; "NO LINK" banner when WS down.

### Settings (`settings`) — tabbed
- **Connect** (`<BackendPicker/>` + `<BackendLinkGrid/>`), **Profiles** (`<ProfileList/>`), **Safety** (`<SafetyPanel/>` — sun-avoidance arm/disarm, exclusion angle), **Updates** (`<UpdatePanel/>`, if `system.update`), **Account** (`<AccountPanel/>`), **Users** (admin only), **Auth** (admin only).

---

## 3. Navigation, global chrome & overlays

**Nav model (no router):**
- **Desktop left rail** — all 11 nav entries, active accent bar, per-tab status LEDs (camera/mount connected, sequence running/error), lock icon on capability-gated tabs.
- **Mobile bottom nav** — 5 primary (Rig/Align/Mount/Focus/Capture) + **"More"** sheet (Guide/Atlas/Plan/Power/Monitor/Settings + global controls: Event Log, Lock Screen, Keep Awake, Haptics, Reverse RA/Dec, Touch size).

**Always-visible chrome:**
- **Header** — wordmark, backend chip (NINA/ALPACA/SIM), `<RoleBadge/>`, live mount/temp/RMS/SEQ readouts (md+), `<SignInButton/>`, brightness controls + **NIGHT** toggle, **LOG** button (+ error badge), `<HealthLeds/>`.
- **`<ConnectionBanner/>`** — WS-down / telemetry-stale / no-rig strip.
- **Run banner** — "SEQUENCE RUNNING" with **"OPEN LIVE"** when running and off Monitor.
- **`<LogDrawer/>`** — docked column (desktop) / bottom sheet (mobile).

**Overlays (never dimmed, `overlay-top`):**
- `<Toasts/>` (max 3; sticky for UNSAFE / sequence-fatal), `<ConfirmHost/>` (modes `ok`/`confirm`/`hold`, driven by `store.confirm`), `<TouchGuard/>` (full-screen lock: slide-to-unlock, **EMERGENCY STOP**, auto-lock).

---

## 4. The concept → view → controls → state → endpoint matrix

The headline deliverable. Each row links a nightly concept (from the onboarding workflow doc) to the UI surface, the live state it renders, the command endpoints, and the stream topic that updates it. State field names are from `poll_status()` / topic payloads (doc 02). Capabilities in **bold** (doc 03).

| Concept (night step) | Primary view(s) | Key controls | Live state fields | Command endpoint(s) | Stream topic | Cap to act |
|---|---|---|---|---|---|---|
| **Connect / power on** | Rig, Settings→Connect | Connect Sim / Bridge / per-role assign, Disconnect All | `connected{role}`, `backend_links[]` (attempted/ok/connected), `boot_connect_failed`, `mode` | `POST /api/connect/{sim,alpaca,nina,phd2,rig}`, `POST /api/disconnect` | `status` | **config.backend** |
| **Profiles / auto-connect** | Settings→Profiles | Activate, Save Current Rig, Rename, Delete | profile list, active profile | `GET/POST /api/profiles`, `…/apply`, `…/activate` (`force`) | — | **config.backend** |
| **Cool the camera** | Capture→Cooler, Monitor→Thermal | Target °C, Cool/Warm, dew slider | `camera.cooler{on,power,target_c,at_target}`, `camera.temperature` | `POST /api/camera/cooler`, `…/dew-heater` | `status` | **control.capture** |
| **Slew / GoTo** | Mount (Pointing, Slew Pad, Catalog) | GOTO, jog pad, STOP, Park/Unpark, tracking | `mount{ra,dec,alt,az,slewing,tracking,parked,pier_side}`, `busy` | `POST /api/mount/{goto,move,stop,tracking,park,unpark}` (`force`) | `status`, `mount` | **control.mount** |
| **Plate-solve & sync** | Mount→Solve & Sync | "✛ Solve & Sync" | `mount` action `centering`/`centered`(+`error_arcmin`) | `POST /api/mount/solve_sync` | `mount` | **control.mount** |
| **Focus** | Focus (Live, V-Curve, Focuser, Autofocus) | Run Autofocus, jog, Go to position, Halt | `focuser{position,max,temperature}`, `focus{state,points,best}` | `POST /api/focuser/{move,autofocus,halt}` | `focus`, `status` | **control.capture** |
| **Polar alignment (TPPA)** | Align | Start/Pause/Resume/Stop | `polar{state,az_error,alt_error,total_error,progress,source}` | `POST /api/polar/{start,stop,pause,resume}` | `polar` | **control.mount** |
| **Guiding (PHD2)** | Guide | Start/Stop Guiding, Dither | `guider{guiding,rms_ra,rms_dec,rms_total,snr,recent[]}` | `POST /api/guide/{start,stop,dither}` | `guide` | **control.guide** |
| **Filters / filter wheel** | Capture→Filter, Plan steps | filter buttons; per-step filter select | `filterwheel{position,names}` | `POST /api/filterwheel/position` | `status` | **control.capture** |
| **Capture frames** | Capture | Single/Loop/Stop, exp/gain/offset/bin | `looping`, `preview{…,hfr,stars,histogram,auto_levels}` | `POST /api/capture`, `…/loop`, `…/stop` | `preview`, `capture_loop` | **control.capture** |
| **Framing / FOV / mosaic** | Atlas | survey, FOV, rows/cols/overlap, camera angle | `framing` (client), `optics{image_scale,fov_*}` | `POST /api/framing/mosaic`, `PUT /api/optics` | — | **config.site_optics** (optics) |
| **Plan & automation** | Plan | targets, steps, automation toggles | `plan` (client), `sequence` state | `POST /api/plans`, `…/import`, `POST /api/sequence/preflight` | `sequence` | **control.capture** (plan), **control.mount** (run) |
| **Run the night** | Plan, Monitor | Run/Pause/Resume/Abort, Resume-from | `sequence{state,progress{…},target,plan_name,live}`, `schedule.state` | `POST /api/sequence/{start,pause,resume,abort,recover}` (`force`) | `sequence` | **control.mount** |
| **Meridian flip** | Monitor→Countdowns | (automatic) | `meridian{status,hours_to_flip,flip_enabled,pier_side}` | (engine-driven; toggle in plan) | `status`, `mount` | — |
| **Safety / environment** | Header, Monitor, Settings→Safety | arm/disarm sun avoidance, exclusion angle | `safety{is_safe,reason,source,stale}`, `disk{low,critical}` | `POST /api/config` (safety fields), `/api/safety/simulate` (sim) | `safety`, `status` | **config.safety** / **config.solar_override** |
| **Power / dew** | Power | output ON/OFF, dew PWM | switch ports (`/api/switch/ports`) | `POST /api/switch/set` | (5 s poll) | **control.power** |
| **Identity / who am I** | Header RoleBadge, Settings→Account | sign in/out | `principal{role,email,caps}` | `GET /api/me`, `/auth/login`, `POST /auth/logout` | — | — |

> **Atlas compute caveat (security seam):** `POST /api/framing/mosaic` and `POST /api/visibility/order` are intentionally **un-capability-gated** (stateless compute). Fine today; flag for the rebuild's threat model.

---

## 5. Shared component library (reuse, don't reinvent)

The current UI already has a coherent design-system layer worth carrying forward (or deliberately replacing) rather than rediscovering:

- **Primitives** (`components/ui.tsx`): `Panel` (title + right slot), `Led` (shape **and** letter — not color-only, an accessibility win), `Field`, `Stat` (label/value/unit/tone, em-dash for null), `Toggle` (`role=switch`), `IconButton` (≥44 px), `Stepper`, **`HoldButton`** (hold-to-confirm: pointer fill + keyboard two-step + vibrate), `Tooltip`, `InfoDot`, `EmptyState`, `Segmented`.
- **Status/health:** `HealthLeds`, `RoleBadge` (VIEW ONLY / OPERATOR), `ReadOnlyBadge`, `ConnectionBanner`, `SeqStateBadge`, `BackendLinkGrid` (tri-state CONNECTED / DEGRADED / FAILED / NOT REQUESTED).
- **Preview stack** (`components/preview/`): `LivePreview`, `PreviewStage` (zoom/pan/gestures + LUT), `PreviewToolbar` (overlays Stars/Clip/Reticle/Center; downloads Stretched PNG / Lossless PNG / FITS), `StretchHistogram` (Auto + B/M/W handles), `FrameStats`, `FocusVerdict`, `StarOverlay`, `Reticle`, `ScaleBar`.
- **Charts** (`components/graphs.tsx`): `Histogram`, `VCurve`, `GuideGraph`, `GuideScatter`; `PolarReticle`.
- **Monitor widgets** (`components/monitor.tsx`): `StateBadge`, `LiveTimer`, `Sparkline`, `CountdownTile`, `ThermometerBar`, `PreviewTile`, `RmsVerdict`, `useReducedMotion`.
- **Atlas** (`components/atlas/`): `SkyCanvas`, `FovOverlay`, `SurveyControls`, `VisibilityPanel`.
- **Mount control:** `SlewPad` + `lib/slewController.ts`.
- **Settings** (`components/settings/`): `BackendPicker`, `BackendLinkGrid`, `ProfileList`, `AccountPanel`, `UsersPanel`, `AuthMethodPanel`, `SafetyPanel`, `UpdatePanel`.

---

## 6. Data cadence (what updates, how fast)

| Source | Cadence | Drives |
|---|---|---|
| `status` (WS) | ~2 s push | header readouts, Mount/Capture/Power/Monitor live values, `BackendLinkGrid`, nav LEDs |
| `preview` (WS) | per frame | LivePreview, Monitor "Last frame", LIVE/stall detection |
| `guide` (WS) | per guide step (~1–3 s) | Guide graphs, Monitor guiding sparkline |
| `focus`/`polar`/`sequence`/`safety` (WS) | per event | their views, run banner, sticky toasts |
| `log` (WS) | per event | LogDrawer + error→toast + unseen-error badge |
| `/api/switch/ports` | 5 s poll | Power view |
| `/api/monitor/snapshot` | once on mount | Monitor cold-load hydration |
| capture progress | client-simulated | progress bar (completion = new `livePreviewId` arrives) |
| Staleness | 20 s no frames → `telemetryStale` | ConnectionBanner |

---

## 7. Notes & gaps for the rebuild

1. **`report` view is a placeholder** — but the backend has full report data (`GET /api/reports`, trends, `frames.csv`). A real Reports/History screen is low-hanging fruit the current UI never built.
2. **Capture progress is faked client-side** (no server progress event) — completion is inferred from a new preview id. A rebuild may want a real progress topic.
3. **Two security seams** worth a deliberate decision: un-gated Atlas compute endpoints (§4), and the WS only re-checks auth at accept time (doc 03 §5).
4. **The IA is feature-indexed, not task-indexed.** 11 peer tabs assume the user knows the workflow. Both designers recommended a task-indexed front door (Guided Night) layered over the same views — see doc 03.
5. **`Led` is shape+letter, `HoldButton` is a real confirm primitive, brightness/NIGHT mode exist** — the current UI already takes the dark-field, fat-finger, fail-closed context seriously. Inventory these before replacing them.
