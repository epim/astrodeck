# AstroDeck next front-end - architecture contract

Status: binding for every task in this programme. Deviations must be named in
the task report and re-verified by the reviewer.

Source of truth for LOOK and COPY: `ASTRODECK-UPDATE/design_handoff_astrodeck_mobile/`
(README.md first, then screenshots/, then the prototype fragments extracted to
`<scratch>/seams/proto/*.html` and `<scratch>/seams/prototype.md`). Source of
truth for BEHAVIOUR that already exists: the seam inventories in
`<scratch>/seams/inventory-*.md`, `store-api.md`, `server-routes.md`,
`shell-and-tests.md`. `<scratch>` =
`C:/Users/bear/AppData/Local/Temp/claude/C--Users-bear-astro/3b3905ef-d651-4f4e-9139-e2810e124cdc/scratchpad`.

## 0. Non-negotiables

1. No functionality lost. Every control, readout, blocked-state reason and
   confirm in the inventories has a home in the new UI. Where the design has
   no place for a feature, it lands in the sheet or sub-nav named in the hub
   spec (tuning editors at tablet/desktop; never dropped).
2. Same auth, RBAC, `/api` and `/ws`. The new UI reuses `ui/src/store.ts`,
   `ui/src/api.ts`, `ui/src/api/*`, `ui/src/ws.ts`, `ui/src/lib/*` as they are.
   No parallel store, no second WebSocket, no new fetch wrapper.
3. The legacy UI keeps working at `#/classic` (and `#/classic/<view>`), untouched
   except for `ui/src/main.tsx` choosing which root to mount.
4. Fidelity high: colours, type, spacing, radii, states, copy per the README and
   screenshots. Micro labels below 10 px are raised to a 10 px floor.
5. Copy rules: hyphens, never em-dashes, in UI strings. "bind", never "slave".
   No emojis anywhere (code, comments, docs, commit messages). Filter names come
   from the wheel driver; never ask the user to type one. Every string must
   carry information the pixels do not (no captions narrating the layout).
6. Honest-disabled: never the native `disabled` attribute on a control a user
   could want to press. Dim + `aria-disabled` + the reason (see section 8).
7. Hit targets >= 44 px (toolbar 48, primary CTA 52-56).
8. Agents never commit. Report the exact file list; the controller commits with
   `git commit -- <paths>`. Never `git add -A`.
9. Tests: every task ships tests in the repo convention (section 13) and the
   report states `npm test` and `npx tsc -b` results verbatim. If the harness
   auto-backgrounds a command, re-run it in the foreground; there are no monitor
   notifications.
10. A session must never depend on the phone: the UI is a remote; nothing that
    matters runs only in the browser (allocation, ranking and advection are
    display math; the engine decides).

## 1. Directory layout

```
ui/src/next/
  NextApp.tsx            root of the new UI (auth gate, shell, hubs, hosts)
  ARCHITECTURE.md        a copy of this document (T0.1 copies it in)
  router.ts              hash router: parse/build, nav API, useRoute
  breakpoint.ts          useBreakpoint(): "phone" | "tablet" | "desktop"
  legacyBridge.ts        store.view / helpTopic -> route mapping
  next.css               the new component classes (nx-* prefix), imported ONLY by NextApp
  icons.tsx              hub icons + device glyphs + misc (design SVG paths)
  ui/                    primitives (section 6)
  shell/                 Header, TabBar, Rail, Banners, CampaignStrip, SheetHost,
                         SessionColumn, Toasts, ConfirmCard, Popover host
  lib/                   pure modules: gate.ts, incidents.ts, allocation.ts,
                         reach.ts, advection.ts, cloudTiles.ts, horizonModel.ts,
                         fov.ts (thin re-export of lib/framing), format.ts, versions.ts
  hubs/
    sky/       SkyHub.tsx + finder/*, sheets/* (targets, sites, horizon, coords,
               quickSession, flowCard)
    weather/   WeatherHub.tsx (conditions, dome, radar)
    session/   SessionHub.tsx + now/*, gallery/*, flows/*, sheets/* (files, report,
               planEditor)
    rig/       RigHub.tsx + devices/*, capture/*, sheets/* (camera, mount, polar,
               focuser, wheel, guider, rotator, safety, power, addDevice, driver,
               profiles, inspect)
    monitor/   MonitorHub.tsx (live, log, alerts)
    settings/  SettingsHub.tsx + sheets/* (setup, connection, optics, sites,
               horizon, quickDefaults, account, users, about, help, ...)
  __tests__/             shell, router, bridge, breakpoint tests
  hubs/<hub>/__tests__/  per-hub DOM tests
  lib/__tests__/         pure tests
```

