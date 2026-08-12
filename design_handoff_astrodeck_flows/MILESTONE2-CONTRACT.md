# IMPLEMENTATION CONTRACT — AstroDeck Flows, Milestone 2 (desktop/tablet web UI)

**Status of this document.** Written read-only against the four sweeps plus my own reads of `scripts/flows_visual_check.py`, `ui/src/lib/flowsApi.ts`, `server/astrodeck/flows/{to_plan,models}.py`, `server/astrodeck/api/app.py` (3520–3870), `server/astrodeck/flows/{doctor,tonight,calibration_health,wizard,examples}.py`, `ui/src/index.css`, and the handoff README. Every number and string below is quoted from a file. Where a value is **not** in any source it is marked `⚠ UNSPECIFIED` and appears in §G — it is never filled in with a guess.

**Five findings that change the shape of the work, discovered in this pass and not present in the sweeps:**

1. `scripts/flows_visual_check.py` — which the definition of done makes mandatory — contains **four selectors/steps that no source specifies**: it presses `f` for fit (the spec has only Delete/Backspace), it clicks a button named **`ADD NODE`** (the design's label is `+ ADD STAGE`), it opens the edit sheet by **double-clicking a node at 1440px** (the design says the ✎ is the only opener, and at ≥1080px there is no sheet at all), and it toggles night by setting `document.documentElement.setAttribute('data-night', 'true')` — **which does nothing**, because `index.css` defines night at `:root.night` only (verified: `grep -n "^:root" ui/src/index.css` → lines 33, 75, 76, 105, 734, 886, 921, 922; no `[data-night]` anywhere).
2. `data-view` **does not exist anywhere in `ui/src`** (grep: no matches). The harness's very first marker, `[data-view='flows'] [data-flows-tab='library']`, needs it.
3. `server/astrodeck/flows/wizard.py` has `generate()` / `generate_record()` / `flow_name()` — and **no API route**. `grep -rn wizard server/astrodeck/api/app.py` returns one unrelated comment hit. The guided wizard is currently unreachable from any client.
4. There is **no endpoint that serves the node vocabulary**. `nodes.py` holds labels/cats/ports/param-defaults; nothing exposes it, and it never held `fields`/`desc`/`sum` at all. The UI must own a second transcription of all 19 node contracts and a first-and-only transcription of 58 select-option strings, 70+ field labels and 11 unit strings.
5. `POST /api/flows/{id}/run` (app.py:3798) emits exactly one thing: `bus.log("info", f"flow '{rec.name}' started: …", "flow")`. There is **no `flow.node` and no `flow.log` topic**. Every animated thing in §6 of the README — busy LEDs, marching wires, ETA, STAGE, FRAMES, the cloud-dodge choreography, and the harness's `[data-flows-run='holding']` capture — has no data source.

---

# A. FILE PLAN

## A.1 New files

All under `C:/Users/bear/astro/ui/src/`. One responsibility each; nothing over ~300 lines.

### Data / pure logic (no React) — build these first, they are testable without a DOM

| Path | Responsibility |
|---|---|
| `components/flows/flowsTypes.ts` | TS mirrors of `models.py`: `FlowNodeRec`, `FlowEdgeRec`, `FlowGraphRec`, `FlowRecordRec`, `FlowNodeStatus`, `PortKind`, `FlowNodeType` (19-member union). |
| `components/flows/nodeDefs.ts` | **The vocabulary table.** All 19 `NodeDef`s: `label`, `cat`, `colorVar`, `ins[]`, `outs[]`, `params` defaults, `fields[]` (key/label/control/options/unit), `desc`, `sum(p)`. The single largest file; ~450 lines of data. |
| `components/flows/palette.ts` | `PALETTE_GROUPS` — group names, group order, item order. Separate from `nodeDefs` because the item order is disputed (§G-3). |
| `components/flows/geometry.ts` | `nodeW(tier)`, `nodeRows(node)`, `nodeLayoutHeight(node)`, `portPos(node, portId, dir)`, `edgePath(p1, p2, mode)`, `fitView(nodes, rect)`. Pure; no store, no DOM. |
| `components/flows/autoLayout.ts` | `flowOrder(graph)` (topological, flow-edges only) + `computeAutoLayout(graph, containerWidth)` → `{pos: Record<id,{x,y}>, height}`. Phone FLOW tab only. |
| `components/flows/summary.ts` | The 19 `sum(p)` footer formatters, if `nodeDefs.ts` grows past ~500 lines. Optional split. |

### Screens and chrome

| Path | Responsibility |
|---|---|
| `views/FlowsView.tsx` | Route entry. Owns `data-view="flows"`, the tier resolution, and the `library ⟷ editor` switch. Renders nothing else itself. |
| `components/flows/FlowHeader.tsx` | The view-local toolbar (54px row): back, wordmark/flow-name, provider badge, validation chip, ETA, TONIGHT, RUN/STOP, night, `i`. |
| `components/flows/FlowLibrary.tsx` | Library screen: heading, sub, toolbar (search + 3 chips), folder sections. |
| `components/flows/FlowLibraryCard.tsx` | One flow card + the dashed `+ NEW FLOW` card. |
| `components/flows/FlowEditor.tsx` | Editor host. Rail ∣ canvas ∣ inspector at desktop; canvas + float at tablet; phone tab switch. Owns `data-screen-label` and `data-flows-run`. |
| `components/flows/FlowCanvas.tsx` | The pan/zoom surface: nebula, screen-fixed grid, world transform, pointer system, wheel, pinch. Owns `data-flows-canvas`. |
| `components/flows/FlowNodeCard.tsx` | One node. `React.memo`'d; subscribes `useFlowNodeStatus(id)` itself. |
| `components/flows/FlowPort.tsx` | One port row (dot + label + hit box). Carries `data-port` / `data-port-dir`. |
| `components/flows/FlowWireLayer.tsx` | The single `<svg>`: all edges (hit path + visible path), the pending wire, nothing else. |
| `components/flows/FlowWireDelete.tsx` | The ✕ at the selected wire's midpoint. Owns `data-flows-wire-selected`. |
| `components/flows/FlowZoomCluster.tsx` | `− / % / + / FIT`. |
| `components/flows/FlowLogStrip.tsx` | 30px collapsed bar → 170px scrollback. |
| `components/flows/FlowPalette.tsx` | Group list. Rendered as the 192px rail **and** as the sheet body — one component, a `variant` prop. |
| `components/flows/FlowInspector.tsx` | 284px column **and** sheet body. Selected state + FLOW-overview state. Owns `data-flows-inspector`. |
| `components/flows/FlowFieldRow.tsx` | One param control (select ∣ text+unit), tier-sized. |
| `components/flows/CalibrationMatrix.tsx` | LIBRARY HEALTH block. Owns `data-calibration-matrix`. |
| `components/flows/FlowEditSheet.tsx` | `<Overlay variant="sheet">` wrapping `FlowInspector`. Owns `data-flows-editsheet`. |
| `components/flows/FlowPaletteSheet.tsx` | `<Overlay variant="sheet">` wrapping `FlowPalette`. Owns `data-flows-palette`. |
| `components/flows/TonightPanel.tsx` | `<Overlay variant="center">` + the 3 pill tabs + `✕`. Owns `data-flows-tonight`. |
| `components/flows/TonightTimeline.tsx` | The SVG geometry layer + the absolutely-positioned HTML label layer + legend + honesty note. |
| `components/flows/TonightStory.tsx` | The `60px 1fr` row list. |
| `components/flows/TonightPlan.tsx` | Caption + `COPY JSON` + the `<pre>` block. |
| `components/flows/FlowWizard.tsx` | `<Overlay variant="center">` 560px sheet. Owns `data-flows-wizard`. |
| `components/flows/wizardGenerate.ts` | The TS port of `genWizard()` — **only if §G-2 is resolved that way.** |
| `components/flows/FlowPhoneTabs.tsx` | 50px + safe-area bottom bar, 3 tabs. |
| `components/flows/FlowPhoneGraph.tsx` | The auto-laid FLOW tab. Owns `data-flows-phone-tab="flow"`. |
| `components/flows/FlowTapWireBar.tsx` | The armed hint bar + CANCEL. Owns `data-flows-wiring-armed`. |
| `components/flows/FlowPhoneMonitor.tsx` | STATE/ETA/STAGE/FRAMES + log + 56px RUN. Owns `data-flows-phone-tab="monitor"`. |

### Tests (one process each, per `run-tests.mjs`)

| Path | Asserts |
|---|---|
| `components/flows/__tests__/geometry.test.ts` | `portPos`/`edgePath`/`fitView` against the worked numbers in §D.3. No DOM. |
| `components/flows/__tests__/autoLayout.test.ts` | The M16 phone layout produces content-y `18 / 117 / 236 / 355` and columns `14 / 228` at `vw=390`. No DOM. |
| `components/flows/__tests__/nodeDefs.test.ts` | 19 types; port ids/kinds/order; `abort` carries `--bad` not `--warn`; `capture.bin` is the **string** `"1"`. No DOM. |
| `components/flows/__tests__/flowNodeDom.test.tsx` | jsdom: a node card renders `data-node-type`, `data-port`, `data-port-dir`; a status change re-renders one card. |
| `components/flows/__tests__/wireDropDom.test.tsx` | jsdom: kind mismatch refuses + toasts; occupied input is replaced; self-wire is silently refused on drag and toasted on tap. |
| `components/flows/__tests__/markersDom.test.tsx` | **The harness contract test**: every `data-*` in §F resolves in the state the harness drives it from. |

## A.2 Existing files — the exact minimal diff

### (1) `ui/src/types.ts` — append to `ViewName` (append-only; never reorder)

```ts
  | "gallery"
+ | "flows";
```

### (2) `ui/src/lib/lazyViews.ts:71` — register the loader

```ts
   gallery: () => import("../views/GalleryView"),
+  flows: () => import("../views/FlowsView"),
 };
```
`lib/__tests__/lazyViews.test.ts:134-144` `fs.existsSync`-checks this specifier, so `views/FlowsView.tsx` must exist on disk before this line lands.

### (3) `ui/src/App.tsx` — `NAV` append (lines 40–116)

```ts
   { id: "gallery", label: "Gallery", icon: "gallery" },
+  { id: "flows",   label: "Flows",   icon: /* ⚠ UNSPECIFIED — §G-9 */ "bridge" },
 ];
```
**Do NOT add `flows` to `GATED` (App.tsx:150).** The `sequence` precedent is exact and its comment says why: *"the builder stays usable offline, just Run is disabled — that nuance lives inside SequenceView."* Flows' library and editor need no rig; only RUN does.

**`data-view` is NOT added here.** It goes on `FlowsView`'s own root (§F), which keeps App.tsx to one line.

### (4) `ui/src/components/NavMoreSheet.tsx:36-63` — `OVERFLOW_VIEWS` append

```ts
   { id: "gallery", label: "Gallery", icon: "gallery" },
+  { id: "flows",   label: "Flows",   icon: "bridge" },
 ];
```
Required by `nav.test.ts`'s phone-parity assertion. **Do not touch `BottomNav.tsx`'s `PRIMARY`** (Risk-10).

### (5) `ui/src/__tests__/nav.test.ts` — two mandatory edits, one blocked

```ts
 const LANDED_ORDER = [
   "connect","polar","mount","focus","capture","guide","atlas",
-  "sequence","power","monitor","tonight","settings","report","help","gallery",
+  "sequence","power","monitor","tonight","settings","report","help","gallery","flows",
 ];
```
```ts
-test("APPEND-ONLY: Gallery is the newest entry and sits last", () => {
-  eq(NAV_IDS[NAV_IDS.length - 1], "gallery");
+test("APPEND-ONLY: Flows is the newest entry and sits last", () => {
+  eq(NAV_IDS[NAV_IDS.length - 1], "flows");
 });
```
**Blocked:** the rail-fit assertion at `nav.test.ts:170-192` fails at 16 entries. With `py-1.5` (App.tsx:663): `entry = 38 + 2·6 = 50`; `railHeight = 16·50 + 16 = 816`; `816 + 50 = 866 > RAIL_CLIENT_H (818)`. The arithmetic fix is `py-1.5 → py-1` at App.tsx:663 (`entry = 46 ≥ 44` floor; `16·46 + 16 = 752`; `752 + 46 = 798 ≤ 818` ✓), which visually changes all 16 tabs. The test's own failure message demands a re-measure on a real 1440×900 viewport. **§G-8 — do not land this without a ruling.**

### (6) `ui/src/store.ts` — four insertions, all additive

- `AppState` (~line 545): one field `flows: FlowsState` (§B).
- The store body (~line 939): the initial value + the actions.
- `handleEvent`'s switch (line 1649): two new `case`s (`"flow.node"`, `"flow.log"`) plus one branch inside the existing `case "log"` (§B.4).
- Selector block (line 2043+): the hooks in §B.3.

### (7) `ui/src/index.css` — additive only

```css
/* Flows: a live wire's dash marches along the run's direction of travel. The
   static fallback is the dash PATTERN itself (7 6 vs the idle solid / event
   4 5), so a reduced-motion reader still sees which lane is live. */
@keyframes flow-dash { to { stroke-dashoffset: -24; } }
.flow-wire-march { animation: flow-dash 0.6s linear infinite; }
@media (prefers-reduced-motion: reduce) {
  .flow-wire-march { animation: none !important; }
}
```
That is the **only** authored class Flows needs. Everything else uses Tailwind utilities or existing authored classes (`.btn`, `.field`, `.field-rig`, `.led-*`, `.prov`, `.prov-sim`, `.panel`, `.label`, `.panel-title`, `.mono`, `.tap`, `.overlay-*`, `.empty-state-inline`, `.view-enter`, `.sheet-enter`).

⚠ Whether this counts as "the token file" under the README's scope fence is genuinely ambiguous (§G-11). It is additive, and `cssClasses.test.ts` cannot be satisfied any other way (a `@keyframes` and a `prefers-reduced-motion` rule cannot be expressed inline).

### (8) `ui/src/__tests__/cssClasses.test.ts:58` — allowlist

```ts
   "more-sheet-in",
+  "flow-wire-march",
 ];
```

### (9) `scripts/flows_visual_check.py` — **four changes needed, all blocked on §G**

See §F.4. Do not edit it silently; it is the definition-of-done instrument.

---

# B. THE STORE SLICE

## B.1 The types

```ts
// ─────────────────────────────────────────── components/flows/flowsTypes.ts
export type FlowNodeType =
  | "dusk" | "target" | "safety" | "cloudwatch"
  | "dome" | "flatpanel"
  | "slew" | "autofocus" | "guide" | "capture" | "duskflats" | "calib"
  | "pool" | "condition"
  | "holdresume" | "notify" | "refocus" | "abort" | "report";

export type PortKind = "flow" | "event";
export type FlowNodeStatus = "idle" | "busy" | "ok" | "warn" | "bad";
export type FlowLogTone = "info" | "good" | "warn" | "bad";

/** Mirrors server FlowNode. `params` is deliberately loose — models.py is
 *  "strict about EDGES and permissive about PARAMS" (models.py:10-16). */
export interface FlowNodeRec {
  id: string; type: FlowNodeType; x: number; y: number;
  params: Record<string, string | number>;
}

/** Mirrors server FlowEdge. The wire key is `from`, NOT `from_`: FlowEdge sets
 *  alias="from" and FastAPI serialises by alias (app.py's get_flow docstring is
 *  explicit that hand-dumping would emit `from_` and every wire would vanish). */
export interface FlowEdgeRec {
  id: string; from: string; fromPort: string; to: string; toPort: string;
}

export interface FlowGraphRec { nodes: FlowNodeRec[]; edges: FlowEdgeRec[]; }

export interface FlowRecordRec {
  id: string; name: string; folder: string; tagline: string;
  graph: FlowGraphRec;
  created_ts: number; updated_ts: number;
  last_run: number | null; last_result: "" | "ok" | "warn" | "bad";
  readonly: boolean;
}

/** A pending wire drag. Screen→world conversion happens in the canvas; the
 *  store holds world coords so the pending path uses the same bezier as a
 *  real edge. */
export interface PendingWire {
  from: string; fromPort: string; kind: PortKind;
  to: { x: number; y: number };
}

export interface FlowLogLine {
  id: number; ts: number; msg: string; tone: FlowLogTone;
}

export type FlowScreen = "library" | "editor";
export type FlowPhoneTab = "flow" | "canvas" | "monitor";
export type TonightTab   = "timeline" | "story" | "plan";
/** Drives the harness's [data-flows-run] marker. `holding` is the cloud-hold
 *  state capture 07 must be taken inside. */
export type FlowRunPhase = "idle" | "running" | "holding" | "stopping";
```

## B.2 The slice — exact TypeScript

One nested field, matching the store's `sequence` / `polar` / `guide` field-group convention. Nesting is safe for the per-node re-render requirement because the selector's **result** is what zustand compares (§B.3).

```ts
// ───────────────────────────────────────────────────────── in store.ts
/** Flows. ONE field rather than fifteen flat ones, matching `sequence`/`polar`:
 *  a domain that is only mounted on one view should not spread fifteen names
 *  through AppState. Every write below is immutable and touches ONE sub-object,
 *  so a pan does not change `graph`'s identity and a status tick does not
 *  change `pan`'s — which is what makes the narrow selectors in §B.3 exact. */
export interface FlowsState {
  // ── library
  /** Card projections from GET /api/flows. NOT graphs — models.py:167 explains
   *  why the server ships a separate shape. */
  cards: FlowCard[];
  folders: FlowFolder[];
  libraryLoaded: boolean;
  libraryError: string | null;

  // ── the open flow
  /** null on the library screen. */
  record: FlowRecordRec | null;
  graph: FlowGraphRec;
  /** Set on every local edit, cleared on a successful save. The editor
   *  re-saves on leaving (README §2: "re-saved on leaving the editor"). */
  dirty: boolean;

  // ── selection vs editing — SEPARATE, per README §"State management".
  /** A node id or an edge id, or null. Drives the accent border and Delete. */
  sel: { kind: "node" | "edge"; id: string } | null;
  /** The node whose parameters are being edited. On desktop the inspector
   *  follows `sel`; on tablet/phone ONLY the ✎ sets this, and only this opens
   *  the sheet. They are different pieces of state and must never be merged. */
  editNode: string | null;

  // ── canvas viewport (its own sub-object: a pan must not re-render nodes)
  pan: { x: number; y: number };
  zoom: number;

  // ── in-flight wiring
  /** Desktop/tablet drag-to-wire. */
  wire: PendingWire | null;
  /** Phone FLOW tab tap-to-wire. Cleared on tab switch and on leaving. */
  tapWire: { from: string; fromPort: string } | null;

  // ── run
  /** Keyed by node id; absent means "idle". A plain string map so a tick is a
   *  primitive write — see useFlowNodeStatus. */
  statuses: Record<string, FlowNodeStatus>;
  run: {
    phase: FlowRunPhase;
    /** Seconds remaining, or null when the server has not said. NEVER a
     *  client-side countdown invented from a scripted total. */
    etaS: number | null;
    curStage: string;
    frames: number;
    frameGoal: number | null;
    /** From the 409 the operator clicked past, kept so the run banner can
     *  still say what is not being honoured. */
    acceptedUnmapped: FlowUnmapped[];
  };
  /** Ring of 120 (README §"State management"). Fed from `flow.log`, and from
   *  the existing `log` event when source === "flow". Separate from the global
   *  `logs` so mount telemetry does not re-render the strip. */
  logs: FlowLogLine[];

  // ── derived-from-server, cached
  /** The last compile. `null` until the first compileDraft lands. */
  compiled: FlowCompileResult | null;
  compiling: boolean;
  /** GET /api/flows/{id}/tonight. Untyped at the store boundary because the
   *  payload is large and only TonightPanel reads it. */
  tonight: Record<string, unknown> | null;
  tonightLoading: boolean;
  tonightError: string | null;
  /** GET /api/calibration/health?flow_id= */
  calHealth: {
    rows: Record<string, unknown>[];
    planned: boolean;
    counts_masters_only: boolean;
    assumed: { offset: number; temp_c: number | null };
  } | null;

  // ── ui
  ui: {
    screen: FlowScreen;
    phoneTab: FlowPhoneTab;
    /** Library toolbar. */
    query: string;
    folderChip: "all" | "mine" | "examples";
    /** Overlays. Only one of tonight/wizard/palette/editSheet is meaningful at
     *  a time, but they are independent booleans because Overlay owns its own
     *  focus trap and stacking. */
    tonightOpen: boolean;
    tonightTab: TonightTab;
    wizardOpen: boolean;
    paletteOpen: boolean;
    notesOpen: boolean;
    logOpen: boolean;
  };
}
```

Initial value:

```ts
const FLOWS_INIT: FlowsState = {
  cards: [], folders: [], libraryLoaded: false, libraryError: null,
  record: null, graph: { nodes: [], edges: [] }, dirty: false,
  sel: null, editNode: null,
  pan: { x: 24, y: 12 }, zoom: 0.92,        // prototype line 794
  wire: null, tapWire: null,
  statuses: {},
  run: { phase: "idle", etaS: null, curStage: "—", frames: 0,
         frameGoal: null, acceptedUnmapped: [] },
  logs: [],
  compiled: null, compiling: false,
  tonight: null, tonightLoading: false, tonightError: null,
  calHealth: null,
  ui: { screen: "library", phoneTab: "flow", query: "", folderChip: "all",
        tonightOpen: false, tonightTab: "timeline", wizardOpen: false,
        paletteOpen: false, notesOpen: false, logOpen: false },
};
```

Actions on `AppState` (flat, like every other action in the store):

```ts
  // ── library
  flowsLoadLibrary: () => Promise<void>;
  flowsOpen: (id: string) => Promise<void>;
  flowsLeaveEditor: () => Promise<void>;     // saves if dirty && !readonly

  // ── graph edits (each sets dirty and schedules a debounced compile)
  flowsAddNode: (type: FlowNodeType, at: { x: number; y: number }) => void;
  flowsMoveNode: (id: string, x: number, y: number) => void;
  flowsSetParam: (id: string, key: string, raw: string) => void;
  flowsDeleteSel: () => void;
  flowsConnect: (from: string, fromPort: string,
                 to: string, toPort: string) => void;   // replace-on-occupied
  flowsSetName: (name: string) => void;

  // ── selection / editing
  flowsSelect: (sel: FlowsState["sel"]) => void;
  flowsSetEditNode: (id: string | null) => void;

  // ── viewport
  flowsSetPan: (pan: { x: number; y: number }) => void;
  flowsSetZoom: (zoom: number, pan?: { x: number; y: number }) => void;
  flowsFit: (rect: { width: number; height: number }) => void;

  // ── wiring
  flowsBeginWire: (w: PendingWire) => void;
  flowsMoveWire: (to: { x: number; y: number }) => void;
  flowsEndWire: (drop: { nodeId: string; portId: string } | null) => void;
  flowsTapPort: (nodeId: string, portId: string, dir: "in" | "out") => void;

  // ── server
  flowsCompile: () => Promise<void>;         // debounced by the caller
  flowsFetchTonight: () => Promise<void>;
  flowsFetchCalHealth: () => Promise<void>;
  flowsRun: (acceptUnmapped?: boolean) => Promise<void>;
  flowsStop: () => Promise<void>;

  // ── ui
  flowsSetUi: (patch: Partial<FlowsState["ui"]>) => void;
```

Every writer follows this shape, which is what keeps sibling references stable:

```ts
flowsSetPan: (pan) =>
  set((s) => ({ flows: { ...s.flows, pan } })),
```

`flowsSetParam` reproduces `setParam`'s coercion **exactly** (prototype lines 1047–1055) — the coercion keys off the type of the **default**, which is why `capture.bin` stays the string `"1"` and every numeric field silently reverts to its default on unparseable input:

```ts
flowsSetParam: (id, key, raw) => set((s) => {
  const node = s.flows.graph.nodes.find((n) => n.id === id);
  if (!node) return {};
  const base = NODE_DEFS[node.type].params[key];
  const v = typeof base === "number"
    ? (Number.isNaN(parseFloat(raw)) ? base : parseFloat(raw))
    : raw;
  return { flows: { ...s.flows, dirty: true, graph: { ...s.flows.graph,
    nodes: s.flows.graph.nodes.map((n) =>
      n.id === id ? { ...n, params: { ...n.params, [key]: v } } : n) } } };
}),
```

`flowsConnect` reproduces the replace-on-occupied rule (prototype line 1003; `models.py:121` calls it out as the invariant the server relies on — *"ONE WIRE PER INPUT. The editor enforces this by REPLACING on drop"*):

```ts
edges: s.flows.graph.edges
  .filter((e) => !(e.to === to && e.toPort === toPort))
  .concat([{ id: nextEdgeId(), from, fromPort, to, toPort }]),
```

## B.3 The selectors — how one node re-renders

The pattern is the store's own, quoted from `store.ts:2038-2042`:

> *Narrow selector hooks (reliability §13). Subscribing to a single slice means a guide tick (which mutates only `guide`) re-renders only guide consumers, not the whole tree. Prefer these over a broad `useStore()` in components.*

and the existing shapes:

```ts
export const useView = () => useStore((s) => s.view);                    // primitive
export const useCamera = () => useStore(useShallow((s) => s.status?.camera ?? null));
export const useFrameSettings = (scope: FrameScope): FrameSettings =>
  useStore(useShallow((s) => s.frameSettings[scope]));
```

**The rule that makes the README's requirement hold:** zustand compares the **selector's return value** with `Object.is`. A selector that returns a `string` is exact — writing `statuses[b]` cannot re-render the subscriber to `statuses[a]`. A selector that returns an object or array needs `useShallow` **or** a store writer that keeps the reference stable.

```ts
// ───────────────────────────────────────── store.ts, selector block (~2043)

// PRIMITIVE selectors — no useShallow, and using it here would be wrong.
// THIS is the one the README's "a node-status tick must re-render ONE node"
// depends on: `statuses` is a flat string map, so the result is a string and
// Object.is is exact. A `flow.node` frame for node B re-renders exactly the
// <FlowNodeCard id="B"> and nothing else on the canvas.
export const useFlowNodeStatus = (id: string): FlowNodeStatus =>
  useStore((s) => s.flows.statuses[id] ?? "idle");

export const useFlowZoom       = () => useStore((s) => s.flows.zoom);
export const useFlowScreen     = () => useStore((s) => s.flows.ui.screen);
export const useFlowPhoneTab   = () => useStore((s) => s.flows.ui.phoneTab);
export const useFlowRunPhase   = () => useStore((s) => s.flows.run.phase);
export const useFlowDirty      = () => useStore((s) => s.flows.dirty);
export const useFlowName       = () => useStore((s) => s.flows.record?.name ?? "");

/** Two booleans, not the sel object — a node card must not re-render when a
 *  DIFFERENT node is selected. */
export const useFlowNodeSelected = (id: string): boolean =>
  useStore((s) => s.flows.sel?.kind === "node" && s.flows.sel.id === id);
export const useFlowEdgeSelected = (id: string): boolean =>
  useStore((s) => s.flows.sel?.kind === "edge" && s.flows.sel.id === id);

// REFERENCE selectors — stable because the writers above never rebuild a
// sibling. `flowsSetPan` replaces `flows` and `pan`; `graph` keeps its
// identity, so the node list does not re-render on a pan.
export const useFlowNodes = () => useStore((s) => s.flows.graph.nodes);
export const useFlowEdges = () => useStore((s) => s.flows.graph.edges);
export const useFlowNode  = (id: string): FlowNodeRec | undefined =>
  useStore((s) => s.flows.graph.nodes.find((n) => n.id === id));   // ⚠ see below

// DERIVED-OBJECT selectors — these BUILD a value, so they need useShallow,
// exactly like useCamera / useGuideRmsByKind do.
export const useFlowPan = () =>
  useStore(useShallow((s) => s.flows.pan));
export const useFlowRun = () =>
  useStore(useShallow((s) => s.flows.run));
export const useFlowUi = () =>
  useStore(useShallow((s) => s.flows.ui));
export const useFlowIssues = (): FlowIssue[] =>
  useStore(useShallow((s) => s.flows.compiled?.issues ?? EMPTY_ISSUES));
export const useFlowUnmapped = (): FlowUnmapped[] =>
  useStore(useShallow((s) => s.flows.compiled?.unmapped ?? EMPTY_UNMAPPED));
export const useFlowLogs = () => useStore((s) => s.flows.logs);   // ring is replaced wholesale
```

⚠ **`useFlowNode` is a trap and must not be used inside `FlowNodeCard`.** `.find()` returns the same object reference as long as `flowsMoveNode` maps only the moved node — which the writer above does — so it is *usually* fine. But a single careless `nodes.map(n => ({...n}))` anywhere makes every node re-render on every edit with no error. **Pass the node object down as a prop from the one component that subscribes to `useFlowNodes()`**, and let `FlowNodeCard` subscribe only to `useFlowNodeStatus(id)` and `useFlowNodeSelected(id)`. `React.memo` on `FlowNodeCard` then does the rest.

```tsx
// FlowCanvas.tsx — the ONE subscriber to the node array
const nodes = useFlowNodes();
return <>{nodes.map((n) => <FlowNodeCard key={n.id} node={n} />)}</>;

// FlowNodeCard.tsx
export default React.memo(function FlowNodeCard({ node }: { node: FlowNodeRec }) {
  const status   = useFlowNodeStatus(node.id);
  const selected = useFlowNodeSelected(node.id);
  …
});
```

`EMPTY_ISSUES` / `EMPTY_UNMAPPED` are module-level frozen constants — a fresh `[]` in a selector defeats `useShallow` on the first render of every consumer.

## B.4 The WS event switch — the exact additions

`handleEvent`'s switch is a literal `switch` at `store.ts:1649` with **no `default:` clause**, so an unknown `ev.type` is silently dropped and a typo in a case label is invisible. Three insertions, placed among the peers:

```ts
      // ── Flows: per-node run status. The canvas animates from truth, not
      //    from a client-side script (README §6 / backend item 3).
      case "flow.node": {
        const nodeId = String(ev.data.node_id ?? "");
        const status = String(ev.data.status ?? "idle") as FlowNodeStatus;
        if (!nodeId) break;
        set((s) => {
          // Ignore frames for a flow that is not open, or the canvas would
          // light up nodes belonging to somebody else's run.
          if (ev.data.flow_id && s.flows.record?.id !== ev.data.flow_id) return {};
          if (s.flows.statuses[nodeId] === status) return {};   // no-op write
          return { flows: { ...s.flows,
            statuses: { ...s.flows.statuses, [nodeId]: status } } };
        });
        break;
      }
      case "flow.log": {
        set((s) => ({ flows: { ...s.flows,
          logs: [...s.flows.logs.slice(-119), {
            id: (s.flows.logs[s.flows.logs.length - 1]?.id ?? 0) + 1,
            ts: Number(ev.data.ts ?? ev.ts ?? Date.now() / 1000),
            msg: String(ev.data.msg ?? ""),
            tone: (String(ev.data.tone ?? "info") as FlowLogTone),
          }] } }));
        break;
      }
```

and inside the **existing** `case "log":`, after the current body — because today the run route's only output rides that event with `source: "flow"` (app.py:3798):

```ts
        if (source === "flow") {
          set((s) => ({ flows: { ...s.flows,
            logs: [...s.flows.logs.slice(-119), {
              id: (s.flows.logs[s.flows.logs.length - 1]?.id ?? 0) + 1,
              ts: ev.ts, msg: String(ev.data.message ?? ""),
              tone: level === "error" ? "bad" : level === "warning" ? "warn" : "info",
            }] } }));
        }
```

**Nothing changes in `ws.ts`.** Dispatch is purely by `ev.type`; the transport is agnostic. The only reason to touch it would be reconnect rehydration, which `ws.ts:81-93` does by replaying cold GETs *through* `handleEvent` — and there is no `/api/flows/run-state` GET to replay.

⚠ **`case "flow.node"` and `case "flow.log"` will never fire today.** `git grep "bus.publish(" -- server/astrodeck/**/*.py` yields exactly eleven topics: `config, focus, guide, guide_assistant, mount, polar, reconnect, report, sequence, update, weather`. Build the cases; leave `// TODO(flows-handoff): the server does not publish flow.node / flow.log yet — see §G-1.` §G-1 is the blocking question.

Note `handleEvent` opens with `if (intakeBlocked(get().authGate)) return;` — Flows events are rig telemetry and correctly inherit that drop-while-locked behaviour.

**Toasts: use the existing store field.** The README's `toasts` in the state list is satisfied by `AppState.toasts` + `enqueueToast`, which is already mounted once as `<Toasts/>` in App's `.overlay-top`. A second toast array would be a second host and a second stacking context. Toast mapping:

| Prototype tone | `enqueueToast` |
|---|---|
| `good` | `{ level: "success", title: … }` — TTL 3000 |
| `warn` | `{ level: "warning", … }` — TTL 6000 |
| `bad` | `{ level: "error", … }` — TTL 10000 |
| default/info | `{ level: "info", … }` — TTL 4000 |

⚠ The prototype's TTL is a flat **4200 ms** for every tone; the app's defaults are 3000/4000/6000/10000 and dedupe within 5000 ms. The app's ladder wins on authority-4 grounds (there is one toast system), but this is a visible behavioural delta from the prototype — note it in the final summary.

---

# C. COMPONENT-BY-COMPONENT SPEC, IN BUILD ORDER

Colour rule throughout: **never a hex where a token exists.** Tailwind bridge names from `index.css:174-195` — `bg-bg bg-raise bg-panel border-line border-line2 text-ink text-dim text-faint text-accent text-accent2 text-good text-warn text-bad text-sky bg-accent-fill font-display font-sans font-mono`. For SVG and for the alpha variants the design uses, write `var(--accent)` / `color-mix(in srgb, var(--accent) 45%, transparent)` rather than re-typing `rgba(0,210,255,0.45)`.

Category → token, from README §"Design tokens" (`sources --accent`, `equipment+rig --sky`, `logic --accent-dim`, `actions --warn`, **`abort --bad`**, `sinks --good`):

| `cat` | token | nodes |
|---|---|---|
| SOURCE | `--accent` | dusk, target, safety, cloudwatch |
| RIG | `--sky` | dome, flatpanel, slew, autofocus, guide, capture, duskflats, calib |
| LOGIC | `--accent-dim` | pool, condition |
| ACTION | `--warn` | holdresume, notify, refocus |
| ACTION **(exception)** | `--bad` | **abort** |
| SINK | `--good` | report |

The `abort` exception has no representation on the server (`nodes.py`'s `CATEGORY_TOKEN` maps `ACTION → --warn` flat), so `nodeDefs.ts` carries a per-node `colorVar` field, not a category lookup.

---

## C.0 `nodeDefs.ts` — the vocabulary (build first; everything reads it)

Shape:

```ts
export interface PortDef { id: string; label: string; kind: PortKind; optional?: true }
export interface FieldDef {
  key: string; label: string;
  control: "select" | "text";
  options?: readonly string[];
  unit?: string;
}
export interface NodeDef {
  type: FlowNodeType;
  label: string;                       // "DUSK WINDOW"
  cat: "SOURCE" | "RIG" | "LOGIC" | "ACTION" | "SINK";
  colorVar: string;                    // "--accent" … per-node, NOT per-cat
  ins: readonly PortDef[];
  outs: readonly PortDef[];
  params: Readonly<Record<string, string | number>>;
  fields: readonly FieldDef[];
  desc: string;
  sum: (p: Record<string, string | number>) => string;
}
export const NODE_DEFS: Record<FlowNodeType, NodeDef>;
```

**The full contract table** (verified codepoint-for-codepoint against `nodes.py`: 19 types, zero differences in type set, `label`, `cat`, port ids/labels/kinds/order, param keys/order/values/types):

| type | label | cat | color | ins (id·label·kind) | outs (id·label·kind) |
|---|---|---|---|---|---|
| `dusk` | DUSK WINDOW | SOURCE | `--accent` | — | `window`·"window opens"·flow |
| `target` | TARGET | SOURCE | `--accent` | `arm`·"arm"·flow | `target`·"target"·flow |
| `safety` | SAFETY MONITOR | SOURCE | `--accent` | — | `unsafe`·"unsafe"·**event** |
| `cloudwatch` | CLOUD WATCH | SOURCE | `--accent` | — | `in`·"clouds in"·**event**; `clear`·"clouds clear"·**event** |
| `dome` | DOME CONTROL | RIG | `--sky` | `run`·**"open"**·flow | `open`·"shutter open"·flow |
| `flatpanel` | FLAT PANEL | RIG | `--sky` | — | `ready`·"panel ready"·**event** |
| `slew` | SLEW + CENTER | RIG | `--sky` | `run`·"run"·flow | `centered`·"centered"·flow |
| `autofocus` | AUTOFOCUS | RIG | `--sky` | `run`·"run"·flow | `focused`·"focused"·flow |
| `guide` | GUIDE | RIG | `--sky` | `run`·"run"·flow | `guiding`·"guiding"·flow |
| `capture` | CAPTURE LOOP | RIG | `--sky` | `run`·"run"·flow | `complete`·"complete"·flow; `frame`·"frame graded"·**event** |
| `duskflats` | DUSK FLATS | RIG | `--sky` | `run`·"run"·flow | `done`·"flats done"·flow |
| `calib` | CALIBRATION QUEUE | RIG | `--sky` | `do`·"do"·**event**; `stop`·"stop"·**event**; `panel`·"panel"·**event** *(optional)* | — |
| `pool` | TARGET POOL | LOGIC | `--accent-dim` | `arm`·"arm"·flow | `target`·**"best target"**·flow |
| `condition` | CONDITION | LOGIC | `--accent-dim` | `events`·"events"·**event** | `fire`·"fire"·**event** |
| `holdresume` | HOLD / RESUME | ACTION | `--warn` | `pause`·"pause"·**event**; `resume`·"resume"·**event** | — |
| `notify` | NOTIFY | ACTION | `--warn` | `do`·"do"·**event** | — |
| `refocus` | REFOCUS | ACTION | `--warn` | `do`·"do"·**event** | — |
| `abort` | ABORT + PARK | ACTION | **`--bad`** | `do`·"do"·**event** | — |
| `report` | SESSION REPORT | SINK | `--good` | `session`·"session"·flow | — |

Two traps: **`dome`'s input id is `run` but its visible label is `"open"`** — the only node where they differ. **`calib.panel` is optional** (`nodes.py`: `optional_ins={"panel"}`) and must be skipped by doctor rule 1.

Param defaults, field metadata (labels, controls, 58 option strings, units) and the 19 `sum()` bodies are transcribed from the prototype's `DEFS` (lines 658–754). The sweep's node-anatomy report carries them in full; reproduce it verbatim, including:

- `capture.bin` default is the **string** `"1"`, not `1`.
- `capture.goal`'s unit string is literally `"h (0 = none)"`.
- `holdresume.sum` hardcodes `"resume: re-center"` and ignores `p.recenter`. **This is a prototype bug. Reproduce it** (authority 3), and name it in the final summary.
- Units in use: `min`, `°`, `′`, `″`, `% cover`, `frames`, `frames each`, `s`, `h`, `h (0 = none)`.

⚠ **This table is the only place in the product where the field metadata exists.** `NodeDef.__dataclass_fields__` on the server is `['type','label','cat','ins','outs','params','optional_ins']` — no `fields`, no `desc`, no `sum`. Grepping the server tree for `"V-curve sweep"`, `"Translucent lens cap"`, `"IR all-sky camera"`, `"Round robin"` finds only the *default values* in `nodes.py`. §G-4.

---

## C.1 `FlowsView.tsx` — route entry

```tsx
export default function FlowsView(): JSX.Element {
  const screen = useFlowScreen();
  const tier = useFlowsTier();          // "phone" | "tablet" | "desktop"
  return (
    <div data-view="flows"
         className="fill-grow flex flex-col min-h-0 min-w-0">
      {screen === "library"
        ? <FlowLibrary />
        : <FlowEditor tier={tier} />}
    </div>
  );
}
```

`data-view="flows"` lives here, not in App.tsx — it is the harness's outermost marker and this is the minimal place to satisfy it.

**Tier hook.** The design's boundaries (700 / 1080) exist nowhere in this codebase; `index.css` has no `--breakpoint-*` override and `ui/src` has **zero** arbitrary `min-[Npx]:` variants. Drive the tier from JS via the existing `useMediaQuery` primitive (`components/Overlay.tsx:61`), which is the codebase's own escape hatch:

```ts
export function useFlowsTier(): "phone" | "tablet" | "desktop" {
  const desktop = useMediaQuery("(min-width: 1080px)");
  const notPhone = useMediaQuery("(min-width: 700px)");
  return desktop ? "desktop" : notPhone ? "tablet" : "phone";
}
```
⚠ This measures the **viewport**, as the prototype's full-bleed root does. Inside `<main>` the Flows container is ~104px narrower at ≥640px (72px rail + 32px `p-4`), so a 1080px viewport gives Flows ~976px of usable width for a layout the design sizes at 1080. §G-6.

---

## C.2 `FlowLibrary.tsx` + `FlowLibraryCard.tsx` — **ref: `01-library.png`**

```tsx
<div data-flows-tab="library" data-screen-label="Flow library"
     className="fill-grow overflow-y-auto"
     style={{ backgroundImage:
       "linear-gradient(rgba(6,7,11,.82),rgba(6,7,11,.94)),url('/bg_nebula.png')",
       backgroundSize: "cover", backgroundPosition: "center" }}>
  <div className="max-w-[1020px] mx-auto px-5 pt-[38px] pb-[60px]">
    <h1 className="font-display font-semibold text-[22px] tracking-[0.14em]">FLOWS</h1>
    <p className="mt-1.5 text-[13px] text-dim max-w-[560px] [text-wrap:pretty]">…</p>
    <div className="mt-[22px] flex flex-wrap gap-2.5"> {/* toolbar */} </div>
    {sections.map((f) => <FolderSection key={f.name} … />)}
  </div>
</div>
```

**Sub-paragraph, verbatim:** `Visual automation for the rig. Wire targets, windows and sensors into capture stages and rules — a flow compiles to a sequence plan plus when/then instructions and runs on the engine, fail-closed.`

**Toolbar.** Search wrapper `flex-1 min-w-[200px] max-w-[340px] relative`; a 13×13 magnifier inset at `left:10px`, `pointer-events-none`; the input is `className="field pl-[30px] min-h-[40px] rounded-[10px] !text-[12px]"` with placeholder `Filter flows…`. `.field` already supplies `bg-bg`, `border-line`, mono, and `focus:border-accent-dim` — but it also supplies `font-size: 13px` from an **unlayered** rule, so the design's 12px needs `!text-[12px]`. Three chips `All / My flows / Examples`: `min-h-[40px] px-3.5 py-1.5 rounded-full font-mono text-[11px]`, active `border-accent text-accent bg-accent-fill`, inactive `border-line2 text-dim bg-transparent`.

Filter predicate, verbatim from the prototype (line 1513): `!q || (name + " " + tag).toLowerCase().includes(q)`.

**No-match state.** Use `<EmptyState size="inline">`. Title `No flows match “{query}”` (curly quotes, U+201C/U+201D); hint `Try a target name, filter, or technique — or clear the search.` `EmptyState` is static with no pulse, which is what README §2 asks for.

**Folder section.** 12×12 folder glyph (`--text-faint`, `translateY(1px)`) + name (`font-display font-semibold text-[10px] tracking-[0.22em] text-dim`) + count (`font-mono text-[10px] text-faint`), `gap-2 items-baseline mb-3`, section `mt-[26px]`. Grid: `grid-template-columns: repeat(auto-fill, minmax(232px,1fr)); gap: 14px`.

⚠ There is no folder glyph in `icons.tsx` (42 names, verified). §G-9.

**Card** (`data-flow-id={card.id}`), `min-h-[150px] p-4 rounded-2xl bg-panel border border-line backdrop-blur-[14px]`, `box-shadow: inset 0 1px 1px rgba(255,255,255,0.05)`, hover `border-accent` + `0 0 14px var(--glow)`. Four stacked children:
1. name — `font-display font-semibold text-[12.5px] tracking-[0.1em] uppercase`
2. tagline — `text-[12px] text-dim leading-[1.45] flex-1`
3. meta — `font-mono text-[10px] text-faint`, built as `` `${stages} stages · ${wires} wires · ${lastRunText}` ``
4. status row — `<Led state={…} label={…}/>` + `font-mono text-[10px]`; `COMPLETED CLEAN` (`text-good`) or `NEVER RUN` (`text-faint`)

**NEW FLOW card**, first cell of MY FLOWS, only when `!query`: `min-h-[150px] bg-transparent border border-dashed border-line2 rounded-2xl flex flex-col items-center justify-center gap-2 text-dim hover:border-accent hover:text-accent`; `+` at `text-[22px] leading-none` over `NEW FLOW` (`font-display font-semibold text-[11px] tracking-[0.14em]`). Its accessible name contains `NEW FLOW`, which is what the harness's `("click","NEW FLOW")` needs.

---

## C.3 `FlowHeader.tsx` — **ref: `02` (idle) and `07` (running)**

⚠ **§G-5 blocks the shape of this component.** The design specifies a 54px header owning the logo, wordmark and night toggle; the app already renders a 48px header (`App.tsx:492`, `h-12`) containing a logo, a wordmark and — in `HeaderControls.tsx:114-118` — a night toggle. Rendered as a routed view, Flows produces two of each. The spec below is written for the **least-invasive reading** — a view-local toolbar under the app header, with the duplicated chrome dropped — and every dropped element is marked `[DROPPED pending §G-5]`.

Row: `h-[54px] flex-none flex items-center gap-2.5 px-3 border-b border-line bg-raise/70 backdrop-blur-[10px] z-20`.

| # | Element | Condition | Text | Chrome |
|---|---|---|---|---|
| 1 | Back | editor | `‹ LIBRARY` | `.btn !text-[10.5px] !tracking-[0.12em] !px-2.5 !py-[7px]` |
| 2 | Phone title | phone ∧ editor | `{name}` | `font-mono text-[11px] flex-1 min-w-0 truncate` |
| 3 | Logo | — | — | `[DROPPED pending §G-5]` — App's header already has one |
| 4 | Wordmark | — | — | `[DROPPED pending §G-5]` |
| 5 | Sub-line | ¬phone | flow name (editor) | `font-mono text-[10px] text-faint truncate` |
| 6 | spacer | — | — | `flex-1` |
| 7 | Provider badge | ¬phone | `SIMULATOR` | `<span className="prov prov-sim"><span className="dot"/>SIMULATOR</span>` |
| 8 | Validation chip | editor ∧ ¬phone ∧ ¬running | `GRAPH VALID` \| `N OPEN CHECKS` \| `1 OPEN CHECK` | `font-mono text-[10px] tracking-[0.06em] px-2 py-1 rounded-[3px] border`, `text-good`/`text-warn`. Full issue list via `<Tooltip>`. |
| 9 | ETA | running | `ETA ` + `m:ss` | `font-mono text-[11px] tabular-nums`; `ETA ` in `text-dim`, value in `text-ink` |
| 10 | Tonight | editor | `◷ TONIGHT` (phone: `◷`) | `.btn !text-[11px] !tracking-[0.12em]`, `title="Tonight: timeline, plain-English brief, compiled plan"` |
| 11 | Run/Stop | editor | `▶ RUN` / `■ STOP` | see below |
| 12 | Night | — | `☾` | `[DROPPED pending §G-5]` — must be `store.toggleNight`, and there is only one |
| 13 | `i` | — | `i` | `.btn` icon, opens design notes |

**The RUN button's border is `--accent-dim` (purple), not `--accent`.** `runBorder = running ? "#ff5470" : "#9B51E0"`, `runBg = "rgba(0,210,255,0.12)"`, `runColor = "#00D2FF"`. In tokens: `border-accent2 bg-accent-fill text-accent` idle → `border-bad text-bad` + `bg-[color-mix(in_srgb,var(--bad)_12%,transparent)]` running. The same purple border is on `+ ADD STAGE`, `GENERATE FLOW` and `COPY JSON`.

**RUN is a `HonestButton`, never a bare `disabled`.** `POST /api/flows/{id}/run` requires `CAP_CONTROL_MOUNT` (app.py:3714) **and** `hub.require("camera")` (app.py:3791). So:

```tsx
<HonestButton
  reason={
    !canControlMount ? `Running a flow needs ${accessPhrase("control.mount")}.`
    : !cameraConnected ? "No camera is connected, so there is nothing to run this flow on."
    : null}
  onExplain={(r) => enqueueToast({ level: "warning", title: r })}
  onClick={() => flowsRun()}
  className="btn"
>{running ? "■ STOP" : "▶ RUN"}</HonestButton>
```

`■ STOP` stays a plain single-tap — `ui.tsx`'s own header: *"Emergency motion stops (STOP/HALT/polar-STOP) stay single-tap — do NOT route them through HoldButton."*

**The validation chip disappears while running** (`showValid` requires `!running`), which is what frees the space for ETA. **Stage and frames are NOT in this header** — they exist only on the phone MONITOR panel.

⚠ `ETA` has no server source (§G-1) and it is unclear whether it renders on a phone at all — the prototype's markup would render it, README §5 does not list it, and all four phone captures are idle. §G-13.

---

## C.4 `FlowCanvas.tsx` — **ref: `02`, `08`**

See §D for all the math. Structure:

```tsx
<div ref={boxRef} data-flows-canvas
     className="flex-1 relative overflow-hidden min-w-0"
     style={{ touchAction: tier === "phone" && phoneTab === "canvas" ? "none" : undefined,
              backgroundImage:
                "linear-gradient(rgba(6,7,11,.86),rgba(6,7,11,.93)),url('/bg_nebula.png')",
              backgroundSize: "cover", backgroundPosition: "center" }}>

  {/* GRID — a SIBLING of the world layer, so it does NOT pan and does NOT
      zoom. It is a fixed 36px screen-space texture. Reproduced from the
      prototype; see §G-12. It also owns background pointer-down and the
      click-to-deselect. */}
  <div onPointerDown={onBgDown} onClick={onBgClick}
       className="absolute inset-0 cursor-grab"
       style={{ backgroundImage:
         "repeating-linear-gradient(0deg,rgba(120,140,200,0.06) 0 1px,transparent 1px 36px)," +
         "repeating-linear-gradient(90deg,rgba(120,140,200,0.06) 0 1px,transparent 1px 36px)" }} />

  {/* WORLD */}
  <div className="absolute left-0 top-0"
       style={{ transform: `translate3d(${pan.x}px,${pan.y}px,0) scale(${zoom})`,
                transformOrigin: "0 0" }}>
    <FlowWireLayer />           {/* BEFORE the nodes: wires paint UNDER cards */}
    {nodes.map((n) => <FlowNodeCard key={n.id} node={n} />)}
    {selEdge && <FlowWireDelete edge={selEdge} />}
  </div>

  <FlowZoomCluster />           {/* left:12 bottom:44 z-5 */}
  {tier === "tablet" && <AddStageFloat />}   {/* right:12 bottom:44 z-5 */}
  <FlowLogStrip />              {/* inset-x-0 bottom-0 z-6 */}
</div>
```

`touch-action: none` is scoped to the phone CANVAS tab only. The app has been bitten twice by an unconditional `touch-none` (`SkyCanvas.tsx:195-224`: *"two 260px upward swipes on the canvas left `main.scrollTop` at 0 with 1648px of page still below. The user was stranded on the Atlas."*). At tablet/desktop the surface is mouse-driven and does not need it.

⚠ The grid does not pan or zoom. That is the prototype's structure (grid div is a sibling of the world div), the screenshots cannot distinguish it (all captured at rest), and README §3 says only "36px grid". §G-12.

**z-index ladder** (from the prototype, reproduce exactly): header 20 · zoom cluster + ADD-STAGE 5 · log strip 6 · edit sheet 40 · palette sheet 41 · tonight 48 · wizard 49 · design notes 50 · toasts 60. All the ≥40 ones are `Overlay`s and inherit the overlay host's own stacking; only 5, 6 and 20 are authored here.

---

## C.5 `FlowNodeCard.tsx` + `FlowPort.tsx` — **ref: `02`, `03`, `10`**

```tsx
<div data-node-id={node.id} data-node-type={node.type}
     onClick={onSelect}
     className="absolute left-0 top-0 rounded-xl bg-panel border backdrop-blur-[8px]"
     style={{ width: w, transform: `translate3d(${x}px,${y}px,0)`,
              borderColor: borderVar, boxShadow: shadowVar }}>

  {/* header — the drag handle */}
  <div onPointerDown={onDown}
       className="flex items-center gap-[7px] h-8 px-[9px] cursor-grab
                  border-b border-[color-mix(in_srgb,var(--line)_78%,transparent)]">
    <span className="w-[7px] h-[7px] rounded-[2px] flex-none"
          style={{ background: `var(${def.colorVar})`,
                   boxShadow: `0 0 6px var(${def.colorVar})` }} />
    <span className="flex-1 min-w-0 truncate font-display font-semibold
                     text-[9.5px] tracking-[0.14em] text-ink">{def.label}</span>
    <Led state={ledState} label={statusName} />
    <IconButton icon={EDIT_ICON} label="Edit parameters"
                onClick={onEdit} size={12}
                className="!min-w-[20px] !min-h-[20px] !w-5 !h-5 !rounded-[5px]" />
  </div>

  {/* ports */}
  <div className={phone ? "pt-[5px] pb-1" : "pt-[5px] pb-0.5"}>
    {def.ins.map((p, i)  => <FlowPort key={p.id} node={node} port={p} dir="in"  />)}
    {def.outs.map((p, i) => <FlowPort key={p.id} node={node} port={p} dir="out" />)}
  </div>

  {/* footer — hidden on phone in BOTH tabs */}
  {!phone && (
    <div className="px-2.5 pt-[3px] pb-2 font-mono text-[9.5px] text-faint truncate">
      {def.sum(node.params)}
    </div>)}
</div>
```

**Width** 188px desktop/tablet, 150px phone — device-based, not tab-based.
**Background** `--bg-panel`. ⚠ The prototype uses `rgba(12,14,22,0.92)`; `--bg-panel` is `0.85`. README §3 says "`rgba(12,14,22,.85/.92)` → `--bg-panel`", so the token is authorised, at the cost of ~7% opacity. §G-14.
**Border / shadow** are status-driven and **selection outranks status**:

```ts
const borderVar = selected ? "var(--accent)"
  : status === "busy" ? "color-mix(in srgb, var(--accent) 55%, transparent)"
  : "var(--line)";
const shadowVar = selected ? "0 0 16px color-mix(in srgb, var(--accent) 35%, transparent)"
  : status === "busy" ? "0 0 14px color-mix(in srgb, var(--accent) 25%, transparent)"
  : "0 4px 14px rgba(0,0,0,0.45)";
```
⚠ The prototype's idle border is `rgba(120,140,200,0.22)` and the header divider is `rgba(120,140,200,0.14)`; `--line` is `0.18`. Neither 0.22 nor 0.14 has a token. README §3 says "1px border `--line`". Using the token is authorised; the divider's 0.14 is expressed above as a `color-mix` off `--line` so night mode still reaches it. §G-14.

**Height is computed, never declared.** Two formulas exist in the prototype and they disagree by 2px:
- `fit()`: `37 + rows·20 + 26`
- `computeAutoLayout()`: `37 + rows·20 + 8`

Reproduce **both**, in their own call sites — `geometry.fitViewHeight()` and `autoLayout.layoutHeight()`. The rendered DOM stack is `1 + 32 + 5 + 20·rows + 2 + footer(3+~11+8) + 1 ≈ 64 + 20·rows` desktop and exactly `43 + 20·rows` phone. **Do not measure the DOM to lay out** — the spacing model is the formula.

**Port row.** 20px tall, `gap:7px`. Inputs render first (all of them), then outputs — never interleaved.

```tsx
<div className="flex items-center h-5 gap-[7px]"
     style={dir === "out" ? { flexDirection: "row-reverse" } : undefined}>
  <span data-port={`${node.id}|${port.id}|${dir}`} data-port-dir={dir}
        onPointerDown={dir === "out" && !auto ? onStartWire : undefined}
        onClick={auto ? onTapPort : undefined}
        className="flex-none flex items-center justify-center"
        style={{ width: hit, height: hit, cursor: auto ? "pointer" : "crosshair",
                 [dir === "in" ? "marginLeft" : "marginRight"]: -(hit / 2) }}>
    <span className="rounded-full"
          style={{ width: dot, height: dot,
                   border: `1.5px solid var(${portColorVar})`,
                   background: filled ? `var(${portColorVar})` : "var(--bg)",
                   boxShadow: ring }} />
  </span>
  <span className="font-mono text-[9.5px] text-dim">{port.label}</span>
</div>
```

| | canvas (desktop/tablet/phone-CANVAS) | phone FLOW tab |
|---|---|---|
| hit box | **20px** | **26px** |
| overhang | `-10px` | `-13px` |
| visible dot | **9px** | **10px** |
| cursor | `crosshair` | `pointer` |
| handler | `onPointerDown` (outputs only) | `onClick` |
| armed ring | — | `0 0 0 3px color-mix(--accent 25%), 0 0 10px var(<portColor>)` |

Port colour: `flow → --accent`, `event → --warn`. Fill = the port colour when **wired or armed**, else `--bg`.

⚠ Three of the four phone overrides (10px dot, 24px pencil, 26px ✕) and the 16px wire hit path are stated only in the prototype; README §5 names only the 26px port hit. §G-15. ⚠ The armed ring's 3px inner ring is hardcoded **cyan** even on an amber event port — only the outer glow takes the port colour. §G-15.

**LED.** Reuse `<Led state={…}/>`. Mapping: `idle→"off"`, `busy→"busy"`, `ok→"on"`, `warn→"warn"`, `bad→"bad"`.

⚠ The shipped `.led-*` are **11px**, not the README's 9px / 10×2px, and `.led.led-on::after` draws a **checkmark** the README never mentions — added deliberately (`index.css:567-570`: *"night mode maps `--good` to the same red family as `--warn`/`--bad`, so a color-blind or red-filtered glance still needs a positive shape cue here"*). Reusing them is what README §3 asks ("Reuse `.led-*` where possible") and what the Do-not list asks ("keep the LED silhouettes"), at a 2px delta from the screenshots. §G-16.

⚠ `busy` and `ok` share the same silhouette under `prefers-reduced-motion` in the prototype (identical 9px circles, differing only by colour + an animation + a glow) — which breaks the Do-not list's *"everything meaningful must survive `prefers-reduced-motion` as a static cue"*. The shipped `.led-on` checkmark **accidentally fixes this** (ok gets a glyph, busy does not), which is a second argument for reusing `.led-*`. §G-16.

**The ✎ never drags.** `onPointerDown` on the button calls `e.stopPropagation()`. On the phone auto-graph the header has **no `onPointerDown` at all** — no free drag.

---

## C.6 `FlowWireLayer.tsx` + `FlowWireDelete.tsx` — **ref: `02`, `07`, `15`**

```tsx
<svg width="10" height="10"
     className="absolute left-0 top-0 pointer-events-none"
     style={{ overflow: "visible" }}>
  {edges.map((e) => (
    <g key={e.id}>
      {/* the ONLY clickable thing: the parent svg is pointer-events:none and
          the visible path never overrides it */}
      <path data-wire data-edge-id={e.id} d={d} fill="none"
            stroke="transparent" strokeWidth={auto ? 16 : 14}
            style={{ pointerEvents: "stroke", cursor: "pointer" }}
            onClick={(ev) => { ev.stopPropagation(); select(e.id); }} />
      <path d={d} fill="none" stroke={stroke} strokeWidth={width}
            strokeLinecap="round" strokeDasharray={dash}
            className={active ? "flow-wire-march" : undefined} />
    </g>))}
  {pending && (
    <path d={pendD} fill="none" stroke="var(--accent)" strokeWidth={2}
          strokeDasharray="5 5" opacity={0.8} />)}
</svg>
```

Lane is decided by the **source port's kind**, never the target's.

| state | flow lane | event lane |
|---|---|---|
| idle | `color-mix(--accent 45%)`, **1.8px**, solid | `color-mix(--warn 45%)`, **1.8px**, `dasharray 4 5` |
| active | `var(--accent)`, **2.5px**, `dasharray 7 6` + `.flow-wire-march` | `var(--warn)`, same |
| selected | `var(--text)`, **2.5px**, dash per the active/kind rule | same |

`active = running && statuses[e.from] ∈ {"busy","ok"}` — note `ok` too, so a finished stage's outgoing wires keep marching for the rest of the run. That is what capture `07` shows (the whole dusk→dome→duskflats→target→slew chain lit at once), and it is deliberate.

**The pending wire is cyan unconditionally**, even when dragged from an amber event output. ⚠ §G-17.

**Delete control** — `data-flows-wire-selected`, a sibling of the nodes **inside** the world transform (so it scales with zoom):

```tsx
<button data-flows-wire-selected title="Delete wire" onClick={onDeleteSel}
  className="absolute left-0 top-0 rounded-full flex items-center justify-center
             border border-bad bg-raise text-bad cursor-pointer"
  style={{ width: sz, height: sz, fontSize: sz === 26 ? 12 : 11,
           transform: `translate3d(${mx}px,${my}px,0) translate(-50%,-50%)` }}>✕</button>
```
22px canvas / 26px phone FLOW. Position is the straight-line midpoint of the two **port anchors**, `((p1.x+p2.x)/2, (p1.y+p2.y)/2)` — which is exactly `B(0.5)` for both bezier forms because both use symmetric control offsets. The button always sits on the curve.

---

## C.7 `FlowPalette.tsx` — **rail ref: none exists** (§G-7) · **sheet ref: `14`**

Rail (desktop only): `w-[192px] flex-none border-r border-line overflow-y-auto px-2.5 py-3 flex flex-col gap-3.5 bg-[color-mix(in_srgb,var(--bg-raise)_60%,transparent)]`. Group headers `font-display font-semibold text-[9.5px] tracking-[0.2em] text-faint px-1 pb-0.5`. Items: 7×7 dot (`rounded-[2px]`, `0 0 5px` glow) + `font-mono text-[10.5px] tracking-[0.03em]`, row `px-2 py-[7px] rounded-lg border border-transparent`, hover `border-line2 bg-raise`, `title={def.desc}`. Footer hint (11th child), `text-[10.5px] text-faint border-t border-line pt-2.5`:
`Click to drop a stage on the canvas, then drag from a port to wire it.`

Groups and order — **group names and group order are agreed by all three sources**:
`SOURCES` [dusk, target, safety, cloudwatch] · `EQUIPMENT` [dome, flatpanel] · `RIG OPS` [slew, autofocus, guide, capture, duskflats, calib] · `LOGIC` [⚠] · `ACTIONS + SINKS` [⚠]

⚠ Item order inside LOGIC and ACTIONS + SINKS is disputed three ways and no source is right about both groups. §G-3. **Do not pick one.**

Sheet variant: identical list, `Overlay variant="sheet"`, 8px dots, `min-h-[44px]` rows, `data-flows-palette` on the surface.

⚠ **`data-node-type` note:** the calibration-queue selector uses `[data-node-type='calib']`. The palette items must therefore **not** carry `data-node-type`, or `("select-node-type","calib")` would resolve to a rail item at 1440px and click the palette instead of the canvas node. Give palette items `data-palette-type` instead.

---

## C.8 `FlowInspector.tsx` + `FlowFieldRow.tsx` — **ref: `03`** ⚠ *(the reference does not contain an inspector — §G-7)*

Column: `w-[284px] flex-none border-l border-line overflow-y-auto px-3.5 pt-3.5 pb-6 flex flex-col gap-3 bg-[color-mix(in_srgb,var(--bg-raise)_60%,transparent)]`, `data-flows-inspector`.

**Which node it shows:** `inspNode = tier === "desktop" ? selNode : editNode`. Selection and editNode are separate state; on tablet, selecting a node gives you an accent border and **nothing else** — only the ✎ opens the sheet.

**Selected state:**
1. Title row `gap-2`: 7×7 dot → label (`font-display font-semibold text-[11px] tracking-[0.16em]`) → category chip (`font-mono text-[9px] px-1.5 py-[3px] rounded-[3px] border border-dashed border-line2 text-dim`), text = `def.cat`. ⚠ DOME CONTROL and FLAT PANEL read **RIG** here while the rail files them under EQUIPMENT — both sides agree, it is not a bug.
2. Description — `text-[11.5px] text-dim leading-[1.5] [text-wrap:pretty]`.
3. `<CalibrationMatrix/>` — **only when `inspNode.type === "calib"`**.
4. Fields — one `<FlowFieldRow>` per `def.fields`, in DEFS order.
5. `DELETE STAGE` — `mt-1.5 font-display font-semibold text-[10.5px] tracking-[0.12em] rounded-[10px] border border-[color-mix(in_srgb,var(--bad)_45%,transparent)] bg-transparent text-bad px-3 py-2`, hover `border-bad` + `0 0 12px color-mix(--bad 28%)`.

**Nothing-selected (FLOW overview):**
- `FLOW` (`font-display font-semibold text-[10px] tracking-[0.22em] text-dim`)
- `NAME` `<Field>` + text input bound to the flow name
- 2-col grid `STAGES` / `WIRES`, labels `.label`, values `font-mono text-[13px]`
- `CHECKS` + one `font-mono text-[10.5px]` line per doctor issue, coloured by `level`; when clean, one line `✓ all inputs wired` in `text-good`
- ⚠ **UNMAPPED** — see below
- closing hint, `text-[11px] text-faint border-t border-line pt-2.5`:
  `Select a stage to edit its parameters. Drag from a right-side port onto a left-side port to wire. Cyan ports carry the run cursor; amber ports carry events.`

⚠ **`unmapped[]` has no designed surface.** README §8 describes only the doctor's ten `issues`. The compile endpoint returns four lists and `unmapped` is the one that stands between the operator and a night that silently does less than the canvas draws (`to_plan.py`'s module docstring: *"a naive hand-off produces a plan that validates clean and starts a run immediately, in daylight, with no altitude gate, no dawn stop, no dome policy and none of the cloud rules the operator drew. Green start, wrong night, no error anywhere."*). **Recommended surface, requiring sign-off (§G-2):** a fourth block in the FLOW overview headed `NOT HONOURED BY A RUN`, one `font-mono text-[10.5px]` line per entry using `detail`, coloured `text-warn`/`text-bad` from `level`; plus the run-time confirm in §C.13. Do not fold it into `CHECKS` — a doctor issue is advice about a *good night*; an unmapped entry is a statement that a drawn feature *will not happen*.

⚠ `structural[]` likewise has no designed surface. It is normally empty (the editor cannot produce a structurally invalid graph — replace-on-occupied and the kind check see to that), so a `danger`-toned line in the same block is the cheapest honest home. §G-2.

**Field rows.** Exactly two control kinds. The caption is uppercased **by CSS, not in the data** — reuse `.label` (`font-size:11px; letter-spacing:0.18em; text-transform:uppercase; color:var(--text-dim); font-weight:500`).

⚠ `.label` is 11px; the design's inspector caption is Chakra 9.5px/500/0.18em. `.label` is IBM Plex Sans, not Chakra. The 10→11px bump was made deliberately "for AA legibility" (`index.css` comment). Reusing `.label` is the convention; matching the design needs `!text-[9.5px] font-display`. §G-16 covers the same class of decision.

Controls, per tier:

| | desktop column | tablet/phone sheet |
|---|---|---|
| select | `.field !text-[12px] !py-[7px] !px-[9px] !rounded-none` | `.field !text-[13px] !p-2.5 min-h-[44px]` |
| text | same, `focus:border-accent-dim` | same + `min-h-[44px]` |
| unit suffix | `font-mono text-[10.5px] text-faint flex-none` | `text-[11px]` |

The `!` prefixes are mandatory: `index.css` is **unlayered**, so `.field`'s `font-size:13px` and `width:100%` beat any Tailwind utility regardless of specificity or order. This trap is recorded three separate times in that file (tooltip portal, `.overlay-surface` max-width, `.field-rig` width — the last one says four passes edited a `w-24` class that never applied and "nothing ever changed on screen").

There is no validation, no min/max, no disabled state anywhere in the design's inspector. Do not add one.

---

## C.9 `CalibrationMatrix.tsx` — **ref: `03`** ⚠ *(not present in the reference)*

```tsx
<div data-calibration-matrix
     className="border border-line rounded-[10px] p-2.5 flex flex-col gap-1.5
                bg-[color-mix(in_srgb,var(--bg)_50%,transparent)]">
  <div className="font-display font-semibold text-[9px] tracking-[0.2em] text-faint">
    LIBRARY HEALTH</div>
  {rows.map((r) => (
    <div key={…} className="grid items-center gap-1.5 font-mono
                            text-[9.5px] lg:text-[9.5px]"
         style={{ gridTemplateColumns: "44px 1fr 42px 56px" }}>
      <span className="text-faint">{r.label}</span>
      <span className="text-dim truncate">{r.summary}</span>
      <span className="text-ink text-right">{r.quantity}</span>
      <span className="text-right" style={{ color: verdictVar(r.verdict) }}>
        {r.verdict}</span>
    </div>))}
  <div className="text-[10px] text-faint leading-[1.45]">
    Drives the queue's 'if stale' decisions — mirrors the calibration library.</div>
</div>
```

Sheet variant: same grid, `text-[10px]`.

`verdictVar`: `OK → var(--good)`, `STALE → var(--warn)`, `MISSING → var(--bad)`. The **server sends no colour** — the token mapping is the UI's, which is exactly what the Do-not list requires.

Server → column mapping, all confirmed against `calibration_health.py`:

| col | width | server field | notes |
|---|---|---|---|
| 1 | 44px | `label` | `KIND_LABEL = {"DARK":"DARKS","BIAS":"BIAS","FLAT":"FLATS"}` (line 104) — reproduces the mixed plural exactly |
| 2 | 1fr | `summary` | ⚠ see below |
| 3 | 42px | `quantity` | `f"{have}/{need}"` — docstring: *"The prototype's `q` column, verbatim: `14/20`"* |
| 4 | 56px | `verdict` | `VERDICT_OK/STALE/MISSING` (lines 106-108) |

`KIND_ORDER = ("DARK","BIAS","FLAT")` is pinned to the compiler's `["dark","bias","flat"]`, so the matrix reads top-to-bottom in the queue's shooting order.

⚠ **Column 2 cannot reproduce the screenshot for BIAS and FLAT.** The fixture shows `g100 · set of 40` and `Ha · this rotation`; the server's `summary` emits `g100 · −5°C` and `Ha g100 · PA 23°`, and its own docstring (lines 271-279) argues the divergence out loud. The DARK row **does** match byte-for-byte, including U+2212. §G-10 — **do not synthesise a third phrasing.**

⚠ **The server computes an entire evidence vocabulary with nowhere to go:** `reasons[{code,text}]` over the closed set `contradicted/drift/short/age`, plus `family`, `contradicted`, `measured`, `newest_ts`, `age_days`, `master_id`, `from_master`, and at route level `planned`, `counts_masters_only: true`, `assumed: {offset: 30, temp_c: null}`. Two of those are honesty flags whose whole purpose is to be seen: `counts_masters_only` means `have` counts stacked masters only and will read MISSING where raw subs exist; `planned: false` means there are no lights planned yet and the module's docstring insists it **must not** be drawn as a healthy empty matrix. `flowsApi.calibrationHealth`'s own comment repeats it. Four columns and no tooltip is the whole design. §G-10.

⚠ Row count is unbounded server-side (one per kind × distinct need); the prototype hardcodes four. No max-height or overflow rule exists anywhere. §G-10.

---

## C.10 `FlowEditSheet.tsx` / `FlowPaletteSheet.tsx` — **ref: `13`, `14`**

Both go through `Overlay`. **Size must ride the CSS vars, never Tailwind classes** — `Overlay.tsx:122-131` records the measurement: *"index.css is unlayered, so `.overlay-surface { max-width: … }` beats any Tailwind `sm:max-w-*` on the same element no matter the order — measured the hard way: the preflight rendered 788px wide on an 820px tablet because the authored clamp ate the utility."*

```tsx
<Overlay open={!!editNode} variant="sheet" label="Edit stage"
         onClose={() => setEditNode(null)}
         surfaceStyle={{ "--ov-max-h": "76dvh" } as CSSProperties}
         surfaceClassName="…" bodyClassName="px-4 pt-3.5 pb-[calc(20px+env(safe-area-inset-bottom,0px))]"
         head={<header data-flows-editsheet>…</header>}>
  <FlowInspector variant="sheet" nodeId={editNode} />
</Overlay>
```

`Overlay`'s sheet default is `--ov-max-h: 85dvh`; the design's 76dvh must be passed. Escape close, focus trap, scrim, body portal and the `.overlay-safe-b` padding all come free — which is what satisfies README §3's *"Esc should close overlays (add in production)"*.

⚠ `data-flows-editsheet` must be on a **visible** element inside the portal. Put it on the sheet's `head` element, which always renders and always has a box.

---

## C.11 `TonightPanel.tsx` + the three tabs — **ref: `04`, `05`, `06`**

```tsx
<Overlay open={tonightOpen} variant="center" label="Tonight"
         onClose={close}
         surfaceStyle={{ "--ov-max-w": "880px", "--ov-max-h": "90dvh" } as CSSProperties}
         head={<TonightHead tab={tab} onTab={setTab} />}>
  <div data-flows-tonight={tab}>{ tab === "timeline" ? <TonightTimeline/>
                                : tab === "story"    ? <TonightStory/>
                                :                      <TonightPlan/> }</div>
</Overlay>
```

Head bar: `px-4 py-3 border-b border-line gap-2 flex-wrap` — `◷ TONIGHT` (`font-display font-semibold text-[11px] tracking-[0.22em] text-ink`) · flow name (`font-mono text-[10px] text-faint`) · `flex-1` · three pills · `✕` (`min-w-[40px] min-h-[34px] rounded-lg border border-line2 text-dim`).

Pills, **in this order and verbatim**: `TIMELINE`, `STORY`, `PLAN`. `font-display font-semibold text-[9.5px] tracking-[0.14em] px-2.5 py-1.5 rounded-lg bg-transparent`; active `border-accent text-accent`, inactive `border-line2 text-dim`. Default `timeline`.

⚠ The design's panel has **square corners** (no `border-radius`). `.overlay-surface` may impose one; if it does, override via the surface class, not by editing `index.css`.
⚠ The pills must expose `role="tab"` **or** be plain `<button>`s with the label as their accessible name, or the harness's `("tab","TIMELINE")` step cannot resolve. `SegmentedControl` is the house primitive for this shape — verify at build time that it emits one of those two, and fall back to plain buttons if not.

### `TonightTimeline.tsx`

`<svg viewBox="0 0 1000 150" width="100%" preserveAspectRatio="none" className="block">` inside a `relative` wrapper, with **every text label in an absolutely-positioned HTML layer** over it (`absolute inset-0 pointer-events-none`, each label `left:{l}% top:{t}% translate(-50%,-50%) font-mono text-[9.5px] leading-none whitespace-nowrap`). README §7 is emphatic: *"do NOT put text inside the SVG (the prototype hit invisible-glyph bugs)"* — and gate 4 of the harness exists precisely to catch SVG text that measures fine and paints nothing.

Draw order: axis line `y=118`, `x 0→1000`, `--line-bright` 1px → hour ticks `y1=114 y2=122` → blocks (`rx=3`) → altitude arcs (`stroke-width 1.3`) → dashed verticals `y1=28 y2=118 dasharray 3 3`.

Geometry, with `X(t) = round(t/570*1000)`, t = minutes after 20:00:

| element | rect | fill / opacity |
|---|---|---|
| twilight left | `x=0 w=X(118) y=36 h=70` | `--sky` @ .08 |
| twilight right | `x=X(500) w=1000−X(500) y=36 h=70` | `--sky` @ .08 |
| moon-up bar | `x=X(217) w=1000−X(217) y=22 h=8` | `--text-faint` @ .25 |
| FLATS block | `x=X(52) w=X(66)−X(52) y=36 h=70` | `--sky` @ .4 |
| target blocks | span `130…505` split N ways, 6-unit gutters, `y=36 h=70` | `--accent` @ .16 |
| altitude arc | `M X(x0) 100 Q X(x0+w/2) 48 X(x0+w) 100` | stroke `color-mix(--accent 60%)` |

Dashed verticals + their labels at `top:6%`: `DUSK` `--accent` @ X(41) · `DARK` `--text-faint` @ X(118) · `☾ 71%` `--text-faint` @ X(217) · `FLIP` `--warn` @ X(324) · `DAWN` `--good` @ X(500). Hour labels at `top:88%`, `--text-faint`, `t = 0…540 step 60`. FLATS label at `top:47.33%` (= `(y+h/2)/1.5`), `--text` ink.

Legend, `mt-2.5 gap-3.5 flex-wrap font-mono text-[9.5px] text-faint`, verbatim and in order:
`■ imaging window + altitude arc` (■ `--accent`) · `■ twilight / flats` (■ `--sky`) · `┆ meridian flip` (┆ `--warn`) · `▬ moon up (71%)` (▬ `--text-faint`).

⚠ The prototype's honesty note (`Simulated ephemeris for the saved site — the shipped app renders this from tonight's visibility math, live.`) must be **replaced** in production and there is no server field for its replacement. §G-18.
⚠ The `X(t)` mapping is hardcoded 20:00→05:00. `tonight.py` states outright *"WHAT THIS RETURNS IS TIMES, NOT PIXELS"* and returns absolute unix seconds with no axis bounds. §G-18.
⚠ The moon-up bar cannot be positioned when the moon is already up at dark start: `visibility._moon_rise_set` searches only inside `[dark_start, dark_end]`, so both `rise_unix` and `set_unix` come back `None` while the story prints "Moon is up all night". `tonight.py:428` drops `samples[].moon_alt` from the curve — the one field that would answer it. §G-18.

### `TonightStory.tsx`

`flex flex-col gap-2`; each row `grid gap-2.5 items-baseline` at `grid-template-columns: 60px 1fr`. Time cell `font-mono text-[10px] text-faint text-right`; sentence `text-[12px] leading-[1.5] [text-wrap:pretty]`.

**This maps 1:1 onto the server's `story[]`.** Row shape is `{t_unix, label, msg, tone}` (tonight.py:568). `label` is `""` for a timed row and literally `"ANY"` / `"BUDGET"` / `"—"`, which is exactly the 60px column. `tone` is the closed set `text | dim | faint | good | warn | bad` (tonight.py:72-73) → `--text` / `--text-dim` / `--text-faint` / `--good` / `--warn` / `--bad`. Render `label` when non-empty, else format `t_unix` as `HH:MM`. **The prototype's own story generator becomes dead code — do not port it.**

### `TonightPlan.tsx`

Header row `flex items-center justify-between gap-2.5 mb-2`. Caption `text-[11px] text-dim leading-[1.45]`:
`The literal plan this graph compiles to — SequencePlan targets + steps, automation, and when/then instructions. Nothing here the engine can't run.`
`COPY JSON` — `font-display font-semibold text-[9.5px] tracking-[0.14em] px-2.5 py-[7px] rounded-lg border border-accent2 text-accent` + `bg-[color-mix(in_srgb,var(--accent)_8%,transparent)]`. Toasts `Plan JSON copied` (success) / `Copy blocked by browser` (warning).
Block: `whitespace-pre overflow-auto font-mono text-[10px] leading-[1.55] text-dim bg-bg border border-line rounded-lg p-3 max-h-[56dvh]`.

Content = `JSON.stringify(compiled.plan, null, 2)` — **`payload.plan` only**, not the four-key payload; the caption describes the plan alone. ⚠ §G-19.

⚠ Capture `06` **will not match byte-for-byte**. `compile.py` adds `integration_goal_h` to steps (line 198) and, by an authorised 2026-08-12 deviation, expands `automation.dome` via `DomePolicy.from_node_params`. Tell the eyeball step this in advance or it reads as a regression.

---

## C.12 `FlowLogStrip.tsx` — **ref: `02`, `07`**

`absolute inset-x-0 bottom-0 z-[6]` inside the canvas. Collapsed bar: full-width `<button>`, `h-[30px] px-3 bg-[color-mix(in_srgb,var(--bg)_85%,transparent)] border-t border-line`, containing `LOG` (`font-display font-semibold text-[9px] tracking-[0.2em] text-faint`), the last line (`font-mono text-[10.5px]`, tone-coloured, truncating), and `▾`/`▴` (`font-mono text-[10px] text-faint`). Idle text when there are no lines: `Idle — no events yet` in `text-faint`.

Expanded panel above it: `max-h-[170px] overflow-y-auto bg-[color-mix(in_srgb,var(--bg)_92%,transparent)] px-3 py-2 flex flex-col gap-[3px]`.

Line markup: `<span className="text-faint">{HH:MM:SS}</span>` + **two literal spaces** + `{msg}`, `font-mono text-[10.5px]`. Timestamp via `toLocaleTimeString("en-GB", {hour12:false})`.

**Up to 60 lines, NEWEST FIRST** — `logs.slice(-60).reverse()`. The store ring is 120.

**Log tone → colour differs from the toast ladder**: log default is `--text-dim`; toast default is `--accent`. `good/warn/bad` are the same on both.

⚠ **30px collapsed vs the 44px touch floor.** `Disclosure` — the house progressive-disclosure primitive — hardcodes `w-full tap min-h-[44px]`, so the design's 30px bar either abandons the primitive or violates the Do-not list. §G-20.

---

## C.13 Run header + the unmapped confirm — **ref: `07`**

`FlowEditor`'s root carries `data-flows-run={run.phase}`. Values: `idle | running | holding | stopping`. The harness waits up to **180 s** for `[data-flows-run='holding']`.

`flowsRun()` must handle five refusals from `POST /api/flows/{id}/run`, each with a distinct `ApiError.code`:

| status | `code` | meaning | UI |
|---|---|---|---|
| 422 | `invalid_graph` | `graph.validation_errors()` or `GraphNotRunnable` | toast error with `detail`; the graph is at fault and the editor can name the node |
| 409 | `dome_unmapped` | a connected dome + a dome policy the engine cannot act on | **not clearable.** A modal with the server's `detail` verbatim and one button, `CLOSE`. `accept_unmapped` must not be offered. |
| 409 | `unmapped` | anything else the compile drops | a confirm listing every `unmapped[].detail`, with `RUN ANYWAY` re-calling `flowsRun(true)` |
| 409 | horizon / solar | `{…, target}` | toast error with `detail` |
| 400 | — | `count_mode=accepted` unbounded | toast error |

⚠ The 409 flows are undesigned — no screenshot, no README paragraph. The shapes above are read out of `app.py:3745-3790` and `to_plan.blocking_reasons`. §G-2 must confirm the presentation.

⚠ **The whole run animation has no source.** `run.phase`, `etaS`, `curStage`, `frames` and every `statuses[id]` require `flow.node` / `flow.log`, which do not exist. §G-1. In particular, capture `07` — a required state — cannot be produced today, and task #234 in the tracker independently records *"The engine cannot evaluate cloud, safety or panel triggers — so the M16 example's whole cloud-dodge is inert."*

Every run string the prototype emits (start, per-frame, per-stage pairs, `fireClouds()`'s thirteen ordered lines, the stop line) is a **storyboard for the server's log wording**, not for the client to synthesise. Do not port `fireClouds()`.

---

## C.14 `FlowWizard.tsx` — **ref: `09`**

```tsx
<Overlay open={wizardOpen} variant="center" label="New flow — guided"
         onClose={close}
         surfaceStyle={{ "--ov-max-h": "90dvh" } as CSSProperties}
         head={<header data-flows-wizard>NEW FLOW — GUIDED …</header>}
         foot={<footer>…</footer>}>
```
`variant="center"`'s `sm` geometry already sets `--ov-max-w: 560px` — **nothing to override.** Footer actions go through `foot`, never inside `children` (Do-not list: "footer actions pinned").

Body `flex-1 overflow-y-auto px-[18px] py-4 flex flex-col gap-4`:

1. `WHAT ARE WE DOING TONIGHT?` (`font-display font-semibold text-[9.5px] tracking-[0.18em] text-dim`) + three buttons `flex-1 min-w-[140px] min-h-[44px] px-2.5 py-2 rounded-[10px] font-mono text-[11px]`: **`Deep-sky target`**, **`Best of several`**, **`EAA quick look`**. Default `Deep-sky target`. Selected → `border-accent text-accent bg-accent-fill`.
2. `ADD AUTOMATION` + six pills `min-h-[38px] px-3 py-[7px] rounded-full font-mono text-[10.5px]`, in order: **`Guiding`**, **`Dusk flats`**, **`Dome`**, **`Cloud-dodge calibration`**, **`HFR watchdog`**, **`Notify my phone`**. Default on: `["Guiding","HFR watchdog"]`.
3. `TARGET (OR CANDIDATES, COMMA-SEPARATED)` + a text input, `.field !text-[13px] !p-2.5 min-h-[44px]`, placeholder **with its literal run of spaces**: `M16    ·    or: M16, M17, M8, NGC 6946`.
4. Blurb `text-[11px] text-faint leading-[1.5] [text-wrap:pretty]`:
   `Generates a complete, valid graph — then everything is just stages and wires you can rearrange. The doctor will flag anything risky.`

Footer `flex-none gap-2.5 px-[18px] py-[13px] border-t border-line` (`Overlay`'s `.overlay-safe-b` supplies the safe-area): **`GENERATE FLOW`** `flex-1 min-h-[46px] rounded-[10px] border border-accent2 bg-accent-fill text-accent font-display font-semibold text-[12px] tracking-[0.14em]` and **`START BLANK`** `flex-none min-h-[46px] px-3.5 border border-transparent bg-transparent text-dim font-display font-semibold text-[11px] tracking-[0.12em]`, hover `border-line2`.

Generation rules are in README §9 verbatim and in `genWizard()` (lines 1327-1375). Toast on success: `Flow generated — every stage is editable`. Every generated graph must pass the doctor. Ends with a `fit()`.

⚠ **`wizard.generate()` exists on the server with no route.** Either the wizard is a TS re-implementation (a third transcription of the generation rules, which will drift) or a route is added. §G-2.

---

## C.15 Phone components — **ref: `10`, `10b`, `11`** (milestone-2 scope check: §G-21)

Full spec is in the phone-tier sweep and is not repeated here, but the load-bearing constants:

**Auto-layout** (`autoLayout.ts`), verified to predict the M16 capture exactly (content y `18 / 117 / 236 / 355` → screen y `72 / 171 / 290 / 409`; measured `72 / 169-172 / 290 / 409`):

```ts
const pad = 14, nw = 150;
const Wc = Math.max(300, Math.min(containerWidth, 700));
const colL = pad, colR = Math.max(colL + 56, Wc - pad - nw);
const H = (n) => 37 + rows(n) * 20 + 8;
let y = pad + 4;                                   // 18
flowOrder().forEach((n, i) => { pos[n.id] = { x: i % 2 ? colR : colL, y };
                                y += H(n) + 34; }); // 34 — FLOW LANE ONLY
// then rule clusters (gap 22), then unwired stragglers (gap 22)
height = y + 90;
```
⚠ README §5 says "34px vertical gaps" without scoping it; the code uses 34 for the flow lane and **22** for the other two passes. §G-22.

`Wc` must measure the **container**, not the window — at 667px (phone landscape) the app's 72px rail is present (`hidden sm:flex`) and `p-4` takes 32 more, leaving ~563px.

**Tap-to-wire** (`flowsTapPort`), verbatim strings: `Tap an output (right-side) port first` (warn) · `Can't wire a stage to itself` (warn) · `Flow output can't feed an event input` / `Event output can't feed a flow input` (warn) · `Wired ✓` (good).

**Hint bar text**, exact codepoints verified: `"WIRING: " + def.label + " \u00B7 " + port.label + " \u2014 tap an input port"` — U+00B7 MIDDLE DOT and U+2014 EM DASH, each with a space either side.

⚠ The server's `models.py:117-119` produces `"Event output can't feed an flow input"` — ungrammatical, and a different sentence from the UI's for the same refusal. §G-23.

**Bottom tab bar**: exactly three, in this order — `FLOW`, `CANVAS`, `MONITOR`. Default `FLOW`. Active = 2px `--accent` top border + accent ink. `padding-bottom: env(safe-area-inset-bottom, 0px)`. Switching tabs clears `tapWire`.

⚠ The prototype's own design-notes copy (line 533) and README §"Information Architecture" both still describe the **superseded** rail variant ("linearized rail (phone)", "CANVAS / LIST / MONITOR"). README §5 + the screenshots + line 202 ("superseded by the auto-graph") govern. The dead rail branch (prototype lines 347-419, inside `display:none`) must not be built.

---

# D. THE CANVAS

## D.1 Which existing pointer system to reuse

**`components/atlas/SkyCanvas.tsx` is the model.** It is the app's only pan/zoom/drag surface and it has already paid for every trap. Do **not** invent a second system.

But there is one authority conflict to resolve explicitly: README §"State management" says *"Drag/pan/wire use window-level pointermove/pointerup"*, while `SkyCanvas` uses `el.setPointerCapture(e.pointerId)`. **README (authority 1) wins → use window-level listeners.** Both approaches work with `elementFromPoint` (pointer capture redirects *events*, not coordinate hit-testing), so nothing is lost.

**Reuse, verbatim:**

| From | What | Why it is mandatory |
|---|---|---|
| `SkyCanvas.tsx:866-868` | `(e.target as Element)?.closest?.('[data-role="…"]')` real-element hit-test | Correct at any zoom, no duplicated geometry maths. Exactly the shape `[data-port]` needs. |
| `SkyCanvas.tsx` | `if (e.button !== 0) return;` | *"A right/middle press started a drag whose release the native context menu eats, so nothing ever ended it."* |
| `SkyCanvas.tsx` | `window.blur` + `document.visibilitychange` → `endDrag()` | Alt-tab / OS switch / screen timeout take the gesture away with **no pointer event at all**. |
| `SkyCanvas.tsx` | `if (e.buttons === 0 && e.pointerType !== "touch") { endDrag(); return; }` | Self-heal: a latch left by a release we never saw ends on the first hover. **A pending wire following the cursor with no finger on the glass is exactly this class of latch.** |
| `SkyCanvas.tsx` | `onPointerCancel` clears the tap candidate and ends the drag, and does **not** fire a tap | pointercancel is the browser taking the gesture, not a release. |
| `SkyCanvas.tsx:~195` | the non-passive wheel listener (below) | React registers `onWheel` as **passive**; `preventDefault()` in a React handler is silently ignored. |
| `usePreviewGestures.ts` | the `zoomAt(next, fx, fy)` maths and the 2-pointer pinch | Already exact for "keep the focal point stationary". Copy the function; the hook is coupled to `Viewport`/`fitScale`. |
| `SkyCanvas.tsx` | `TAP_MS` / `TAP_SLOP_PX` tap-vs-drag discrimination | Needed for the phone FLOW tab's tap-to-wire. |

The wheel listener, with ref-mirroring so it never re-attaches and never reads a stale closure:

```tsx
const zoomRef = useRef(zoom); zoomRef.current = zoom;
const panRef  = useRef(pan);  panRef.current  = pan;
useEffect(() => {
  const el = boxRef.current; if (!el) return;
  const handler = (e: WheelEvent) => {
    e.preventDefault();                 // honored: registered with passive:false
    const r = el.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const dir = e.deltaY < 0 ? 1.1 : 0.9;
    const z0 = zoomRef.current;
    const z = Math.min(1.6, Math.max(0.35, z0 * dir));
    const k = z / z0;
    const p = panRef.current;
    setZoom(z, { x: mx - (mx - p.x) * k, y: my - (my - p.y) * k });
  };
  el.addEventListener("wheel", handler, { passive: false });
  return () => el.removeEventListener("wheel", handler);
}, []);
```

## D.2 Pan / zoom maths

**Pan.** Screen-space delta, **not divided by zoom**, so the graph tracks the cursor 1:1 at any zoom:
```
pan = { x: px0 + (clientX - sx), y: py0 + (clientY - sy) }
```
`onBgClick` clears the selection — and it also fires at the end of a background drag, which is the prototype's behaviour; reproduce it.

**Wheel.** Fixed ±10% per notch, ignores `deltaMode` and magnitude. Clamp **0.35 – 1.6**. Anchored under the cursor.

**Buttons.** `zoomIn = zoomBy(1.15)`, `zoomOut = zoomBy(0.87)`. ⚠ `zoomBy` changes `zoom` only — it does **not** re-anchor, so button zoom pivots on the world origin while wheel zoom pivots under the cursor, and 1.15 / 0.87 are not reciprocal. Reproduce verbatim (authority 3); §G-24.

**Pinch** (two pointers on the background), re-anchored on the live gesture midpoint:
```
d0 = hypot(a-b) at start; z = clamp(z0 * d / d0, 0.35, 1.6)
mid = ((ax+bx)/2 - r.left, (ay+by)/2 - r.top)
```

**Fit.**
```ts
const hs = ns.map((n) => 37 + rows(n) * 20 + 26);
const x0 = min(n.x) - 30,  y0 = min(n.y) - 30;
const x1 = max(n.x + 188) + 30,  y1 = max(n.y + hs[i]) + 30;
const z  = clamp(min(r.width / (x1 - x0), (r.height - 40) / (y1 - y0)), 0.35, 1.15);
pan = { x: (r.width - (x1 - x0) * z) / 2 - x0 * z,
        y: (r.height - 34 - (y1 - y0) * z) / 2 - y0 * z };
```
Note the asymmetry and **reproduce it**: the fit **scale** reserves 40px of height, the fit **pan** reserves 34px, and the log strip is 30px — none of the three matches.

**Worked check on M16** (`example-m16`, nodes at the coordinates in `examples.py`): bbox = `x0=0, x1=1168, y0=20, y1=993`. At 924×486 (the reference captures' canvas) → `z = min(1.15, min(924/1168, 446/973)) = 0.4584` → the zoom chip reads **46%**, which is the literal text in captures 02, 03, 07 and 08. `pan ≈ (194.3, −6.2)`, putting DUSK WINDOW's left edge at x≈208 and top at y≈71, and a node 188·0.4584 ≈ **86px** wide on screen. The captures agree. **Use this as the `geometry.test.ts` fixture.**

Initial state before any fit: `pan {x:24, y:12}, zoom 0.92`. A blank new flow gets `zoom 0.95` and **no** fit.

**Node drop position** (`addNode`): `c = toCanvas(r.left + r.width/2, r.top + r.height/2.4)`, then `x = round(c.x - nodeW/2)`, `y = round(c.y)` — horizontally centred, vertically **above** centre. On phone outside the CANVAS tab it instead appends: `x = max(all n.x) + 250, y = 160`.

## D.3 Wires — the bezier, with real numbers

**Port anchors** (`portPos`):
```
in : { x: n.x,          y: n.y + 37 + idx * 20 + 10 }
out: { x: n.x + nodeW,  y: n.y + 37 + (ins.length + outIdx) * 20 + 10 }
```

**Canvas path** — control points are purely horizontal:
```
c = max(46, |p2.x - p1.x| * 0.5)
"M {p1.x} {p1.y} C {p1.x+c} {p1.y}, {p2.x-c} {p2.y}, {p2.x} {p2.y}"
```

**Phone FLOW path** — vertical bias:
```
vy = clamp(|p2.y - p1.y| * 0.4, 24, 70)
"M {p1.x} {p1.y} C {p1.x+18} {p1.y+vy}, {p2.x-18} {p2.y-vy}, {p2.x} {p2.y}"
```
The ±18 is **always +18 on the source and −18 on the target**, regardless of direction — that is what produces the wide S-sweep for a right-column → left-column wire.

**Two real M16 edges, computed. Pin these in `geometry.test.ts`:**

*(a) `n1.window → n16.run` — dusk at (30,50), dome at (260,50).* `dusk` has 0 ins, 1 out → outIdx row 0 → `p1 = (30+188, 50+37+0+10) = (218, 97)`. `dome.run` is input idx 0 → `p2 = (260, 97)`. `dx = 42`, so `c = max(46, 21) = 46`:
```
M218 97 C264 97, 214 97, 260 97
```
Note the control points **cross** (264 > 214) — the design's short-span kink. Do not clamp it away.

*(b) `n4.centered → n5.run` — slew at (950,50), autofocus at (160,320).* `slew` has 1 in + 1 out → the out is row index 1 → `p1 = (950+188, 50+37+20+10) = (1138, 117)`. `autofocus.run` is input idx 0 → `p2 = (160, 367)`. `dx = 978`, so `c = max(46, 489) = 489`:
```
M1138 117 C1627 117, -329 367, 160 367
```
That is the long right-then-back loop visible in capture `02`. **It is the design, not a bug** — do not "fix" the routing.

⚠ **A 1px offset is baked in and README §3 ratifies it.** With `padding:5px 0 2px` and no horizontal padding, the input dot's visual centre is at `n.x + 1` and the output's at `n.x + 187`, while `portPos` returns `n.x` and `n.x + 188` — the wire attaches 1px outside the dot on each side. Vertically the first row's visual centre is `n.y + 48` while `portPos` returns `n.y + 47`. README §3 states the anchor formula as `node.y + 37 + rowIndex·20 + 10`, i.e. it ratifies the offset. **Reproduce it.**

## D.4 Hit testing

**Wires.** Two paths per edge inside one `<svg pointer-events="none">`:
- transparent hit path, `stroke-width` **14** (canvas) / **16** (phone FLOW), `pointer-events: stroke`, `cursor: pointer`, carrying `data-wire` + `data-edge-id` + `onClick`
- the visible path, which never overrides the parent's `pointer-events: none`

So **only** the invisible path is clickable — a clean separation. Both widths are in **world units**: at zoom 0.35 the desktop band is ~4.9 screen px, at 1.6 it is ~22px.

⚠ **Harness risk, with a concrete mitigation.** The harness clicks `page.locator("[data-wire]").filter(visible=True).first`, and Playwright clicks the **centre of the bounding box**. For a curved path the bbox centre may not be on the stroke, so the actionability hit-test would fail or the click would land on the canvas background and *deselect*. Mitigation: render edges in **stored order**, so the first `[data-wire]` in DOM order for `example-m16` is edge (a) above — `M218 97 → 260 97`, perfectly horizontal, bbox centre `(239, 97)` exactly on the stroke, in the 42px gap between two cards, with the 14px hit band covering y 90-104. It resolves. Pin this in `markersDom.test.tsx` so a future reorder of `examples.py` cannot silently break capture 15.

**Ports.** The drop resolves on **pointerup coordinates**, which is what makes it work for touch:

```ts
const el = document.elementFromPoint(e.clientX, e.clientY);
const host = el?.closest?.("[data-port]") ?? null;
if (host) {
  const [nid, pid, dir] = host.getAttribute("data-port")!.split("|");
  if (dir === "in" && nid !== wire.from) {
    const kOut = portKind(wire.from, wire.fromPort, "out");
    const kIn  = portKind(nid, pid, "in");
    if (kOut && kIn && kOut !== kIn) {
      toast(`${kOut === "flow" ? "Flow" : "Event"} output can't feed ` +
            `${kIn === "flow" ? "a flow" : "an event"} input`, "warn");
    } else {
      connect(wire.from, wire.fromPort, nid, pid);   // replace-on-occupied
    }
  }
}
clearWire();
```

Eight rules fall out of that, exactly:
1. `elementFromPoint` + `.closest('[data-port]')` on the **pointerup** coordinates.
2. **Only `dir === "in"` accepts a drop.** There is no reverse drag: input spans have no `onPointerDown` at all.
3. **Self-wiring is refused silently** on drag (`nid !== wire.from`, no toast) — but tap-to-wire **does** toast `Can't wire a stage to itself`. Reproduce the asymmetry.
4. Kind mismatch refuses with a toast, no edge.
5. **Inputs are single-occupancy** (the incumbent is filtered out before concat); **outputs fan out freely** — nothing limits how many edges leave one output.
6. New edge id from one counter shared with node ids.
7. A miss (dropped on empty canvas) clears the pending wire — no toast, no dangling edge.
8. **No toast on a successful drag-drop.** Only tap-to-wire toasts `Wired ✓`.

