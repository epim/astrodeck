# Sequencer Control-Flow Expansion — Design (PRO-3 additive)

Status: DESIGN ONLY (do not implement / do not commit)
Date: 2026-07-24
Builds on: PRO-3 conditional sequencer (`sequence/instructions.py`, `models.py:Instruction`,
`engine.py:_run_instructions` / `_dispatch_actions`, `ui/.../InstructionsPanel.tsx`,
`ui/src/lib/instructions.ts`).

## 0. One-paragraph summary

Three additive capabilities on top of the existing when-this-then-that rule layer,
each guarded so a plan with no rules (and no new fields set) runs **byte-identical**
to today:

1. **Target-jump actions** (`run_target` / `skip_target`) — the only *real* control-flow
   change: a fired rule can jump the scheduler to a named target or drop a named target
   from the night. HIGHEST RISK (infinite-loop vector) — scoped with a hard jump budget +
   once-guard + self-jump no-op.
2. **AND/OR condition grammar** — an optional, **bounded, 1-level** compound (`all`/`any`
   over a closed set of leaf predicates). Not a scripting runtime. Edge-triggered on the
   *whole expression's* rising edge, so the no-double-fire guarantee generalizes cleanly.
3. **Client-side dry-run simulator** — a pure `ui/src/lib` helper: "given this state,
   which rules would fire and why." Preview-only; the server evaluator stays authoritative.

## 1. Current architecture (verified seams)

### Data model — `server/astrodeck/sequence/models.py`
- `Instruction` (`models.py:76`): flat closed-enum shape. Fields: `id, enabled, trigger,
  threshold, at_time, action, message, level, once, cooldown_s, only_target`.
  `_validate_at_time` (`models.py:100`) enforces HH:MM when `trigger=="at_time"`.
- `TriggerKind` (`models.py:69`): `on_hfr_above | on_guide_rms_above | on_frame_rejected |
  on_target_complete | at_time`.
- `ActionKind` (`models.py:73`): `notify | pause | refocus | dither | abort`.
- `SequencePlan.instructions` (`models.py:148`): `list[Instruction] = []` — `[]` ⇒
  byte-identical run.

### Pure evaluator — `server/astrodeck/sequence/instructions.py`
- `evaluate_instructions(instructions, ctx, fire_state)` (`instructions.py:79`): pure,
  no I/O. Gate order per rule: `enabled → only_target → trigger eligibility (+edge) →
  once → cooldown` (`instructions.py:86-125`).
- `TriggerContext` (`:33`): `now_ts, frame_hfr, guide_rms, frame_rejected,
  target_complete, active_target`.
- `FireRecord` (`:43`): `fired_count, last_fire_ts, armed`. Edge-trigger arm/re-arm lives
  in `armed` (`:100-104`, `:124-125`).
- `FiredAction` (`:50`): `instruction_id, action, message, level`.

### Engine — `server/astrodeck/sequence/engine.py`
- Exceptions: `SafetyAbort` (`:160`, terminal wind-down), `StopTarget` (`:167`, advance
  scheduler past current target — **the existing unwind idiom we mirror**),
  `NightQualityStop` (`:173`).
- `_run_scheduled(plan)` (`:769`): the window-sorted skip-ahead scheduler. `remaining`
  list (`:791`); `while remaining:` (`:807`); selects one `ready` target (`:817-820`);
  runs its steps in `for si, step in enumerate(ready.steps): await self._run_step(...)`
  (`:839-840`); post-target `on_target_complete` eval (`:843-848`); `except StopTarget`
  marks skipped + keeps the night going (`:849-853`); `remaining.remove(ready)` (`:854`).
  **This try/except block (`:831-855`) is where the jump signal is caught.**
- `_run_step` (`:1141`): per-frame loop. Per-frame instruction eval at `:1211-1219`
  (guarded by `if plan.instructions`) — builds a `TriggerContext` and calls
  `_run_instructions`. Fires happen at a **safe frame boundary** (after `_reporter_record`,
  `:1207`), the same place a `StopTarget`-shaped unwind is already tolerated.