File ownership is by directory. A task that needs a change outside its
directory names it in the report and does NOT make it unless the task brief
pre-authorises it.

## 2. Entry and roots

`ui/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import NextApp from "./next/NextApp";
import { isClassicHash } from "./next/router";

function Root() {
  // Re-evaluated on hashchange so a user can switch roots without a reload.
  const [classic, setClassic] = useState(() => isClassicHash(location.hash));
  useEffect(() => {
    const on = () => setClassic(isClassicHash(location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return classic ? <App /> : <NextApp />;
}
createRoot(document.getElementById("root")!).render(<StrictMode><Root /></StrictMode>);
```

`isClassicHash(h)` is true for `#/classic` and `#/classic/...`. When the
classic root mounts with `#/classic/<view>`, `App` is NOT modified; instead
`Root` calls `useStore.getState().setView(view)` once before rendering if
`<view>` is a valid `ViewName` (list in `ui/src/types.ts`). A footer link in the
new Settings hub ("Open the classic UI") and a header link in nothing else
(the classic UI already has no link back; add none - the user types `#/`).

`NextApp` owns: `connectWs()` on mount (exactly as `App.tsx:343-345` does; the
legacy root does the same, and only one root is mounted at a time), the auth
gate publishing (`setAuthGate`, same three-state logic as `App.tsx:293-314`;
copy the effect verbatim, including the 4 s grace), the resume-arm poll
(`App.tsx:384-399`), the weather alert dialog (`App.tsx:451-469`), the weather
refetch on login -> open (`App.tsx:488-507`), the wake lock hook, the
brightness reset hatch and Shift+B. These are lifted, not re-invented: read
App.tsx and transcribe.

Gate screens: while `authGate === "resolving"` render the same neutral splash;
while `"login"` render the existing `views/Login.tsx` inside the new chrome
(a `Sheet` with no BACK, wordmark above). Login is reused as-is; only its
container changes. Toasts and the confirm card stay mounted under both gates,
exactly as App does.

## 3. Router (hash)

Grammar: `#/<hub>[/<sub>][/<sheet>[/<sheet2>]][?k=v&k2=v2]`

- `hub` in `sky | weather | session | rig | monitor | settings`; unknown -> `sky`.
- `sub` is recognised only if it is in `SUBS[hub]`:
  - sky: none
  - weather: `conditions | sky | radar` (default `conditions`)
  - session: `now | gallery | flows` (default `now`)
  - rig: `devices | capture` (default `devices`)
  - monitor: `live | log | alerts` (default `live`)
  - settings: `general | users | about` (default `general`)
- Every remaining segment is a sheet name; at most two (the third is dropped).
- Query params are string -> string; sheets read them via `params`.
- `#/classic[/<view>]` is the legacy root (section 2).

```ts
export type HubId = "sky" | "weather" | "session" | "rig" | "monitor" | "settings";
export interface Route {
  hub: HubId;
  sub: string;                 // resolved default when absent
  sheets: string[];            // 0..2
  params: Record<string, string>;
}
export const SUBS: Record<HubId, readonly string[]>;
export function parseHash(hash: string): Route;
export function buildHash(r: Route): string;        // canonical form
export function isClassicHash(hash: string): boolean;
export function classicView(hash: string): string | null;
export function useRoute(): Route;                  // subscribes to hashchange
export const nav: {
  go(path: string): void;               // path like "/rig/devices/camera?x=1" (pushState via location.hash)
  replace(path: string): void;
  hub(id: HubId, sub?: string): void;   // keeps no sheets
  sheet(name: string, params?: Record<string, string>): void;  // pushes onto the stack (max 2; a third replaces the second)
  closeSheet(): void;                   // pops one; no-op with none open
  back(): void;                         // closeSheet if any, else history.back()
};
```