The `data-port` value has **three pipe-delimited fields with no escaping**, so node ids and port ids must never contain `|`. Server node ids are opaque strings from the client, so `nextNodeId()` must produce `n<counter>` and nothing else.

## D.5 Keyboard

```ts
useEffect(() => {
  const onKey = (e: KeyboardEvent) => {
    const tag = (e.target as HTMLElement)?.tagName?.toLowerCase() ?? "";
    if (tag === "input" || tag === "select" || tag === "textarea") return;
    if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); deleteSel(); }
    if (e.key === "f" || e.key === "F")             { e.preventDefault(); fit(); }   // ⚠ harness
  };
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}, [deleteSel, fit]);
```

Delete/Backspace works on both a selected node and a selected edge; deleting a node removes **every edge touching it**. Escape is handled by `Overlay` (`closeOnEscape` defaults true).

⚠ **`f` = fit is not in any design source.** It exists only because `scripts/flows_visual_check.py:441` does `await page.keyboard.press("f")` with the comment `# fit-to-view, per the editor`. §G-25.

Mark consumed keys, following `Tooltip`'s convention (`ui.tsx:525-529` calls `preventDefault()` on Escape *specifically so an outer dismissable can tell the key was already used*).

## D.6 Animation discipline

Any entry animation uses fill-mode **`backwards`, never `both`**. `index.css:793-825` records the measurement: `both` leaves an identity `matrix()` transform, which makes the element a containing block for `position: fixed`, and that put dialogs at `x=-10` on tablet and `y=899..947` against `vh=900` on desktop.