- `_run_instructions` (`:2215`) → `_dispatch_actions` (`:2228`): maps each `FiredAction`
  to an EXISTING capability. `abort` raised OUTSIDE the try (`:2238-2239`); others guarded.
  `self._fire_state` (`:246`), `self._frozen` (`:240`), `_jumps_spent` (new).

### UI
- `ui/src/lib/instructions.ts`: pure helpers — `defaultInstruction`, `validateInstruction`,
  `describeInstruction`, `triggerNeedsThreshold/Time`, `TRIGGER_LABELS`, `ACTION_LABELS`.
- `ui/src/components/sequence/InstructionsPanel.tsx`: thin render; per-row `advanced`
  disclosure (`:140-189`); honest-disabled viewer path (`:254-257`).
- `ui/src/types.ts:857`: `Instruction` TS mirror; `ui/src/lib/caps.ts:accessPhrase`.

## 2. Feature A — Target-jump actions (HIGHEST RISK)

### 2.1 Model additions (additive, back-compat)
Extend `ActionKind`:
```
ActionKind = Literal["notify","pause","refocus","dither","abort","run_target","skip_target"]
```
Add ONE nullable field to `Instruction`:
```
target_arg: str | None = None   # target NAME for run_target / skip_target; None otherwise
```
Extend `_validate_at_time` (rename intent, keep method) to also require `target_arg`
non-empty when `action in ("run_target","skip_target")`. **Recommendation:** a new field
rather than overloading `message` (message is semantically notify-text/abort-reason) or
`only_target` (that is a *gate*, not a *destination* — conflating them is a footgun).

TS mirror in `types.ts`: add the two action strings + `target_arg: string | null`.

### 2.2 Control-flow mechanism — new `JumpTarget` signal
Add an engine exception mirroring `StopTarget`:
```
class JumpTarget(Exception):
    def __init__(self, kind: str, name: str):  # kind ∈ {"run","skip"}
        self.kind, self.name = kind, name
```
- `_dispatch_actions` raises `JumpTarget("run"/"skip", fa.target_arg)` **outside** the
  per-action try (like `abort`), so it always unwinds cleanly out of `_run_step` and the
  step loop back to `_run_scheduled`.
- `FiredAction` gains a `target_arg: str | None` field so dispatch has the destination.

### 2.3 Scheduler handling — in the `:831-855` try/except
Add `except JumpTarget as j:` alongside `except StopTarget`:
- **`skip("NAME")`**: mark the named target skipped (`reporter.mark_skipped`) and remove
  it from `remaining`. If NAME is the *current* `ready`, the unwind already abandoned it;
  the existing `remaining.remove(ready)` covers it. If NAME is a *future* target, remove
  it there. Skipping an already-gone/unknown name is a **no-op** (logged).
- **`run("NAME")`**: locate the target object by name in `plan.targets`. If it is the
  currently-active target → **no-op** (prevents trivial self-loop). Otherwise: ensure it
  is in `remaining` (re-insert if already removed) and move it to the FRONT so it is the
  next `ready` selected. The current target is **abandoned** (its remaining steps are not
  shot) — this is the real control-flow change. The normal `_target_complete` guard
  (`:832`) still applies, so `run_target` at an already-complete target logs
  "already complete — skipping" rather than re-shooting.

### 2.4 Loop safety — the infinite-loop backstop (CRITICAL)
Three independent guards, defense-in-depth:
1. **Hard jump budget.** `self._jumps_spent` (reset in `_reset` alongside `_fire_state`,
   `:311`), `MAX_JUMPS` constant (recommend **64**). Each *executed* jump increments; once
   exhausted, further jumps are **ignored** (logged `warning`, degrade to normal
   scheduling) — the night NEVER hangs and NEVER aborts on this alone. This is the true
   backstop against the `A→run B, B→run A` (both incomplete) cycle.
2. **Self-jump no-op** (§2.3): `run_target` to the active target does nothing.
3. **Once-default in the UI** (§2.6): jump rules default `once=true`, and `cooldown_s`
   remains available. Author-level throttle; not relied on for correctness — guard #1 is.

