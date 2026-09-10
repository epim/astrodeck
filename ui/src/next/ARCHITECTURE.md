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

This is the tree as shipped (verified against the working tree, not the plan):

```
ui/src/next/
  NextApp.tsx            root of the new UI (auth gate, shell, hubs, hosts)
  ARCHITECTURE.md        this document
  router.ts              hash router: parse/build, nav API, useRoute
  breakpoint.ts          useBreakpoint(): "phone" | "tablet" | "desktop"
  legacyBridge.ts        store.view / helpTopic -> route mapping
  next.css               the new component classes (nx-* prefix), imported ONLY by NextApp
  icons.tsx              hub icons + device glyphs + misc (design SVG paths)
  env.d.ts               ambient type for __APP_VERSION__ and other build-time globals
  ui/                    primitives (section 6): ActionButton, BannerCard, Bar, Card,
                         Checkbox22, Chip, DeviceGlyphTile, Dial, Divider, EmptyCard,
                         Field, IconButton48, IncidentCard, Label, ListRow, Mono, Pill,
                         Popover, Readout(Grid/Tile), RingGauge, Segmented, Sheet,
                         StatusPill, Stepper2, SubNav, Switch, TextInput, Wordmark,
                         honest.ts (the shared lock-press guard), index.ts, types.ts
  shell/                 Header, Rail, TabBar, SubNav, Banners, CampaignStrip, SheetHost,
                         SessionColumn, Toasts, ConfirmCard, HubBoundary (error boundary
                         per hub), subContext.ts (per-hub chip/badge counts), explain.ts
                         (explainLock), useIncidents.ts, shell.css, boundary.css
  lib/                   pure modules: gate.ts, gateHook.ts, incidents.ts, allocation.ts,
                         reach.ts, advection.ts, cloudTiles.ts, horizonModel.ts, fov.ts,
                         format.ts, versions.ts, index.ts
  hubs/
    index.ts             HUB_ORDER, HUB_META, the composed SubContext hook
    sheets.ts            the SheetProps/SheetComponent contract every hub's sheets export follows
    sky/       SkyHub.tsx + finder/* (SkyView, model, camera, gyro, projection,
               equatorial, gestures, targets, clouds, wind, track, prefs), frame/*
               (FrameHost, FrameTools, FramingCard, FramedOverlay, MosaicNightCard,
               SurveyPopover, mosaic, zoom, degraded), cards/* (LockCard, LensDial,
               LayersPopover, ReachStrip, StatusRow, BrowseBanner, PatchCard, glyphs,
               lockCta), sheets/* (targets, sites, horizon, coords, quick, flow, brief,
               photosphere + their model/copy/lane helper modules)
    weather/   WeatherHub.tsx + conditions/* (ConditionsScreen, ConditionsBand,
               CloudChart, moon, verdict), dome/* (DomeScreen, domeOverlay), radar/*
               (RadarScreen), sheets/* (CloudmapSheet, WeatherSettingsSheet)
    session/   SessionHub.tsx + now/* (NowScreen, IncidentStack, LiveStack,
               ChannelStrip, VitalsBand, IntegrationBar, ArmedRules, CampaignLedger,
               Interrupted, PoolChips, QuotaRows, RunControls, RunHeader, NowBanners,
               NowEmpty + model/helper modules), gallery/*, flows/*, sheets/* (files,
               archive, planEditor, report)
    rig/       RigHub.tsx + devices/* (DevicesScreen, DeviceRow, ConnectOnceCard,
               ProfileRow, ProfilesPopover, QuickActions, rigConnect, roster),
               capture/* (CaptureScreen, CaptureStage, CaptureControls,
               CaptureReadouts, CoolerRow, ResultCard, captureGate, useArm), sheets/*
               (camera, mount, polar, focuser, wheel, guider, rotator, safety, power,
               addDevice, driver, profiles, inspect, inspectStack + the reg-*.ts
               sheet-registration fragments), lib/* (coolerCurve, guiderModel,
               safetyBars, safetyChain, wheelRing, pendingValue)
    monitor/   MonitorHub.tsx + live/* (LiveScreen, VitalsBand, FlipTile,
               RecoveryCards, StallStrip, LockControls, RunProgress), log/* (LogScreen,
               LogExport, logSources), alerts/* (AlertsScreen, NotifyRow)
    settings/  SettingsHub.tsx + general/* (GeneralScreen, Group, MoreGroup,
               PhoneGroup, SetupCard, setupSteps, useSetup), sheets/* (Connection,
               Optics, Users, About, Help, Update, Sync, Standards, Calibration
               (library + tolerances), Naming, WcsStamp, Restricted, FactoryReset,
               Credits, AuthMethods, Account, QuickDefaults, LogExport, Setup +
               connectionModel/opticsModel + set2/set3/set4 wiring modules)
  __tests__/             shell, router, bridge, breakpoint tests
  hubs/<hub>/__tests__/  per-hub DOM tests (also nested under finder/, frame/,
                         sheets/, capture/, flows/, gallery/ inside sky/session/rig)
  lib/__tests__/         pure tests
  ui/__tests__/          primitive tests
```

