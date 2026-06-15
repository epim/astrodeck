# AstroDeck — Reliability & Error Surfacing — Build-Ready Spec

**Date:** 2026-06-15
**Status:** Final (revised against 3 adversarial UX critiques)
**Addresses:** Panel review §"Reliability/error UX" (P0–P1) and the §P0 blocker
"Error surfacing is fragile."

---

## 0. Overview

Seven deliverables, re-scoped after adversarial review so they are **clearer for a
cold beginner at 1 a.m. on a phone**, not merely louder:

1. **Toast queue** with severity-and-consequence TTL (not "errors are sticky"),
   coalescing that does **not** reset the age, and a single focal toast for
   sequence-fatal events.
2. **LOG badge** counting **unseen errors only** (warnings live in the log, not the
   badge), reset on open, backfilled from `/api/logs` after a reconnect so a
   backpressure gap can't desync it.
3. **Log drawer on all viewports**: docked column on `lg+`, a **bottom sheet that
   coexists with the run panel** on phone/tablet (no full-screen scrim that hides
   progress), with full dialog a11y (Escape, focus-trap, return-focus).
4. **Render the sequence error/aborted state** — banner decoupled from `progress`
   so an early (pre-first-frame) failure still shows; recovery offers
   **Re-run / Edit** always and **Resume from frame N only when actually
   resumable**.
5. **Two-hop connection health**: browser↔backend (LINK) and backend↔NINA (NINA),
   each an LED with a **shape/motion + letter cue** so it survives the all-red
   night palette. "Display disconnected" copy that makes clear **the rig keeps
   running**. Telemetry-stale is suppressed while the backend is legitimately busy
   (slew/solve/AF/capture).
6. **Robust fetch**: per-endpoint timeouts (long connect calls get a long budget),
   `AbortSignal.timeout` with an `AbortController` fallback for old Safari/WebView,
   typed `ApiError`. **Stop-type controls are never disabled.**
7. **Server-side Alpaca proxy** for the manual host/port scan (fixes browser CORS),
   with **differentiated** error messages (unreachable vs. reached-but-not-Alpaca).
8. **Push/audible alert** (opt-in Web Notification + short beep) on sequence
   `error`/`complete` and on a *sustained* backend-link loss — so an absent
   operator is actually alerted (panel Tier-3 "push/ntfy alerting").

### Core design principle (the thing the critiques converged on)

**One event → one focal alarm.** A sequence failure must not light up five
independent red things. The **sequence panel is the focal surface** for sequence
failures; the toast, badge, and LED are quiet status that point *to* it. Toasts
carry a **human sentence** ("Camera isn't responding — check the Rig page"), not a
raw Python `str(e)`; the raw text stays in the log for experts.

All token references resolve to existing `index.css` vars (`--bad`, `--warn`,
`--good`, `--accent`, `.panel`, `.btn`, `--img-filter`). §10 lists the **CSS
additions, which are a hard prerequisite landed before any component** (a missing
`led-bad` renders the most safety-critical indicator as an invisible transparent
dot).

---

## 1. Disjoint file ownership (parallel implementers)

Work splits into **5 non-overlapping lanes**. Each lane owns its files outright;
the only shared files (`types.ts`, `store.ts`, `index.css`, `App.tsx`) are
**append-only at agreed anchors** per §3 (Shared contracts). Land lane A first
(it adds the tokens + types + store slice everyone depends on), then B–E in
parallel.

| Lane | Owns (create)                                   | Owns (modify)                          | Depends on |
|------|-------------------------------------------------|----------------------------------------|------------|
| **A — foundation** | `ui/src/lib/health.ts`, `ui/src/lib/humanize.ts`, `ui/src/lib/notify.ts` | `ui/src/types.ts`, `ui/src/store.ts`, `ui/src/index.css` | — |
| **B — transport** | —                                               | `ui/src/ws.ts`, `ui/src/api.ts`         | A (types) |
| **C — chrome** | `ui/src/components/Toasts.tsx`, `ui/src/components/LogDrawer.tsx`, `ui/src/components/ConnectionBanner.tsx`, `ui/src/components/HealthLeds.tsx`, `ui/src/components/Icon.tsx` | `ui/src/App.tsx` | A (store, css), B (wsPhase) |
| **D — views** | —                                               | `ui/src/views/SequenceView.tsx`, `ui/src/views/ConnectView.tsx` | A (store) |
| **E — backend** | —                                               | `server/astrodeck/devices/nina.py`, `server/astrodeck/devices/alpaca.py`, `server/astrodeck/hub.py`, `server/astrodeck/api/app.py` | — |

Lanes A–D and E are fully independent (frontend vs. backend). Within the
frontend, C and D both read the store slice A defines but never edit the same
file. `App.tsx` is owned solely by C; D never touches it.

---

## 2. Data flow (end to end)

```
┌────────────────────────────── backend (lane E) ──────────────────────────────┐
│ NinaClient.get/get_bytes  → stamp last_ok / last_error (monotonic + wall)     │
│ Hub._nina_heartbeat()  (NEW, fixed 5s GET /version)  → independent last_ok    │
│ Hub.busy_label  (NEW: "slewing"|"solving"|"focusing"|"capturing"|None)        │
│ hub.poll_status() → out["nina_link"] = {active,last_ok_age_s,last_error,       │
│                                          healthy,warming_up}                   │
│                     out["busy"] = busy_label                                   │
│ alpaca.query_server(host,port) (NEW) → server-side fetch, typed failures      │
│ bus.publish("status"/"sequence"/"log", …)                                     │
└───────────────────────────────────────────────────────────────────────────────┘
                                   │ ws frame {type, data, ts}
                                   ▼
ws.ts onmessage → noteWsEvent() (stamp wsLastEvent) → store.handleEvent(ev)
  ├ "status"   → status (+nina_link,+busy) → deriveNinaHealth() → ninaHealth
  ├ "sequence" → sequence; on state==="error" → ONE focal toast + notify()+beep
  │                         on state==="complete" → notify()+beep (no toast)
  │                         on state==="aborted"  → nothing (user did it)
  └ "log"      → push log; if level==="error" → bump unseenError + (humanized) toast
                 (warnings go to log only; never a toast, never the badge)
ws.ts onopen  → setWsPhase("up"); GET /api/logs → reconcile drawer + badge
ws.ts onclose → setWsPhase("down"); after >LINK_DOWN_ALERT_MS → notify()
1s staleness ticker (client-only): wsPhase==="up" && now-wsLastEvent>STALE_MS
                 && status.busy===null  → telemetryStale=true
                                   │ Zustand split selectors
                                   ▼
App  ConnectionBanner  Toasts  LogDrawer(+badge)  HealthLeds(LINK/NINA)  SequenceView
```

Single source of truth stays bus → WS → Zustand. No new browser polling loop; the
existing 2 s `_status_loop` carries health, and a backend 5 s heartbeat keeps NINA
`last_ok` honest independent of incidental traffic.