**Recommendation:** budget-exhaustion logs and degrades (does not abort). Aborting a real
imaging night over a rule-authoring mistake is worse than finishing the plan normally.

### 2.5 Why the unwind is safe
Fires occur only at the frame boundary in `_run_step` (`:1219`), AFTER
`_reporter_record` and frame bookkeeping (`:1207`), the same boundary `StopTarget` already
unwinds through. No partial-frame state leaks; panel-off/park wind-down is unaffected
(those live on the terminal `SafetyAbort`/`_run` arms, not disturbed by `JumpTarget`).
`on_target_complete` for the abandoned target is intentionally NOT fired (it did not
complete) — matches `StopTarget` semantics.

### 2.6 UX (progressive disclosure)
- Jump actions appear in the existing action `<select>`, labelled "Jump to target" /
  "Skip target". Selecting one swaps the notify/abort message row for a **target picker**
  (`<select>` of `plan.targets` names — reuses `targetNames`).
- **Honest-disabled (§11.8):** when the plan has `< 2` targets, the two jump options are
  dimmed + `aria-disabled` + `title="Target jumps need 2 or more targets."` — never the
  native `disabled` attribute.
- Jump rules created via `defaultInstruction`-style factory default `once=true`, and the
  row shows a one-line plain-language note: *"Jumps change which target runs next.
  'Once' is on so a rule can't loop."* Novices never see jumps unless they open the action
  menu; the default rule stays notify-on-reject.

## 3. Feature B — AND/OR condition grammar (bounded, 1-level)

### 3.1 Model — optional compound `when`
Add ONE optional field to `Instruction`:
```
class Predicate(BaseModel):
    kind: Literal["hfr_above","guide_rms_above","frame_rejected",
                  "target_complete","at_time"]
    threshold: float = Field(0.0, ge=0)
    at_time: str | None = None

class Condition(BaseModel):
    op: Literal["all","any"]              # all = AND, any = OR
    terms: list[Predicate] = Field(min_length=2, max_length=8)

# on Instruction:
when: Condition | None = None            # None ⇒ flat single-trigger path (unchanged)
```
- **1-level only** (op + leaf predicates, no nested `Condition`). Closed vocabulary,
  reusing the existing trigger vocab as predicate `kind`s. This is a *composition of a
  bounded predicate set*, not a scripting runtime.
- Validation: each `at_time` predicate needs valid HH:MM; `terms` length 2–8.
- **Back-compat:** `when is None` for every existing/serialized plan ⇒ the flat path
  (`trigger/threshold/at_time`) runs exactly as today. When `when` is set, the flat
  `trigger` field is **ignored** (kept in the schema with its default to avoid churn).

### 3.2 Evaluator — extend `evaluate_instructions`
Refactor leaf-eligibility into `_eval_predicate(kind, threshold, at_time, ctx) ->
bool | None` (None = metric unreadable). The existing flat branch calls it for the single
`trigger`; the compound branch calls it per term. Compound semantics:
- Compute each term to `bool | None`. **Indeterminate rule:** if any term needed to decide
  the expression is `None` (unreadable), the compound is treated as *not eligible* and
  `armed` is left unchanged — mirrors the flat `v is None` path (`instructions.py:98-99`).
  (all: a `None` when no term is decisively False ⇒ indeterminate; any: a `None` when no
  term is decisively True ⇒ indeterminate.)
- **Edge-trigger the whole expression:** treat the composed boolean as a level. Fire on the
  false→true crossing; re-arm (`armed=True`) when it evaluates decisively false. This
  *unifies* the semantics: a momentary predicate (`frame_rejected`/`target_complete`) makes
  the expression briefly true → fires once → next frame it is false → re-arms. `once` /
  `cooldown` gates apply unchanged AFTER eligibility, exactly as the flat path.

This keeps `FireRecord` unchanged and preserves the no-double-fire guarantee by
construction.