`sites` (the Sites sheet) physically lives under `hubs/sky/sheets/` and Settings reuses the
same registered component (section 5's "sites is shared ... registered once").

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
  with the line "FITS originals need syncer or admin access" - the roles that
  hold `view.media` per `ui/src/lib/caps.ts`'s `ROLE_CAPS` table; a syncer
  genuinely holds it, so naming only admin would be the exact defect class
  `accessPhrase()` exists to prevent. Amended from the original "admin access"
  wording during review).
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

Status: landed. All five routes exist and are consumed by the UI named below.

S1. Per-site horizon polyline: `Location.horizon_points: list[[az, alt]] | null`
    on saved locations (`server/astrodeck/locations.py`), returned by
    `GET /api/locations`, accepted (partial-update: an omitted field leaves it
    unchanged) by `PUT /api/locations/{loc_id}`; applying a location as the
    site (`POST /api/locations/{loc_id}/apply`) copies its points into
    `config.safety.horizon` so the engine's obstruction rule uses the drawn
    line. `GET /api/site` echoes `horizon_points`. Consumed by the Sky hub's
    Sites and Horizon sheets (`hubs/sky/sheets/sites.tsx`, `horizon.tsx`) -
    the horizon is per-site, not the single global polyline this section
    originally described as an interim state.
S2. Weather feed fields: `GET /api/weather` gains `now: {temp_c, dewpoint_c,
    humidity_pct, wind_kmh, wind_dir_deg, gust_kmh, cloud_base_m}` and hourly
    `wind_kmh[]`, `wind_dir_deg[]`, `humidity_pct[]`, `dewpoint_c[]` from
    Open-Meteo. Consumed by the Weather hub's Conditions screen and the Sky
    finder's wind arrows (`hubs/sky/finder/wind.ts`).
S3. `GET /api/remote/status` (view.status): `{enabled, home_id, relay_host,
    connected, last_error, since_unix, via: "direct"|"relay"}` where `via` is
    how THIS request arrived. Consumed by Settings > Connection
    (`hubs/settings/sheets/ConnectionSheet.tsx`, `connectionModel.ts`).
S4. PWA: `ui/public/manifest.json` + `ui/public/sw.js` ship; `/sw.js` is in
    `_AUTH_OPEN_EXACT` alongside `/icon-192.png` / `/icon-512.png`
    (`server/astrodeck/api/app.py`).
S5. `GET /api/sessions/{id}/files` and `GET /api/sessions/current/files`
    (`server/astrodeck/sequence/session_files.py`): per-filter `{filter,
    count, accepted, exposure_s, bytes, integration_s, frames: [{id, ts,
    bytes, accepted, override, hfr, stars, guide_rms, thumb}]}` plus totals.
    **Amended from the original spec**: the frame row deliberately omits
    `path`. `SessionFrame.path` is redacted below `config.backend`
    (admin-only), so shipping it would blank on every row for the Files
    sheet's real audience (operator/admin) and the grade join key does not
    need it. Do not "fix" this back in.