---

## 3. Shared contracts

These are the only edits to shared files; everything else is lane-local. Implement
exactly as written so lanes don't collide.

### 3.1 `ui/src/types.ts` (append, lane A)

```ts
export type ToastLevel = "error" | "warning" | "info" | "success";

export interface Toast {
  id: number;          // monotonic, also React key
  level: ToastLevel;
  title: string;       // human, short, sentence-case (e.g. "Sequence failed")
  detail?: string;     // optional human second line (e.g. a suggested action)
  kind: "generic" | "sequence";  // "sequence" toasts are the de-duped focal one
  createdAt: number;   // Date.now() — NEVER mutated on coalesce
  ttl: number;         // ms before auto-dismiss; 0 = sticky (sequence-fatal only)
  count: number;       // coalesce counter for identical generic toasts
  action?: { label: string; kind: "openLog" };  // optional inline action
}

export type WsPhase = "connecting" | "up" | "down" | "reconnecting";

export type NinaState = "ok" | "stale" | "error" | "down" | "warming" | "na";

export interface NinaHealth {
  active: boolean;          // mode === "nina" && nina_link.active
  ageMs: number | null;     // ms since backend's last successful NINA call
  state: NinaState;
  lastError?: string | null;
}
```

Extend `RigStatus` (mirrors the backend block in §8.2):

```ts
export interface RigStatus {
  // …existing…
  busy?: "slewing" | "solving" | "focusing" | "capturing" | null;
  nina_link?: {
    active: boolean;
    last_ok_age_s: number | null;   // seconds since last successful NINA HTTP call
    last_error: string | null;
    healthy: boolean;               // backend verdict (warming_up or age<=threshold)
    warming_up: boolean;            // true until first successful poll after bridge
  };
}
```

`SequenceState.state` already includes `"error"` and `"aborted"` (types.ts:65) —
no change. The engine also publishes `target_index` and `progress.rejected`
(engine.py:110,181); add them so the error panel can show rejects without `any`:

```ts
export interface SequenceState {
  state: "idle" | "running" | "paused" | "complete" | "aborted" | "error";
  detail?: string;
  target?: string;
  target_index?: number;
  plan_name?: string;
  progress?: { frames_done: number; frames_total: number; percent: number;
               elapsed_s: number; rejected?: number };
}
```

### 3.2 `ui/src/store.ts` slice (lane A)

New state fields (added to `AppState`, replacing the single `toast`):

```ts
wsPhase: WsPhase;            // "connecting" initially
wsLastEvent: number;        // Date.now() of last WS frame
telemetryStale: boolean;
toasts: Toast[];            // QUEUE (replaces toast | null)
logOpen: boolean;           // moved from App local state (toasts can open it)
unseenError: number;        // unseen ERROR logs since drawer last opened
ninaHealth: NinaHealth;     // derived from status.nina_link each status frame
notifyEnabled: boolean;     // user opted into Web Notifications/beep
```

New actions:

```ts
setWsPhase(p: WsPhase): void;          // also clears telemetryStale when !== "up"
noteWsEvent(): void;                    // wsLastEvent = Date.now()
setTelemetryStale(v: boolean): void;
enqueueToast(t: EnqueueInput): void;    // see §5.2
dismissToast(id: number): void;
openLog(): void;                        // logOpen=true; unseenError=0
closeLog(): void;
reconcileLogs(history: LogLine[]): void;// merge /api/logs after reconnect
setNotifyEnabled(v: boolean): void;
```

`EnqueueInput = { level: ToastLevel; title: string; detail?: string;
kind?: Toast["kind"]; ttl?: number; action?: Toast["action"] }`.

**Backward-compat shims** (keep existing call sites compiling — the old store
exposed `wsConnected`, `setWsConnected`, `showToast`):

```ts
// derived: maintained in every setWsPhase() call (Zustand has no computed fields)
wsConnected: boolean;          // = wsPhase === "up"; set alongside wsPhase
setWsConnected(ok: boolean): void;   // → setWsPhase(ok ? "up" : "down")
showToast(level: string, message: string): void;
  // → enqueueToast({ level: level as ToastLevel, title: humanizeLog(message) })
```

`showToast` is retained so the ~8 view call sites (`act`/`run` wrappers in
ConnectView, SequenceView, etc.) compile unchanged; they get the humanized,
queued behavior for free. Migrate opportunistically — not required for this spec.

### 3.3 `ui/src/index.css` additions (lane A — land FIRST)

See §10.4 for the exact CSS. New classes/keyframes: `.led-bad`, `.led-letter`,
`.blink-alert`, `@keyframes blink-alert`, `@keyframes slide-up`, global
`:focus-visible`, and a **class-targeted** `prefers-reduced-motion` block.

### 3.4 REST endpoints (lane E)

| Method | Path | Query/Body | Returns | Notes |
|--------|------|-----------|---------|-------|
| `GET` | `/api/discover/alpaca` | `host` (str), `port` (int=11111) | `AlpacaServer` | NEW. Server-side proxy for manual scan. Differentiated errors (§8.3). |
| `GET` | `/api/nina/health` | — | `nina_link` block | NEW, optional. Same shape as in `status`; for tests + a future Rig-page readout. UI consumes via `status`. |
| `GET` | `/api/logs` | — | `LogLine[]` | **Existing** (app.py:517). Now consumed by `ws.ts` on reconnect for badge/drawer reconciliation. |

No existing endpoint signature changes. `nina_link` and `busy` are additive keys
on the existing `status` event (old clients ignore them).

### 3.5 Backend fields (lane E)

- `NinaClient.last_ok: float|None`, `last_ok_wall: float|None`,
  `last_error: str|None` (stamped in `get`/`get_bytes`).
- `Hub.busy_label: str|None` property derived from `hub._busy` task names
  (`goto`→"slewing", `solve`→"solving", `autofocus`/`focuser`→"focusing",
  `capture`/`looping`→"capturing"); also set during sequence centering.
- `Hub._nina_heartbeat()` task: every 5 s, if bridged, `await
  nina_client.get("/version")` (refreshes `last_ok`); never fatal.
- `Hub._bridge_ready: bool` — false from bridge connect until first successful
  heartbeat/poll (drives `warming_up`).

---

## 4. `ws.ts` (lane B)

Adds phase reporting, `noteWsEvent` stamping, a single staleness ticker (busy-aware),
log reconciliation on reconnect, and a sustained-down notification.

