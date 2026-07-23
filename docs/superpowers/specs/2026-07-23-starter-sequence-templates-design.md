# NOV-5 — Starter sequence templates (novice, 1–2 taps)

Combined design spec + TDD implementation plan. One file.

---

## 1. Design

### Goal
Give a first-timer a one-tap way to fill a target's exposure recipe from a tiny
gallery of ready-made starters ("60 × 120s Luminance", "OSC broadband 30 × 180s",
"Quick 20 × 60s test") instead of hand-typing the expert step grid. A novice
who has added a target (catalog search → tap a result) taps one starter chip and
that target now holds a real, runnable step list — no filter/gain/exposure/count
typing. Reuse the existing plan model (`ExposureStep[]`) and the existing
reversible-edit machinery; invent no new plan shape.

### Current-state seams (all read; cited file:line)

- **`ui/src/types.ts:386-397`** — `ExposureStep` is the plan's per-step shape
  (`id?`, `filter: string|null`, `exposure_s`, `gain`, `offset`, `binning`,
  `count`, `frame_type: string`). `id` is optional (backfilled). A template's
  output is a plain `ExposureStep[]` — no new type on the plan side.
- **`ui/src/types.ts:399-415`** — `Target.steps: ExposureStep[]`; a template
  replaces one target's `steps`.
- **`ui/src/views/SequenceView.tsx:26-28`** — `DEFAULT_STEP` (the values a
  hand-added step gets: `gain:100, offset:30, binning:1, count:10, frame_type:"Light"`,
  `filter:null`, `exposure_s:120`). A freshly-added target carries exactly one of
  these (`addTarget`, `SequenceView.tsx:317-318`). Our starter-step baseline mirrors
  the non-exposure fields so a template-applied step is indistinguishable from a
  hand-added one except in the values the template deliberately sets.
- **`ui/src/views/SequenceView.tsx:143-153`** — `setPlanWithUndo(label, next)` /
  `doUndo()`: instant-apply plan edits with a 5s undo toast (the SAME machinery
  the mosaic "apply to all panels" copy and target/step deletes use). Applying a
  template is exactly this kind of reversible, potentially-clobbering edit → it
  MUST go through `setPlanWithUndo` (a mis-tap on a built-up target is then one
  Undo away).
- **`ui/src/views/SequenceView.tsx:256-262`** — `applyGroupSteps(group, sourceTi)`
  + **`ui/src/lib/planGroups.ts:12-22`** `applyStepsToGroup(...)`: the existing
  "copy this panel's steps onto every group member" flow. It is **mosaic_group-keyed**,
  so it is not the right primitive for "replace ONE target's steps"; we reuse the
  *undo wrapper* it sits on (`setPlanWithUndo`), not `applyStepsToGroup` itself.
  This is the "apply/add machinery" the brief points at.
- **`ui/src/views/SequenceView.tsx:239`** — `const filters = status?.filterwheel?.names ?? []`.
  The connected wheel's real filter names. The step editor's filter `<select>`
  (`SequenceView.tsx:671-675`) only offers `""` (no filter) + these names, so a
  template that hard-codes a filter string absent from the wheel would render as a
  blank select. The template's filter is therefore an **intent** resolved against
  this list at apply time (mono wheel "L" vs OSC no-wheel).
- **`ui/src/views/SequenceView.tsx:628-631`** — the per-target `+ step` button:
  `disabled={running}`, `min-h-[44px]`, `!text-[11px]`. Our starter chips are the
  same class of run-time-locked plan-edit control and follow this native-`disabled={running}`
  sibling convention (see §2 note on honest-disabled scope).