Rules: `nav.hub` clears sheets and params. Switching hubs never opens on top
of another hub. Sheets are route state, so the browser back button closes a
sheet (history entries are pushed for sheets and hub changes; `replace` for
sub-nav changes). `useRoute` must be cheap: one module-level listener, one
parsed value, React subscribers via `useSyncExternalStore`.

## 4. Breakpoints and layout regions

`useBreakpoint()` uses `matchMedia("(min-width: 768px)")` and
`("(min-width: 1200px)")`. jsdom stubs return `matches: false`, i.e. phone.

Phone (< 768):
```
[Header]            58 px top inset area on iOS standalone (env(safe-area-inset-top))
[Banners]           max 2
[CampaignStrip]     when a campaign exists and hub != session/now
[SubNav chips]      per hub
[Hub body]          scrolls
[TabBar]            6 hubs, active shows label; fixed bottom, safe-area padded
Sheets: full-height layer over everything except toasts; stack max 2.
```
Tablet (768-1199):
```
[Rail 72][ Header/Banners/Strip/SubNav/Hub (flex 1) ][ Panel 420 (only when a sheet is open) ]
```
Desktop (>= 1200):
```
[Rail 72][ Header/... /Hub (flex 1, max 1100) ][ Panel 420 when a sheet is open ][ SessionColumn 360 ]
```
The SessionColumn is the persistent Session · Now condensed: phase pill,
incident card, live stack (smaller), vitals band, integration bar, PAUSE/STOP.
It is hidden while the Session hub itself is open (no duplication) and shows
the "NO SESSION RUNNING" empty state otherwise. The Session hub's Now screen
and the column share components (`hubs/session/now/*` export them).

The finder in the Sky hub is 370 x 372 on phone; at tablet/desktop it fills
its column up to 720 px square. The Flows canvas (Session · Flows at tablet
and desktop) fills the hub body.

## 5. Sheets, popovers, confirm, toasts

`SheetHost` (shell) renders `route.sheets` through a registry composed from
each hub's `sheets` export:

```ts
export interface SheetProps { params: Record<string, string>; depth: 0 | 1; }
export type SheetComponent = (p: SheetProps) => JSX.Element;
// each hub: export const sheets: Record<string, SheetComponent>
```
Names are global and unique (prefix by hub where ambiguous: `sites` is shared
by sky and settings and is ONE component, registered once). On phone the host
is a fixed layer (`.nx-sheet-layer`) with the top sheet visible and the one
under it kept mounted but `inert`; on tablet/desktop the host is the right
panel and shows only the top sheet (BACK pops). Body scroll is locked while a
phone sheet is open.

`Sheet` (primitive) is the content frame: header row (`‹ BACK` pill 44 px,
icon tile 34 px, title 13 px Chakra 600, live line 10.5 px mono dim), body
scroll area, optional sticky footer. BACK calls `nav.back()`.

Popovers (layers, profiles) are local: `Popover` primitive anchored to a
trigger, dismiss on outside tap and Escape, portaled to `#nx-popover-root`.

Confirm: `shell/ConfirmCard.tsx` reads `store.confirm` (the same slice
`confirmDialog()` writes) and renders the design's bottom card: title, body,
KEEP (cancel) and the destructive/primary button; `mode: "hold"` renders the
existing `HoldButton`. So `confirmDialog({...})` from any reused component
just works. `ConfirmHost` from the legacy tree is NOT mounted.