```ts
import { useStore } from "./store";
import { api } from "./api";
import type { LogLine } from "./types";

let socket: WebSocket | null = null;
let retryMs = 1000;
let everConnected = false;
let staleTicker: number | null = null;
let downSince = 0;

const STALE_MS = 20000;          // socket up but no frame for 20s AND not busy
const LINK_DOWN_ALERT_MS = 30000;// notify only after a sustained 30s outage

export function connectWs(): void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const s = useStore.getState();
  s.setWsPhase(everConnected ? "reconnecting" : "connecting");
  socket = new WebSocket(`${proto}://${location.host}/ws`);

  socket.onopen = async () => {
    retryMs = 1000; everConnected = true; downSince = 0;
    const st = useStore.getState();
    st.setWsPhase("up"); st.noteWsEvent();
    if (!staleTicker) staleTicker = window.setInterval(tickStale, 1000);
    // Reconcile after any gap: the bus drops frames under backpressure, so the
    // drawer/badge could be missing log lines that arrived while we were away.
    try { st.reconcileLogs(await api.get<LogLine[]>("/api/logs")); } catch { /* ignore */ }
  };

  socket.onmessage = (msg) => {
    try {
      const st = useStore.getState();
      st.noteWsEvent();                 // stamp BEFORE handling so a throw still counts liveness
      st.handleEvent(JSON.parse(msg.data));
    } catch { /* malformed frame */ }
  };

  socket.onclose = () => {
    const st = useStore.getState();
    st.setWsPhase("down");
    if (!downSince) downSince = Date.now();
    if (staleTicker) { clearInterval(staleTicker); staleTicker = null; }
    setTimeout(connectWs, retryMs);
    retryMs = Math.min(retryMs * 1.7, 15000);
  };

  socket.onerror = () => socket?.close();
}

function tickStale(): void {
  const s = useStore.getState();
  if (s.wsPhase !== "up") return;
  // Stale ONLY if socket is up, frames stopped, AND the backend is not in a known
  // long op (slew/solve/AF/capture legitimately block the 2s poll). This is the
  // fix for "TELEMETRY STALE cries wolf on every GOTO".
  const busy = s.status?.busy ?? null;
  const stale = busy === null && Date.now() - s.wsLastEvent > STALE_MS;
  if (s.telemetryStale !== stale) s.setTelemetryStale(stale);
  // sustained outage alert handled in onclose path via downSince + a 1s check:
  if (downSince && Date.now() - downSince > LINK_DOWN_ALERT_MS) { /* see store.notify */ }
}
```

Note: the sustained-down notification fires from a tiny check in the store's
`setWsPhase("down")` path scheduled with `setTimeout(LINK_DOWN_ALERT_MS)` and
cancelled on reconnect — kept out of the ticker so it survives the ticker being
torn down on close. `telemetryStale` is a **first-class** store field (never the
optional `s.setTelemetryStale?.()` of the draft).

---

## 5. Toasts (lane C component, lane A store reducer)

### 5.1 Policy (TTL by consequence, not log level)

```ts
// ui/src/store.ts
const TOAST_MAX = 3;                          // hard cap; on phone effectively 1–2
const TTL: Record<ToastLevel, number> = {
  success: 3000, info: 4000, warning: 6000, error: 10000,
};
```

- **No "errors are sticky" rule.** A transient error (one failed loop frame, a
  dither timeout) is in the log and auto-dismisses at 10 s. Only the **one
  sequence-fatal toast** (`kind:"sequence"`, enqueued from the `sequence` handler
  with `ttl:0`) is sticky, because the run is *over* and the user must act.
- **Warnings never toast.** `_run_step` logs warnings every cloud frame / dither
  miss / cooler-slow (engine.py); surfacing those as toasts is pure noise. They
  go to the log only and do **not** touch the badge. (Resolves Critique-1 B1/B2,
  Critique-2 #13.)
- **Coalesce without resetting age.** Identical `title`+`level` generic toasts
  within 5 s bump `count` and **leave `createdAt` unchanged**, so a flapping error
  still ages out at its original 10 s instead of becoming immortal. (Resolves
  Critique-1 C3, Critique-2 #1.)
- **Single focal sequence toast.** Enqueuing a `kind:"sequence"` toast first
  removes any existing `kind:"sequence"` toast — there is never more than one, and
  it is the *only* place a `state:"error"` produces a toast (the `log` handler
  ignores `source:"sequence"` errors to avoid the double-surface). (Resolves
  Critique-1 B7, Critique-2 #3, the "five alarms" problem.)

### 5.2 `enqueueToast` reducer (lane A)

```ts
let toastId = 0;
enqueueToast: (input) => set((s) => {
  const now = Date.now();
  const level = input.level;
  const kind = input.kind ?? "generic";
  let toasts = s.toasts;

  if (kind === "sequence") {
    // exactly one focal sequence toast — replace any prior
    toasts = toasts.filter((t) => t.kind !== "sequence");
  } else {
    const dupe = toasts.find(
      (t) => t.kind === "generic" && t.title === input.title &&
             t.level === level && now - t.createdAt < 5000);
    if (dupe) {
      return { toasts: toasts.map((t) =>
        t.id === dupe.id ? { ...t, count: t.count + 1 } : t) }; // createdAt UNCHANGED
    }
  }

  const toast: Toast = {
    id: ++toastId, level, title: input.title, detail: input.detail, kind,
    createdAt: now, ttl: input.ttl ?? TTL[level], count: 1, action: input.action,
  };
  let next = [...toasts, toast];
  if (next.length > TOAST_MAX) {
    // drop oldest dismissible generic first; never drop the focal sequence toast
    const victim = next.find((t) => t.ttl > 0 && t.kind === "generic")?.id ?? next[0].id;
    next = next.filter((t) => t.id !== victim);
  }
  return { toasts: next };
}),
```

### 5.3 `handleEvent` changes (lane A)

```ts
case "status": {
  const status = ev.data as unknown as RigStatus;
  set({ status, ninaHealth: deriveNinaHealth(status) });   // lib/health.ts
  break;
}
case "sequence": {
  const seq = ev.data as unknown as SequenceState;
  const prev = get().sequence.state;
  set({ sequence: seq });
  if (seq.state === "error" && prev !== "error") {
    get().enqueueToast({
      level: "error", kind: "sequence", ttl: 0,
      title: "Sequence failed",
      detail: humanizeSeqError(seq.detail),          // lib/humanize.ts
      action: { label: "View log", kind: "openLog" },
    });
    notifyAndBeep(get(), "Sequence failed", humanizeSeqError(seq.detail));
  } else if (seq.state === "complete" && prev !== "complete") {
    notifyAndBeep(get(), "Sequence complete",
      `${seq.progress?.frames_done ?? 0} frames captured`);
    // no toast — the green panel is enough; a success toast auto-dismisses if desired
  }
  break;
}
case "log": {
  const line = ev as unknown as LogLine;
  const level = (ev.data.level as string) ?? "info";
  const source = (ev.data.source as string) ?? "";
  set((s) => ({
    logs: [...s.logs.slice(-199), line],
    unseenError: s.unseenError + (!s.logOpen && level === "error" ? 1 : 0),
  }));
  // Errors → toast, EXCEPT sequence-fatal (handled by the sequence frame above,
  // so we don't double-surface). Warnings never toast.
  if (level === "error" && source !== "sequence") {
    get().enqueueToast({ level: "error", title: humanizeLog(ev.data) });
  }
  break;
}
```

### 5.4 Renderer (`ui/src/components/Toasts.tsx`, lane C)

- Position: `fixed z-40`. **Top-center on phone** (`top-14 left-3 right-3`) so a
  sticky toast never sits over the bottom-nav / Abort row; `sm:bottom-6 sm:right-6
  sm:left-auto sm:w-[360px]` on desktop. (Resolves Critique-1 D1, Critique-2 #7,
  Critique-3 #11.)
- Cap visible at `TOAST_MAX=3`; on phone realistically 1–2.
- One shared **`useEffect`** TTL sweeper at 500 ms over
  `useStore.getState().toasts`; it batches all expired ids into a single
  `set`-per-sweep helper `dismissExpired()` so toasts don't dribble away
  one-per-tick. The effect deps are `[]` (it reads fresh state each tick), so it
  is **not** torn down/recreated mid-sweep. (Resolves Critique-1 C4.)
- Layout: severity `<Icon>` (16px, from `components/Icon.tsx`) + text column
  (`title` in `--text`, `detail` in `--text` at 12px — **never** `text-dim`
  micro-copy) + a **single action row beneath** with real `gap-2` spacing (NOT
  side-by-side negative-margin 44px targets). Each control is a `min-h-[44px]`
  button with ≥8 px between them; the toast grows to contain them. (Resolves
  Critique-3 #9, D3.)
- a11y: a **single shared** `aria-live` region wraps the stack —
  `aria-live="assertive"` only when the newest toast is an error, else `polite`.
  Coalesce `count` bumps do **not** re-announce (the count chip has
  `aria-hidden`). (Resolves Critique-3 #6.)
- Night-vision: error toasts are **not** blinking and **not** full-saturation —
  they use `border-bad/60` + a neutral-dark panel fill + a `--bad` icon, so a
  sticky failure toast does not strobe dark-adapted eyes. (Resolves Critique-1 D4.)

---

## 6. Sequence error/aborted rendering (`ui/src/views/SequenceView.tsx`, lane D)

**Root cause** (SequenceView.tsx:110): the panel renders only when
`(running || state==="complete") && sequence.progress`. On `error`/`aborted` it
unmounts and the failed run silently disappears, even though the engine published
`state:"error", detail` (engine.py:172) with a `progress` block.

**Fix — decouple the failure banner from `progress`** (defensive; the early-abort
path is real for the *recover* file even if `_set_state` always carries progress):

```tsx
const running  = sequence.state === "running" || sequence.state === "paused";
const finished = ["complete", "error", "aborted"].includes(sequence.state);
const failed   = sequence.state === "error" || sequence.state === "aborted";
const showPanel = running || finished;             // NOT gated on progress
```

- Render the **failure banner unconditionally** when `failed` (no `progress`
  needed); render the **progress bar only when `sequence.progress` exists**.
- Title gets a state-tone badge with a **shape glyph + word** (`<Icon name="x"/>`
  ERROR, `<Icon name="square"/>` ABORTED, `<Icon name="check"/>` COMPLETE) so it
  reads in night mode. Panel border `!border-bad/60` on error.
- Banner copy uses `humanizeSeqError(sequence.detail)` — a plain sentence, with
  the raw `detail` shown small underneath for experts.
- Buttons by state:
  - `running` → Pause; `paused` → Resume; `running||paused` → **Abort** (always
    enabled — see §7.2).
  - `failed` → **Re-run plan** (POST `/api/sequence/start` with the in-store plan),
    **Edit plan** (scrolls to the Targets panel), **View log**. Plus **Resume from
    frame N** *only when* `/api/sequence/recoverable` returned `recoverable:true`
    (it won't for a pre-first-frame failure — the resume file is written per-frame
    in `_record_frame`). The Resume button reads `recoverable.frames_done`, not a
    racy re-GET. (Resolves Critique-1 A5/B7, Critique-2 #5, Critique-3 #3.)

The existing `recoverable` effect (line 53) already re-fires when `running` flips
false after an error, so the resume point is fetched without backend change — but
Re-run/Edit/View-log are the **always-present** fallback so the user is never left
at a dead-end red banner. The left-rail sequence LED (App.tsx:113) also reflects
error: render `<HealthLeds>`-style dot with `blink-alert` + the `led-bad` class
when `sequence.state === "error"`.

---

## 7. Connection health (lane C: `ConnectionBanner.tsx`, `HealthLeds.tsx`; lane B/E supply data)

### 7.1 ConnectionBanner

```tsx
const phase = useStore((s) => s.wsPhase);
const stale = useStore((s) => s.telemetryStale);
if (phase === "up" && !stale) return null;

