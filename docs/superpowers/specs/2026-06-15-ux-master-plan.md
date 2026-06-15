# AstroDeck UX — MASTER IMPLEMENTATION PLAN (9 surfaces reconciled)

**Date:** 2026-06-15
**Role:** Tech lead reconciliation of 9 build-ready surface specs into one conflict-free build.
**Inputs (all read):**
`ux-settings-profiles`, `ux-live-preview`, `ux-monitor-dashboard`, `ux-sky-atlas-framing`,
`ux-design-system`, `ux-onboarding-safety-ux`, `ux-touch-mobile`, `ux-reliability-surfacing`,
`ux-automation-safety` (all dated `2026-06-15-ux-*-design.md`).

The 9 specs were each written in isolation. Each independently declared additions to the same five
high-traffic files (`store.ts`, `types.ts`, `index.css`, `App.tsx`, `hub.py`, `api/app.py`) and several
re-invented the same primitives under different names. This document is the single source of truth that:

1. Reconciles every shared contract into ONE final shape (§A) so there are no two definitions of the
   same thing.
2. Groups all work into 4 dependency-ordered BATCHES with disjoint per-implementer file ownership (§B),
   naming the single owner of each shared file per batch.
3. Lists the cross-cutting risks the orchestrator must actively manage (§C).

> **Golden rule for every implementer:** the five "magnet" files — `ui/src/store.ts`, `ui/src/types.ts`,
> `ui/src/index.css`, `ui/src/App.tsx`, `server/astrodeck/hub.py`, `server/astrodeck/api/app.py` — are
> **single-owner per batch**. If your task needs to touch one of them and you are not its owner this
> batch, you hand your diff to the owner as an *append at a named anchor*. Never edit a magnet file you
> do not own.

---

## A. RECONCILED SHARED CONTRACTS (the one true shape)

### A.0 Naming reconciliation table (renames that resolve direct collisions)

| Concept | Names used across specs | **CANONICAL** | Reason |
|---|---|---|---|
| Hold-to-confirm button | `Confirm` (design-sys), `ConfirmHold` (onboarding), `ConfirmButton` (settings), `HoldButton` (monitor, touch) | **`HoldButton`** in `ui/src/components/HoldButton.tsx` | Most specs consume it by this name; touch + monitor pin its keyboard contract. design-system OWNS/BUILDS it; everyone else consumes. Drop `Confirm`/`ConfirmHold`/`ConfirmButton` aliases. |
| Promise modal | `ConfirmDialog`/`confirmDialog`/`ConfirmHost` (onboarding) | **keep as-is** (`ConfirmDialog.tsx`) | No collision; distinct from `HoldButton`. |
| Working plan in store | `editorPlan` (settings), `plan`/`setPlan` (atlas) | **`plan` / `setPlan`** | atlas's SSOT-in-setter design is stronger (persists in setter, kills the data-loss race). settings' `editorDirty` is kept as a sibling flag. |
| Toast queue | `ToastItem`+`pushToast` (design-sys), `Toast`+`enqueueToast` (reliability), `toasts[]`+`pushToast` (automation) | **`Toast` + `enqueueToast` + `toasts[]`** (reliability shape) | Reliability's shape is the richest (coalesce-without-age-reset, single focal sequence toast, TTL-by-consequence). `showToast`/`pushToast` kept as thin aliases. |
| Screen-lock flag | `locked` (design-sys + touch) | **`locked`** (single field) | Same name already; merge the two slices. |
| Site (backend) | `{lat,lon}` (current), `Site{name,lat,lon,elevation_m}` (settings), `+is_default,+horizon_min_deg` (onboarding), `+elevation_m` (automation) | **one merged dict** (see A.5) | All four must agree or `hub.site` breaks `altaz`. |
| Optics endpoint | `PUT /api/optics {optics,version}` (settings), `POST /api/optics Optics` (atlas) | **`PUT /api/optics`** canonical; `POST` kept as alias | settings owns the persisted config; atlas reads the same `hub.optics`. |
| `/api/sequence/preflight` | `GET ?ra&dec → alt verdict` (onboarding), `POST → warnings` (automation) | **BOTH, different verbs** | `GET` = single-target altitude; `POST` = full plan warnings. No collision (different method). |

### A.1 `ui/src/types.ts` — final additive set (one owner per batch lands these)

All additions are additive (no existing field changes). Grouped by the batch that lands them.

**Batch 1 (foundation) lands:**
```ts
// reliability
export type ToastLevel = "error" | "warning" | "info" | "success";
export interface Toast { id:number; level:ToastLevel; title:string; detail?:string;
  kind:"generic"|"sequence"; createdAt:number; ttl:number; count:number;
  action?:{ label:string; kind:"openLog" }; source?:string; }
export type WsPhase = "connecting"|"up"|"down"|"reconnecting";
export type NinaState = "ok"|"stale"|"error"|"down"|"warming"|"na";
export interface NinaHealth { active:boolean; ageMs:number|null; state:NinaState; lastError?:string|null; }
// RigStatus += busy?, nina_link?, disk?  (reliability + onboarding)
//   busy?: "slewing"|"solving"|"focusing"|"capturing"|null;
//   nina_link?: { active, last_ok_age_s, last_error, healthy, warming_up };
//   disk?: DiskInfo;
export interface DiskInfo { free_gb:number; low:boolean; critical:boolean; }
// SequenceState += target_index?:number; progress.rejected?:number;  (reliability + monitor)

// settings/site + optics  (settings-profiles is the OWNER of the persisted config types)
export interface Site { name:string; latitude:number; longitude:number; elevation_m:number; } // lon +E
export interface Optics { focal_length_mm:number; pixel_size_um:number;
  sensor_width_px:number; sensor_height_px:number; auto_from_camera:boolean; }
export interface OpticsComputed { have_optics:boolean; source:"config"|"camera"|"mixed"|"none";
  focal_length_mm:number; pixel_size_um:number; sensor_width_px:number; sensor_height_px:number;
  image_scale_arcsec_px:number|null; fov_w_deg:number|null; fov_h_deg:number|null; fov_diag_deg:number|null; }
export interface AppConfig { version:number; site:Site; optics:Optics;
  optics_computed:OpticsComputed; active_profile_id:string|null; }
export interface SkyInfo { sun_alt_deg:number; dark_window:{start_iso:string;end_iso:string}|null;
  place_hint:string; lst_str:string; }
// SiteInfo is the LIVE/status view of the site (onboarding); distinct from the persisted Site.
export interface SiteInfo { latitude:number; longitude:number; is_default:boolean; horizon_min_deg:number; }
export interface PreflightAlt { alt:number|null; az:number|null;
  verdict:"ok"|"low"|"below"|"unknown"; horizon_min_deg:number; site_is_default:boolean; sets_in_min?:number|null; }
export type CheckStatus = "ok"|"warn"|"blocked"|"checking"|"skipped"|"disabled";
export interface CheckItem { id:string; label:string; status:CheckStatus; word:string;
  detail?:{ value:string; unit?:string }; help?:HelpKey;
  fix?:{ label:string; view?:ViewName; onClick?:()=>void|Promise<void>; inPlace?:boolean }; }
// design-system display primitives
export type LedState = "off"|"on"|"warn"|"bad"|"busy";
export type Tone = "good"|"warn"|"bad";
// goto/start bodies accept optional force?: boolean
// RigStatus += site?:{name,latitude,longitude} + optics?:OpticsComputed  (settings, survives 2s replace)
```