Toasts: `shell/Toasts.tsx` reads `store.toasts`; card `#12141C`, border by
tone (good/info/warn/bad), 2.8 s for success/info; warning/error keep the
store's longer TTL and sticky (`ttl 0`) toasts stay until dismissed (a design
deviation, deliberate: an UNSAFE toast must not vanish in 2.8 s). Top-centre
on phone, bottom-right on tablet/desktop.

## 6. Primitives (`ui/src/next/ui/`)

All primitives: no store reads, no fetch, props only, `nx-*` classes, tokens
from `index.css` (`--accent`, `--bg-raise`, ...). Every interactive primitive
accepts `lockedReason?: string | null` and renders honest-disabled when set:
dimmed, `aria-disabled="true"`, still focusable, and a press calls
`onExplain?.(reason)` (default: a warning toast via `useStore.getState().enqueueToast`
is NOT allowed here - primitives have no store; the CALLER passes `onExplain`;
the shell exports `explainLock(reason)` which enqueues the toast).

```ts
// text + surfaces
Wordmark()                                   // ASTRO<span accent>DECK</span>
Card({ children, tone?: "default"|"accent"|"purple"|"dashed", padding?: number, className? })
Pill({ children, tone?: Tone, glyph?: ReactNode, onClick?, ariaLabel? })    // 26 px, mono 10px
Chip({ children, active?, count?: number, onClick?, tone?, dashed? })       // sub-nav + filters, 36-40 px
Label({ children, size?: 10|11 })            // Chakra 600 uppercase tracking .16em, text-3
Mono({ children, size?, tone? })
Divider()

// controls
ActionButton({
  kind: "primary"|"secondary"|"ghost"|"danger"|"purple",
  size?: "md"|"lg"|"xl",                     // 44 / 52 / 56
  glyph?: ReactNode, children, onPress: () => void,
  lockedReason?: string|null, onExplain?: (r: string) => void,
  arm?: { label: string; ms?: number },      // two-tap arm: first press shows label, second within ms (3000) fires
  busy?: boolean, full?: boolean, ariaLabel?: string
})
IconButton48({ glyph, label, onPress, active?, lockedReason?, onExplain? })   // toolbar 48 px w/ caps label
Switch({ checked, onChange, label, note?, lockedReason?, onExplain? })      // 40x24
Segmented({ options: {value, label, sub?}[], value, onChange, label, lockedReason?, onExplain? })
Checkbox22({ checked, onChange, label })
ReadoutGrid({ children, cols?: 3|4 })
ReadoutTile({ label, value, sub?, selected?, onSelect?, tone?, ariaLabel? })   // 60 px, accent border when selected
Dial<T>({ label, hint?: string, options: {value: T, label: string}[], value: T, onChange,
          stopPx?: number /*64*/, lockedReason?, onExplain? })              // drag or tap, centre marker, snap 160 ms
RingGauge({ value: number, min, max, label, sub?, tone?: "accent"|"warn"|"dim", size?: 92 })
Bar({ value: number /*0..1*/, tone?, height?: 3|6, segments?: {frac: number, fill: number, color: string, label?: string}[] })
Stepper2({ value, onChange, step, min?, max?, format?, label, lockedReason? })  // "- +" 44 px pair
Field({ label, hint?, children })            // labelled input row
TextInput({ value, onChange, placeholder?, mono?, type?, ariaLabel, lockedReason? })

// composites (still store-free)
StatusPill({ text, tone, pulse?: boolean })  // phase pill with dot
IncidentCard({ incident: Incident, onAction: (id: string) => void, lockedFor?: (id: string) => string|null })
BannerCard({ tone: "good"|"info"|"warn", text: ReactNode, cta?: {label, onPress}, onDismiss })
EmptyCard({ title, hint?, action?: ReactNode })              // dashed
ListRow({ icon: ReactNode, title, sub?, right?: ReactNode, onPress?, chevron?, tone? })   // settings / device rows
DeviceGlyphTile({ glyph, led?: "on"|"off"|"warn"|"bad" })    // 34 px tile with LED corner
Sheet({ title, sub?, icon?: ReactNode, live?: ReactNode, right?: ReactNode, footer?: ReactNode, children, backLabel?: string })
Popover({ open, anchorRef, onClose, children, align?: "start"|"end" })
Tabs/SubNav({ items: {id, label, count?, dot?: Tone}[], value, onChange })
```