const down = phase !== "up";
const label = down ? "DISPLAY DISCONNECTED" : "TELEMETRY CATCHING UP";
// Copy must NOT imply the rig stopped — the backend, engine, mount, cooler keep
// running; only this browser's view lost the socket. (Critique-2 #2.)
const detail = down
  ? "Your view lost the AstroDeck server — the rig keeps running. Reconnecting…"
  : "Waiting for fresh telemetry — values may be a few seconds old.";
```

- Strip is `border-b`, `min-h-9` (not a cramped 32px alert), tone via **border +
  LED + glyph**, fill is a **dim neutral-dark** (`bg-raise/80`), not a saturated
  `bg-bad/10` red wash behind red text (which fails AA in night mode). The word
  label is `--text` weight; only the LED/glyph carries the hue. (Resolves
  Critique-3 #13.)
- `down` uses amber-leaning treatment, not alarm-red, because a Wi-Fi blip is not
  an emergency; reserve red for actual rig faults that arrive over the reconnected
  WS as `sequence`/`status` events. Detail text `hidden sm:inline`; on phone it's
  just the LED + short label, no wrap.

### 7.2 Action-disable policy (the safety fix)

- **Never disable Abort, Stop, mount Stop, Disconnect, Park.** A dropped WS
  almost always means the *browser's* socket hiccupped while the backend + REST
  channel are alive; greying out emergency controls at the moment the user most
  needs them is backwards. Stop-type buttons stay live and simply attempt the
  POST; `api.ts` surfaces a clean toast if it truly fails. (Resolves Critique-1
  B3, Critique-2 #2, Critique-3.)
- **Only gate *initiating* actions** (Run Sequence, GOTO/center, capture loop
  start, autofocus, cooler on) and only on **confirmed backend-down**, established
  by a cheap REST health ping (`GET /api/summary`, 4 s timeout) fired once when the
  socket drops — **not** by WS-frame staleness. Expose
  `actionsDisabled = backendConfirmedDown` from the store; initiating buttons read
  it via a tiny `useActionsDisabled()` hook and show `title="server not
  responding"`. The Rig/Connect page is fully exempt so recovery is always
  reachable.