`.panel` is also a containing block (`backdrop-filter: blur(14px)` + `position: relative`; `index.css:819` records a `fixed inset-0` probe inside a panel resolving to `700x1515`). **Do not host the canvas's floating controls, or anything `fixed`, inside a `Panel`.** The zoom cluster and ADD-STAGE float are `absolute` inside the canvas, which is correct.

---

# E. THE DATA-CAPABILITY TABLE

Column 4 is the point. Every entry there is a place a component would otherwise invent a value.

## E.1 Library — `01-library.png`

| Endpoint | Fields rendered | **Shown by the design, NOT returned by the server** |
|---|---|---|
| `GET /api/flows` → `FlowCard[]` | `id` (→ `data-flow-id`), `name`, `tagline`, `folder`, `readonly`, `stages`, `wires`, `last_run`, `last_result` | The meta line's **format**. Server sends `last_run` as a unix float; the design shows `last run 2026-08-09 · 02:37` and `never run`. Format client-side from `last_run`. **No ambiguity — flagged only so nobody hard-codes the fixture strings.** |
| | | ⚠ **The status row has only two designed states.** `last_result` is `"" \| "ok" \| "warn" \| "bad"` (models.py:150). The design defines `COMPLETED CLEAN` (good) and `NEVER RUN` (faint). **There is no designed string or LED for `warn` or `bad`.** §G-26. |
| `GET /api/flows/folders` → `{name,count,readonly}[]` | section name, count | The design's folder **headings are uppercase** (`MY FLOWS`, `EXAMPLES`); the server sends `"My flows"` / `"Examples"` (models.py:34-35). Uppercase in CSS, not in the data. |
| — | sub-line `Automation library · 5 saved flows` | Count from `cards.length`. ✓ |
| — | folder glyph, magnifier | ⚠ Neither icon exists in `icons.tsx`. §G-9. |