`Tone = "accent" | "accent2" | "good" | "warn" | "bad" | "info" | "dim"`.

Sizes, radii, shadows, motion come from the README token section: radii 16
cards/sheets, 14 inner cards, 12 buttons, 10 small buttons/tiles, 999 pills;
accent glow `0 0 18px rgba(0,210,255,.35)` on primary; sheets `0 12px 40px
rgba(0,0,0,.6)`; dial snap 160 ms ease-out; wheel 500 ms cubic-bezier(.2,.8,.2,1);
pulse 1.4 s; bar fills 500 ms. Reduced motion: honour `prefers-reduced-motion`.

Night mode: the app already swaps tokens with `:root.night` (`index.css`).
The design's blend-mode layer is NOT used; token swap is the house mechanism
and every primitive must read correctly under it (shape and text carry state,
never hue alone). `Switch` shows its state by knob position AND an `ON/OFF`
micro label under night.

## 7. CSS (`next.css`)

- Class prefix `nx-`. Unlayered (like `index.css`), so utilities do not beat
  it; keep specificity flat (single class selectors) and put variants on
  `data-*` attributes (`data-tone`, `data-selected`, `data-locked`).
- Reuse tokens from `index.css`; define nothing that already exists. Add only
  `--nx-filter-L/R/G/B/Ha/OIII/SII/OSC` (filter colours from the README).
- `.nx-app` is the root; `.nx-phone`, `.nx-tablet`, `.nx-desktop` on it drive
  layout. Safe areas: `padding-top: env(safe-area-inset-top)` on the header,
  `padding-bottom: env(safe-area-inset-bottom)` on the tab bar and phone sheet
  footers.
- Fonts: `font-family` via the existing Tailwind bridge (`font-display`,
  `font-mono`, `font-sans`) or the `nx-display/mono/sans` classes.

## 8. RBAC and gating (read-only rendering)

Roles: viewer, syncer, operator, admin (`lib/caps.ts`). Rules:

- Every control that issues a command names its capability. The lock reason
  comes from ONE helper, `next/lib/gate.ts`:

```ts
export interface GateInput {
  cap?: Capability;                 // control.capture, control.mount, control.guide, control.power,
                                    // config.backend, config.safety, config.solar_override,
                                    // config.site_optics, config.alerts, admin.users, system.update,
                                    // view.media, view.weather, view.site_precise, view.site_derived
  needsRole?: string;               // device role that must be connected: camera|telescope|guider|switch|focuser|filterwheel|rotator
  busyLane?: string;                // blocked while this lane is busy
  extra?: string | null;            // caller-specific reason (e.g. "a flow owns the mount")
}
export function lockReason(inp: GateInput, s: {
  principal: Principal | null; status: RigStatus | null; equipConnected: boolean; wsPhase: WsPhase;
}): string | null;   // priority: link down -> cap -> role not connected -> busy lane -> extra
export function useLock(inp: GateInput): { lockedReason: string | null; onExplain: (r: string) => void };
```
Reason copy: link down: "the rig is not reachable"; cap: `needs ${accessPhrase(cap)}`
(from `lib/caps.ts`, e.g. "needs operator or admin access"); role:
"connect a camera first"; busy: the `lib/humanize.ts` lane sentence.

- Viewer rendering: primary CTAs (IMAGE THIS, RUN NOW, CAPTURE, PARK, pad,
  toggles) render honest-disabled with the reason; nothing is hidden, so a
  viewer sees the same screen as the operator. Exception (design): the Files
  sheet shows FITS only when `view.media` is held (JPEG previews otherwise,
  with the line "FITS originals need admin access").
- Operator without `control.power` / `config.backend`: Power sheet, Add device,
  Profiles and driver declaration render read-only with the reason.
