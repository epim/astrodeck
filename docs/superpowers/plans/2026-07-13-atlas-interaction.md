# Atlas Interaction Quality (Wave 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A real rotation handle ON the framing box (element hit-test, rotates with the box), catalog search on the Atlas itself (header + empty state), a truthful recenter label, and deletion of the dead `activePanel` contract.

**Architecture:** The handle is drawn by FovOverlay inside the box's rotated `<g>` and re-enables pointer events on itself (`data-role="rotate-handle"`); SkyCanvas's `onPointerDown` picks rotate-vs-pan by `closest('[data-role=…]')` instead of the screen-zone heuristic. Search is one new shared `CatalogSearch` component (SequenceView's proven idiom) mounted in the Atlas header (pick → `setFraming` target+center, keeps session) and in the empty state (pick → `openFraming`).

**Tech Stack:** React 18 + TS + Tailwind; no server changes; no new pure modules (gates are `npm run build` + the three Wave-1 tsx regression tests).

**Spec:** `docs/superpowers/specs/2026-07-13-atlas-interaction-design.md` (5942cb3). Seams (verbatim, current disk): `.superpowers/sdd/seams/wave2-interaction.md`.

## Global Constraints

- UI gates for EVERY task, run from `ui/`: `npm run build` (tsc + vite, must pass) AND the three regression tests `npx tsx src/lib/__tests__/surveyView.test.ts` (6/6), `npx tsx src/lib/__tests__/tooltipMachine.test.ts` (7/7), `npx tsx src/lib/__tests__/missingOptics.test.ts` (4/4) — CI never executes tsx tests; run them yourself and paste outputs. KNOWN HARNESS ISSUE: if a command is auto-backgrounded, re-run immediately and stay foreground; never park.
- Rotation SEMANTICS unchanged: everything funnels into `onRotate` → `setFraming({rotation_deg})`; stepper, ±5° buttons, and `[`/`]` keys untouched.
- `GET /api/catalog?q=` is the only search endpoint; 250 ms debounce; top 6 results (SequenceView idiom).
- Line numbers below are from the wave2 seam file; files may shift a few lines — match on content.
- Commit per task with the two standard trailers (Co-Authored-By: Claude Fable 5 <noreply@anthropic.com> + Claude-Session line).
- No server files, no Plan/SequenceView changes, no store changes.

---

### Task 1: Stalk rotation handle on the framing box

**Files:**
- Modify: `ui/src/components/atlas/FovOverlay.tsx` (draw the handle inside the rotated group)
- Modify: `ui/src/components/atlas/SkyCanvas.tsx` (element hit-test replaces knob-zone; delete fixed knob)

**Interfaces:**
- Produces: `FovOverlayProps.rotateHandle?: boolean` (default false). The handle group carries `data-role="rotate-handle"`; SkyCanvas's pointer-down tests for it. Task 3 later removes `activeIndex`/`activePanel` — do NOT touch them here.

- [ ] **Step 1: FovOverlay — prop + handle geometry**

1a. Add to `FovOverlayProps` (after `haveOptics`):

```tsx
  /** Draw the grabbable rotation stalk on the box's top edge (wave-2 §1). */
  rotateHandle?: boolean;
```

Destructure it with the others: `haveOptics, rotateHandle = false,`.

1b. After the `stepY` const (`const stepY = frameH * (1 - overlap);`) add:

```tsx
  // Overall mosaic-grid half-height — the stalk hangs off the TOP of the whole
  // grid (not one panel) so it never overlaps a frame. Grid is symmetric about
  // the origin: half-height = one panel's half + half the row span.
  const gridHalfH = halfH + ((rows - 1) * stepY) / 2;
```

1c. Inside the returned JSX, the rotated group currently reads
`<g transform={`translate(${cx} ${cy}) rotate(${rotationDeg})`}>{panels}</g>`.
Replace it with:

```tsx
      <g transform={`translate(${cx} ${cy}) rotate(${rotationDeg})`}>
        {panels}
        {/* rotation stalk — PowerPoint-style grip on the box's top edge. It
            rotates WITH the box; SkyCanvas hit-tests data-role, so this is a
            real element hit, valid at any angle/zoom (wave-2 §1). The parent
            SVG is pointer-events-none; this group re-enables itself. */}
        {rotateHandle && (
          <g
            data-role="rotate-handle"
            style={{ pointerEvents: "all", cursor: "grab" }}
          >
            {/* invisible touch pad: r=64 viewBox units ≈ 46px dia at a 360px
                canvas — keeps the target ≥44px CSS on the smallest layout */}
            <circle cx={0} cy={-gridHalfH - 34} r={64} fill="transparent" stroke="none" />
            <line
              x1={0} y1={-gridHalfH} x2={0} y2={-gridHalfH - 34}
              className="svg-halo" stroke="var(--accent)" strokeWidth={2}
            />
            <circle
              cx={0} cy={-gridHalfH - 34} r={10}
              className="svg-halo" fill="var(--bg)" stroke="var(--accent)" strokeWidth={2}
            />
          </g>
        )}
      </g>
```

- [ ] **Step 2: SkyCanvas — element hit-test replaces the knob zone**

2a. In `onPointerDown` (seam lines 69-90 of the pointer block), REPLACE the two knob-zone lines

```tsx
    // Top-edge knob zone (the rotation handle) is the top ~14% strip center.
    const knobZone = py < rect.height * 0.14 && Math.abs(px - rect.width / 2) < rect.width * 0.22;
```

with:

```tsx
    // Real element hit-test on the drawn stalk handle (wave-2 §1) — correct at
    // any rotation/zoom, no duplicated geometry math.
    const onHandle = !!(e.target as Element | null)?.closest?.('[data-role="rotate-handle"]');
```

and change `mode: knobZone ? "rotate" : "pan",` to `mode: onHandle ? "rotate" : "pan",`.

2b. In the SVG layer (seam SkyCanvas.tsx:467-498), pass the new prop to FovOverlay — add `rotateHandle={haveOptics}` after `haveOptics={haveOptics}` — and DELETE the fixed-knob group:

```tsx
          {/* rotation knob handle (top edge) */}
          <g className="svg-halo" stroke="var(--accent)" strokeWidth={2}>
            <circle cx={cx} cy={VIEW * 0.07} r={10} fill="var(--bg)" />
            <line x1={cx} y1={VIEW * 0.07 + 10} x2={cx} y2={VIEW * 0.07 + 34} />
          </g>
```

(The compass-ticks group above it stays.)

2c. Pointer capture already lives on the container div and redirects the stream there after `setPointerCapture` — no changes to `onPointerMove`/`onPointerUp`/rotate math. The container's `cursor` style line is unchanged.

- [ ] **Step 3: Gates**

From `ui/`: `npm run build` (success) + the three tsx regression tests (6/6, 7/7, 4/4).

- [ ] **Step 4: Commit**

```bash
git add ui/src/components/atlas/FovOverlay.tsx ui/src/components/atlas/SkyCanvas.tsx
git commit -m "feat(atlas): stalk rotation handle on the framing box — real element hit-test replaces the top-strip knob zone (wave-2 §1)"
```

---

### Task 2: Catalog search on the Atlas (header + empty state)

**Files:**
- Create: `ui/src/components/atlas/CatalogSearch.tsx`
- Modify: `ui/src/views/AtlasView.tsx` (header mount + empty-state mount + copy)

**Interfaces:**
- Produces: `CatalogSearch({ onPick, placeholder }: { onPick: (e: CatalogEntry) => void; placeholder?: string })` — self-contained (own state + debounce + fetch), clears itself after a pick.
- Consumes: `api.get<CatalogEntry[]>("/api/catalog?q=…")`; `CatalogEntry` from types (id/name/type/ra_hours/dec_deg/mag/size_arcmin/alt/az).

- [ ] **Step 1: Create `ui/src/components/atlas/CatalogSearch.tsx`**

```tsx
// CatalogSearch — the Atlas's inline target search (wave-2 §2). Same idiom as
// Plan's search (SequenceView): 250 ms debounce, GET /api/catalog?q=, top 6.
// Self-contained: owns its query/results state and clears itself after a pick;
// the parent decides what "pick" means (setFraming vs openFraming).

import { useEffect, useState, type JSX } from "react";
import { api } from "../../api";
import type { CatalogEntry } from "../../types";

export function CatalogSearch({
  onPick,
  placeholder = "Search catalog — frame a target",
}: {
  onPick: (e: CatalogEntry) => void;
  placeholder?: string;
}): JSX.Element {
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<CatalogEntry[]>([]);

  useEffect(() => {
    if (!search) { setResults([]); return; }
    const t = setTimeout(async () => {
      try { setResults((await api.get<CatalogEntry[]>(`/api/catalog?q=${encodeURIComponent(search)}`)).slice(0, 6)); }
      catch { /* ignore — transient search errors just yield no dropdown */ }
    }, 250);
    return () => clearTimeout(t);
  }, [search]);

  const pick = (e: CatalogEntry) => {
    onPick(e);
    setSearch("");
    setResults([]);
  };

  return (
    <div className="relative">
      <input
        className="field btn-touch !w-56"
        placeholder={placeholder}
        aria-label="Search the target catalog"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      {results.length > 0 && (
        <div className="absolute left-0 top-full mt-1 w-72 panel z-20 max-h-60 overflow-y-auto">
          {results.map((r) => (
            <button key={r.id} type="button" onClick={() => pick(r)}
              className="w-full text-left px-3 py-2 text-xs hover:bg-raise transition-colors flex justify-between cursor-pointer">
              <span><span className="mono text-accent">{r.id}</span> {r.name}</span>
              <span className={`mono ${r.alt > 40 ? "text-good" : r.alt < 20 ? "text-warn" : "text-dim"}`}>
                {r.alt.toFixed(0)}°
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default CatalogSearch;
```

- [ ] **Step 2: AtlasView — header mount**

2a. Add import: `import { CatalogSearch } from "../components/atlas/CatalogSearch";` and add `CatalogEntry` to the types import line.

2b. Add the pick handler near the other session patchers (after `setMosaic`):

```tsx
  // Search-pick with a live session: swap the framed target + recenter on it,
  // keeping the user's survey/zoom/rotation/mosaic setup (wave-2 §2).
  const pickSearchTarget = (entry: CatalogEntry) =>
    setFraming({
      target: entry,
      center: { ra_hours: entry.ra_hours, dec_deg: entry.dec_deg },
    });
```

2c. In the header JSX (seam AtlasView.tsx:507-590), insert the search between the title block and the `<div className="flex-1" />` spacer:

```tsx
        <CatalogSearch onPick={pickSearchTarget} />
```

(Exactly one line-of-JSX mount; the component owns its dropdown.)

- [ ] **Step 3: AtlasView — empty-state mount + copy**

Replace the `AtlasEmpty` component (seam AtlasView.tsx:90-109) with:

```tsx
// Empty-state shell when the Atlas is reached with no active session (e.g. direct
// nav before picking an object). Search opens a fresh session; free-roam opens
// centered on the mount/0,0.
function AtlasEmpty({
  onFreeRoam,
  onPick,
}: {
  onFreeRoam: () => void;
  onPick: (e: CatalogEntry) => void;
}): JSX.Element {
  return (
    <div className="grid place-items-center min-h-[60vh] p-4">
      <div className="panel p-8 max-w-md text-center">
        <EmptyState
          icon="atlas"
          title="Frame a target"
          hint="Search a target right here, pick one from the Mount catalog, or free-roam the sky. Overlay your camera's field, plan a mosaic, and check tonight's visibility."
          action={
            <div className="flex flex-col items-center gap-3 mt-2">
              <CatalogSearch onPick={onPick} placeholder="Search catalog — e.g. M 31" />
              <button type="button" className="btn btn-accent btn-touch" onClick={onFreeRoam}>
                Free-roam the sky
              </button>
            </div>
          }
        />
      </div>
    </div>
  );
}
```

and the early return (seam :342-344) becomes:

```tsx
  if (!framing) {
    return <AtlasEmpty onFreeRoam={onFreeRoam} onPick={openFraming} />;
  }
```

(`openFraming(e)` seeds a full fresh session — same path the Mount "Frame" button uses.)

- [ ] **Step 4: Gates**

From `ui/`: `npm run build` + the three tsx regression tests.

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/atlas/CatalogSearch.tsx ui/src/views/AtlasView.tsx
git commit -m "feat(atlas): catalog search on the Atlas — header picks re-frame the live session, empty state opens one (wave-2 §2)"
```

---

### Task 3: Truthful recenter label + delete the dead activePanel contract

**Files:**
- Modify: `ui/src/components/atlas/SurveyControls.tsx` (hasTarget prop + label)
- Modify: `ui/src/components/atlas/FovOverlay.tsx` (remove activeIndex + cornerTicks)
- Modify: `ui/src/components/atlas/SkyCanvas.tsx` (remove activePanel prop + pass-through)
- Modify: `ui/src/views/AtlasView.tsx` (pass hasTarget)

**Interfaces:**
- Produces: `SurveyControlsProps.hasTarget: boolean`. REMOVES `FovOverlayProps.activeIndex` and `SkyCanvasProps.activePanel` (grep-verified: AtlasView never passed `activePanel`, so nothing else breaks).

- [ ] **Step 1: SurveyControls — hasTarget + label**

1a. Add to `SurveyControlsProps` after `haveOptics: boolean;`:

```tsx
  /** True when the session has a catalog target (labels recenter truthfully). */
  hasTarget: boolean;
```

Add `hasTarget,` to the component's destructure.

1b. The recenter IconButton (seam SurveyControls.tsx:152) becomes:

```tsx
        <IconButton
          icon="align"
          label={hasTarget ? "Recenter on target" : "Recenter on mount"}
          onClick={onRecenter}
        />
```

1c. AtlasView's `<SurveyControls …>` render gains `hasTarget={!!target}` (next to `haveOptics={haveOptics}`).

- [ ] **Step 2: FovOverlay — delete the never-fed active-panel emphasis**

2a. Remove `activeIndex?: number | null;` (and its doc comment) from `FovOverlayProps`; remove `activeIndex = null,` from the destructure.

2b. Delete the `cornerTicks` helper function and the `tickLen` const.

2c. Replace the panel-walk loop (the `const panels: JSX.Element[] = []` block through its closing brace — including the boustrophedon comment block above it) with a plain row-major walk (snake order only existed to align `activeIndex` with `mosaicGrid`'s flat index):

```tsx
  // The frame group: translate to center, rotate by PA, then draw each panel.
  const panels: JSX.Element[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const gx = (c - (cols - 1) / 2) * stepX;
      const gy = ((rows - 1) / 2 - r) * stepY;
      panels.push(
        <g key={`p-${r}-${c}`} transform={`translate(${gx} ${gy})`}>
          <path
            d={rectPath(halfW, halfH)}
            className="svg-halo"
            fill="none"
            stroke="var(--accent)"
            strokeWidth={1.5}
            strokeDasharray={haveOptics ? undefined : "8 6"}
            opacity={haveOptics ? 0.95 : 0.7}
          />
        </g>,
      );
    }
  }