## E.2 Editor canvas — `02`, `08`

| Endpoint | Fields rendered | **Shown but not returned** |
|---|---|---|
| `GET /api/flows/{id}` → `FlowRecord` | `name`, `graph.nodes[{id,type,x,y,params}]`, `graph.edges[{id,from,fromPort,to,toPort}]`, `readonly` | ⚠ **The entire node vocabulary.** Node `label`, `cat`, port `label`/`kind`/order, param field labels, control kinds, 58 select-option strings, 11 unit strings, `desc`, `sum()`. `nodes.py` has half of it and **serves none of it**; the other half exists only in the prototype. §G-4. |
| | | ⚠ **The per-node colour.** `nodes.py`'s `CATEGORY_TOKEN` maps `ACTION → --warn` flat, so a server-derived colouring paints ABORT + PARK amber. The `--bad` exception lives only in the UI table. |

**Edge JSON key is `from`, not `from_`.** `FlowEdge` sets `alias="from"` and FastAPI serialises by alias — `get_flow`'s docstring says hand-dumping with `model_dump()` would emit `from_` and *"every wire in the canvas would vanish with no error anywhere."* Send `from` back on save.

## E.3 Header chip / FLOW overview / doctor

| Endpoint | Fields rendered | **Shown but not returned** |
|---|---|---|
| `POST /api/flows/compile` (draft, debounced) and `POST /api/flows/{id}/compile` → `{plan, structural[], issues[], unmapped[]}` | `issues[{text, level}]` → chip count + CHECKS list + tooltip | — |
| | | ⚠ **`unmapped[{key, detail, level}]` has no designed home.** §G-2 / §C.8. |
| | | ⚠ **`structural[]` has no designed home.** §G-2. |
| | STAGES / WIRES | from `graph.nodes.length` / `graph.edges.length`. ✓ |