- Site privacy: coordinates render only with `view.site_precise`; otherwise
  the derived line ("site set · precise location hidden for this role") from
  `lib/site.ts`'s existing helper.
- Settings shows "signed in as <email or name> · <role>" and the sign-out
  control (reuse `components/settings/AccountPanel.tsx` logic).

## 9. Data access rules

- Read the store with narrow selectors (`useStore(s => s.x)`), never the whole
  state. Use existing hooks from `store.ts` (see `store-api.md` section 1.4).
- Call the existing `api/*.ts` functions; where a view used a raw
  `api.post("/api/...")` (documented in the inventories), call the same path.
  New endpoints (section 12) get typed wrappers in `ui/src/api/<domain>.ts`
  (existing files, appended - the server wave owns those appends).
- Images: `u(path)` from `lib/base.ts` for every `<img src>`; the cookie
  carries auth.
- Persist per-phone state (lens kinds, layers, mode, dlPref, night, connMode)
  under `localStorage` keys prefixed `astrodeck-next-`; wrap every access in
  try/catch; render correctly with nothing stored.
- Never write `store.view` from the new UI except the classic handoff.

## 10. Incident model

`next/lib/incidents.ts` derives the design's incident list from engine state.
It is PURE (inputs are plain store slices) and tested.

```ts
export type IncidentKind = "cloud" | "safety" | "link" | "solve" | "af" | "guide" | "disk" | "cooler" | "stall";
export interface IncidentAction { id: string; label: string; primary?: boolean; cap?: Capability; }
export interface Incident {
  kind: IncidentKind;
  pill: "HOLDING" | "PARKED" | "STALE" | "RETRYING" | "RECOVERING" | "GATE CLOSED" | "NO PROGRESS";
  color: string;                    // #ffb454 warn, #ff5470 bad, #7683a5 dim
  title: string;                    // "CLOUD HOLD", "LINK LOST", ...
  sinceMs: number | null;
  resolvesItself: boolean;          // "resolves itself if it can" | "needs you"
  engine: string;                   // what the engine is doing (README table)
  next: string;                     // what happens automatically
  actions: IncidentAction[];
}
export function deriveIncidents(inp: IncidentInputs, nowMs: number): Incident[];  // most severe first
```
The exact engine fields per kind are fixed by the Session hub plan
(`inventory-session-monitor.md` sections on hold reasons, safety, stall,
mountOp.stuck, focus failed, guide lost, disk, cooler gate). Every incident's
actions call existing endpoints (ignore weather tonight, resume, stop, retry
solve, skip target, re-acquire, continue unguided, accept setpoint, ...).
Where the engine has no such verb, the action is omitted, never faked.

Cross-hub: the Session tab dot pulses in the top incident's colour while one
is active and another hub is open; an amber banner announces it on every other
hub with a "session ›" CTA.

## 11. Reuse map (existing components mounted inside the new UI)

Mount as-is (props-driven or self-subscribed), wrapped in the new chrome:

- Preview stack: `components/preview/PreviewStage`, `StretchHistogram`,
  `LoupePanel`, `FrameStats`, `FrameFilmstrip`, `SnrChip`, `PreviewToolbar`
  (Inspect mode in Rig · Capture and Session · Now).
- `components/preview/SessionStack` (live stack), `LiveStackReadout`.
- Sky: `components/atlas/SkyCanvas` (+ `TileEngine`), `CatalogSearch`,
  `FovOverlay`, `PointingFrame`, `AnnotationMarkers`, `VisibilityPanel`,
  `MosaicNight`, `TonightPicker` (ranking source for Suggested targets).
- Flows: `components/flows/FlowsView` (canvas, editor, inspector, library) at
  tablet/desktop; `QuickFlow` (quick-session compile); `TonightCampaign`,
  `TonightTimeline`, `TonightStory`, `TonightPlan`, `CalibrationMatrix`.
- Plan editor: `views/SequenceView` inside the `planEditor` sheet at
  tablet/desktop (quotas, scheduling, identity, import/export, instructions).