- Telemetry **reads** dim (`opacity-40 saturate-50` on the header readout block
  and `opacity-60` on `<main>`, with `transition-opacity`) while down or stale, so
  the user sees values are not live — but `<main>` is **not** `pointer-events-none`
  (that would also block the Rig page).

### 7.3 HealthLeds (two hops, night-safe)

Replaces App.tsx:92–95. Browser↔backend (LINK) and backend↔NINA (NINA, only when
`mode==="nina"`). Each LED is **not color-only**: it carries a single letter
(`L`/`N`) inside the dot and a **distinct blink rate per severity** so it survives
the all-red night palette where `--good`/`--warn`/`--bad` are three near-identical
reds. (Resolves Critique-1 B5, Critique-2 #15, Critique-3 #4 — the panel's actual
A11y ask.)

```tsx
// LINK
state up      → steady dot, letter L, class led-on
state !up     → fast blink (blink-alert) + halo, letter L, class led-bad
// NINA (when ninaHealth.active)
ok            → steady, led-on
warming       → slow blink (blink), led-warn      // first poll after bridge — NOT red
stale         → slow blink (blink), led-warn
error|down    → fast blink (blink-alert) + halo, led-bad
```

Each LED keeps an always-visible short text label in night mode (drop the
`hidden sm:inline` when `night` is true) and a descriptive `title`
(`ninaTitle(ninaHealth)` → "NINA link healthy (last reply 3 s ago)" /
"No NINA reply for 47 s" / "NINA error: …" / "NINA connecting…"). A NINA stall
shows **amber on NINA while LINK stays green**; a browser drop shows **red on LINK
while NINA is untouched** (the NINA LED derives from the last-known `status`, but
when LINK is down the whole strip dims, so it doesn't read as a NINA fault — see
note). (Resolves Critique-1 C5.)

`deriveNinaHealth` (`ui/src/lib/health.ts`, pure + unit-tested):

```ts
export function deriveNinaHealth(status: RigStatus | null): NinaHealth {
  const nl = status?.nina_link;
  if (status?.mode !== "nina" || !nl?.active)
    return { active: false, ageMs: null, state: "na", lastError: null };
  if (nl.warming_up)                                   // suppress red right after bridge
    return { active: true, ageMs: null, state: "warming", lastError: null };
  if (nl.last_error && (nl.last_ok_age_s == null || nl.last_ok_age_s > 15))
    return { active: true, ageMs: nl.last_ok_age_s != null ? nl.last_ok_age_s*1000 : null,
             state: "error", lastError: nl.last_error };
  if (nl.last_ok_age_s == null)
    return { active: true, ageMs: null, state: "down", lastError: nl.last_error };
  const ageMs = nl.last_ok_age_s * 1000;
  // backend already gates `healthy` on busy-awareness + a generous threshold;
  // trust it for ok/stale so the LED doesn't flip amber mid-exposure.
  return { active: true, ageMs, state: nl.healthy ? "ok" : "stale", lastError: nl.last_error };
}
```

---

## 8. Backend (lane E)

### 8.1 NINA timestamps + heartbeat (`devices/nina.py`, `hub.py`)

`NinaClient.__init__`: add `self.last_ok = None`, `self.last_ok_wall = None`,
`self.last_error = None`. Wrap the body of `get` and `get_bytes` so **every**
successful NINA round-trip refreshes `last_ok = time.monotonic()` /
`last_ok_wall = time.time()` / `last_error = None`, and any exception sets
`last_error = str(e)[:200]` before re-raising.

**Heartbeat** (`hub.py`) — the fix for "NINA LED false-alarms on every long sub".
Because `_NinaDevice.info()` caches 0.4 s and a 300 s capture is one long HTTP
call, the 2 s status poll often makes **no** NINA round-trip for minutes. A
dedicated 5 s `GET /version` keeps `last_ok` honest independent of incidental
traffic:

```python
def ensure_status_poller(self):
    ...
    if self.mode == "nina" and (self._nina_hb_task is None or self._nina_hb_task.done()):
        self._nina_hb_task = asyncio.create_task(self._nina_heartbeat())

async def _nina_heartbeat(self):
    while self.nina_client is not None:
        try:
            await self.nina_client.get("/version", timeout=8.0)   # stamps last_ok
            self._bridge_ready = True
        except Exception:
            pass
        await asyncio.sleep(5.0)
```

Cancel/clear `_nina_hb_task` and reset `_bridge_ready=False` in `disconnect_all`
and at the top of `connect_nina`.

### 8.2 `poll_status()` → `nina_link` + `busy` (`hub.py`)

```python
out["busy"] = self.busy_label   # see §3.5: maps hub._busy task names → label

if self.mode == "nina" and self.nina_client is not None:
    c = self.nina_client
    age = (time.monotonic() - c.last_ok) if c.last_ok is not None else None
    busy = self.busy_label is not None
    # "healthy" is busy-aware: a long exposure/solve/AF legitimately starves the
    # heartbeat's window, so don't call it unhealthy while we know we're busy.
    healthy = (not self._bridge_ready) or busy or (age is not None and age <= 45.0)
    out["nina_link"] = {
        "active": True,
        "last_ok_age_s": round(age, 1) if age is not None else None,
        "last_error": c.last_error,
        "healthy": healthy,
        "warming_up": not self._bridge_ready,
    }
```