⚠ **README §8 says "UI re-checks locally for latency, server is authoritative."** Implementing the ten doctor rules a second time in TypeScript is a third transcription of the same logic and a guaranteed drift source. A 200ms-debounced `compileDraft` gives the same UX with one implementation. §G-27 — **do not build the local mirror without a ruling.**

## E.4 Inspector / LIBRARY HEALTH — `03`

| Endpoint | Fields rendered | **Shown but not returned / returned but not shown** |
|---|---|---|
| — (inspector fields) | local graph + `NODE_DEFS` | see E.2 |
| `GET /api/calibration/health?flow_id=` | `rows[].label`, `rows[].summary`, `rows[].quantity`, `rows[].verdict` | ⚠ **`summary` cannot reproduce the fixture** for BIAS (`set of 40` lives in `quantity`) or FLAT (`this rotation` is really `PA {deg}°`). Only DARK matches. §G-10. |
| | | ⚠ **Returned, no home:** `reasons[{code,text}]`, `family`, `contradicted`, `measured`, `newest_ts`, `age_days`, `master_id`, `from_master`, `exposure_s`, `gain`, `offset`, `temp_c`, `binning`, `filter`, `rotation_deg`, `have`, `need` — and at route level `planned`, `counts_masters_only: true`, `assumed: {offset: 30, temp_c: null}`. The last three are honesty flags whose purpose is to be seen. §G-10. |
| | | ⚠ **Row count is unbounded** server-side (one per kind × distinct need); the design shows four. §G-10. |