- Weather: `components/weather/RadarMap`, `SkyConditionsPanel`,
  `components/cloudmap/SkyDome` (+ `SkyDomePanel`).
- Polar: `components/polar` (`PolarReticle`), `PolarQuickBar`, `PolarSolveRing`,
  `GuideFramePreview`.
- Guide: `GuideGraph`/`GuideScatter` (components/graphs), `GuideProviderControl`,
  `GuideFramePreview`, guide-assistant panel logic (`lib/guideAssistant.ts`).
- Mount: `SlewPad` + `lib/slewController.ts`, `GotoStrip`.
- Focus: `components/focus/FocusPod`, `BahtinovAid`, `FocusVerdict`.
- Equipment: `DriversPanel`, `ProfileList`, `BackendLinkGrid`, `RotatorCard`,
  `TasksPanel` logic; the role-assignment table logic from `EquipmentView`
  (`lib/equipment.ts`).
- Settings panels: `UsersPanel`, `AuthMethodPanel`, `AccountPanel`, `AlertsPanel`,
  `EscalationPanel`, `SafetyPanel`, `SafetyLimitsPanel`, `StandardsPanel`,
  `CalibrationLibraryPanel`, `CalibrationTolerancesPanel`, `NamingPanel`,
  `WcsStampPanel`, `SyncPanel`, `RestrictedAssetsPanel`, `FactoryResetPanel`,
  `UpdatePanel`, `CreditsPanel`, `CloudmapPanel`, `WeatherPanel`,
  `SkyAtlasPanel`, `OpticsPanel` logic, `SitePanel` logic, `HelpView`,
  `views/ReportView`, gallery `FrameTile`/`FrameViewer`/`TrashPanel`.
- Login: `views/Login` (whole).

Rebuilt in the new language (design-specified screens): the six hubs' primary
screens and the device sheets listed in the README. A reused panel is mounted
inside a `Sheet` with the design header; its internal `Panel` chrome is
acceptable for tuning editors (tablet/desktop) and MUST be restyled where the
README specifies the screen (device sheets, Settings groups, Files, Gallery).

## 12. Server additions (Wave S, Python, own tests, additive only)

S1. Per-site horizon polyline: `Location.horizon_points: list[[az, alt]] | null`
    on saved locations (`locations.py`), returned by `GET /api/locations`,
    accepted by the create/update routes; when a location is applied as the
    site (`/api/site` from a saved location), copy its points into
    `config.safety.horizon` so the engine's obstruction rule uses the drawn
    line. `GET /api/site` echoes `horizon_points`.
S2. Weather feed fields: `/api/weather` gains `now: {temp_c, dewpoint_c,
    humidity_pct, wind_kmh, wind_dir_deg, gust_kmh, cloud_base_m}` and hourly
    `wind_kmh[]`, `wind_dir_deg[]`, `humidity_pct[]`, `dewpoint_c[]` from
    Open-Meteo (`windspeed_10m`, `winddirection_10m`, `windgusts_10m`,
    `relativehumidity_2m`, `dewpoint_2m`, `temperature_2m`); cloud base as the
    lifting-condensation estimate `125 m * (T - Td)` when no feed value.
S3. `GET /api/remote/status` (view.status): `{enabled, home_id, relay_host,
    connected, last_error, since_unix, via: "direct"|"relay"}` where `via` is
    how THIS request arrived (`request.scope["state"]["astrodeck_remote"]`).
S4. PWA: `ui/public/manifest.json` + `ui/public/sw.js` (network-first for
    navigations, cache-first for `/assets/*`, never `/api`, `/ws`, `/auth`);
    server adds `/sw.js` to `_AUTH_OPEN_EXACT`; SW registered only in
    production builds and only under a secure context.
S5. `GET /api/sessions/{id}/files` and `GET /api/sessions/current/files`:
    per-filter `{filter, count, exposure_s, bytes, frames: [{id, path, bytes,
    accepted, override, hfr}]}` plus totals (folded from the session ledger
    and gallery index). If the Session plan finds an existing route that
    serves this, S5 is dropped.