45 s threshold (vs. the draft's fiction-30 s) exceeds the 5 s heartbeat plus
slack; combined with `busy`-suppression and `warming_up`, the NINA LED stays green
through a normal multi-minute sub and only goes amber/red on a genuine stall.
(Resolves Critique-1 A1/A4, Critique-2 #4/#14.)

### 8.3 Alpaca proxy (`devices/alpaca.py` + `api/app.py`)

There is **no `_client()` factory** in alpaca.py (the draft invented it); the
template is the inline `httpx.AsyncClient(timeout=3.0)` in `discover()`. Write the
helper from scratch with **differentiated** failures so a beginner who typo'd the
IP gets a useful message:

```python
# devices/alpaca.py
class AlpacaScanError(DeviceError):
    def __init__(self, kind: str, msg: str):
        super().__init__(msg)
        self.kind = kind   # "unreachable" | "not_alpaca" | "timeout"

async def query_server(host: str, port: int) -> dict:
    url = f"http://{host}:{port}/management/v1/configureddevices"
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(url)
    except httpx.ConnectTimeout:
        raise AlpacaScanError("timeout", f"{host}:{port} did not respond in time")
    except httpx.ConnectError:
        raise AlpacaScanError("unreachable",
            f"could not connect to {host}:{port} — check the IP, port and that the device is on")
    if r.status_code != 200:
        raise AlpacaScanError("not_alpaca",
            f"{host}:{port} answered but is not an Alpaca server (HTTP {r.status_code})")
    try:
        devices = r.json().get("Value", [])
    except ValueError:
        raise AlpacaScanError("not_alpaca", f"{host}:{port} answered but did not return Alpaca JSON")
    return {"address": host, "port": port, "devices": devices}
```

```python
# api/app.py — next to /api/discover
@app.get("/api/discover/alpaca")
async def discover_alpaca_one(host: str, port: int = 11111):
    try:
        return await hub.discover_alpaca_server(host, port)   # thin pass-through to alpaca.query_server
    except alpaca_backend.AlpacaScanError as e:
        raise HTTPException(502, str(e))
```

Returns the same `AlpacaServer` shape ConnectView already renders. (Resolves
Critique-1 A2/A3, Critique-2 #8.) `http://` is hardcoded for now; an https/scheme
field is noted as a follow-up, not in scope.

### 8.4 `busy_label` and sequence centering

`busy_label` reads `hub._busy` task names; additionally the sequence engine's
centering goes through `hub.goto_and_center` which already publishes
`mount action=centering` — but the authoritative signal for `busy` is the presence
of a live `goto`/`solve`/`autofocus`/`capture` task in `hub._busy`, plus a
`hub._sequence_busy` flag the engine sets around slew/solve/AF blocks so the
status poll and the staleness ticker both know "busy, not stalled."

---

## 9. LOG button + badge (`App.tsx` header, lane C)

Replaces App.tsx:89–91 and the `logsOpen` local state (now `store.logOpen`).

- Badge counts **`unseenError` only** (errors are what's broken; warnings live in
  the log). An always-lit amber badge from benign warnings is wallpaper. (Resolves
  Critique-1, Critique-2 #13.)
- Badge is a filled `--bad` chip with the count; **inline** after the "LOG" text
  (not floating `-top-1.5 -right-1.5`, which clips in the `h-12` header).
  (Resolves Critique-3 #10.) Shape: filled chip + numeral, so it reads without
  relying on hue.
- Resets to 0 on `openLog()`; errors arriving while the drawer is open don't
  increment.
- After a reconnect, `reconcileLogs(history)` recomputes `logs` from `/api/logs`
  but **does not** retroactively inflate `unseenError` (we can't know which the
  user already saw); it only fills the drawer so the badge can't claim N unseen
  that aren't in the drawer. (Resolves Critique-1 §F backpressure, Critique-3 §F.)
- Button is `min-h-[44px]` on phone (`sm:min-h-0` desktop), `aria-label` includes
  the unseen count.

---

## 10. UI states, night-mode, 375px phone, CSS

### 10.1 UI state matrix

| Surface | Loading | Empty | Error | Success/Up |
|---|---|---|---|---|
| **Toasts** | — | nothing | error toast (10 s) or one sticky focal sequence toast w/ View-log action; no blink | success auto-dismiss 3 s |
| **LOG badge** | — | no badge | filled `--bad` chip + error count (inline) | no badge; →0 on open |
| **Log drawer** | — | "no events yet" | `✕[source]` rows (glyph + color) | rows, newest-first |
| **Sequence panel** | "starting plan…" detail | not rendered when `idle` | red border + "Sequence failed" banner (shows even with no progress) + Re-run/Edit/View-log (+Resume-from-N if resumable) | green ✓ complete, frames count |
| **LINK LED** | "connecting" gray | — | fast-blink red + halo + letter L + DISPLAY DISCONNECTED banner, telemetry dimmed | steady green, letter L |
| **NINA LED** | "warming" amber slow-blink | hidden (mode≠nina) | amber slow-blink (stale) / red fast-blink (down/error) + reason in title | steady green, age in title |
| **Manual Alpaca scan** | button "Querying…" | "server has no configured devices" | toast w/ specific cause (unreachable / not Alpaca / timeout) | device list w/ Assign |
| **ConnectionBanner** | "connecting…" | null when up+fresh | DISPLAY DISCONNECTED (amber, "rig keeps running") | null |

### 10.2 375px phone

- Header `h-12`; LOG button `min-h-[44px]`; NIGHT button `min-h-[44px]`. Telemetry
  readouts stay `hidden md:flex`; LINK/NINA LEDs stay (dot + letter, label appears
  in night mode).
- **Toasts top-center** (`top-14 left-3 right-3`), max 3, never over the bottom-nav
  / Abort row. Action buttons stack in their own row at full 44px with `gap-2`.
- **ConnectionBanner** full-width strip under the header; phone shows LED + short
  label only.
- **LogDrawer = bottom sheet** on `< lg`: a `~55vh` panel anchored to the bottom
  that **does not cover** the sequence progress panel and uses a **light scrim only
  over the area it occupies** (or no scrim, tap-outside-to-close), so during a run
  the user can watch progress and read the log together. `slide-up` entry. On
  `lg+` it's the docked right column as today. (Resolves Critique-1 D2,
  Critique-2 "keep", Critique-3.)
- Sequence error panel stacks (the `xl:grid-cols` collapses to one column); banner
  wraps; buttons wrap via `flex gap-2`.

### 10.3 Red night mode

- The known limitation ("status colors collapse to coral-red") is mitigated by
  **shape + motion + letters**, not new colors:
  - LEDs carry a letter (`L`/`N`) and a **per-severity blink rate** (steady = ok,
    slow `blink` = warming/stale, fast `blink-alert` + halo = down/error). This is
    the safety-critical surface the draft punted; it is fixed here.
  - Toasts/log/badge/sequence use a **consistent icon** per severity (same mark
    everywhere) from `components/Icon.tsx` at **≥16px** — a real single-weight
    inline-SVG set (Lucide-style: `x-circle`, `alert-triangle`, `info`,
    `check-circle`, `square`), **not** 9–11px Unicode punctuation. (Resolves
    Critique-3 #7.)
- `--img-filter` sepia only touches `img.astro` (preview), so chrome renders in
  the pure night palette.
- **Reduced-motion fallback keeps a severity cue.** The `prefers-reduced-motion`
  guard disables `blink`/`blink-alert`/`slide-up`/`fade-up` animations but the
  error/down LEDs then get a **static halo + thicker ring** (`.led-bad` includes a
  non-animated `box-shadow` ring), so severity is never conveyed by motion *alone*.
  (Resolves Critique-1 §F, Critique-2 #15.)
- Text: state-bearing copy uses `--text` (`#c8d4e8` / night `#e08585`), **never**
  `--text-dim` at 9px. Minimum chrome copy size 11px. Banner/toast fills are
  neutral-dark, not saturated red washes. (Resolves Critique-3 #8/#13.)

### 10.4 `index.css` additions (land FIRST, lane A)

```css
/* error LED — does not exist today; the link-down indicator is invisible without it */
.led-bad { background: var(--bad); box-shadow: 0 0 8px var(--bad), 0 0 0 2px color-mix(in srgb, var(--bad) 30%, transparent); }
/* letter inside an LED dot — night-safe shape cue */
.led-letter { display: inline-flex; align-items: center; justify-content: center;
  width: 14px; height: 14px; border-radius: 50%; font-size: 9px; font-weight: 700;
  font-family: "IBM Plex Mono", monospace; line-height: 1; }

@keyframes blink-alert { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }
.blink-alert { animation: blink-alert 0.7s steps(2,end) infinite; }   /* faster than .blink */

@keyframes slide-up { from { transform: translateY(100%); } to { transform: none; } }
.sheet-enter { animation: slide-up 0.2s ease both; }

/* keyboard focus rings (review: outline:none everywhere today) — accent is a shape, fine in night */
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

@media (prefers-reduced-motion: reduce) {
  .blink, .blink-alert, .view-enter, .sheet-enter { animation: none !important; }
  /* keep a non-motion severity cue: error LEDs retain their static halo ring */
}
```

Animations live on **classes** (`.sheet-enter`, `.blink-alert`), not inline
`style=` strings, so the reduced-motion guard is robust (no brittle
`[style*="…"]` attribute selectors). (Resolves Critique-3 #2/F.)

---

## 11. Helpers (lane A)

- `ui/src/lib/health.ts` — `deriveNinaHealth` (pure, §7.3) + `ninaTitle`.
- `ui/src/lib/humanize.ts` — `humanizeLog({source,message})` and
  `humanizeSeqError(detail)`: a small map from `source`/pattern → plain sentence +
  suggested action. Examples:
  - `source:"capture"` + repeated failure → "Camera isn't responding. Check the
    camera connection on the Rig page."
  - `"NINA HTTP 5xx"` → "NINA reported an error. Check NINA on the imaging PC."
  - `"plate solve failed"` → "Plate-solve failed — check focus/exposure, or solve
    manually."
  - cooler / guiding / dither warnings → never toast (log only).
  - Unknown → fall back to the raw message (truncated), still queued. Raw text is
    always preserved in the log drawer for experts. (Resolves Critique-1 B2,
    Critique-2 #6 partial.)
- `ui/src/lib/notify.ts` — `requestNotifyPermission()`, `notifyAndBeep(state,
  title, body)`: fires a `Notification` when `notifyEnabled && permission ===
  "granted"` and plays a short WebAudio beep; no-ops otherwise. Opt-in via a small
  "Alerts" toggle on the Rig page that calls `setNotifyEnabled` +
  `requestNotifyPermission`. (Resolves Critique-2 #6.)

---

## 12. `api.ts` (lane B) — per-endpoint timeout + typed errors + fallback

```ts
export class ApiError extends Error {
  constructor(message: string, public status: number, public timedOut = false) {
    super(message); this.name = "ApiError";
  }
}

// per-endpoint budgets: the synchronous connect path does real device I/O
// (build_nina_rig handshake; NINA read timeout is 120s) — a blanket 15s aborts a
// legitimately-slow bridge and desyncs UI vs backend. (Critique-1 §F, Critique-2 #9.)
function timeoutFor(path: string): number {
  if (/\/api\/connect\/(nina|alpaca|phd2)/.test(path)) return 130000;
  if (/\/api\/discover/.test(path)) return 30000;
  return 15000;  // _spawn'd ops return {started} immediately, so 15s is plenty
}

// AbortSignal.timeout is recent; fall back for old iPad Safari / Android WebView
// or timeouts mis-label as generic "network error". (Critique-2 #10.)
function timeoutSignal(ms: number): { signal: AbortSignal; done(): void } {
  if (typeof AbortSignal !== "undefined" && "timeout" in AbortSignal) {
    return { signal: (AbortSignal as any).timeout(ms), done() {} };
  }
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(new DOMException("Timeout", "TimeoutError")), ms);
  return { signal: ac.signal, done() { clearTimeout(t); } };
}

async function req<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const { signal, done } = timeoutSignal(timeoutFor(path));
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (e) {
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    throw new ApiError(
      timedOut ? "request timed out — server not responding" : "network error — server unreachable",
      0, timedOut);
  } finally { done(); }
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); }
    catch { /* keep statusText */ }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}
export const api = {
  get: <T = unknown>(path: string) => req<T>("GET", path),
  post: <T = unknown>(path: string, body?: unknown) => req<T>("POST", path, body),
};
```

Existing callers already `try/catch → showToast("error", e.message)`, which now
flows through `humanizeLog` + the queue, so a timeout produces a clear toast
instead of a forever-spinner.

---

## 13. App.tsx assembly + perf (lane C)

Switch App's broad `useStore()` (which re-renders the whole tree every guide tick —
panel perf finding) to **split selectors**. Chrome components subscribe to their
own slice so a guide tick (mutates only `guide`) re-renders none of them.

```tsx
const view = useStore((s) => s.view);
const setView = useStore((s) => s.setView);
const night = useStore((s) => s.night);
const toggleNight = useStore((s) => s.toggleNight);
const sequence = useStore((s) => s.sequence);
const status = useStore((s) => s.status);
const linkDown = useStore((s) => s.wsPhase !== "up");
const telemetryStale = useStore((s) => s.telemetryStale);
const dim = linkDown || telemetryStale;
```

Skeleton:

```tsx
<div className="h-full flex flex-col">
  <header …>…HealthLeds(§7.3)…LOG+badge(§9)…NIGHT…</header>
  <ConnectionBanner />                          {/* null when healthy */}
  <div className="flex flex-1 min-h-0">
    <nav …left rail (sequence LED reflects error)…/>
    <main className={`flex-1 overflow-y-auto p-4 pb-20 sm:pb-4 ${dim ? "opacity-60 transition-opacity" : ""}`} key={view}>
      <div className="view-enter max-w-[1500px] mx-auto"><Active /></div>
    </main>
    <LogDrawer />                               {/* docked lg+, bottom sheet below */}
  </div>
  <nav className="sm:hidden …bottom nav…"/>
  <Toasts />                                    {/* top-center phone, bottom-right desktop */}
</div>
```

---

## 14. Rejected critique points (with reasons)

- **"`sequence.progress` may be absent on `error`, so the panel still vanishes."**
  Partially rejected: per engine.py:100–110, `_set_state` *always* attaches a
  `progress` block once `self.plan` is set, and `error` is published after
  `start()` set the plan — so in practice `progress` exists. **But I adopt the
  decouple anyway** (banner not gated on `progress`) because it is near-zero cost,
  defends against future engine changes, and the *recover* file genuinely is absent
  pre-first-frame (which the Resume button already guards). Net: the fix ships; the
  premise about the current engine is the part rejected.
- **"Drop warnings from the badge AND don't toast them" vs. "warnings should still
  toast."** I side with badge=errors-only and warnings=log-only (both critiques
  ultimately want less noise). A user who wants every warning has the log; the
  badge stays meaningful.
- **"Use Lucide as a dependency."** Adopted in spirit (a real ≥16px single-weight
  SVG set) but implemented as a tiny local `components/Icon.tsx` with inline SVG
  paths to avoid adding a dependency for ~6 glyphs; visually equivalent and
  night-safe. Not a rejection of the requirement, a scoping of the implementation.
- **"Add a backend dead-man's switch / safety monitor so the rig is safe when the
  *backend* dies."** Real and important, but **out of scope** for this surface — it
  is the panel's separate "Unattended safety" P0 (build order item 4). This spec
  explicitly does **not** paint a backend-down rig as safe; the banner copy is
  honest ("your view lost the server") and §14 cross-references the safety work.

---

## 15. Interop & non-regressions

- No new WS frame types; `nina_link`, `busy`, sequence `error` ride existing
  `status`/`sequence`/`log`. No protocol bump.
- `wsConnected`/`setWsConnected`/`showToast` retained as shims → existing call
  sites compile unchanged.
- Sequence Re-run/Edit/View-log are pure UI; Resume reuses existing
  `/api/sequence/recoverable` + `/api/sequence/recover`.
- Backend additions are additive: `/api/discover/alpaca`, optional
  `/api/nina/health`, `NinaClient` timestamps, `nina_link`/`busy` keys,
  heartbeat/`busy_label` internals. No existing endpoint signature changes.
- `/api/logs` (existing) now consumed on reconnect to reconcile the drawer/badge
  with `bus.log_history` — closes the backpressure-gap desync.

---

## 16. Implementation checklist

**Lane A — foundation (land first; unblocks C/D)**
- [ ] `index.css`: add `.led-bad`, `.led-letter`, `.blink-alert`, `@keyframes
      blink-alert`, `@keyframes slide-up`, `.sheet-enter`, `:focus-visible`,
      class-targeted reduced-motion guard.
- [ ] `types.ts`: add `ToastLevel`, `Toast`, `WsPhase`, `NinaState`, `NinaHealth`;
      extend `RigStatus` (`busy`, `nina_link`) and `SequenceState`
      (`target_index`, `progress.rejected`).
- [ ] `store.ts`: replace `toast` with `toasts[]` queue + `enqueueToast`
      (coalesce-without-age-reset, single focal sequence toast, TOAST_MAX trim);
      add `wsPhase`/`wsLastEvent`/`telemetryStale`/`logOpen`/`unseenError`/
      `ninaHealth`/`notifyEnabled` + actions; `handleEvent` status/sequence/log
      branches (§5.3); `reconcileLogs`; keep `wsConnected`/`setWsConnected`/
      `showToast` shims.
- [ ] `lib/health.ts`, `lib/humanize.ts`, `lib/notify.ts`.
- [ ] Unit tests: `deriveNinaHealth` (na/warming/ok/stale/error/down); toast
      reducer (coalesce keeps `createdAt`, sequence-toast singleton, trim prefers
      generic dismissible); `humanizeSeqError`/`humanizeLog`.

**Lane B — transport**
- [ ] `ws.ts`: phase reporting, `noteWsEvent`, busy-aware staleness ticker
      (`STALE_MS=20000`, suppressed when `status.busy`), `/api/logs` reconcile on
      open, sustained-down notify scheduling.
- [ ] `api.ts`: `ApiError`, `timeoutFor` per-endpoint, `timeoutSignal` with
      `AbortController` fallback.
- [ ] Test: hanging fetch → `ApiError{timedOut:true}`; connect path uses 130 s.

**Lane C — chrome**
- [ ] `components/Icon.tsx` (≥16px severity glyphs, consistent across surfaces).
- [ ] `components/Toasts.tsx` (top-center phone, single shared aria-live, shared
      500 ms sweeper, stacked action row with real spacing, no blink on error).
- [ ] `components/LogDrawer.tsx` (docked `lg+`; bottom sheet `< lg` that coexists
      with progress; Escape/focus-trap/return-focus; glyph+color rows).
- [ ] `components/ConnectionBanner.tsx` ("Display disconnected — rig keeps
      running"; amber for link, dim neutral fill).
- [ ] `components/HealthLeds.tsx` (LINK + NINA, letter + per-severity blink,
      night-mode labels, titles).
- [ ] `App.tsx`: split selectors; mount banner/leds/badge/drawer/toasts; dim
      (not pointer-events-none) telemetry while down/stale; sequence-error left-rail
      LED; `useActionsDisabled()` gating initiating buttons on confirmed
      backend-down only.

**Lane D — views**
- [ ] `SequenceView.tsx`: `showPanel` decoupled from `progress`; failure banner
      unconditional on `failed`; state-tone badge w/ glyph+word; Re-run/Edit/
      View-log always + Resume-from-N only when resumable; Abort always enabled.
- [ ] `ConnectView.tsx`: `scanManual` → `GET /api/discover/alpaca?host=&port=`;
      surface differentiated error message.

**Lane E — backend**
- [ ] `devices/nina.py`: `last_ok`/`last_ok_wall`/`last_error` stamped in
      `get`/`get_bytes`.
- [ ] `devices/alpaca.py`: `AlpacaScanError` + `query_server` (differentiated
      failures).
- [ ] `hub.py`: `_nina_heartbeat` (5 s `/version`), `_bridge_ready`, `busy_label`,
      `nina_link`+`busy` in `poll_status` (busy-aware `healthy`, 45 s threshold);
      `discover_alpaca_server` pass-through; cancel heartbeat in `disconnect_all`.
- [ ] `api/app.py`: `GET /api/discover/alpaca` (→502 with specific cause); optional
      `GET /api/nina/health`.
- [ ] Tests: `query_server` (mock transport → unreachable/not_alpaca/ok);
      `poll_status` includes `nina_link` only when bridged, `warming_up` true before
      first heartbeat, `healthy` true while `busy`; `NinaClient.get` stamps
      `last_ok`/`last_error`.

**Integration acceptance**
- [ ] Kill the WS (stop dev server): LINK fast-blinks red + letter L, banner says
      "rig keeps running", telemetry dims, **Abort/Stop/Disconnect stay enabled**,
      Run/GOTO disable only after the `/api/summary` ping confirms backend-down.
- [ ] Flapping capture error every 1 s: one error toast with a human sentence that
      auto-dismisses at 10 s and re-bumps `count` without becoming immortal; badge
      counts errors; no warning toasts.
- [ ] Bridge to NINA, start a 120 s sub: NINA LED stays green throughout (heartbeat
      + busy-suppression); pull the NINA PC's network → LED goes red within ~50 s
      with the error in the title; LINK stays green.
- [ ] Force a sequence error before the first frame: red banner shows (no progress
      bar), Re-run/Edit/View-log present, Resume-from-N absent.
- [ ] Phone 375px: toasts top-center clear of the nav; log opens as a bottom sheet
      with the progress panel still visible; tap targets ≥44px; Escape closes the
      sheet.
```