**Batch 1 also lands the profiles/plans/touch contract types** (no UI consumer until later, but the
shapes are frozen now so nothing re-defines them):
```ts
export interface ProfileDevice { role:string; backend:"alpaca"|"nina"; host:string; port:number; dev_type:string; dev_num:number; name:string; }
export interface Profile { id:string; name:string; devices:ProfileDevice[]; nina_host:string|null; nina_port:number; phd2_host:string|null; phd2_port:number; optics:Optics|null; site_name:string|null; }
export interface ProfileRow { id:string; name:string; mode:"alpaca"|"nina"|"mixed"|"empty"; devices_count:number; site_name:string|null; active:boolean; }
export interface ApplyResult { summary:RigStatus; results:{role:string;ok:boolean;error?:string}[]; connected:number; total:number; }
export interface PlanRow { id:string; name:string; frames:number; integration_min:number; targets:number; mtime:number; }
// touch ergonomics
export type SlewRateId = "pulse"|"fine"|"set";
export interface SlewRateOption { id:SlewRateId; label:string; rateDegS:number; pulseMs?:number; }
export interface MountCaps { max_rate_deg_s?:number; }   // RigStatus.mount MAY gain caps?:MountCaps
export interface TouchSettings { hapticsEnabled:boolean; touchSizing:"auto"|"on"|"off"; reverseRa:boolean; reverseDec:boolean; autoLockMs:null|180000|300000; }
```

**Batch 2 (design-system owner) lands:** `ToastItem` is DROPPED in favor of `Toast` (A.0). Icon `IconName`
union is design-system's; everyone imports from `ui/src/components/icons.tsx`.

**Batch 2 (live-preview owner) lands:** `PreviewSource`, `StarMark`, `Viewport`, `StretchParams`,
`OverlayToggles`; rewrites `PreviewInfo` (rename `width/height`→`data_width/data_height`, +new fields).
**This is the only breaking rename in the whole program** — see §C-Risk-7.

**Batch 2 (monitor owner) lands:** `SequenceProgress`, `CoolerInfo`, `MeridianInfo`/`MeridianStatus`,
`MonitorSnapshot`; `SequenceState.state += "nina_native"`; `RigStatus.camera.cooler?` + `RigStatus.meridian?`.