S6. Versions: `/healthz` already has the engine version; the UI version is
    injected at build time (`define: { __APP_VERSION__ }` from `ui/package.json`).

Each S-task: pytest tests beside the module, `-n0`, RBAC boot assertion must
stay green, `test_rbac_enforcement.py::test_ws_valid_principal_survives_recheck`
is a known pre-existing failure and is not ours.

## 13. Testing

- Unit/DOM tests follow `shell-and-tests.md` section 4 exactly (jsdom manual
  globals, `createRoot` + `act`, native events, `settle()`, printed tally +
  `export { passed, failed, total }`). Put tests INSIDE the counted region.
- Every hub task: at least one DOM test per screen/sheet that (a) asserts a
  precondition marker rendered, (b) exercises one real interaction and asserts
  the API path requested or the store action fired, (c) asserts the viewer
  read-only rendering (lock reason text present, no request fired).
- Sabotage check: for each new test, name in the report which assertion goes
  red when the guarded behaviour is removed.
- `npx tsc -b --pretty false` clean and `npm test` all green before reporting.
- End-to-end: a Playwright probe (Python, `tools/ui_probe/next_probe.py`)
  against an isolated server (`ASTRODECK_CONFIG_DIR=<scratch>/e2e-cfg`,
  `python -m astrodeck --port 8801`, simulator rig connected via the API) at
  390, 820 and 1440 px; it asserts the header shows rig chips (vacuity guard),
  walks every hub, opens every sheet, and screenshots to `<scratch>/e2e/`.
  Run by the controller after each hub.

## 14. Work plan

Wave 0 (foundation)
- T0.2 primitives + next.css + icons (opus) - `ui/src/next/ui/*`, `next.css`, `icons.tsx`, tests.
- T0.3 pure libs (sonnet) - `ui/src/next/lib/*` + tests: gate, incidents (types
  + derivation skeleton with the kinds fixed by the README), allocation, reach,
  advection, cloudTiles, horizonModel, format, versions.
- T0.1 shell (opus, after T0.2) - NextApp, router, breakpoint, legacyBridge,
  shell/*, main.tsx switch, PWA files; hubs are placeholders that render their
  sub-nav and an EmptyCard; tests for router/bridge/breakpoint/shell.
- Wave S (opus, Python) in parallel with T0.1.

Hubs, in the user's priority order, each: planner (opus) writes
`<scratch>/plan/hub-<name>.md` grounded in the inventories and proto
fragments -> controller review -> implementers (opus for screens with
math/gesture logic, sonnet for list/settings-style sheets) partitioned by
directory -> reviewer (opus; re-runs tests, checks the inventory for lost
features, checks viewer rendering) -> controller commits -> e2e probe.

1. Roles: in Wave 0 (gate.ts, viewer rendering in primitives, Login in chrome).
2. SKY hub (search, survey tiles under FRAME, lens hides satellites/comets).
3. SESSION hub (Now, incidents, live stack + Inspect, Files with grades and
   FITS gating and stacking bundle, Gallery, Flows, campaign ledger + quotas
   read-only, plan editor sheet) and RIG · Capture (still/loop/video-as-SER
   where the backend supports it, Inspect).
4. RIG · Devices (driver declaration, role assignment, providers, all device
   sheets incl. rotator, safety with sun avoidance / pier floor / meridian /
   escalation read-out).
5. WEATHER hub (settings and states, dome pierce point, radar).
6. MONITOR hub (live, log, alerts with sinks + test, interrupted-run recovery).
7. SETTINGS hub (everything left, first-time setup, connection with relay
   status, optics, sites, horizon, night, downloads, users, about).
8. Cross-hub chrome, campaign strip, session dot, PWA, night check, e2e at
   three widths, final whole-branch review.

## 15. Copy

Use the README's strings verbatim where given. Hold-to-learn explanations
("hold any control for a plain-language explanation") are one sentence each
and state something the screen does not. Blocked reasons name the blocker.