S6. Versions: `/healthz` already has the engine version; the UI version is
    injected at build time (`define: { __APP_VERSION__ }` from
    `ui/package.json`), read by `SettingsHub.tsx` and the About sheet. Done as
    a mechanism; `ui/package.json`'s `version` field itself is still `0.1.0`
    and needs bumping as part of a release, not this contract (see
    DEVIATIONS.md's Follow-ups).

Each S-task shipped with pytest tests beside the module (`test_locations_horizon.py`,
`test_remote_status.py`, `test_open_paths.py`, `test_session_files.py`,
plus weather/RBAC coverage in the existing suites), run `-n0`.
`test_rbac_enforcement.py::test_ws_valid_principal_survives_recheck` is a
known pre-existing failure and is not ours.

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

## 14. What landed

The work plan (Wave 0 -> Wave S -> eight hub/chrome waves, each planned,
implemented, reviewed and probed in order) ran as written in the superseded
version of this section. What shipped, on `feat/ui-next`, 39 commits ahead of
`main` (`fec53970` pure library modules through `a34338cf` the relay-fence and
redaction fixes; `git log --oneline main..HEAD` for the full list):

- Wave 0: primitives + `next.css` + icons; pure libs (`gate`, `incidents`,
  `allocation`, `reach`, `advection`, `cloudTiles`, `horizonModel`, `format`,
  `versions`); the shell (`NextApp`, `router`, `breakpoint`, `legacyBridge`,
  `shell/*`, the `main.tsx` root switch, the PWA files).
- Wave S: all five server additions (section 12) landed and are consumed.
- All six hubs shipped with their device/screen sheets, in the plan's
  priority order (Sky; Session + Rig - Capture; Rig - Devices; Weather;
  Monitor; Settings), each hub's own plan document
  (`<scratch>/plan/hub-<name>.md`) recording its screen-by-screen spec and its
  own Deviations section (see `DEVIATIONS.md`, one table per hub).
- Cross-hub chrome, campaign strip, session tab dot, error boundaries per
  hub, PWA registration, and an end-to-end Playwright probe
  (`tools/ui_probe/`) walking 60 real routes at 390/820/1440 px, both
  unauthenticated and per-role.
- A whole-branch review (`<scratch>/plan/REVIEW-FINDINGS.md`) found one P0 and
  eleven P1 findings; a follow-up wave of commits fixed the P0, all four
  dead-control-with-a-promise P1s (the dropped mosaic, the two orphaned
  preference keys, the dead toast button), both server data-loss/leak P1s
  (the horizon write paths, the meridian-flip longitude leak), the missing
  error boundary, detect-my-hardware, polar's running message, and the
  missing focus-scope shutter. `DEVIATIONS.md`'s "Legacy defects found and
  fixed on this branch" list has the commit hashes.
- Deferred, by name, to a later wave: several P2/P3 findings and the items in
  `DEVIATIONS.md`'s Follow-ups list (a UI version bump, code-splitting the
  hub bundle, restyling the reused legacy panels mounted in the new chrome,
  the dome overlay's missing yaw hook, and others named there).

`npm test`, `npx tsc -b --pretty false` and the server pytest suites were
green at hand-off (see the review document's "Command output" section for the
exact runs); re-verify before shipping past this branch, since more commits
may have landed since this document was last amended.

## 15. Copy

Use the README's strings verbatim where given. Hold-to-learn explanations
("hold any control for a plain-language explanation") are one sentence each
and state something the screen does not. Blocked reasons name the blocker.

## 16. How to run it

**Dev server (new UI, against a real or simulator rig).** The AstroDeck
server must already be running on port 8800 (`server/.venv/Scripts/python -m
astrodeck run --port 8800`, then connect a rig or the simulator over the API
or the legacy UI). Then, in a second terminal:

```
cd ui
npm run dev
```

Vite serves the new UI at `http://localhost:5173` and proxies `/api` to
`http://127.0.0.1:8800` and `/ws` (websocket) to `ws://127.0.0.1:8800`
(`ui/vite.config.ts`). The new root mounts by default; append `#/classic` to
the URL (or use the footer link in Settings) for the legacy UI - see below.

**Isolated probe server, for a clean rig with no manual setup.** `tools/ui_probe/`
owns a fully isolated server (own config dir, own captures dir, own port,
simulator rig auto-connected) built for the end-to-end walk but equally
useful for manual poking:

```
python tools\ui_probe\server_ctl.py start --fresh
#   -> prints {"base": "http://127.0.0.1:8801", "pid": ..., ...}
```

Point a browser (or a dev-mode `npm run dev` proxy target) at that base URL;
`stop` it with `python tools\ui_probe\server_ctl.py stop` when done. Add
`--auth` to start it with local auth bootstrapped and three probe accounts
(`probe_admin` / `probe_operator` / `probe_viewer`) - see
`tools/ui_probe/README.md`'s "Auth / first-run mechanism" section for exactly
how those are seeded and why.

**The probe itself**, one command (builds `ui/dist`, starts the isolated
server, walks every hub/sheet at 390/820/1440 px, stops the server):

```
powershell -NoProfile -ExecutionPolicy Bypass -File tools\ui_probe\run.ps1
```

Flags worth knowing: `-SkipBuild` (probe whatever is already in `ui/dist`),
`-Auth -Role viewer|operator|admin` (walk the routes signed in as that role),
`-Routes routes_next.json` (the new UI's 60 real hash routes; the default is
`routes_classic.json`, the legacy UI). Screenshots and `report.jsonl` land in
`<repo>/.probe/out/`. Full details, including the vacuity/false-pass guards
and the quirks this harness has already caught, are in
`tools/ui_probe/README.md` - read it before extending the route lists.

**Legacy UI.** `#/classic` (and `#/classic/<view>` for any of the 16
`ViewName`s in `ui/src/types.ts`) mounts the old root unchanged, in the same
build, at the same dev server or probe server - no separate build step.