## E.5 Tonight — `04`, `05`, `06`

`GET /api/flows/{id}/tonight` returns `{ok, reason, now_unix, twilight_deg, night{…}, flats{…}, moon{…}, targets[…], budget[…], story[…]}`. It is gated on `CAP_VIEW_SITE_DERIVED`, not `CAP_VIEW_STATUS` — *"an audit of this codebase recovered the observatory to 2.9 km from three viewer-legal requests"* (app.py). The header's `◷ TONIGHT` button must therefore be a `HonestButton` for a viewer.

**TIMELINE consumes:** `night.dusk_unix`, `night.dawn_unix`, `night.dark_start_unix`, `night.window_start_unix`, `flats.start_unix`/`end_unix`, `targets[].window`, `targets[].curve`, `targets[].meridian_flip_unix`, `moon.rise_unix`, `moon.illumination`.

**STORY consumes:** `story[{t_unix, label, msg, tone}]` — 1:1, no transformation. `label` is `""` / `"ANY"` / `"BUDGET"` / `"—"`; `tone` ∈ `text|dim|faint|good|warn|bad`.

**PLAN consumes:** `POST /api/flows/{id}/compile` → `payload.plan`. **It does not touch `/tonight` at all.**

**⚠ Shown but NOT provided:**
1. **The moon-up band's start and end in the common case.** `visibility._moon_rise_set` searches only inside `[dark_start, dark_end]`, so a moon already up at dark start returns `rise_unix = None` **and** `set_unix = None`, while the story prints "Moon is up all night". `samples[].moon_alt` would answer it and `tonight.py:428` deliberately drops it.
2. **The axis range and hour ticks.** The prototype hardcodes 20:00→05:00 as `X(t)=t/570·1000`; the server returns absolute unix seconds and no bounds.
3. **The honesty note's production replacement.** The nearest fields are `ok`/`reason`.
4. **`moon up (71%)`** — the literal 71 is a fixture; `moon.illumination` is the real source. ✓ resolvable.

**⚠ Provided but rendered NOWHERE:** `ok`, `reason` (there is **no failure rendering** for the panel — the server can legitimately answer `ok:false` with `No observatory site is set, so there is no night to resolve…` and there is nowhere to put it); `now_unix` (no "now" marker); `twilight_deg`; `night.window_stop_unix`; `night.dark_end_unix` (the right twilight band starts at **dawn**, not dark end); `night.darkness_kind`; `flats.{window,adu_target,count,method}`; `moon.{phase_name,alt,az,set_unix}` (the bar runs to the right edge and never ends); `targets[].{transit_unix,transit_alt,transit_in_daylight}` — **nothing on any tab shows transit**, even though README backend item 4 names it explicitly; `targets[].{coords_from,resolved,ra_hours,dec_deg,pool_rank,never_rises,min_altitude_deg,moon_sep_deg}`, `window.mean_alt`; and `budget[]` as a structure (the BUDGET line is already a `story[]` row).

**⚠ Four degraded states are built server-side and designed nowhere:** `ok:false + reason`; `flats.start_unix/end_unix` null (unparseable window text); `targets[]` with `resolved:false` and an empty `curve`; `budget` rows with `has_ledger:false` / `banked_h:null`. The prototype fabricates banked hours as 35% of the goal (line 1286) and never renders an error. §G-18.

## E.6 Run — `07`