**Batch 4 (atlas owner) lands:** `Optics` (atlas variant uses `sensor_width`/`sensor_height` —
RECONCILE to settings' `sensor_width_px`/`sensor_height_px`; see §C-Risk-3), `FovRect`, `MosaicSpec`,
`MosaicPanel`, `MosaicResult`, `FramingSession`, `MoonInfo`, `VisibilitySample`, `VisibilityNight`,
`VisibilityTarget`; `Target += rotation_deg?, mosaic_group?`.

**Batch 4 (automation-safety owner) lands:** `SafetyReading`, `SafetyState`, `Schedule`, extended
`SequenceState` (schedule/live/end_reason), `AlertSink`, `SafetyConfig`, `EscalationConfig`,
`SiteConfig`, `FilterBreakdown`, `TargetBreakdown`, `SessionReportSummary`, `SessionReport`;
`Target += schedule`; `SequencePlan += safety_check, meridian_flip_warn_min`.

> **AppConfig conflict (settings vs automation):** settings defines `AppConfig{version,site,optics,active_profile_id}`;
> automation defines `AppConfig{site,safety,escalation,alerts,deadman_url,schedule_defaults}`. **Resolution
> (§C-Risk-2):** ONE `AppConfig` that is the UNION — settings owns `version/site/optics/active_profile_id`
> (Batch 1), automation APPENDS `safety/escalation/alerts/deadman_url/schedule_defaults` (Batch 4) to the
> same pydantic model + TS interface. Site is shared (A.5). Neither redefines the other's keys.

### A.2 `ui/src/store.ts` — final slice set

`store.ts` is single-owner per batch. The reconciled superset of all slices:

**Batch 1 owner adds:**
```ts
// --- config / plan (settings + atlas, reconciled) ---
config: AppConfig | null;
plan: SequencePlan;            // LIFTED to store (atlas SSOT); setPlan persists localStorage in the setter
setPlan: (p:SequencePlan)=>void;
editorDirty: boolean; siteDirty: boolean; opticsDirty: boolean;   // settings dirty flags
loadConfig: ()=>Promise<void>;
// --- site (onboarding) ---
site: SiteInfo | null; setSite:(s:SiteInfo)=>void;
equipConnected: boolean;       // sticky equipment flag, NOT wsConnected
// --- reliability transport + toasts (the ONE toast model) ---
wsPhase: WsPhase; wsLastEvent:number; telemetryStale:boolean;
toasts: Toast[]; logOpen:boolean; unseenError:number;
ninaHealth: NinaHealth; notifyEnabled:boolean;
enqueueToast/dismissToast/openLog/closeLog/reconcileLogs/setWsPhase/noteWsEvent/setTelemetryStale/setNotifyEnabled;
wsConnected (=wsPhase==="up"); setWsConnected; showToast(→enqueueToast w/ humanizeLog);  // shims
// --- design-system display (merged with touch's locked) ---
night:boolean; locked:boolean; brightness:number; dayBrightness:number; nightBrightness:number;
toggleNight/setBrightness/resetBrightness/setLocked;
// --- confirm host (onboarding) ---
confirm: ConfirmRequest | null; pushConfirm:(req)=>Promise<boolean>;
// ViewName += "settings" | "monitor" | "atlas"
// handleEvent: case "config" -> loadConfig(); case "status"->deriveNinaHealth + equipConnected maint;
//   case "sequence"->focal toast on error + notify; case "log"->unseenError bump + humanized toast
```

**Batch 2 owner adds (live-preview + monitor + design-system, all to the same store):**
```ts
// live-preview
previews:PreviewInfo[](cap 24); selectedPreviewId; livePreviewId;
viewport; stretch; overlays; hfrGood; hfrWarn;
pushPreview/selectPreview/setViewport/setStretch/setOverlays;  // "preview" event -> pushPreview
usePreviews/useLivePreview/useViewport/useStretch/useOverlays/useNight;  // narrow hooks
// monitor
lastFrameAtMs; lastGuideAtMs; autoMonitor; runBanner;
setAutoMonitor/dismissRunBanner;  // handleEvent preview->lastFrameAtMs, guide->lastGuideAtMs,
//   sequence rising-edge -> runBanner; granular useShallow selectors (useSeq/useCamera/useMeridian/...)
```

**Batch 3 owner adds (touch):**
```ts
lockAvailable:boolean;   // false until reliability error-render ships (already gated)
monitorAwake:boolean; touch:TouchSettings;
setMonitorAwake/setTouch;  // setLocked (already exists) MUST forceStop active slew
```

**Batch 4 owner adds (atlas + automation):**
```ts
// atlas
settings:{optics:Optics|null; site:{latitude:number;longitude:number}|null}; setSettings;
framing:FramingSession|null; openFraming/setFraming; atlasHandoff:number; addTargetsToPlan(targets,group);
// automation
safety; configAutomation(merged into config); alert; lastReportId;
// handleEvent cases safety|alert|report|config|hello
```

> Persistence rules (reconciled): localStorage keys — `astrodeck-night`, `astrodeck-bright-day`(="1"),
> `astrodeck-bright-night`(="0.45") [design-system]; `astrodeck-plan` [atlas, in setPlan];
> `astrodeck-preview` persists only `overlays`+`stretch.auto`+`stretch.advancedOpen` [live-preview];
> `astrodeck-monitor-thumb-brightness` [monitor]; `astrodeck-haptics/-touch-size/-rev-ra/-rev-dec/-autolock`
> [touch]. `locked` is NEVER persisted (both design-system and touch agree). Never persist absolute B/M/W
> or viewport.

### A.3 `ui/src/index.css` — one merged additive block per batch

Every surface independently asked for the SAME three things: a global `:focus-visible` ring, a
`prefers-reduced-motion` guard, and AA-corrected dim text tokens. **These land ONCE in Batch 1/2 and no
later surface re-adds them.**

- **`:focus-visible { outline:2px solid var(--accent); outline-offset:2px }`** — ONE rule, landed by
  the design-system owner in Batch 2 (or Batch 1 reliability lane A if Batch 2 slips). Every other spec's
  copy of this rule is DELETED from its scope.
- **`@media (prefers-reduced-motion: reduce)`** guard — ONE class-targeted block (reliability/design-system).
- **Token corrections** (design-system owns the canonical palette): `--text-dim` raised to AA both modes;
  `--text-faint` decoration-only; `--danger-ink` split from `--bad`; night LUT luminance ladder
  (alarm=brightest); `--img-filter` luminance-preserving. touch's `--text-dim2`, `--tap-min/-lg/-gap`,
  `.tap/.tap-lg` land in Batch 3 (additive, no conflict). atlas's `--survey-filter` + `.svg-halo` land in
  Batch 4 (additive). live-preview's `--halo` + `.astro-surface` + `.preview-*` land in Batch 2 (additive).
- **`.label` bump 10→11px** (settings) folds into the design-system token pass.
- Monitor/onboarding/automation each asked for `:focus-visible` + reduced-motion + LED/blink classes —
  they CONSUME the Batch-2 design-system versions; their only net-new CSS is surface-specific
  (`.led-bad/.led-letter/.blink-alert` from reliability, `.dim-content/.dim-scrim/.overlay-top` from
  design-system, `.confirmhold-fill/.check-glyph` from onboarding).

### A.4 REST endpoints — final reconciled map (no two owners edit the same handler)

`api/app.py` is single-owner per batch. New routers are registered via `include_router` so survey/framing/
visibility/safety/etc. each ship a SEPARATE `APIRouter` module and never edit a shared function.

| Batch | Method | Path | Owner module | Notes |
|---|---|---|---|---|
| 1 | GET | `/api/config` | config (settings) | `AppConfig`+`optics_computed` |
| 1 | PUT | `/api/site` | config (settings) | `{site,version}`→409 on stale; **also** accepts onboarding's `horizon_min_deg`, sets `is_default=False`, calls `push_site_to_mount`. Replaces old `SiteBody`. `POST /api/site` kept as thin alias. |
| 1 | PUT | `/api/optics` | config (settings) | `{optics,version}`. `POST /api/optics` (atlas) is an alias that also seeds `hub.optics`. |
| 1 | GET | `/api/site/sky` | config (settings) | server-computed sun_alt/dark_window/place_hint/lst |
| 1 | GET | `/api/sequence/preflight?ra_hours&dec_deg` | onboarding | live alt verdict; `unknown` on default site |
| 1 | — | `POST /api/mount/goto` += `force` ; `POST /api/sequence/start` += `force` | onboarding | `hub._check_horizon` gated on `!is_default`, alt<0 only, NOT in `goto_and_center` |
| 1 | — | `poll_status()` += `disk` block | onboarding | one `shutil.disk_usage` |
| 1 | GET | `/api/discover/alpaca?host&port` | reliability E | 502 differentiated cause |
| 1 | GET | `/api/nina/health` (optional) | reliability E | also `nina_link`+`busy` on status |
| 1 | GET/POST/PATCH/DELETE | `/api/profiles(+/{id}/apply{force?},/capture)` | profiles (settings) | 409 if engine/loop/polar running |
| 1 | GET/POST/DELETE | `/api/plans(+/{id}/export,/import)` | plans (settings) | 422 version_too_new/invalid; 409 name_collision |
| 2 | GET | `/api/preview/{id}(.png,/lossless.png,/thumb.jpg,/fits,/png)` | live-preview | `/fits` guarded by is_relative_to CAPTURE_DIR; `/crop`,`/render.png` stubbed 501 |
| 2 | GET | `/api/monitor/snapshot` | monitor | one-shot cold hydration; non-fatal |
| 4 | GET | `/api/survey/cutout.jpg` | survey router (atlas) | hips2fits proxy, 503→schematic |
| 4 | POST | `/api/framing/mosaic` | framing router (atlas) | ra %24-wrapped |
| 4 | GET | `/api/visibility` ; POST `/api/visibility/order` | visibility router (atlas) | astropy |
| 4 | GET/POST | `/api/config` (redacted) ; GET/POST `/safety/*` ; GET/POST/DELETE `/alerts*` ; GET `/reports*` ; POST `/sequence/preflight` (warnings) | automation routers | `POST /sequence/preflight` is the WARNINGS verb (distinct from Batch-1 GET) |

> `bus.publish("config"|"safety"|"alert"|"report", ...)` are schemaless — **no `events.py` change** in
> any batch (verified by settings + automation specs). WS protocol is never bumped.

### A.5 `server/astrodeck/hub.py` — final reconciled fields

`hub.py` is single-owner per batch; within a batch, split by clearly-delimited method blocks (settings
already documents the BE-A/BE-B split-by-comment-block pattern; reuse it for every multi-lane batch).

- **`hub.site`** — THE reconciliation point. Becomes a property backed by `config_store` (settings)
  returning the merged dict:
  ```python
  {"name", "latitude", "longitude", "elevation_m",   # settings + automation
   "is_default", "horizon_min_deg"}                   # onboarding
  ```
  The hardcoded `hub.py:38` SF site is removed. `coords.lst_hours` stays East-positive (do not flip).
  Default `is_default=True`, `horizon_min_deg=15.0`; `/api/site` flips `is_default=False`.
- **`hub.optics`** + `effective_optics()` (settings) — per-profile read-time override; `image_scale`/`fov`
  bin-1; added to `poll_status()` AND `summary()`. atlas's `hub.optics` seed-from-camera is the SAME object.
- **`poll_status()` additive keys** (all batches append to the one dict): `site`,`optics` (settings);
  `disk` (onboarding); `busy`,`nina_link` (reliability); `camera.cooler`,`meridian` (monitor);
  `safety` (automation). Because the store does `set({status})` wholesale every 2s, ALL of these MUST be
  in `poll_status`, not just `summary`.
- **Touch deadman** (`last_move_ts`, `note_move`, `_move_watchdog` 250ms/1200ms) — **co-owned with the
  safety surface; implement EXACTLY ONCE** (§C-Risk-5). Batch 3 lands it; Batch 4 safety reuses it.
- **Reliability** (`busy_label`, `_nina_heartbeat` 5s, `_bridge_ready`) — Batch 1.
- **monitor** (server-side `meridian` from HA, `camera.cooler`) — Batch 2.
- **automation** (`ROLES+=("safety",)`, `safety_reading()`, `reconnect_role()`, `_last_connect`) — Batch 4.

### A.6 Device-identity prerequisite (blocks Profiles)

`devices/alpaca.py` + `base.py`: `AlpacaConnection` stores host/port; `_AlpacaDevice` stores
host/port/backend/dev_type; `connect_alpaca_device` sets `dev.role`; `_AlpacaDevice.describe()` adds
`{host,port,dev_type,dev_num,role,backend}`. **Hard prerequisite for profiles — lands first in Batch 1.**

### A.7 Shared constants (one definition, mirrored where noted)

`ARCSEC_PER_RAD = 206.265` in `config.py` AND `ui/src/lib/optics.ts` (settings + atlas use the same value;
atlas writes `206.265` inline — make it import the shared constant). `TOUCH_MAX_RATE_DEG_S = 0.6`,
`MOVE_DEADMAN_MS = 1200` (touch + safety). `COOLER_AT_TARGET_C = 1.0` defined ONCE in `engine.py`
(monitor). `LIVE_WINDOW_S=8`, `STALL_MARGIN_S=20`, etc. exported from `lib/eta.ts` (monitor).

---

## B. THE 4 BATCHES (dependency + priority ordered, disjoint ownership)

Ordering principle: **correctness/foundation first → the design-system primitives everything depends on →
features that consume them → big net-new features last.** Each batch's magnet-file owner is named in bold.

### BATCH 1 — FOUNDATION & CORRECTNESS (lands all shared contracts + the P0 bugs)

**Why first:** fixes the hardcoded-SF-site correctness P0 (poisons altaz/polar/meridian everywhere),
the plate-solve FOV bug, AND lands every shared `types.ts`/`store.ts` contract the later batches compile
against. Also lands reliability (error surfacing is a hard dependency of onboarding + touch's `lockAvailable`).

Magnet-file owners this batch: **store.ts → Impl-1A**, **types.ts → Impl-1A**, **index.css → Impl-1A
(reliability lane A subset only: led/blink/focus/reduced-motion)**, **hub.py → Impl-1B (split blocks)**,
**api/app.py → Impl-1C**, **App.tsx → NOT TOUCHED this batch beyond reliability's split-selector rewrite by Impl-1A**.

| Impl | Owns (create) | Owns (edit) | Must not touch |
|---|---|---|---|
| **1A — FE foundation** | `lib/health.ts`, `lib/humanize.ts`, `lib/notify.ts`, `lib/optics.ts`, `lib/horizon.ts`, `lib/preflight.ts`, `components/icons.tsx` (gear + shared SVGs only if design-sys not yet landed; else defer), `components/Segmented.tsx` | **`types.ts`**, **`store.ts`**, **`index.css`** (reliability+focus subset), `api.ts` (put/patch/del + ApiError + per-endpoint timeout), `ws.ts`, `components/ui.tsx` (Toggle a11y ONLY) | App.tsx, any view |
| **1B — backend hub/config** | `config.py`, `profiles.py`, `plans.py`, `persist.py`(stub for atlas), `tests/test_config.py`, `tests/test_profiles.py`, `tests/test_plans.py`, `catalog/coords.py` sun_altaz/dark_window | **`hub.py`** (site property, effective_optics, poll_status site/optics/disk, FOV, push_site_to_mount, apply_profile, capture_profile, `_check_horizon`, busy_label, `_nina_heartbeat`) | api/app.py, devices |
| **1C — backend API + devices** | — | **`api/app.py`** (config/site/optics/sky, preflight GET, profiles, plans, discover/alpaca, force on goto/start, remove old SiteBody), `devices/alpaca.py` (device identity + query_server/AlpacaScanError), `devices/base.py` (device identity), `devices/nina.py` (last_ok/last_error stamps) | hub.py internals, config.py |
| **1D — IA reorder + nav order** | — | **`App.tsx`** (split broad useStore into selectors per reliability §13; IA reorder Rig→Align→Mount→Focus→Capture→Guide→Plan→Power; mount ConnectionBanner/HealthLeds/Toasts/LogDrawer placeholders) | store/types/css internals (consume only) |
| **1E — reliability chrome** | `components/Toasts.tsx`, `components/LogDrawer.tsx`, `components/ConnectionBanner.tsx`, `components/HealthLeds.tsx`, `components/Icon.tsx` | `views/SequenceView.tsx` (error/aborted render), `views/ConnectView.tsx` (alpaca scan) | App.tsx, store/types |

**Batch-1 sequencing inside the batch:** 1A lands `types.ts`+`store.ts`+`index.css` contracts FIRST
(blocks 1D/1E). 1C lands device-identity FIRST (blocks 1B's profiles). 1B and 1C split `hub.py`/`api/app.py`
cleanly (1B owns hub, 1C owns api). 1D's App.tsx split is the single owner of App.tsx this batch.

**Batch-1 acceptance:** site saved with no devices survives restart and `/api/site/sky` reflects it;
catalog/header ALT reflect real site once mount connects; solve logs computed FOV hint not `1.0`; profile
apply returns per-device outcome; WS-kill keeps Abort/Stop enabled and shows "rig keeps running"; sequence
error renders a banner with no progress bar.

### BATCH 2 — DESIGN SYSTEM + LIVE PREVIEW + MONITOR (the primitives + their first consumers)

**Why second:** the design-system surface IS the shared visual/interaction foundation (Icon, HoldButton,
Led, Stat, Tooltip, EmptyState, the dimmer, the night LUT) that Batches 3–4 consume. Live-preview and
Monitor are bundled here because they share the `previews[]` store slice and the preview REST routes, and
Monitor embeds live-preview's filmstrip.

Magnet-file owners this batch: **store.ts → Impl-2A (preview+monitor slices)**, **types.ts → Impl-2A**,
**index.css → Impl-2B (design-system, the canonical token+LUT+dimmer pass)**, **App.tsx → Impl-2B
(dimmer cluster, `.dim-content`/`.overlay-top`, nav icons)**, **hub.py → Impl-2C (preview + meridian +
cooler)**, **api/app.py → Impl-2C (preview routes + monitor snapshot)**.

| Impl | Owns (create) | Owns (edit) | Must not touch |
|---|---|---|---|
| **2A — preview/monitor store+contracts** | `lib/eta.ts`(+test), `lib/stateMeta.ts` | **`types.ts`** (PreviewInfo rewrite, monitor types), **`store.ts`** (preview+monitor slices, narrow hooks) | App.tsx, index.css |
| **2B — design system** | (none new beyond icons if 1A deferred) | **`index.css`** (CANONICAL tokens/LUT/dimmer/focus/motion/LED shapes/stars/flex/empty), **`App.tsx`** (dimmer cluster, single mode icon, lock, `.dim-content`+`.dim-scrim`+`.overlay-top`, nav icons + 4-corner brackets), `components/ui.tsx` (Led/Stat/Confirm→**HoldButton**/Tooltip/InfoDot/EmptyState), `components/graphs.tsx`, `components/polar.tsx`, `index.html` (anti-flash), light view sweeps (unicode→Icon, Led state=) | store.ts, types.ts, hub.py |
| **2C — backend imaging + preview/monitor telemetry** | — | **`hub.py`** (PreviewEntry ring, `_publish_preview`, server meridian from HA, camera.cooler), **`api/app.py`** (preview routes + monitor snapshot), `imaging/processing.py`, `imaging/stars.py`, `imaging/__init__.py`, `devices/alpaca.py` (MaxADU→full_well; cooler), `devices/nina.py` (data_is_linear; cooler), `devices/sim.py` (cooler power model), `sequence/engine.py` (paused-aware elapsed + deterministic ETA + COOLER_AT_TARGET_C) | api/app.py preview-route signatures landed as 501 stubs first |
| **2D — live-preview UI** | all `components/preview/*` (LivePreview, PreviewStage, usePreviewGestures, useImageRemap, Reticle, ScaleBar, StretchHistogram, StarOverlay, ClipMaskLayer, FrameStats, PreviewMeta, FocusVerdict, PreviewToolbar, FrameFilmstrip) | `views/CaptureView.tsx` (swap inline preview), `views/FocusView.tsx` (embed PreviewStage) | App.tsx, store internals (consume hooks) |
| **2E — monitor UI** | `components/monitor.tsx`, `views/MonitorView.tsx` | (App.tsx monitor NAV entry handed to 2B as an append) | App.tsx (hand append to 2B), store/types |

**Batch-2 sequencing:** 2A freezes `PreviewInfo` rewrite + monitor types FIRST (blocks 2D/2E). 2C lands
preview-route 501 stubs in api/app.py FIRST, then fills them (so the route signatures exist for 2D). 2B
owns the entire `index.css` token pass — every other surface's CSS asks resolve to 2B's versions.
HoldButton ships here (2B) — Batch 3 deletes its `HoldButton.stub.tsx` once 2B merges.

**Batch-2 acceptance:** NINA B/M/W disabled + no clip mask + histogram labeled "of displayed image";
no-flash 2s loop (snap) and 30s sub (fade); night good/warn/bad distinguishable by shape with color
removed; alarm (bad) is the brightest rung; focus legible in preview under night mode; Monitor ETA shows
`~` for first <3 frames; finish clock largest header text; meridian populates from server HA.

### BATCH 3 — ONBOARDING/SAFETY-UX + TOUCH ERGONOMICS (consume the primitives, add field safety)

**Why third:** both depend on Batch-2's `HoldButton` and on Batch-1's reliability error-render
(`lockAvailable` hard-gates on it). Both add safety-of-motion guards. Disjoint UI areas (preflight/checklist
vs slew-pad/nav), so they parallelize cleanly within the batch.

Magnet-file owners this batch: **store.ts → Impl-3A (onboarding slice) then Impl-3B appends touch slice
at a named anchor**, **App.tsx → Impl-3A (gating + ConfirmHost) then Impl-3B appends TouchGuard/HeaderControls/
BottomNav as narrow-selector children**, **index.css → Impl-3B (touch tokens, additive)**,
**hub.py/api/app.py → Impl-3C (deadman + clamp)**.

> Onboarding's `store.ts`/`App.tsx` edits and touch's are DISJOINT KEYS/regions (onboarding: `site`,
> `equipConnected`, `confirm`; touch: `locked`,`lockAvailable`,`monitorAwake`,`touch`). The named-anchor
> append pattern (both specs already mandate it) lets 3A land first, 3B append. One implementer could own
> both magnet edits if the orchestrator prefers zero hand-off.

| Impl | Owns (create) | Owns (edit) | Must not touch |
|---|---|---|---|
| **3A — onboarding primitives + wiring** | `components/Tooltip.tsx`, `help.ts`, `components/Checklist.tsx`, `components/ConfirmDialog.tsx`(+ConfirmHost), `components/PreflightStrip.tsx`, `components/PreflightModal.tsx`, `components/NotConnectedInterstitial.tsx` | **`store.ts`** (site/equipConnected/confirm — Batch-1 may have stubbed; 3A finalizes), **`App.tsx`** (gating render path, mount ConfirmHost, Night-vision Toggle, LOG ≥44px, nav lock icons), `views/MountView.tsx` (GOTO guard + alt glyph — coordinate with 3B), `views/CaptureView.tsx` (HelpSheet — coordinate with 3B) | touch's store keys, slewController |
| **3B — touch ergonomics** | `lib/slewController.ts`(+test), `lib/haptics.ts`(+test), `lib/useWakeLock.ts`, `components/SlewPad.tsx`, `components/TouchGuard.tsx`, `components/NavMoreSheet.tsx`, `components/navIcons.tsx`, `components/HeaderControls.tsx`, `components/BottomNav.tsx`, `components/HoldButton.stub.tsx`(delete on Batch-2 merge) | **`store.ts`** (touch slice append), **`App.tsx`** (mount TouchGuard/HeaderControls/BottomNav append), **`index.css`** (touch tokens append), `views/MountView.tsx` (SlewPad — coordinate with 3A), `views/CaptureView.tsx`/`FocusView.tsx`/`SequenceView.tsx` (tap sizing, deletes) | onboarding's store keys |
| **3C — backend safety motion** | `tests/test_move_watchdog.py` | `api/app.py` (TOUCH_MAX_RATE clamp + note_move; preflight force already landed B1), `hub.py` (last_move_ts, note_move, `_move_watchdog` — the ONE deadman), `devices/alpaca.py` (stop() zeroes both axes), `devices/sim.py` (no stop() override + clamp) | config/profiles |

**View co-ownership note (MountView/CaptureView/SequenceView):** onboarding (3A) and touch (3B) BOTH edit
these. Resolve by region: 3A owns the GOTO-guard + Help affordances + Abort→HoldButton + readiness strip;
3B owns the SlewPad replacement + tap-sizing + delete-undo + mobile card layout. **The orchestrator must
serialize these three view files within Batch 3** (3A then 3B, or one implementer) — they are the only
non-magnet collisions in the batch.

**Batch-3 acceptance:** every `CheckItem.word` non-empty; keyboard reaches every new control with a ring
(day+night); below-horizon GOTO blocks with single-OK dialog (no hold); kill network mid-slew → mount halts
≤1.2s ≤0.72°; STOP zeroes both axes; lock mid-hold stops slew; Park/Disconnect use HoldButton, STOP/Abort/
Halt stay single-tap; haptics toggle hidden on no-vibrate device.

### BATCH 4 — SKY ATLAS/FRAMING/MOSAIC/VISIBILITY + AUTOMATION/SAFETY MONITOR (big net-new features)

**Why last:** largest net-new surface area, depends on everything below (atlas needs `hub.optics`+`plan`
store-lift from B1, the design-system primitives from B2, the touch overflow-sheet pattern from B3;
automation needs the merged `AppConfig`, the safety-deadman from B3, the reports/alerts infra). Both add
new top-level views and new backend packages.

Magnet-file owners this batch: **store.ts → Impl-4A (atlas) then Impl-4D (automation) at named anchors**,
**types.ts → Impl-4A then 4D**, **index.css → Impl-4A (atlas tokens, additive)**, **App.tsx → Impl-4A
(atlas nav + More overflow)**, **hub.py → Impl-4B/4D split blocks**, **api/app.py → all via include_router
(no shared function edits)**.

| Impl | Owns (create) | Owns (edit) | Must not touch |
|---|---|---|---|
| **4A — atlas contracts+store+shell** | `views/AtlasView.tsx`, `lib/framing.ts`, `lib/visibility.ts`, `persist.py`(finalize) | **`types.ts`** (atlas types + Target fields), **`store.ts`** (atlas slice), **`index.css`** (--survey-filter/.svg-halo/Stepper), **`App.tsx`** (atlas nav + More overflow), `components/ui.tsx` (Stat glyph/hint, Stepper, IconButton), **`hub.py`** (optics seed block), `sequence/models.py` (Target.rotation_deg/mosaic_group), `api/app.py` (POST /api/optics alias + include_router survey/framing/visibility), `views/MountView.tsx` (Frame button), `views/SequenceView.tsx` (mosaic group rendering) | automation's store keys, survey/framing/visibility router internals |
| **4B — atlas math+canvas+mosaic+visibility** | `components/atlas/*` (SkyCanvas, FovOverlay, MosaicPanel, VisibilityPanel, SurveyControls), `catalog/survey.py`, `catalog/framing.py`, `catalog/visibility.py`, `catalog/__init__.py` exports | — | App.tsx, store/types (consume only) |
| **4C — automation backend** | `alerting.py`, `sequence/schedule.py`, `sequence/report.py` | **`config.py`** (SafetyConfig/EscalationConfig/AlertSink + AppConfig append), `sequence/models.py` (Schedule, Target.schedule, SequencePlan.safety_check — coordinate Target edit with 4A), `sequence/engine.py`, `devices/base.py`/`alpaca.py`/`sim.py` (SafetyMonitor, destination_pier_side), **`hub.py`** (ROLES+safety, safety_reading, reconnect_role — split block from 4A's optics block), `api/app.py` (safety/alerts/reports routers via include_router) | atlas modules |
| **4D — automation UI** | `views/ReportView.tsx` | **`store.ts`** (automation slice append), **`types.ts`** (automation types append), `views/SettingsView.tsx` (safety/alerts/schedule panels — coordinate with settings Batch-1 SettingsView owner; see note), `App.tsx` (automation handled via 4A's nav), `components/ui.tsx`/`graphs.tsx` (additive) | atlas store keys |

**SettingsView ownership note:** the Batch-1 settings spec creates `SettingsView.tsx`+`settings/*Panel.tsx`,
but automation (4D) ALSO wants safety/alerts/schedule panels in SettingsView. **Resolution:** Batch-1
lands `SettingsView.tsx` as a panel-grid shell + Site/Optics/Profiles/Plans panels; Batch-4 (4D) APPENDS
Safety/Alerts/Schedule/Report panels as new files under `settings/` and registers them in the grid via a
one-line addition the SettingsView owner accepts. Do not rewrite the shell.

**`sequence/models.py` `Target` co-edit:** 4A adds `rotation_deg/mosaic_group`; 4C adds `schedule`. Both
nullable/additive — serialize 4A first, 4C appends. One implementer may own the file.

**Batch-4 acceptance:** open M31→Send 1×1→no NaN (ρ→0); Send 3×1→no 422 (%24 wrap); re-frame replaces
panels not drops; high-lat summer→labeled fallback window never blank; safety state transitions
pause/resume the engine; alert round-trip sets `verified`; report shows by-filter headline.

---

## C. CROSS-CUTTING RISKS (the orchestrator must actively manage these)

**Risk-1 — Five-magnet-file contention is the whole-program failure mode.** `store.ts`, `types.ts`,
`index.css`, `App.tsx`, `hub.py`, `api/app.py` are each demanded by 4–8 surfaces. Mitigation: the
single-owner-per-batch + named-anchor-append rule in §B. The orchestrator must REJECT any task that edits
a magnet file it does not own this batch. The append anchors must be physically present (sentinel comments)
before parallel work starts.

**Risk-2 — `AppConfig` is defined incompatibly by two specs.** settings = `{version,site,optics,active_profile_id}`;
automation = `{site,safety,escalation,alerts,deadman_url,schedule_defaults}`. If both land naively, one
clobbers the other and config files written by one version fail to load in the other. Mitigation (§A.1):
ONE union model — settings owns its keys in Batch 1, automation APPENDS its keys in Batch 4 to the same
pydantic class + TS interface. `config.py` `ConfigStore` migration must tolerate missing automation keys
(default them) and missing settings keys. Add a `test_config` case round-tripping the union.

**Risk-3 — `Optics` has two field-name shapes.** settings/config = `sensor_width_px`/`sensor_height_px`;
atlas = `sensor_width`/`sensor_height`. These feed the same `hub.optics` and the same FOV math. Mitigation:
pick `sensor_width_px`/`sensor_height_px` (settings, the persisted config owner) as canonical; atlas's
`lib/framing.ts` + `POST /api/optics` adapt at the boundary. Flag to atlas implementer (4A) explicitly.

**Risk-4 — `hub.site` shape must satisfy four consumers simultaneously.** altaz/polar/catalog read it;
settings makes it a config-backed property; onboarding needs `is_default`+`horizon_min_deg`; automation
needs `elevation_m`; monitor's meridian reads `longitude`. If any consumer's expected key is missing,
`altaz` or the meridian calc throws. Mitigation (§A.5): Batch-1 1B lands the FULL merged dict on day one,
even though onboarding's UI for `horizon_min_deg` lands later. Every later batch only READS it.

**Risk-5 — The move-axis deadman is co-owned by touch (Batch 3) and safety (Batch 4) and must exist exactly
once.** Two implementations = two watchdog tasks fighting over `tel.stop()`. Mitigation: Batch-3 3C lands
`hub._move_watchdog`/`note_move`/`last_move_ts` as the canonical implementation; Batch-4 automation REUSES
it (its `safety_reading`/pause logic calls the same primitives, does not re-create the task). The
`MOVE_DEADMAN_MS=1200`/`TOUCH_MAX_RATE_DEG_S=0.6` constants live in one place.

**Risk-6 — `lockAvailable` and onboarding both hard-depend on reliability's sequence-error render.** touch's
screen-lock is gated `false` until reliability ships error rendering; onboarding's pre-flight trust depends
on the same. Mitigation: reliability (Batch 1, lane 1E) lands the sequence-error banner + toast queue
BEFORE Batch 3 starts. The orchestrator must verify the Batch-1 reliability acceptance gate before
green-lighting Batch 3, or touch's lock stays disabled (acceptable degradation, but call it out).

**Risk-7 — `PreviewInfo.width/height → data_width/data_height` is the only breaking rename.** Exactly one
existing consumer (CaptureView header span). live-preview (Batch 2, 2A) owns the rename AND the consumer fix
(2D's CaptureView). monitor's `PreviewTile` uses the same `/api/preview/{id}.png` URL — verify monitor (2E)
reads the renamed fields. Any code touching `preview.width` after Batch 2 is a bug.

**Risk-8 — Toast model: three specs invented three queues.** design-system `ToastItem`, reliability
`Toast`, automation `toasts[]`. If two land, `handleEvent` has two toast paths and errors double-surface.
Mitigation (§A.0/§A.2): reliability's `Toast`+`enqueueToast` is THE model, landed in Batch 1. design-system
(Batch 2) and automation (Batch 4) CONSUME it; `showToast`/`pushToast` are thin aliases so the ~12+8 call
sites compile unchanged. design-system must NOT land its own `ToastSlice`; it folds into reliability's.

**Risk-9 — `:focus-visible` + `prefers-reduced-motion` are added by all 9 specs.** Duplicate CSS rules are
harmless but a maintenance smell; worse, conflicting `outline` colors/offsets cause flicker. Mitigation
(§A.3): ONE `:focus-visible` rule and ONE reduced-motion block, owned by the design-system token pass
(Batch 2, 2B), with the reliability subset landed in Batch 1 (1A) if Batch 2 hasn't started. Every other
spec's copy is deleted from scope. Same for the `Toggle` a11y addition (land once in Batch 1, 1A).

**Risk-10 — IA nav reorder is claimed by 3 specs with the same target order.** design-system, touch, and
atlas all reorder nav to `Rig→Align→Mount→Focus→Capture→Guide→Plan→Power` and add new entries
(settings gear, monitor, atlas). Mitigation: Batch-1 1D lands the canonical 8-entry order + the gear/header
pattern; Batch-2 2B adds nav ICONS (not order); Batch-2/3/4 each APPEND their one new nav entry (monitor,
atlas) or header control (settings gear) without reordering. The 5+More mobile split (touch) lands in
Batch 3 against the already-canonical order. "Land the order once, append entries thereafter."

**Risk-11 — `engine.py` is edited by monitor (ETA), onboarding (cooling band constant), reliability
(busy flag), and automation (schedule/safety/report).** Mitigation: monitor (Batch 2, 2C) lands the ETA +
`COOLER_AT_TARGET_C` + paused-elapsed rewrite; automation (Batch 4, 4C) appends schedule/safety hooks.
Reliability's `_sequence_busy` flag is a small Batch-1 addition by 1B/1C. Serialize engine.py edits by
batch; within a batch it is single-owner.

**Risk-12 — `devices/alpaca.py`/`base.py`/`sim.py`/`nina.py` are touched by 5+ surfaces** (device identity,
full_well/MaxADU, cooler, stop()-zeroes-axes, safety monitor, nina timestamps). Mitigation: each batch's
device edits are single-owner (B1-1C device identity + nina stamps; B2-2C full_well + cooler; B3-3C
stop()/clamp; B4-4C SafetyMonitor). No two batches edit the same device method; within a batch, one owner.

**Risk-13 — Profiles depend on device-identity which is a different lane than profiles.** settings BE-B
(profiles) needs settings BE-C (device identity) to land first. Mitigation: Batch-1 1C lands device
identity before 1B's `capture_profile`. Stated as an intra-batch ordering constraint.

**Risk-14 — Performance: broad `useStore()` in App.tsx.** reliability, monitor, and touch all independently
demand narrowing App's subscription. Mitigation: Batch-1 1D does the ONE App.tsx split-to-selectors;
Batch-2/3 render new header/nav/lock pieces as separate narrow-selector children and do NOT re-widen App.
The performance work is not a separate surface — it is folded into 1D and enforced as a review gate.

**Risk-15 — `bus.publish` schemaless assumption.** Multiple specs add new WS event payload keys
(`config`, `safety`, `alert`, `report`, `nina_link`, `busy`, `meridian`, `cooler`) without an `events.py`
change, relying on the bus being schemaless. Mitigation: verify `events.py` is genuinely schemaless ONCE
in Batch 1 (settings + reliability both assert this); if any event is typed, that becomes a Batch-1 blocker.
No WS protocol bump in any batch.

---

## D. ONE-SCREEN SUMMARY

- **Batch 1 — Foundation/correctness:** real observing-site (kills hardcoded SF P0), plate-solve FOV,
  config/profiles/plans backend, device identity, reliability error-surfacing + toast queue (THE toast
  model), disk/horizon/preflight backend, the canonical `types.ts`/`store.ts` contracts, App.tsx
  split-selectors + IA reorder. Owners 1A–1E; magnets: store/types/css→1A, hub→1B, api+devices→1C, App→1D.
- **Batch 2 — Design system + Live preview + Monitor:** the Icon/HoldButton/Led/Stat/Tooltip/EmptyState
  primitives + canonical token/LUT/dimmer pass (everything downstream consumes these), the live-preview
  overhaul (PreviewInfo rename, preview routes, imaging), the Monitor dashboard (ETA, meridian, cooler).
  Owners 2A–2E; magnets: store/types→2A, css+App→2B, hub+api→2C.
- **Batch 3 — Onboarding/safety-UX + Touch ergonomics:** readiness checklist + pre-flight + below-horizon
  guard + interstitial + ConfirmDialog (consumes HoldButton), predictable fixed-rate slew + tap-nudge +
  move-axis deadman + screen-lock + 5+More nav. Owners 3A–3C; magnets: store/App→3A then 3B-append,
  css→3B, hub/api→3C. Serialize MountView/CaptureView/SequenceView between 3A and 3B.
- **Batch 4 — Sky Atlas/Framing/Mosaic/Visibility + Automation/Safety monitor:** new Atlas view (survey
  proxy, mosaic, visibility via astropy, plan handoff) and the unattended-safety/schedule/alerts/reports
  automation surface. Owners 4A–4D; magnets: store/types/css/App→4A then 4D-append, hub→4A/4C split,
  api→include_router only. Append Safety/Alerts panels to the Batch-1 SettingsView shell.

**Reconciled magnets at a glance:** `hub.site` = one merged dict (lat/lon/elevation_m/name/is_default/
horizon_min_deg). `AppConfig` = one union model (settings keys + automation keys). Toast = reliability's
`Toast`/`enqueueToast` (others alias). Plan = `plan`/`setPlan` (atlas SSOT, drop `editorPlan`). Hold = one
`HoldButton` (design-system builds, all consume). `:focus-visible`/reduced-motion/Toggle-a11y = land once.
Deadman = one `hub._move_watchdog`. `ARCSEC_PER_RAD=206.265` shared by config.py and lib/optics.ts.