### 3.3 UX (progressive disclosure)
- Flat single-trigger stays the **default** row. Inside the existing per-row `advanced`
  disclosure (`InstructionsPanel.tsx:140`), add a **"Combine conditions (AND/OR)"** link.
  Clicking it sets `when` to `{op:"all", terms:[<current flat trigger as a predicate>, <a
  second default predicate>]}` — a lossless upgrade so the author never loses their first
  condition. The row then renders an `all`/`any` toggle + a small list of predicate rows
  (each: kind `<select>` + threshold/time as needed) + add/remove term buttons (2–8).
- A "back to single condition" link clears `when` (drops to `terms[0]`). Novice path is
  untouched: they never open advanced, never see AND/OR.
- `describeInstruction` gains a compound branch: `"When HFR > 3.5 AND guide RMS > 1.2\" →
  refocus"`.

## 4. Feature C — client-side dry-run simulator (pure lib)

### 4.1 Helper — `ui/src/lib/instructionSim.ts` (new, pure, tested)
```
interface SimSnapshot {
  hfr: number | null; guideRms: number | null;
  frameRejected: boolean; targetComplete: boolean;
  now: string;              // "HH:MM"
  activeTarget: string | null;
}
type Outcome =
  | { id; wouldFire: true;  summary }                       // fires this frame
  | { id; wouldFire: false; reason: string;                 // gated / not met
      gate: "disabled"|"only_target"|"not_met"|"needs_input"|"once_or_cooldown" };

function simulateInstructions(rules: Instruction[], s: SimSnapshot): Outcome[]
```
- **Single-frame, "assuming armed" preview.** It mirrors the server gate ORDER
  (`enabled → only_target → predicate → note once/cooldown`) and the compound `all`/`any`
  logic, but does NOT reconstruct runtime edge/arm/cooldown history — it explicitly
  reports once/cooldown as an informational note ("would fire, subject to once/cooldown")
  rather than pretending to know prior fires. Copy makes this honest: *"Preview assumes
  each rule is ready to fire — actual runs also apply edge/once/cooldown timing."*
- Pure, no React, no clock (takes `now` as input) ⇒ fully unit-testable.