| Endpoint / event | Fields | **Missing** |
|---|---|---|
| `POST /api/flows/{id}/run` → `{started, flow_id, frames, unmapped[]}` | `frames` → the FRAMES denominator | — |
| 409/422 refusals | `detail`, `code`, `unmapped` | ⚠ no designed presentation. §G-2. |
| WS `log` with `source: "flow"` | the log strip's lines | tone is inferred from `level` (`error→bad`, `warning→warn`, else `info`) — the server's `bus.log` has no `tone` |
| **WS `flow.node`** | node status | ⚠ **DOES NOT EXIST** |
| **WS `flow.log`** | `{msg, tone, ts}` | ⚠ **DOES NOT EXIST** |
| — | `run.phase` (incl. `holding`), `etaS`, `curStage` | ⚠ **NO SOURCE AT ALL.** §G-1. |

`git grep "bus.publish(" -- server/astrodeck/**/*.py` → `config, focus, guide, guide_assistant, mount, polar, reconnect, report, sequence, update, weather`. No `flow` topic.

## E.7 Wizard — `09`

| Endpoint | **Missing** |
|---|---|
| — | ⚠ `wizard.generate()` / `generate_record()` / `flow_name()` exist in `server/astrodeck/flows/wizard.py` and **have no route**. Either the generation rules are re-implemented in TS (third transcription) or a route is added. §G-2. |
| `POST /api/flows` | saves the generated record. ✓ |

---

# F. VERIFICATION — the exact `data-*` contract

`scripts/flows_visual_check.py` is already written against these names. **The state table is data, not code — only the selectors below have to resolve.** Every one is quoted from the harness.

## F.1 The complete marker list

| `data-*` | Component | Element | Harness use |
|---|---|---|---|
| `data-view="flows"` | `FlowsView` | root `<div>` | outer scope of state 01 / 20 |
| `data-flows-tab="library"` | `FlowLibrary` | root | states 01, 20 (must be **visible**) |
| `data-flows-tab="editor"` | `FlowEditor` | root | symmetry; unused by the harness |
| `data-screen-label` | `FlowLibrary` / `FlowEditor` | root | README §IA — `"Flow library"` / `"Flow editor"` / `"Flow editor (phone)"` |
| `data-flow-id="{card.id}"` | `FlowLibraryCard` | the clickable card root | `("open-flow","example-m16")` — must be **visible** and clickable |
| `data-flows-canvas` | `FlowCanvas` | the canvas `<div>` | states 02, 08, 12, 15, 21 |
| `data-flows-run="{phase}"` | `FlowEditor` | root | state 07 waits ≤180 s for `='holding'` |
| `data-node-id="{n.id}"` | `FlowNodeCard` | card root | not used by the harness; needed by `markersDom.test.tsx` |
| `data-node-type="{n.type}"` | `FlowNodeCard` | card root | `select-node-type` (click), `edit-node-type` (**dblclick**), `hover-node` |
| `data-port="{nodeId}\|{portId}\|{in\|out}"` | `FlowPort` | the hit `<span>` | drag-drop resolution + `arm-wire` |
| `data-port-dir="{in\|out}"` | `FlowPort` | same `<span>` | `("arm-wire","")` selects `[data-port][data-port-dir='out']` |
| `data-wire` | `FlowWireLayer` | the **transparent hit `<path>`** | `("select-wire","")` |
| `data-edge-id="{e.id}"` | `FlowWireLayer` | same path | `markersDom.test.tsx` |
| `data-flows-wire-selected` | `FlowWireDelete` | the ✕ `<button>` | state 15 — *"the selected wire's remove control must be visible"* |
| `data-flows-inspector` | `FlowInspector` | the 284px column root | state 03 (ancestor selector) |
| `data-calibration-matrix` | `CalibrationMatrix` | block root | state 03 |
| `data-flows-editsheet` | `FlowEditSheet` | the `head` element inside the portal | state 13 |
| `data-flows-palette` | `FlowPaletteSheet` | the surface inside the portal | state 14 |
| `data-flows-tonight="{tab}"` | `TonightPanel` | the body wrapper | states 04, 05, 06 |
| `data-flows-wizard` | `FlowWizard` | the `head` element | state 09 |
| `data-flows-phone-tab="{tab}"` | `FlowPhoneGraph` / `FlowPhoneMonitor` | tab content root | states 10, 11 |
| `data-flows-wiring-armed` | `FlowTapWireBar` | the hint bar (exists only while armed) | state 10b |
| `data-palette-type="{type}"` | `FlowPalette` | rail/sheet item | **deliberately NOT `data-node-type`** — see §C.7 |

## F.2 Accessible names the harness needs