```

2d. Update the header comment's last sentence (the "ACTIVE panel is emphasized…" line) to note the active-panel emphasis was removed as a never-fed contract (wave-2 §4).

- [ ] **Step 3: SkyCanvas — remove the pass-through**

Remove `activePanel?: number | null;` from `SkyCanvasProps`, `activePanel = null,` from the destructure, and `activeIndex={activePanel}` from the FovOverlay render. Run `grep -rn "activePanel\|activeIndex" ui/src` afterwards — expect ZERO matches.

- [ ] **Step 4: Gates**

From `ui/`: `npm run build` + the three tsx regression tests + the grep from Step 3 (paste its empty result).

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/atlas/SurveyControls.tsx ui/src/components/atlas/FovOverlay.tsx ui/src/components/atlas/SkyCanvas.tsx ui/src/views/AtlasView.tsx
git commit -m "fix(atlas): truthful recenter label (target vs mount); delete never-fed activePanel highlight contract (wave-2 §3-4)"
```

---

## Final verification (after Task 3, before the whole-branch review)

- [ ] From `ui/`: `npm run build` + all three tsx tests green.
- [ ] `grep -rn "activePanel\|activeIndex\|knobZone" ui/src` — zero matches.
- [ ] Server suite NOT required (zero server files touched) — state this explicitly in the final report rather than silently skipping.
- [ ] Manual smoke list for the user: grab the stalk and rotate (any angle/zoom, touch + mouse); search "M 31" in header → frames it keeping zoom; search from empty state → opens session; recenter label flips in free-roam.