### 4.2 UX (progressive disclosure — the novice win)
- A **"Preview what these rules do"** button in `InstructionsPanel` (visible whenever
  ≥1 rule exists, works for novices with zero config). One tap opens a compact panel:
  - Sensible **default snapshot**: HFR/guide-RMS sliders pre-set to mid-range, a
    "current target" dropdown (defaults to first target), a "frame rejected?" toggle, and
    a time field defaulted to now. Every control optional — the preview renders instantly
    with defaults.
  - Renders the rule list with each rule **lit green ("would fire")** or **dim ("waiting:
    <reason>")**, updating live as the sliders/toggles move. This is the "just works,
    one obvious tap" novice affordance and rewards experimentation for experts.
- The panel is a thin render over `simulateInstructions` — no logic in the component.

## 5. Byte-identical guarantee (unchanged contract)
A plan with `instructions == []` never enters any new path (existing `if
plan.instructions` guards at `:843` and `:1211`). A plan WITH rules but `when=None` and no
`run_target`/`skip_target` actions runs exactly the PRO-3 path — the compound branch is
behind `if i.when is not None`, and `JumpTarget` is only raised for the two new actions.
`_jumps_spent`/`MAX_JUMPS` are inert unless a jump action fires. Covered by the existing
`test_empty_is_noop` (`test_instructions_eval.py:13`) and `test_no_instructions_is_noop`
(`test_instructions_engine.py:68`) — assert they still pass unchanged; add nothing there.

## 6. LEAN test plan (estimated NET new: ~7 tests)

Discipline: parametrize related cases into ONE test, reuse `sim_hub`/`temp_store`
fixtures, keep pure logic in tested lib/eval helpers and leave the render shells
untested-by-DOM.

**Server (~4-5 net):**
1. `test_instructions_eval.py` — ONE parametrized test for the compound grammar: rising
   edge of `all` and `any`, re-arm on decisive-false, `None`-indeterminate (leave armed),
   momentary predicate inside a compound fires-once-then-rearms. (1 test, ~6 cases.)
2. `test_instructions_model.py` — ONE parametrized validation test: `run_target`/
   `skip_target` require `target_arg`; `when` term-count 2–8; `at_time` predicate needs
   HH:MM. (1 test.)
3. `test_instructions_engine.py` — the HIGH-RISK integration test(s), reusing `sim_hub`:
   (a) `run_target` jumps the scheduler to a named target and abandons the current one;
   `skip_target` drops a future target from the night. (b) **infinite-loop backstop**: a
   two-target plan whose rules mutually `run_target` each other terminates via `MAX_JUMPS`
   (assert the run ends and the budget-exhaustion warning is logged), NOT a hang. (2 tests.)
4. Existing byte-identical tests: assert-still-pass, no new test.

**UI (~2-3 net):**
5. `instructionSim.test.ts` (new) — ONE parametrized test over `simulateInstructions`:
   fires / gated-by-only_target / threshold-not-met / needs-input / compound all+any.
   (1 test, ~6 cases.)
6. `instructions.test.ts` — extend the existing file: compound `describeInstruction` +
   `validateInstruction` for jumps (`target_arg` required) and compound (term count).
   (1 parametrized test.)

No DOM/render tests for `InstructionsPanel` or the preview panel (thin shells over tested
helpers), consistent with the current "logic in lib, thin render" split.

## 7. Open decisions (each with a recommendation)

1. **Jump destination field.** New `target_arg` vs reuse `message`/`only_target`.
   → **New `target_arg`** (avoids semantic overloading; `only_target` stays a gate).
2. **Compound nesting.** 1-level (op + leaf terms) vs arbitrary tree.
   → **1-level for v1.** Covers "A AND B" / "A OR B"; nesting is a bounded follow-up if
   ever needed. Keeps it a closed vocabulary, not a scripting runtime.
3. **`run_target` and the current target.** Abandon-and-jump vs finish-then-jump vs
   requeue-current-for-later. → **Abandon-and-jump** (simplest real control-flow; the
   `_target_complete` guard prevents re-shooting a done target). Requeue is a follow-up.
4. **Jump budget on exhaustion.** Abort the night vs degrade to normal scheduling.
   → **Degrade + warn** (finishing the plan beats aborting a real night over a rule typo).
   Recommend `MAX_JUMPS = 64`.
5. **`when` vs flat `trigger` coexistence.** → **`when` overrides**; flat `trigger`
   ignored when `when != null` (kept in schema with default to avoid churn). One active
   path per rule, validated.
6. **Dry-run fidelity.** Replicate edge/cooldown history vs single-frame "assuming armed".
   → **Single-frame preview** with explicit honest copy; runtime timing is the server's
   job. Avoids a second, drift-prone semantics implementation.
7. **`on_target_complete` on an abandoned (`run_target`) target.** → **Do not fire** (it
   did not complete; matches `StopTarget`).

## 8. Risks

- **HIGHEST — infinite loop via jumps.** Mitigated by the hard `MAX_JUMPS` budget (true
  backstop), self-jump no-op, once-default, and the `_target_complete` guard. The
  integration test (§6.3b) pins the backstop.
- **Unwind through `_run_step` skipping cleanup.** Mitigated by raising `JumpTarget` only
  at the frame boundary in `_dispatch_actions` (the same safe point `StopTarget` uses),
  after frame recording; terminal wind-down arms are untouched.
- **Byte-identical regression.** Mitigated by guarding every new branch behind
  `plan.instructions` / `when is not None` / new-action checks; existing no-op tests pin it.
- **Schema/type mirror drift** (pydantic ↔ `types.ts` ↔ lib). Mitigated by the model
  validation test (§6.2) + keeping the TS `Instruction`/`Condition`/`Predicate` shapes in
  lockstep in the same change.
- **Dry-run divergence from server semantics.** Mitigated by scoping it as an explicitly
  labelled single-frame preview (edge/once/cooldown noted, not simulated).
- **UI complexity creep.** Mitigated by keeping compound + jumps behind the existing
  `advanced` disclosure; the novice default (flat notify + one-tap preview) is unchanged.
```