| Step | Needs | Design's label | OK? |
|---|---|---|---|
| `("nav","Flows")` | a **visible** button/link whose a11y name contains `Flows` | rail/More-sheet tab `Flows` | ✓ (the `visible=True` filter matters — the desktop rail is present-but-hidden on phone and its labels duplicate the bottom bar's) |
| `("click","NEW FLOW")` | a visible button containing `NEW FLOW` | `+ NEW FLOW` card | ✓ (substring) |
| `("run","")` | a visible button containing `RUN` | `▶ RUN` | ✓ |
| `("tab","TONIGHT")` | role=tab or button containing `TONIGHT` | `◷ TONIGHT` | ✓ |
| `("tab","TIMELINE"/"STORY"/"PLAN")` | role=tab or button | pill tabs | ✓ *(verify `SegmentedControl` emits one of the two, else use plain buttons)* |
| `("phone-tab","MONITOR")` | role=tab or button | bottom bar `MONITOR` | ✓ |
| `("click","ADD NODE")` | a visible button containing **`ADD NODE`** | **`+ ADD STAGE`** | ✗ **§G-28** |

## F.3 Gates every capture must clear

1. **View marker visible** (`state="visible"`, 15 s) — a capture can otherwise be a perfectly-rendered photograph of the wrong page.
2. **`document.fonts.status === "loaded"`** and the display face resolved. Chakra Petch comes from `@fontsource`; a fallback renders a page that looks fine in a DOM dump and wrong in a screenshot.
3. **No error boundary** — the body text must not contain `Something went wrong`, `ErrorBoundary`, `Unhandled`, `Failed to fetch dynamically imported`. That last one fires if `lazyViews.ts` points at a module that does not build.
4. **The PNG is not blank** — decoded and measured. Fails if `distinct < 12`, `modal_share > 0.985`, `stdev < 3.0`, or `ink < 0.01`. This is the only gate that can catch SVG text which measures fine and paints nothing, which is precisely why README §7 forbids text inside the timeline SVG.

The context blocks service workers and sets `device_scale_factor: 1`. Viewports are `1440×900`, `820×1180`, `390×844` — **not** the references' `924×540` / `392×540`, and nine of the thirteen references are JPEGs with a `.png` extension. The harness records this in `report.json` and says so: a numeric diff is *"not just unimplemented, it is MEANINGLESS."* Comparison is **by eye**, protocol step 3.

## F.4 The four harness changes required — all blocked

| # | Harness line | Problem | Options |
|---|---|---|---|
| 1 | `:441` `page.keyboard.press("f")` | No source specifies an `f` shortcut | (a) add `f` = fit to the editor's keydown handler *(assumed above, §D.5)*; (b) change the step to click `FIT` |
| 2 | `:314` `("click","ADD NODE")` | The design's label is `+ ADD STAGE`, and at 1440px there is **no** add-stage float at all — desktop has the palette rail | (a) change the harness to `ADD STAGE`; (b) add `aria-label="ADD NODE"` — which breaks WCAG 2.5.3 (visible label not in the accessible name); (c) change state 14's viewport to TABLET |
| 3 | `:308-311` state 13 dblclicks a node at **DESKTOP** to open `[data-flows-editsheet]` | README §3 says the ✎ is *"the ONLY thing that opens the edit sheet on tablet/phone"*, and at ≥1080px the design has **no sheet** — it has the inspector column | (a) change state 13 to TABLET; (b) make dblclick an additional opener; (c) point the marker at the desktop inspector instead |
| 4 | `:443-445` `setAttribute('data-night', …)` | **Does nothing.** Night is `:root.night` only (verified) | (a) change the harness to `document.documentElement.classList.add('night')`; (b) add `:root[data-night="true"]` to the four night blocks in `index.css` — an edit to the token file |

**§G-29.** Do not pick any of these silently: the harness is the definition-of-done instrument, and its `README §"Verify by looking"` framing means a state that never rendered was never verified.

## F.5 The contract test

`components/flows/__tests__/markersDom.test.tsx` follows `polarSolveRingDom.test.tsx` exactly: jsdom constructed by hand **first** via top-level `await import("jsdom")`; globals installed with `Object.defineProperty(globalThis, k, {value, writable: true, configurable: true})` over an explicit key list — and it **must include `matchMedia`**, because `useMediaQuery`/`Overlay`/`useIsLg` all call it; `g.IS_REACT_ACT_ENVIRONMENT = true`; React imported **after**; hand-rolled `test()`/`assert`; every render in `act()`; queries against `data-*`, never text; assertion messages that state the **consequence** (*"without this marker the harness photographs the wrong page and calls it a pass"*); `act(() => root.unmount())`; `console.log(\`name: ${passed}/${total} passed\`)`; `export default { passed, failed, total }`.

A file whose output cannot be scored **fails** — `run-tests.mjs` reports *"no pass/fail tally in its output — cannot be scored."*

---

# G. RANKED OPEN QUESTIONS

Most-blocking first. **None of these may be resolved by inventing.**

---

### G-1 — The entire run surface has no data source. `flow.node` and `flow.log` do not exist.
**Blocks:** capture `07` (a required state), the header ETA, node LEDs, marching wires, the phone MONITOR panel, `data-flows-run='holding'`, and README §Definition-of-done clause 3 ("the M16 example reproduces the full cloud-dodge choreography from real engine events").
**Evidence:** README backend item 3 states the two events as a requirement. `git grep "bus.publish(" -- server/astrodeck/**/*.py` yields exactly eleven topics and no `flow`. `app.py:3798` emits only `bus.log("info", f"flow '{rec.name}' started: …", "flow")`. Tracker task #234 independently records that the engine cannot evaluate cloud/safety/panel triggers, so the cloud-dodge is inert even if the events existed.
**Settled by:** the user choosing (a) this milestone includes adding the `flow.node`/`flow.log` publishers and the trigger plumbing server-side — the scope fence permits *"the new server modules/endpoints in Backend work list"*, but the shipped Flows backend commits did not deliver them; or (b) the UI drives node status client-side from the existing `sequence` event's step/target progress for this pass, with a `TODO(flows-handoff):` per README rule 5. The two produce visibly different code, different tests and a different capture-07 outcome.

---

### G-2 — Four surfaces exist in the API and nowhere in the design.
**Blocks:** the FLOW overview's layout, the RUN button's failure path, and the wizard entirely.
1. **`unmapped[]`** — `POST compile` returns it, `POST run` 409s on it, `flowsApi.run`'s comment explains `acceptUnmapped`, and `to_plan.py`'s docstring says the whole module exists so *"the operator finds out that their cloud rule is not running now, from a list on screen, instead of at 3 a.m."* README §8 describes only the doctor's ten `issues`. No screenshot shows it.
2. **`structural[]`** — same, one severity up.
3. **The 409 refusals** — `unmapped` (clearable) vs `dome_unmapped` (**not** clearable: *"nobody should be able to click past a roof"*) vs `invalid_graph` vs horizon/solar. No designed dialog.
4. **The wizard's generator** — `wizard.py` has `generate()` with no route (verified: `grep -rn wizard server/astrodeck/api/app.py` → one unrelated comment).
**Settled by:** for 1–3, a ruling on placement (my recommendation in §C.8/§C.13 is a proposal, not a design); for 4, the user choosing between adding `POST /api/flows/wizard` and re-implementing `genWizard()` in TypeScript as a third transcription.

---

### G-3 — Palette item order inside LOGIC and ACTIONS + SINKS.
**Blocks:** `palette.ts`, the rail, and the add-stage sheet.
**Evidence:** prototype line 1456 → `LOGIC: [condition, pool]`, `ACTIONS+SINKS: [notify, refocus, holdresume, abort, report]`. `nodes.py:189-190` → `LOGIC: (pool, condition)`, `ACTIONS+SINKS: (holdresume, notify, refocus, abort, report)`. The README's own node-vocabulary contract table lists TARGET POOL before CONDITION (agreeing with the **server**) but NOTIFY, REFOCUS, HOLD/RESUME, ABORT, REPORT (agreeing with the **prototype**). README §3 specifies only the group names and their order. **No single source is right about both groups.**
**Settled by:** the user stating the order, or a desktop-tier (≥1080px) screenshot of the rail — none exists.

---

### G-4 — Where the field/control vocabulary lives in production.
**Blocks:** `nodeDefs.ts`, which is the largest new file and every inspector field.
**Evidence:** the prototype's `DEFS.fields` is the **only** place any of it exists — 58 select-option strings, ~70 field labels, 11 units, 19 `desc` strings, 19 `sum()` bodies. `nodes.py` deliberately carries no `fields`/`desc`/`sum`, and `models.py:10-16` states params are validated permissively on purpose (so a graph can be saved with `capture.filter = "Z"` and only fail at compile time). No endpoint serves the vocabulary at all. README's backend work list never mentions serving it, and never says the UI owns it.
**Settled by:** the user choosing — extend `NodeDef` with `fields`/`options`/`units` and serve them (one source, no drift), or declare the UI the owner of presentation metadata and accept a second transcription of the ports/labels plus a first-and-only home for the option sets.

---

### G-5 — Does the Flows header replace the app header, nest under it, or is the app header suppressed?
**Blocks:** `FlowHeader`, and at 390×844 it decides whether ~208px of the viewport is chrome.
**Evidence:** README §1 specifies a 54px header owning the logo, the two-line wordmark, the provider badge, the night toggle and `i`. The app already renders a 48px header at `App.tsx:492` (`h-12`) with a logo, a wordmark, `HealthLeds`, `RoleBadge`, `SignInButton` and `HeaderControls` — which **already owns the night toggle** (`HeaderControls.tsx:114-118` → `store.toggleNight`) — plus a 72px rail and a phone `BottomNav` (`min-h-[56px]`) plus `.main-safe-pad`'s `calc(5rem + env(safe-area-inset-bottom))`. The prototype is a standalone `100vw/100dvh` page with no app shell. README §Overview says *"Recreate it INSIDE the existing `ui/src` app"* and the scope fence permits only *"nav registration for the new surface"*. There is exactly one night mechanism (`:root.night` + `localStorage("astrodeck-night")` + the pre-paint script in `index.html:11-27`) and a second visible toggle would be confusing at best.
**Settled by:** the user choosing (a) Flows is a normal routed view and its header is a **second, view-local toolbar** under the 48px app header, dropping the duplicated logo/wordmark/night toggle; (b) Flows is full-bleed and suppresses the app chrome while active (needs a new mechanism in App.tsx — outside the additive fence); or (c) the 54px figure is prototype-only and the toolbar should match the app's 48px.

---

### G-6 — The tier boundaries 700 / 1080 exist nowhere in this codebase.
**Blocks:** `useFlowsTier`, and therefore which layout every screen gets.
**Evidence:** `index.css` imports Tailwind at line 7 and its only `@theme inline` block (line 174) defines colours and fonts — **no `--breakpoint-*` override in 998 lines**, so v4 defaults hold (sm 640, md 768, lg 1024, xl 1280). Usage across `ui/src`: 126 `sm:`, 46 `lg:`, 9 `md:`, 2 `xl:`, and **zero** arbitrary `min-[Npx]:`/`max-[Npx]:` variants. Authority level 4 points at 640/768/1024, which brackets 700 and 1080 without hitting either. Two war stories bear on this: `PolarView.tsx:221-229` (*"a phone in landscape is 667-932px wide — under lg's 1024, so the reticle and the error readout used to stack"*) and `SequenceView.tsx:648` (*"`md:grid-cols-[1fr_300px]` split at 768, so an 820 tablet got a 416px plan"*). Also: at ≥640px the 72px rail is present, so the Flows **container** is ~104px narrower than the viewport, while `computeAutoLayout`'s `Wc` reads a width.
**Settled by:** the user choosing (a) add `--breakpoint-*` to `@theme` (a token-file edit); (b) arbitrary variants (no precedent); or (c) drive the tier from JS via `useMediaQuery` (assumed in §C.1). **And** confirming whether 700/1080 measure the viewport or the Flows container.

---

### G-7 — There is no ground-truth render of the desktop tier, and capture `03` does not contain what its filename names.
**Blocks:** the definition-of-done clause *"Every screen matches its screenshot at the same breakpoint (side-by-side)"* for the 192px rail, the 284px inspector and the LIBRARY HEALTH matrix.
**Evidence:** the nine non-phone captures are **JPEG** (SOI `ffd8ffe0`/JFIF) at exactly **924×540** despite `.png` names — 924 is inside the prototype's **tablet** band (`w < 700 ? phone : w < 1080 ? tablet : desktop`), where `showRail` and `showInspCol` are both false. Capture `03` is pixel-identical to `02` except that the CALIBRATION QUEUE node carries the selected border and glow; there is no inspector column, no sheet, and no matrix in the frame. The harness itself captures at 1440×900 / 820×1180 / 390×844 and records the mismatch in `report.json` (*"a numeric diff … is MEANINGLESS"*). The phone pair is 392×540, not the prescribed 390×844.
**Settled by:** a 1440×900 capture of the editor with the rail + inspector + CALIBRATION QUEUE selected, or the user waiving side-by-side parity for the desktop tier and accepting the prototype DOM as the sole authority there. Also: is capture `03` meant to show the desktop inspector column, the tablet edit sheet on CALIBRATION QUEUE, or just the selected node?

---

### G-8 — Adding a 16th nav entry breaks `nav.test.ts`, and the only fix edits every existing tab.
**Blocks:** step (5) of the file plan, and therefore `npm test`.
**Evidence:** `nav.test.ts:175-192`. With `py-1.5` (App.tsx:663): `entry = 38 + 2·6 = 50`, `railHeight = 16·50 + 16 = 816`, `816 + 50 = 866 > RAIL_CLIENT_H (818)` → fail. `py-1` gives `entry = 46 ≥ 44` floor and `752 + 46 = 798 ≤ 818` → pass, but changes all sixteen tabs on every screen. The test's own failure message says *"Reduce the padding (and re-measure on a real 1440x900 viewport) before appending"* — a measurement no read-only pass can take.
**Settled by:** the user choosing (a) accept `py-1.5 → py-1` and re-measure at 1440×900; (b) keep Flows off the desktop rail (which then also breaks the PHONE PARITY test, since it asserts the More sheet offers nothing the rail does not); or (c) evict an existing rail entry, which Risk-10 forbids. Also confirm that editing `LANDED_ORDER` and the "Gallery is the newest entry" test is in scope — it is unavoidable.

---

### G-9 — Six glyphs the design asks for have no icon in the shipped set.
**Blocks:** the node ✎, the library folder headings and search field, the log chevron, the wire ✕, and the nav entry.
**Evidence:** README §Assets says *"use the app's Icon set; the prototype's pencil is a stand-in for its edit glyph"* — acknowledging the pencil is missing without naming a replacement. The complete `IconName` union is 42 names with **no** pencil/edit, folder, magnifier, chevron, node-graph, or ✕-in-a-circle (`x` exists as a bare cross). `PATHS` is a total record so a missing path is a compile error, and `nav.test.ts:133-141` greps for it too (*"a name in the union with no path renders an empty `<svg>`, which on a 72px rail reads as a missing tab"*).
**Settled by:** the user either nominating substitutes from the 42 (edit → `frame`/`settings`; search → none; folder → `grid`/none; chevron → `arrow-down`/`arrow-up`; wire-delete → `x`; nav → `bridge`/`link`/`grid`/`plan`) or authorising new entries in `icons.tsx`, drawn in the v2 instrument-glyph language (24×24, `currentColor`, round caps, filled 1.2px dots reserved for stars).

---

### G-10 — LIBRARY HEALTH: column 2's text, the evidence with no home, and the row count.
**Blocks:** `CalibrationMatrix`, and capture `03`'s content.
**Evidence:** three separate disagreements.
(a) The fixture's BIAS `v` is `g100 · set of 40` but the server's `summary` emits `g100 · −5°C` (temperature is deliberately part of a bias's identity, lines 168-172) with the count in `quantity`; the fixture's FLAT rows are `Ha · this rotation` and bare `OIII` but the server emits `Ha g100 · PA 23°` and its docstring says so out loud (*"where the prototype's fixture shows only the filter"*). Only DARK matches, byte-for-byte including U+2212.
(b) The server returns `reasons[]`, `family`, `contradicted`, `measured`, `age_days`, `master_id`, `from_master`, plus `planned`, `counts_masters_only`, `assumed` — none of which has a slot in a four-column colour-only grid. The module's own docstring argues at length that a bare STALE *"is a colour, not a fact"*, and `flowsApi.calibrationHealth`'s comment says an empty matrix **must not** be drawn as healthy.
(c) `health_matrix` returns one row per (kind × distinct need), unbounded; the prototype hardcodes four; no max-height rule exists anywhere.
**Settled by:** (a) render the server's `summary` verbatim and accept the screenshot strings are fixtures, or have the server emit a second display string — **do not synthesise a third phrasing in the UI**; (b) a decision on the disclosure surface (hover title on the verdict cell / expandable row / a line under the block) and where the `planned:false` sentence goes; (c) a cap-and-"+N more" rule or an explicit "let the column scroll".

---

### G-11 — Is `index.css` inside or outside the additive fence?
**Blocks:** `.flow-wire-march`, and therefore the active-wire animation and the reduced-motion cue.
**Evidence:** the scope fence's one sentence points both ways: *"You may NOT refactor, reformat, or 'clean up' unrelated files, existing views, **the token file**, or the engine — **additive changes only**."* README §3 requires the marching dash and the Do-not list requires a reduced-motion static cue, which together need a `@keyframes`, a class, and a media rule — none of which can be expressed inline. `cssClasses.test.ts` (F-C1) then requires the class be defined in `index.css` and allowlisted, because *"Tailwind v4 silently no-ops an undefined custom class."*
**Settled by:** the user confirming additive `@keyframes`/classes in `index.css` are permitted (and that `COMPONENT_CLASSES` should gain `flow-wire-march`). The same ruling covers G-29 option 4b.

---

### G-12 — Should the 36px grid pan and zoom with the graph?
**Evidence:** the prototype's grid `<div>` is a **sibling** of the world-transform `<div>`, so it is a fixed screen-space texture that neither translates nor scales. README §3 says only *"Canvas: nebula bg + 36px grid … pan = drag background, zoom = wheel"*, which reads naturally as a graph-space grid and is how most node editors behave. Every screenshot is captured at rest, so pixels cannot distinguish the two.
**Settled by:** the user confirming the grid is deliberately screen-fixed. A graph-space grid is a behaviour change from the prototype, however slight.

---

### G-13 — Does the ETA readout render in the phone header while running?
**Evidence:** `showProv` and `showValid` are explicitly phone-gated; the ETA span is gated only on `running`, so the prototype's markup **would** render `ETA 0:12` at 390px between the truncating title and `◷`. README §5 lists the phone header as *"flow name between `‹ LIBRARY` and the ◷/RUN/☾/i cluster"* with no ETA, and warns *"nothing may overlap at 390px"*. All four phone captures are idle.
**Settled by:** a 390px capture of a running flow, or the user stating ETA is suppressed on phone because MONITOR carries it.

---

### G-14 — The node card's idle border and background alpha.
**Evidence:** README §3 says *"bg `rgba(12,14,22,0.92)`, 1px border `--line`"*, and README's own token map says `--line = rgba(120,140,200,.18)` and `--bg-panel = rgba(12,14,22,.85/.92)`. The prototype uses border `rgba(120,140,200,0.22)` — matching **no** token — plus a header divider at `rgba(120,140,200,0.14)`, also matching no token. And `--bg-panel` in `index.css` is `0.85`, so swapping the hex for the token changes the card's fill by 7% opacity.
**Settled by:** the user confirming whether to snap to `--line`/`--bg-panel` (accepting a visible delta from the prototype — assumed in §C.5) or to add 0.22 / 0.14 / 0.92 verbatim (violating *"no hardcoded hex where a token exists"*).

---

### G-15 — Four phone-tier geometry overrides stated only in the prototype, plus the armed ring's colour.
**Evidence:** README §3 gives the canvas numbers (9px dot, 20px hit, 20px pencil, 14px wire hit path, 22px ✕) and README §5 states exactly **one** phone override ("Port hit targets grow to 26px here"). The prototype's auto-graph markup differs on three more: visible dot **10px** (lines 319/327 vs 177/185), pencil **24×24 with a 13px glyph** (line 311 vs 169), wire hit path **stroke-width 16** (line 300 vs 154), ✕ **26×26 font-size 12** (line 336 vs 198). At 392px the screenshots cannot resolve a 9-vs-10px dot. Separately, `ring: "0 0 0 3px rgba(0,210,255,0.25), 0 0 10px " + color` (line 1410) hardcodes the 3px inner ring **cyan** even on an amber event port.
**Settled by:** the user confirming the four values are intended per-tier overrides rather than prototype drift, and whether the armed ring takes the port's own colour on event ports.

---

### G-16 — LED geometry: reuse `.led-*` (11px + checkmark) or a Flows-only variant (9px, no checkmark)?
**Evidence:** README §3 gives exact px (idle 10×2, busy/ok/warn/bad 9px, bad SQUARE) and then says *"Reuse `.led-*` where possible"*. `index.css:559-592` defines `.led {11px}`, `.led-off {11px × 2px}`, and `.led.led-on::after` draws a checkmark added deliberately for EQ-02 with the comment *"night mode maps `--good` to the same red family as `--warn`/`--bad`, so a color-blind or red-filtered glance still needs a positive shape cue here."* README §Fidelity says *"Recreate pixel-perfectly"*; the Do-not list says *"keep the LED silhouettes."* Reuse gives 11px + a checkmark; matching the design gives 9px, no checkmark, **and no static way to tell `busy` from `ok`** under reduced motion — which the Do-not list separately forbids. The prototype's own design-notes copy (line 531) claims *"check = ok"* while `statusStyleOf` draws a plain circle; README §3 sides with the code.
**Settled by:** the user ruling. A Flows-only variant is a new authored class and must join `cssClasses.test.ts`'s allowlist. **The same ruling should cover `.label` (11px Plex Sans vs the design's 9.5px Chakra) and `.btn` (12px vs the design's 10.5–11px).**

---

### G-17 — Should the pending wire take the source port's lane colour?
**Evidence:** the prototype hardcodes `stroke="#00D2FF"` (line 159) regardless of the source kind, so dragging from an amber event output draws a cyan wire that turns amber on drop. README §3 says only *"pending dashed wire follows cursor"* and does not name a colour, while the same section is emphatic that event wires are amber and dashed.
**Settled by:** the user saying reproduce-verbatim or colour-by-kind.

---

### G-18 — Tonight: the axis mapping, the moon-up band, the DUSK tick's meaning, four degraded states, and the honesty note.
**Evidence:** five sub-questions, all with the same root — `tonight.py` states outright *"WHAT THIS RETURNS IS TIMES, NOT PIXELS"* and refuses to decide layout.
(a) Axis bounds and tick set: the prototype hardcodes 20:00→05:00 as `X(t)=t/570·1000`; README §7 specifies the viewBox and the label layer but never the time→x mapping.
(b) The moon-up bar cannot be positioned when the moon is up at dark start (`rise_unix` and `set_unix` both `None`, `samples[].moon_alt` deliberately dropped at line 428).
(c) Does the DUSK tick mark `night.dusk_unix` or `night.window_start_unix`? They coincide at 20:41 in the fixture; the server returns both; the prototype's own story row at 20:41 reads "Autorun window opens (…offset applied)", i.e. the offset one.
(d) Four degraded states with no design: `ok:false + reason`; `flats.start_unix/end_unix` null; targets with `resolved:false` and an empty curve; budget rows with `has_ledger:false`/`banked_h:null` (the prototype fabricates 35% of the goal; the server's honest sentence is longer than the row the design sized).
(e) The honesty note's production replacement.
**Settled by:** a stated rule for (a) (e.g. "span = [dusk − 30 min, dawn + 30 min], ticks on the hour" — or confirmation the fixed window is intended); for (b), either adding `moon_alt` back server-side or a rule for what the bar does when both crossings are None (*"draw it full width" would assert a moon-up night nobody measured*); for (c) which instant the tick means; for (d) what TIMELINE draws in each state; for (e) the replacement copy.

---

### G-19 — Does the PLAN tab show `payload.plan` or the whole compile payload?
**Evidence:** the prototype shows `JSON.stringify(compilePlan())` — the plan alone. `_compile_payload` returns four keys. The caption (*"The literal plan this graph compiles to"*) describes the plan alone. Rendering the payload would put the doctor's issues inside the JSON block.
**Settled by:** confirmation that the block shows `payload.plan` only, and that COPY JSON copies the same subset. *(Assumed in §C.11; low risk but it is a visible choice.)*

---

### G-20 — The 30px log strip vs the 44px touch floor, and the other sub-44px graph controls.
**Evidence:** README §3 specifies a 30px bar; the Do-not list says *"44px touch floor for anything driven at the scope."* `Disclosure` (`ui.tsx:750-783`) — the house primitive that fourteen call sites hand-rolled before extraction — hardcodes `w-full tap min-h-[44px]`. `.field-rig` is the existing exemption precedent, but its justification (*"typed once when a scope is set up and then never touched"*) does not describe a log strip you tap in the dark mid-run. The same tension covers 20px port hits on the **phone CANVAS tab** — which README §5 calls *"the full editor"* for phones and which therefore has the **smallest** hit targets of any tier — plus the 24px pencil, the 36px CANCEL, the 34px zoom buttons and the 26px ✕.
**Settled by:** the user ruling whether these are exempt at the sizes named; whether the log strip is 30px on fine pointers and bumps to 44px on coarse; and specifically whether the phone CANVAS tab's 20px port hits should rise to the FLOW tab's 26px. If exempt, whether the hit area should be bought InfoDot-style (`-m-[15px] p-[15px]`, `ui.tsx:614-616` — layout footprint unchanged, hit area 44px) rather than by shrinking the target.

---

### G-21 — Is the phone tier in milestone 2 at all?
**Evidence:** the task is scoped *"the desktop/tablet web UI"*, but the harness's state table — which the definition of done makes mandatory — contains three PHONE states (`10`, `10b`, `11`) and will report them as failures, and `run-tests.mjs`/`report.json` treat a missing capture as a failed state, never an absent one (*"NOT DONE. A missing capture is a failed state"*).
**Settled by:** the user confirming whether milestone 2 must clear the phone states, or whether the harness should be run with `--only` for this milestone.

---

### G-22 — The 34px gap applies to only one of the three auto-layout passes.
**Evidence:** README §5 says *"34px vertical gaps"* unscoped. The code uses 34 for the flow lane (line 846) and **22** for rule clusters (855-856) and unwired stragglers (858). The screenshot only shows the flow lane; the rule clusters are below the 540px fold. Separately, `H(n) = 37 + rows·20 + 8` is a **layout** height ~2-3px larger than the rendered box (measured: DUSK WINDOW card 62px vs H=65) — fine if reproduced as a formula, wrong if implemented by measuring the DOM. The README describes neither.
**Settled by:** confirmation that the cluster/orphan gaps are 22px and that `H(n)` is the spacing model to reproduce verbatim. A 390px capture scrolled to the CLOUD WATCH cluster would settle it visually.

---

### G-23 — The kind-mismatch refusal is two different sentences.
**Evidence:** prototype lines 871 and 998 produce `Event output can't feed a flow input`; `models.py:117-119` produces `Event output can't feed an flow input` — ungrammatical. README §3 quotes only the flow→event direction, so it does not adjudicate. The UI toast and the server's `validation_errors` string are currently two different sentences for the same refusal.
**Settled by:** the user confirming the server string should be fixed to match (a one-line change to a file outside the stated Flows-UI scope fence).

---

### G-24 — Button zoom does not re-anchor, and 1.15/0.87 are not reciprocal.
**Evidence:** `zoomBy` changes `zoom` only, so `+`/`−` pivot on the world origin while the wheel pivots under the cursor.
**Settled by:** the user confirming reproduce-verbatim. *(Assumed in §D.2.)*

---

### G-25 — The `f` = fit shortcut has no design source.
**Evidence:** README §3's keyboard clause is *"Delete/Backspace removes selection (ignored while typing). Esc should close overlays."* Nothing else. `flows_visual_check.py:441` presses `f`.
**Settled by:** see G-29 item 1.

---

### G-26 — The library card has no designed state for `last_result` `"warn"` or `"bad"`.
**Evidence:** `FlowRecord.last_result` is `"" | "ok" | "warn" | "bad"` (models.py:150). The design defines exactly two strings: `COMPLETED CLEAN` and `NEVER RUN`. A flow whose last run ended `warn` or `bad` has no card copy and no LED state.
**Settled by:** the user supplying the two missing strings (and their LED states), or confirming both collapse into one of the existing two.

---

### G-27 — Should the doctor be re-implemented in TypeScript?
**Evidence:** README §Backend item 2 says *"server-side doctor mirrors §8 (UI re-checks locally for latency, server is authoritative)."* `doctor.py` reproduces all ten rules with the prototype's exact strings. A TS copy is a third transcription of the same wording and the same thresholds, and every future rule change has to land twice.
**Settled by:** the user confirming a 200ms-debounced `compileDraft` is acceptable in place of a local mirror, or requiring both.

---

### G-28 — `("click","ADD NODE")` at 1440px.
**Evidence:** the design's control is `+ ADD STAGE` and it exists only on **tablet** (`showAddFloat: dev === "tablet" || (isPhone && phoneTab === "canvas")`). At 1440px the desktop editor opens the palette from the 192px rail, which is a list, not a button. So state 14 as written cannot pass at DESKTOP with any label.
**Settled by:** see G-29 item 2. This is the one harness change that cannot be resolved by renaming alone.

---

### G-29 — Four required edits to `scripts/flows_visual_check.py`.
Enumerated with their options in §F.4: the `f` keypress, `ADD NODE`, the desktop dblclick-to-edit-sheet, and `data-night` (which does nothing against `:root.night`). The harness is the definition-of-done instrument; editing it silently would make the evidence agree with the implementation by construction.
**Settled by:** the user picking one option per item, and confirming that editing the harness counts as in-scope (*"tests for the above"* in the scope fence plausibly covers it, but the harness is also the thing that grades the work).

---

## Files referenced (all absolute)

`C:/Users/bear/astro/design_handoff_astrodeck_flows/README.md` · `C:/Users/bear/astro/design_handoff_astrodeck_flows/AstroDeck Flows.dc.html` · `C:/Users/bear/astro/design_handoff_astrodeck_flows/screenshots/` · `C:/Users/bear/astro/scripts/flows_visual_check.py` · `C:/Users/bear/astro/ui/src/lib/flowsApi.ts` · `C:/Users/bear/astro/ui/src/index.css` · `C:/Users/bear/astro/ui/src/App.tsx` · `C:/Users/bear/astro/ui/src/store.ts` · `C:/Users/bear/astro/ui/src/types.ts` · `C:/Users/bear/astro/ui/src/ws.ts` · `C:/Users/bear/astro/ui/src/api.ts` · `C:/Users/bear/astro/ui/src/lib/caps.ts` · `C:/Users/bear/astro/ui/src/lib/lazyViews.ts` · `C:/Users/bear/astro/ui/src/components/ui.tsx` · `C:/Users/bear/astro/ui/src/components/Overlay.tsx` · `C:/Users/bear/astro/ui/src/components/icons.tsx` · `C:/Users/bear/astro/ui/src/components/Logo.tsx` · `C:/Users/bear/astro/ui/src/components/NavMoreSheet.tsx` · `C:/Users/bear/astro/ui/src/components/BottomNav.tsx` · `C:/Users/bear/astro/ui/src/components/HeaderControls.tsx` · `C:/Users/bear/astro/ui/src/components/atlas/SkyCanvas.tsx` · `C:/Users/bear/astro/ui/src/components/preview/usePreviewGestures.ts` · `C:/Users/bear/astro/ui/src/components/__tests__/polarSolveRingDom.test.tsx` · `C:/Users/bear/astro/ui/src/__tests__/nav.test.ts` · `C:/Users/bear/astro/ui/src/__tests__/cssClasses.test.ts` · `C:/Users/bear/astro/ui/src/lib/__tests__/lazyViews.test.ts` · `C:/Users/bear/astro/ui/run-tests.mjs` · `C:/Users/bear/astro/ui/index.html` · `C:/Users/bear/astro/server/astrodeck/api/app.py` (3520–3870) · `C:/Users/bear/astro/server/astrodeck/flows/{nodes,models,doctor,compile,to_plan,tonight,calibration_health,wizard,examples,store}.py`