- **`ui/src/store.ts:759-767`** — `setPlan(p, dirty=true)` runs `ensurePlanIds(p)`,
  so steps handed in **without** an `id` get fresh ids minted for free
  (`ui/src/lib/ids.ts:28-32`). Applying a template marks the editor dirty (correct —
  it's an edit; `PlanLibraryPanel` cue reads "Unsaved changes").
- **`ui/src/components/sequence/PlanLibraryPanel.tsx:23`** + **`ui/src/lib/planLibrary.ts:10-14`**
  — the mirror pattern for this feature: catalog/branching logic lives in a pure
  `lib/*.ts` with a test; the panel is a thin render. We follow it exactly
  (`lib/sequenceTemplates.ts` pure + tested, gallery render thin).
- **Test idiom** — `ui/src/lib/__tests__/eta.test.ts:1-43` and
  `ui/src/components/ui/__tests__/SegmentedControl.test.tsx:20-30`: dependency-free
  inline-assert harness run under `npx tsx`, no jsdom. Our pure logic gets the same.

### Approach (concrete)

**Data (pure).** A new `ui/src/lib/sequenceTemplates.ts`:
- `SEQUENCE_TEMPLATES: SequenceTemplate[]` — the three-card catalog (pure data).
- A `SequenceTemplate` authoring shape carrying `TemplateStepSpec[]` (filter
  *intent* + `exposure_s` + `count` only — the authoring convenience, NOT a plan
  shape).
- `resolveFilter(intent, available)` — case-insensitive best-effort match of a
  filter intent to a real wheel name (tiny alias table: Luminance ↔ Lum ↔ L),
  falling back to `null` (no filter) when unmatched / OSC / no wheel.
- `templateSteps(t, available)` — the required pure **template → `ExposureStep[]`**
  mapping: fills each spec onto the `STARTER_BASE` baseline (gain/offset/bin/Light),
  resolves the filter, emits full `ExposureStep`s **without ids** (`setPlan`'s
  `ensurePlanIds` mints them). This is the function the tsx test exercises.

**Copy tables.**

| id            | label (chip)                | blurb (title=)                                             | steps (intent × exp × count) |
|---------------|-----------------------------|------------------------------------------------------------|------------------------------|
| `lum-60x120`  | `60 × 120s Luminance`       | Deep mono luminance — the backbone of an LRGB image.       | Luminance × 120s × 60        |
| `osc-30x180`  | `OSC broadband 30 × 180s`   | One-shot-colour broadband — no filter wheel needed.        | (no filter) × 180s × 30      |
| `quick-20x60` | `Quick 20 × 60s test`       | A fast test run to check framing, focus and tracking.      | (no filter) × 60s × 20       |

`STARTER_BASE = { gain: 100, offset: 30, binning: 1, frame_type: "Light" }`
(mirrors `DEFAULT_STEP`, `SequenceView.tsx:26-28`).

**Filter resolution behavior.** `resolveFilter("Luminance", ["L","R","G","B"]) → "L"`;
`resolveFilter("Luminance", ["Luminance"]) → "Luminance"`; `resolveFilter("Luminance", []) → null`;
`resolveFilter(null, anything) → null`. So the Luminance starter lands on a mono
user's actual L filter, and cleanly falls back to no-filter for an OSC rig. The two
broadband starters carry `filter:null` and are always no-filter.

**Behavior.** Tapping a chip **replaces** that target's `steps` with
`templateSteps(tpl, filters)` via `setPlanWithUndo("Applied '<label>' to <name>", next)`.
Replace (not append) matches "use this starter as the recipe"; the 5s Undo covers a
mis-tap on a target the user had already built up. Chips are `disabled` while a run
is in progress (`running`), matching the sibling `+ step` / delete controls.

**Placement.** A single subtle chip row **inside `renderTarget`**, between the
target header/action row (ends `SequenceView.tsx:656`) and the steps grid
(`SequenceView.tsx:657`): a dim `starter` label + one `<button>` per template.
Always visible (no toggle state — thinnest render, and the novice finds it right
where they'd type steps); experts ignore it. It reads as part of each target card,
so it works for the empty-plan first-timer and for adding a second target alike.
No plan-level/empty-state gallery in v1 (a template needs a target's RA/Dec to be
runnable; there is no target-less "plan" to drop steps onto).

---

## 2. Global Constraints (verbatim, binding)

- **Privacy.** The real coordinates **37.348110 / 121.801704** and the label
  **"My Backyard"** MUST NEVER appear in code, tests, or docs. Site default is
  **"My Observatory" / 0.0**. (This feature introduces no coordinates at all —
  templates carry only exposure recipes.)
- **Never `git add -A`.** Stage explicit paths only.
- **UI typecheck gate:** `cd ui && npx tsc -b`.
- **NO jsdom / DOM harness.** Pure logic is tested via `npx tsx` inline-assert
  (idioms: `ui/src/components/ui/__tests__/SegmentedControl.test.tsx`,
  `ui/src/components/__tests__/healthStrip.test.ts`, `ui/src/lib/__tests__/eta.test.ts`).
- **Backend tests:** `server/.venv/Scripts/pytest.exe` (run from repo root; `-n0`
  for a single test). *(No backend in this feature.)*
- **Client toasts** via `useStore.getState().enqueueToast` (this feature raises no
  toasts of its own — it uses the existing `setPlanWithUndo` undo toast).
- **Honest-disabled idiom (§11.8):** dim token + lock glyph + `aria-disabled` +
  `title`, never native `disabled` — this is for **capability**-gated controls.
  The plan BUILDER is available to every role (local state, not a device write),
  so starter chips are NOT capability-gated. Their only disable reason is the
  transient `running` run-lock, which across this view uses native `disabled={running}`
  (`+ step` `SequenceView.tsx:628`, delete `:646`, apply-to-all `:620`). Starter
  chips follow that sibling convention — do not flag them for §11.8.
- **Do not disrupt astrotown.**

---

## 3. TDD Plan

Two tasks. Task 1 is the pure, tested core; Task 2 is the thin render + wiring
(verified by `tsc -b`, no new DOM test — per the constraint above).

### Task 1 — pure catalog + `template → ExposureStep[]` mapping (+ tsx test)

**Files**
- Create: `ui/src/lib/sequenceTemplates.ts`
- Create (test): `ui/src/lib/__tests__/sequenceTemplates.test.ts`

**Interfaces (exact)**
```ts
// ui/src/lib/sequenceTemplates.ts
import type { ExposureStep } from "../types";

export interface TemplateStepSpec {
  /** Filter INTENT, resolved against the connected wheel at apply time
   *  (resolveFilter). null = broadband / no filter (any OSC rig). */
  filter: string | null;
  exposure_s: number;
  count: number;
}

export interface SequenceTemplate {
  id: string;      // stable catalog id (React key, test identity)
  label: string;   // gallery chip text, e.g. "60 × 120s Luminance"
  blurb: string;   // one-line description (chip title=)
  steps: TemplateStepSpec[];
}

export const SEQUENCE_TEMPLATES: SequenceTemplate[];

/** Case-insensitive best-effort match of a filter INTENT to a real wheel name
 *  (tiny alias table), else null. Pure. */
export function resolveFilter(intent: string | null, available: string[]): string | null;

/** Instantiate a template into concrete ExposureStep[] for a target. Steps carry
 *  NO id — setPlan.ensurePlanIds mints fresh ones (sessions spec §1). Pure. */
export function templateSteps(t: SequenceTemplate, available?: string[]): ExposureStep[];
```

**Steps (actual code)**

1. Write `ui/src/lib/sequenceTemplates.ts`:
```ts
// sequenceTemplates.ts — starter exposure recipes for first-timers (NOV-5). Pure
// data + a pure template->ExposureStep[] mapping; SequenceView renders a thin
// gallery over SEQUENCE_TEMPLATES and applies templateSteps() through
// setPlanWithUndo. No new PLAN shape — the output is plain ExposureStep[]
// (types.ts:386), mirroring the plan-library "logic in lib, thin render"
// split (lib/planLibrary.ts).
import type { ExposureStep } from "../types";

// Non-exposure step baseline. Mirrors SequenceView.DEFAULT_STEP's gain/offset/
// bin/frame_type (SequenceView.tsx:26-28) so a template-applied step is
// indistinguishable from a hand-added one except in the values a template sets.
const STARTER_BASE = { gain: 100, offset: 30, binning: 1, frame_type: "Light" } as const;

// Filter-intent aliases (lowercased). A mono wheel usually exposes "L"; the
// Luminance starter should still land on it. Broadband starters use null intent.
const FILTER_ALIASES: Record<string, string[]> = {
  luminance: ["luminance", "lum", "l"],
};

export interface TemplateStepSpec {
  filter: string | null;
  exposure_s: number;
  count: number;
}

export interface SequenceTemplate {
  id: string;
  label: string;
  blurb: string;
  steps: TemplateStepSpec[];
}

export const SEQUENCE_TEMPLATES: SequenceTemplate[] = [
  {
    id: "lum-60x120",
    label: "60 × 120s Luminance",
    blurb: "Deep mono luminance — the backbone of an LRGB image.",
    steps: [{ filter: "Luminance", exposure_s: 120, count: 60 }],
  },
  {
    id: "osc-30x180",
    label: "OSC broadband 30 × 180s",
    blurb: "One-shot-colour broadband — no filter wheel needed.",
    steps: [{ filter: null, exposure_s: 180, count: 30 }],
  },
  {
    id: "quick-20x60",
    label: "Quick 20 × 60s test",
    blurb: "A fast test run to check framing, focus and tracking.",
    steps: [{ filter: null, exposure_s: 60, count: 20 }],
  },
];

export function resolveFilter(intent: string | null, available: string[]): string | null {
  if (intent == null) return null;
  const key = intent.trim().toLowerCase();
  const wanted = FILTER_ALIASES[key] ?? [key];
  const hit = available.find((n) => wanted.includes(n.trim().toLowerCase()));
  return hit ?? null;
}

export function templateSteps(t: SequenceTemplate, available: string[] = []): ExposureStep[] {
  return t.steps.map((s) => ({
    ...STARTER_BASE,
    filter: resolveFilter(s.filter, available),
    exposure_s: s.exposure_s,
    count: s.count,
  }));
}
```

2. Write `ui/src/lib/__tests__/sequenceTemplates.test.ts` (harness copied from
   `eta.test.ts:21-43`):
```ts
// Pure tests for the NOV-5 starter template catalog + mapping. No jsdom — runs
// under `npx tsx`, compiles under `tsc -b` (idiom: lib/__tests__/eta.test.ts).
//   npx tsx src/lib/__tests__/sequenceTemplates.test.ts
import {
  SEQUENCE_TEMPLATES,
  resolveFilter,
  templateSteps,
} from "../sequenceTemplates";
import type { ExposureStep } from "../../types";

let passed = 0, failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`✗ ${name}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// -------- catalog integrity
test("catalog: non-empty, unique ids, ≥1 positive-valued step each", () => {
  assert(SEQUENCE_TEMPLATES.length >= 3, "expected the 3 briefed starters");
  eq(new Set(SEQUENCE_TEMPLATES.map((t) => t.id)).size, SEQUENCE_TEMPLATES.length, "ids unique");
  for (const t of SEQUENCE_TEMPLATES) {
    assert(t.label.length > 0 && t.blurb.length > 0, `${t.id} needs label+blurb`);
    assert(t.steps.length >= 1, `${t.id} needs steps`);
    for (const s of t.steps) {
      assert(s.exposure_s > 0, `${t.id} exposure_s > 0`);
      assert(Number.isInteger(s.count) && s.count > 0, `${t.id} count int > 0`);
    }
  }
});

// -------- resolveFilter
test("resolveFilter: Luminance intent lands on a mono wheel's 'L'", () => {
  eq(resolveFilter("Luminance", ["L", "R", "G", "B"]), "L", "L");
});
test("resolveFilter: exact name preserved (case-insensitive alias)", () => {
  eq(resolveFilter("Luminance", ["Luminance"]), "Luminance", "exact");
  eq(resolveFilter("luminance", ["Lum"]), "Lum", "alias+case");
});
test("resolveFilter: no wheel / no match / null intent → null", () => {
  eq(resolveFilter("Luminance", []), null, "no wheel");
  eq(resolveFilter("Luminance", ["Ha", "OIII"]), null, "no match");
  eq(resolveFilter(null, ["L"]), null, "null intent");
});

// -------- templateSteps
test("templateSteps: emits full ExposureStep on STARTER_BASE, no id", () => {
  const lum = SEQUENCE_TEMPLATES.find((t) => t.id === "lum-60x120")!;
  const steps: ExposureStep[] = templateSteps(lum, ["L", "R", "G", "B"]);
  eq(steps.length, 1, "one step");
  const s = steps[0];
  eq(s.exposure_s, 120, "exp"); eq(s.count, 60, "count"); eq(s.filter, "L", "resolved filter");
  eq(s.gain, 100, "gain"); eq(s.offset, 30, "offset"); eq(s.binning, 1, "bin");
  eq(s.frame_type, "Light", "frame_type"); eq(s.id, undefined, "no id (ensurePlanIds mints)");
});
test("templateSteps: OSC rig (no wheel) → filter null for every step", () => {
  for (const t of SEQUENCE_TEMPLATES) {
    for (const s of templateSteps(t, [])) eq(s.filter, null, `${t.id} filter null on OSC`);
  }
});
test("templateSteps: frame totals match labels (60/30/20)", () => {
  const total = (id: string) =>
    templateSteps(SEQUENCE_TEMPLATES.find((t) => t.id === id)!).reduce((a, s) => a + s.count, 0);
  eq(total("lum-60x120"), 60, "lum frames");
  eq(total("osc-30x180"), 30, "osc frames");
  eq(total("quick-20x60"), 20, "quick frames");
});

const total = passed + failed;
console.log(`\nsequenceTemplates.test: ${passed}/${total} passed`);
if (failures.length) { console.error(failures.join("\n")); (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1); }
export const result = { passed, failed, total };
```

**Verify (exact commands + expected output)**
```
cd ui && npx tsx src/lib/__tests__/sequenceTemplates.test.ts
```
Expected (last line): `sequenceTemplates.test: 8/8 passed`  (no `✗` lines, exit 0)
```
cd ui && npx tsc -b
```
Expected: clean (no output, exit 0).

**Impl tier: Sonnet.** Mechanical, pattern-following (mirrors `lib/planLibrary.ts`
+ `eta.test.ts`); `resolveFilter`'s aliasing is small and fully pinned by tests.

---

### Task 2 — thin gallery render + apply wiring in SequenceView

**Files**
- Modify: `ui/src/views/SequenceView.tsx`

**Interfaces (exact)**
```ts
// new import
import { SEQUENCE_TEMPLATES, templateSteps, type SequenceTemplate } from "../lib/sequenceTemplates";

// new handler in the component body (near applyGroupSteps, SequenceView.tsx:256)
const applyTemplate: (ti: number, tpl: SequenceTemplate) => void;
```

**Steps (actual code)**

1. Add the import beside the existing `applyStepsToGroup` import
   (`SequenceView.tsx:13`):
```ts
import { SEQUENCE_TEMPLATES, templateSteps, type SequenceTemplate } from "../lib/sequenceTemplates";
```

2. Add the handler right after `applyGroupSteps` (after `SequenceView.tsx:262`).
   `filters` (`SequenceView.tsx:239`) and `setPlanWithUndo` are already in scope:
```ts
// Starter templates (NOV-5): replace ONE target's steps with a ready-made
// recipe from SEQUENCE_TEMPLATES, resolving filter intents against the connected
// wheel. Reversible (setPlanWithUndo — same 5s-undo path as delete / apply-to-all),
// so a mis-tap on a built-up target is one Undo away. ids are minted by
// setPlan.ensurePlanIds (store.ts:760).
const applyTemplate = (ti: number, tpl: SequenceTemplate) => {
  const target = plan.targets[ti];
  setPlanWithUndo(
    `Applied '${tpl.label}' to ${target.name}`,
    {
      ...plan,
      targets: plan.targets.map((t, i) =>
        i === ti ? { ...t, steps: templateSteps(tpl, filters) } : t),
    },
  );
};
```

3. Render the chip row inside `renderTarget`, between the header/action row's
   closing `</div>` (`SequenceView.tsx:656`) and the steps block that opens at
   `SequenceView.tsx:657` (`<div className="mt-2 flex flex-col gap-1.5">`):
```tsx
{/* Starter templates (NOV-5): one-tap exposure recipes for first-timers.
    Always visible on each target card; run-locked like +step/delete. */}
<div className="mt-2 flex flex-wrap items-center gap-1.5">
  <span className="label !text-[9px] text-dim">starter</span>
  {SEQUENCE_TEMPLATES.map((tpl) => (
    <button
      key={tpl.id}
      type="button"
      className="tap min-h-[44px] !px-2.5 !text-[11px] inline-flex items-center gap-1
        border border-line2 text-dim hover:text-accent hover:border-accent/50 disabled:opacity-40"
      disabled={running}
      title={tpl.blurb}
      aria-label={`Apply starter template ${tpl.label} to ${t.name}`}
      onClick={() => applyTemplate(ti, tpl)}
    >
      <Icon name="plan" size={12} /> {tpl.label}
    </button>
  ))}
</div>
```
   (`Icon` is already imported `SequenceView.tsx:9`; `plan` is a valid icon name,
   `icons.tsx:38`.)

**Verify (exact command + expected output)**
```
cd ui && npx tsc -b
```
Expected: clean (no output, exit 0). Thin render — no new DOM test (per §2: no
jsdom; the pure logic behind it is covered by Task 1's tsx test).

Manual smoke (optional, not a gate): add a target → tap a starter chip → the
step grid becomes the recipe; an Undo toast appears; Undo restores the prior
steps; chips are disabled while a run is running.

**Impl tier: Sonnet.** Mechanical wiring against an established per-target render
+ the existing `setPlanWithUndo` idiom; no new algorithm.

---

## 4. Open decisions

- **Replace vs append the target's steps.** *Recommend REPLACE* — a starter is
  "the recipe for this target", and the 5s Undo (`setPlanWithUndo`) removes the
  data-loss risk. Append would double up on a freshly-added target's DEFAULT_STEP.
- **Always-visible chip row vs a "Starter templates ▾" toggle.** *Recommend
  always-visible* (thinnest render, best discovery for the novice who is looking
  right at the step grid). Revisit only if experts report clutter — a toggle
  keyed by target id is a trivial follow-up.
- **Fourth "LRGB (L+R+G+B)" multi-step starter.** *Recommend defer* — the brief
  names exactly three; a novice with a mono wheel is the less common first-timer,
  and `resolveFilter` already makes adding one later a pure-data edit (no logic
  change). Catalog test invariants already cover any future addition.
- **Group-aware apply (fan a template across a mosaic group in one tap).**
  *Recommend defer* — the existing per-panel "apply to all panels"
  (`SequenceView.tsx:611-627`) already lets a novice template one panel then fan
  it out, so no new primitive is needed for v1.
- **Filter alias breadth (only Luminance today).** *Recommend keep minimal* —
  the two broadband starters are `null` intent, so Luminance is the only intent
  that needs matching; widen the alias table only when a template needs R/G/B/Ha
  intents (pure-data, test-covered).
