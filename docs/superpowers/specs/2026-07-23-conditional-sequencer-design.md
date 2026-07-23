# PRO-3 — Conditional / trigger-based sequencing (design + TDD plan)

_2026-07-23. Combined design spec + test-driven implementation plan. One file._

Feature slug: `conditional-sequencer`

---

## 1. Design

### 1.1 Goal

Give a pro an **additive, bounded when-trigger-do-action instruction layer** on top of
the existing fixed plan (`targets × steps`), so custom recovery logic and hardware
side-effects the fixed scheduler cannot express become author-editable rules — WITHOUT
an arbitrary scripting runtime and WITHOUT changing any existing sequence.

Scope v1 is a **small closed vocabulary** of triggers, one optional gate, and actions,
each action mapping to an **already-existing engine capability**. When a plan carries no
instructions the run is **byte-identical** to today: the evaluator returns `[]` and the
engine's new block is skipped entirely.

Non-goals (deferred, §5): a rich condition grammar, target-jumping (`run_target`), a
dedicated roof-close action, a client-side dry-run simulator, a timer thread for
sub-frame `at_time` precision.

### 1.2 Current-state seams (grounded — every line read)

**The fixed plan shape (extend ADDITIVELY):**
- `server/astrodeck/sequence/models.py:66` `SequencePlan` — the frozen plan. New field
  `instructions: list[Instruction] = []` appends here (after `warm_cooler_when_done`,
  `models.py:99`); `total_frames`/`total_seconds` (`models.py:101-105`) are untouched.
- `models.py:10` `ExposureStep`, `models.py:49` `Target`, `models.py:26` `Schedule` — the
  additive-field precedent: every new block defaults so "existing plans deserialize
  unchanged" (e.g. `Schedule` docstring `models.py:29-33`; PRO-14 fields `models.py:38-41`).

**The engine already has IMPLICIT triggers hard-wired — the model for explicit ones:**
- `server/astrodeck/sequence/engine.py:1105` `_run_step` — the per-frame capture loop.
  Inside the `while` (`engine.py:1127`) the existing implicit triggers fire, in order:
  - meridian-flip trigger `_maybe_meridian_flip` (`engine.py:1137`, def `:1894`)
  - guiding-recovery trigger `_maybe_recover_guiding` (`engine.py:1138`, def `:1981`)
  - **dither** trigger + action (`engine.py:1140-1153`) — `self.hub.guider.dither(...)`
  - **refocus** trigger `_refocus_due` (`:1155`, def `:1999`) → **action** `_autofocus`
    (`:1156`, def `:2077`)
  - quality gate `_check_quality` (`:1170`, def `:2025`) → `_reporter_record` (`:1171`) →
    accept branch (`:1172`) / reject handling (`_handle_reject` `:1202`, def `:1232`).
- `engine.py:2015` `_guide_rms()` — current total guide RMS (arcsec) or `None`. Exactly
  the read the `on_guide_rms_above` trigger needs; already used by `_check_quality:2059`.
- `_check_quality` reads `info["hfr"]` (`engine.py:2040`) — the same per-frame HFR the
  `on_hfr_above` trigger reads.

**Actions map 1:1 to existing capabilities (no new teardown code):**
- `refocus` → `self._autofocus(label)` (`engine.py:2077`).
- `dither`  → `self.hub.guider.dither(plan.dither_pixels)` (`engine.py:1145`), bounded by
  `_bounded(..., GUIDE_OP_TIMEOUT_S, ...)` (`engine.py:112`, const `:89`).
- `pause`   → `self.pause()` (`engine.py:287`): clears `self._paused`; the loop's
  `_checkpoint()` (`:567`, called top-of-loop `:1128`) then blocks until operator resume.
- `notify`  → `bus.log(level, msg, "sequence")`; the AlertDispatcher routes `warning`/
  `error` bus logs to every configured sink (`_on_bus_event`, referenced `engine.py:2203-2206`).
- `abort`   → `raise SafetyAbort(reason)` (`engine.py:137`): rides the EXISTING shielded
  wind-down in `_run` (`engine.py:650-677`) — park + optional warm + optional roof-close
  per `cfg.safety.close_dome_on_unsafe` (`engine.py:665`, roof via
  `sequence/roof.py:31` `close_observatory`). This is the "close the roof on a bad
  condition" story reusing PRO-4, with zero new teardown ordering.

**Run lifecycle hooks for reset + target-complete eval:**
- `engine.py:156` `__init__` (per-run counters); `engine.py:230` `start()` reset block
  (`:248-284`) — where a new `self._fire_state` is initialised.
- `engine.py:741` `_run_scheduled`; the per-target step loop `:811-812`
  (`for si, step ...: await self._run_step(...)`) — the point AFTER which a non-calibration
  target is complete → `on_target_complete` eval.

**Plan persistence + start path already carry any new SequencePlan field for free:**
- `server/astrodeck/plans.py:121` `plan.model_dump()` — the library envelope serializes
  the whole plan; `plans.py:160` `import_plan` re-validates it. No plans.py change needed.
- `server/astrodeck/api/app.py:642` `class StartSequenceBody(SequencePlan)` — the run body
  IS the plan; `app.py:3225` `SequencePlan.model_validate(body...)` validates instructions
  at start. `session.py` snapshots `plan` verbatim. No route change needed.

**UI seams:**
- `ui/src/types.ts:496` `SequencePlan` (+`:462` `ExposureStep`, `:478` `Target`, `:831`
  `Schedule`) — add `Instruction` interface + optional `instructions?: Instruction[]`.
- `ui/src/store.ts:145` `defaultPlan()`, `:177` `loadPlan()`, `:199` `defaultSchedule()`,
  `:860` `setPlan` (persists localStorage), `:987` `enqueueToast`, `:49` `ensurePlanIds`
  import. `ui/src/lib/ids.ts:8` `uid()`, `:28` `ensurePlanIds`.
- `ui/src/views/SequenceView.tsx:1-32` imports, `:34` `DEFAULT_STEP`, `:40` `FRAME_TYPES`,
  sub-panels imported `:8-10` (`SchedulePanel`/`SessionsPanel`/`PlanLibraryPanel`) — the
  place a new `InstructionsPanel` slots in.
- **Pure-logic-in-lib idiom** (thin render + tested helper): `ui/src/lib/planLibrary.ts`
  (pure fns, comment `:1-4`), `ui/src/lib/planGroups.ts:12` `applyStepsToGroup`,
  `ui/src/lib/sequenceTemplates.ts:1-6` ("logic in lib, thin render"). New helpers land in
  `ui/src/lib/instructions.ts`.
- **Test idiom**: `ui/src/lib/__tests__/eta.test.ts:1-43` — no jsdom, inline-assert
  harness (`test`/`eq`/`assert`), run via `npx tsx <file>`; each `test()` maps to `it()`.

### 1.3 Approach

**Data model (flat closed enums — mirrors how `Schedule`/`SequencePlan` do additive
flat fields, not a discriminated union, so pydantic + TS mirror trivially):**

```
Instruction:
  id: str                                  # uuid4().hex, stable (like Target/ExposureStep)
  enabled: bool = True
  trigger: "on_hfr_above" | "on_guide_rms_above" | "on_frame_rejected"
           | "on_target_complete" | "at_time"
  threshold: float = 0.0                   # on_hfr_above / on_guide_rms_above value
  at_time: str | None = None               # "HH:MM" local, for trigger == at_time
  action: "notify" | "pause" | "refocus" | "dither" | "abort"
  message: str = ""                        # notify text / log + abort reason
  level: "info" | "warning" | "error" = "warning"   # notify severity
  once: bool = False                       # fire at most once per run
  cooldown_s: float = 0.0                  # min seconds between fires (0 = every boundary)
  only_target: str | None = None           # gate: only while this target (by name) active

SequencePlan.instructions: list[Instruction] = []   # ADDITIVE; [] => byte-identical run
```

**Algorithm — the pure evaluator (the tested, correctness-critical core):**

A pure function `evaluate_instructions(instructions, ctx, fire_state) -> (fired, next_state)`.
No engine/hub/clock access — `ctx` carries the snapshot, `fire_state` carries per-instruction
bookkeeping the engine holds between calls. Determinism + purity ⇒ exhaustively unit-testable.

The **no-double-fire** semantics (the subtle part):
- **Level triggers** (`on_hfr_above`, `on_guide_rms_above`) are **edge-triggered**: fire only
  on the rising crossing (was at/below threshold, now above). While the value stays above,
  they do NOT re-fire (a stuck-high HFR must not refocus every single sub). They **re-arm**
  when the value drops back to/below threshold. `armed` lives in `FireRecord`.
- `on_frame_rejected` is per-event: fires each frame where `ctx.frame_rejected`.
- `on_target_complete` fires when `ctx.target_complete`; `once`-per-target via the
  `only_target`/target-scoped key (engine passes a fresh `fire_state` view keyed by run;
  target-complete is naturally one boundary per target).
- `at_time` is implicitly `once`: fires at the first boundary with `now_ts >= parsed(at_time)`.
- Cross-cutting gates applied to EVERY trigger, in order: `enabled` → `only_target` match →
  trigger-specific eligibility (+ edge) → `once` (fired_count == 0) → `cooldown_s`
  (`now - last_fire_ts >= cooldown_s`). Only when all pass is a `FiredAction` emitted and
  `fired_count`/`last_fire_ts`/`armed` updated.
- **Order**: instructions fire in list order (author-controlled priority). The engine
  dispatches in that order, so e.g. `notify` before `abort` both land.

**Behavior — engine integration (thin dispatch, reuses existing capabilities):**
- Per frame, in `_run_step` right after `_reporter_record` (`engine.py:1171`, so
  `accepted`/`hfr` are known) and **before** the accept/reject branch (`:1172`): build a
  `TriggerContext`, call the evaluator, and `await self._dispatch_actions(fired, target, step)`.
- Per target, in `_run_scheduled` after the step loop (`engine.py:812`) for a
  non-calibration target: evaluate with `target_complete=True`.
- `_dispatch_actions` maps each `FiredAction.action` to the existing capability (§1.2).
  `abort` raises `SafetyAbort` (propagates to `_run`); every other action is individually
  guarded so a `notify`/`dither` hiccup never breaks capture. `refocus`/`dither` set
  `self._frame_had_event = True` (matching the existing dither/AF blocks `:1149`,`:1157`)
  so their wall-time is excluded from the overhead EMA.
- `at_time` granularity: evaluated only on the per-frame path, so it fires at the next
  frame boundary at/after the time (documented; a dedicated timer is deferred, §5).

**Placement:**
- Model: extend `sequence/models.py` (new `Instruction`, field on `SequencePlan`).
- Pure evaluator: new `server/astrodeck/sequence/instructions.py` (no engine imports).
- Engine wiring: `sequence/engine.py` (`__init__`/`start` reset + `_run_step`/`_run_scheduled`
  eval + new `_dispatch_actions`).
- UI model: `ui/src/types.ts`; defaults/backfill in `ui/src/store.ts`.
- UI pure helpers: new `ui/src/lib/instructions.ts` + test
  `ui/src/lib/__tests__/instructions.test.ts`.
- UI render: new `ui/src/components/sequence/InstructionsPanel.tsx`, wired into
  `SequenceView.tsx`.

---

## 2. Global Constraints (verbatim)

- **Privacy** — the real site coords `37.348110` / `121.801704` and the label `My Backyard`
  must NEVER appear in code, tests, or docs. The site default is `"My Observatory"` / `0.0`.
  (Instruction tests use synthetic thresholds/times and need no coordinates.)
- **Never `git add -A`.** Stage only the files each task names.
- **UI gate**: `cd ui && npx tsc -b`.
- **NO jsdom** — pure logic is tested with `npx tsx` inline-assert (idiom
  `ui/src/lib/__tests__/eta.test.ts`).
- **Backend tests**: `server/.venv/Scripts/pytest.exe` run from the **repo root**, `-n0`
  (single, no xdist) for these targeted tests.
- **Client toasts** go through `useStore.getState().enqueueToast` (never a bespoke toaster).
- **Honest-disabled (§11.8)**: a control a viewer cannot use is dimmed + locked +
  `aria-disabled` + `title` — **never** the native `disabled` attribute.
- **Do not disrupt astrotown** (no deploys, no touching the live box).

---

## 3. TDD Plan

Interfaces are frozen up front so tasks compose. Every task: write the failing test first,
then the impl, then run the exact command and confirm the exact expected output.

### 3.0 Interfaces block (exact signatures)

**`server/astrodeck/sequence/models.py`** (append):

```python
from typing import Literal

TriggerKind = Literal[
    "on_hfr_above", "on_guide_rms_above", "on_frame_rejected",
    "on_target_complete", "at_time",
]
ActionKind = Literal["notify", "pause", "refocus", "dither", "abort"]

class Instruction(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    enabled: bool = True
    trigger: TriggerKind
    threshold: float = Field(0.0, ge=0)
    at_time: str | None = None            # "HH:MM" 24h local; validated when trigger==at_time
    action: ActionKind
    message: str = ""
    level: Literal["info", "warning", "error"] = "warning"
    once: bool = False
    cooldown_s: float = Field(0.0, ge=0)
    only_target: str | None = None
```

**`server/astrodeck/sequence/instructions.py`** (new — the pure evaluator):

```python
from dataclasses import dataclass, field
from .models import Instruction

@dataclass
class TriggerContext:
    now_ts: float
    frame_hfr: float | None = None
    guide_rms: float | None = None
    frame_rejected: bool = False
    target_complete: bool = False
    active_target: str | None = None

@dataclass
class FireRecord:
    fired_count: int = 0
    last_fire_ts: float = 0.0
    armed: bool = True                    # level triggers: True when currently below threshold

@dataclass
class FiredAction:
    instruction_id: str
    action: str
    message: str
    level: str

def parse_hhmm(at_time: str | None, now_ts: float) -> float | None:
    """"HH:MM" -> today's epoch ts in LOCAL time, or None if malformed/absent. Pure."""

def evaluate_instructions(
    instructions: list[Instruction],
    ctx: TriggerContext,
    fire_state: dict[str, FireRecord],
) -> tuple[list[FiredAction], dict[str, FireRecord]]:
    """Pure. Returns (actions to fire in list order, updated fire_state). No I/O."""
```

**`server/astrodeck/sequence/engine.py`** (additions):

```python
# __init__ / start():   self._fire_state: dict[str, FireRecord] = {}
async def _run_instructions(self, ctx: TriggerContext, target, step) -> None: ...
async def _dispatch_actions(self, fired: list[FiredAction], target, step) -> None: ...
```

**`ui/src/types.ts`** (append; mirrors the pydantic model):

```ts
export type TriggerKind =
  | "on_hfr_above" | "on_guide_rms_above" | "on_frame_rejected"
  | "on_target_complete" | "at_time";
export type ActionKind = "notify" | "pause" | "refocus" | "dither" | "abort";
export interface Instruction {
  id?: string;
  enabled: boolean;
  trigger: TriggerKind;
  threshold: number;
  at_time: string | null;
  action: ActionKind;
  message: string;
  level: "info" | "warning" | "error";
  once: boolean;
  cooldown_s: number;
  only_target: string | null;
}
// on SequencePlan (additive, optional): instructions?: Instruction[];
```

**`ui/src/lib/instructions.ts`** (new — pure helpers):

```ts
export function defaultInstruction(): Instruction;
export function describeInstruction(i: Instruction, targetNames?: string[]): string;
export function validateInstruction(i: Instruction): string[];   // [] === valid
export const TRIGGER_LABELS: Record<TriggerKind, string>;
export const ACTION_LABELS: Record<ActionKind, string>;
export function triggerNeedsThreshold(t: TriggerKind): boolean;   // hfr/rms
export function triggerNeedsTime(t: TriggerKind): boolean;        // at_time
```

---

### Task 1 — Backend model (`Instruction` + `SequencePlan.instructions`)
**Impl tier: Sonnet** (mechanical pydantic; validators are straightforward).

**Files**: `server/astrodeck/sequence/models.py`,
`server/astrodeck/sequence/__init__.py` (export `Instruction`),
`server/tests/test_instructions_model.py` (new).

**Step 1 — failing test.** `server/tests/test_instructions_model.py`:

```python
from astrodeck.sequence import Instruction, SequencePlan

def test_plan_default_has_no_instructions():
    assert SequencePlan().instructions == []

def test_instruction_roundtrip_and_ids():
    i = Instruction(trigger="on_hfr_above", threshold=3.5, action="refocus")
    assert i.enabled and i.id and len(i.id) == 32
    p = SequencePlan(instructions=[i])
    assert SequencePlan(**p.model_dump()).instructions[0].threshold == 3.5

def test_bad_trigger_and_action_rejected():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Instruction(trigger="on_moon_phase", action="refocus")
    with pytest.raises(ValidationError):
        Instruction(trigger="at_time", action="launch_rocket")

def test_at_time_requires_valid_hhmm():
    import pytest
    from pydantic import ValidationError
    Instruction(trigger="at_time", at_time="23:30", action="pause")  # ok
    with pytest.raises(ValidationError):
        Instruction(trigger="at_time", at_time="9pm", action="pause")
```

**Step 2 — impl.** Add the `Literal` types + `Instruction` (with a `@model_validator` that,
when `trigger == "at_time"`, requires `at_time` to match `^\d{2}:\d{2}$` with hour<24,
minute<60), then `instructions: list[Instruction] = []` on `SequencePlan` after
`models.py:99`. Export `Instruction` from `sequence/__init__.py` next to `SequencePlan`.

**Step 3 — run.**
`server/.venv/Scripts/pytest.exe server/tests/test_instructions_model.py -n0 -q`
**Expected**: `4 passed`.

---

### Task 2 — Pure evaluator (`instructions.py`) — THE tested core
**Impl tier: Opus.** Justification: the edge-trigger + re-arm + once + cooldown +
only_target interaction is the genuinely subtle correctness core (a wrong edge/re-arm
refocuses every sub or never fires). Everything downstream trusts it.

**Files**: `server/astrodeck/sequence/instructions.py` (new),
`server/tests/test_instructions_eval.py` (new).

**Step 1 — failing test.** `server/tests/test_instructions_eval.py`:

```python
import time
from astrodeck.sequence.models import Instruction
from astrodeck.sequence.instructions import (
    TriggerContext, FireRecord, evaluate_instructions, parse_hhmm,
)

def _fire(instrs, ctx, state=None):
    return evaluate_instructions(instrs, ctx, state or {})

def test_empty_is_noop():
    fired, st = _fire([], TriggerContext(now_ts=1000.0))
    assert fired == [] and st == {}

def test_hfr_above_is_edge_triggered_not_every_frame():
    i = Instruction(id="a", trigger="on_hfr_above", threshold=3.0, action="refocus")
    st = {}
    f1, st = _fire([i], TriggerContext(now_ts=1.0, frame_hfr=4.0), st)   # cross up
    f2, st = _fire([i], TriggerContext(now_ts=2.0, frame_hfr=4.5), st)   # stays high
    f3, st = _fire([i], TriggerContext(now_ts=3.0, frame_hfr=2.0), st)   # drop -> re-arm
    f4, st = _fire([i], TriggerContext(now_ts=4.0, frame_hfr=5.0), st)   # cross up again
    assert [len(f) for f in (f1, f2, f3, f4)] == [1, 0, 0, 1]
    assert f1[0].action == "refocus"

def test_once_fires_at_most_once():
    i = Instruction(id="b", trigger="on_frame_rejected", action="notify", once=True)
    st = {}
    f1, st = _fire([i], TriggerContext(now_ts=1.0, frame_rejected=True), st)
    f2, st = _fire([i], TriggerContext(now_ts=2.0, frame_rejected=True), st)
    assert len(f1) == 1 and len(f2) == 0

def test_cooldown_throttles():
    i = Instruction(id="c", trigger="on_frame_rejected", action="dither", cooldown_s=100)
    st = {}
    f1, st = _fire([i], TriggerContext(now_ts=1000.0, frame_rejected=True), st)
    f2, st = _fire([i], TriggerContext(now_ts=1050.0, frame_rejected=True), st)  # within
    f3, st = _fire([i], TriggerContext(now_ts=1200.0, frame_rejected=True), st)  # past
    assert [len(f) for f in (f1, f2, f3)] == [1, 0, 1]

def test_only_target_gate():
    i = Instruction(id="d", trigger="on_frame_rejected", action="pause", only_target="M31")
    f_off, _ = _fire([i], TriggerContext(now_ts=1.0, frame_rejected=True, active_target="M42"))
    f_on, _ = _fire([i], TriggerContext(now_ts=1.0, frame_rejected=True, active_target="M31"))
    assert len(f_off) == 0 and len(f_on) == 1

def test_at_time_fires_once_after_time():
    now = time.time()
    hhmm = time.strftime("%H:%M", time.localtime(now - 120))  # 2 min ago
    i = Instruction(id="e", trigger="at_time", at_time=hhmm, action="notify")
    st = {}
    f1, st = _fire([i], TriggerContext(now_ts=now), st)
    f2, st = _fire([i], TriggerContext(now_ts=now + 60), st)
    assert len(f1) == 1 and len(f2) == 0

def test_order_is_list_order():
    a = Instruction(id="n", trigger="on_frame_rejected", action="notify")
    b = Instruction(id="z", trigger="on_frame_rejected", action="abort")
    fired, _ = _fire([a, b], TriggerContext(now_ts=1.0, frame_rejected=True))
    assert [f.action for f in fired] == ["notify", "abort"]

def test_disabled_never_fires():
    i = Instruction(id="x", enabled=False, trigger="on_frame_rejected", action="pause")
    fired, _ = _fire([i], TriggerContext(now_ts=1.0, frame_rejected=True))
    assert fired == []

def test_none_metric_never_fires():
    i = Instruction(id="g", trigger="on_guide_rms_above", threshold=1.0, action="pause")
    fired, _ = _fire([i], TriggerContext(now_ts=1.0, guide_rms=None))
    assert fired == []
```

**Step 2 — impl.** Implement `parse_hhmm` (strptime to today's local date; return `None`
on any error) and `evaluate_instructions` per §1.3: iterate in list order; per instruction
`rec = fire_state.setdefault(i.id, FireRecord())`; skip when `not i.enabled`; skip when
`i.only_target` set and `!= ctx.active_target`; compute eligibility + edge:

- `on_hfr_above`/`on_guide_rms_above`: `v = frame_hfr|guide_rms`; if `v is None` → not
  eligible (leave armed as-is); else `eligible = v > threshold and rec.armed`; and re-arm
  `rec.armed = v <= threshold or rec.armed and not (v > threshold)` — concretely: set
  `rec.armed = True` when `v <= threshold`, and only fire when `v > threshold and was armed`.
- `on_frame_rejected`: `eligible = ctx.frame_rejected`.
- `on_target_complete`: `eligible = ctx.target_complete`.
- `at_time`: `t = parse_hhmm(i.at_time, ctx.now_ts)`; `eligible = t is not None and
  ctx.now_ts >= t`; treat as implicit-once (`rec.fired_count == 0`).

Then apply `once` (`rec.fired_count == 0`), then `cooldown_s`
(`ctx.now_ts - rec.last_fire_ts >= cooldown_s` when `cooldown_s > 0`). On fire: append
`FiredAction(i.id, i.action, i.message, i.level)`; `rec.fired_count += 1`;
`rec.last_fire_ts = ctx.now_ts`; for level triggers set `rec.armed = False`. Return
`(fired, fire_state)`.

**Step 3 — run.**
`server/.venv/Scripts/pytest.exe server/tests/test_instructions_eval.py -n0 -q`
**Expected**: `9 passed`.

---

### Task 3 — Engine integration (eval + dispatch, byte-identical when empty)
**Impl tier: Sonnet** (wiring follows the existing dither/refocus block pattern exactly;
the hard correctness is already in Task 2).

**Files**: `server/astrodeck/sequence/engine.py`,
`server/tests/test_instructions_engine.py` (new).

**Step 1 — failing test.** Reuse the fixture pattern from
`server/tests/test_sequence_engine_fixes.py:38-73` (`temp_store` + `sim_hub`, sim run).
Assert three things:

```python
# (a) empty instructions => the eval/dispatch path is never entered (no-op).
async def test_no_instructions_is_noop(sim_hub, temp_store, monkeypatch):
    eng = SequenceEngine(sim_hub)
    calls = []
    monkeypatch.setattr(eng, "_dispatch_actions",
                        lambda *a, **k: calls.append(a) or _noop())
    plan = _tiny_plan(instructions=[])          # 1 target, 1 step, count=1
    eng.start(plan); await _wait_done(eng)
    assert calls == []                          # dispatch never called

# (b) a high-HFR frame fires a refocus action, dispatched to _autofocus.
async def test_high_hfr_triggers_refocus(sim_hub, temp_store, monkeypatch):
    eng = SequenceEngine(sim_hub)
    af = []
    async def fake_af(label): af.append(label)
    monkeypatch.setattr(eng, "_autofocus", fake_af)
    # force every captured frame to report a high HFR
    monkeypatch.setattr(eng, "_capture",
        _returns({"hfr": 9.9, "stats": {"median": 100}, "saved_path": None}))
    plan = _tiny_plan(instructions=[Instruction(
        trigger="on_hfr_above", threshold=3.0, action="refocus")])
    eng.start(plan); await _wait_done(eng)
    assert af and af[0] == "triggered refocus"

# (c) an abort action tears the run down via SafetyAbort (end_reason unsafe/aborted).
async def test_abort_action_ends_run(sim_hub, temp_store, monkeypatch):
    eng = SequenceEngine(sim_hub)
    monkeypatch.setattr(eng, "_capture",
        _returns({"hfr": 9.9, "stats": {"median": 100}, "saved_path": None}))
    plan = _tiny_plan(instructions=[Instruction(
        trigger="on_hfr_above", threshold=3.0, action="abort", message="hfr runaway")])
    eng.start(plan); await _wait_done(eng)
    assert eng.state["state"] in ("aborted",)  # SafetyAbort => aborted, end_reason unsafe
```

(`_tiny_plan`, `_returns`, `_wait_done`, `_noop` are 3-line local helpers; a sim run with
`count=1` and no cooling/guide gates completes in test time.)

**Step 2 — impl.** In `engine.py`:
- `__init__` (`:156`) + `start()` reset block (`:248-284`): add
  `self._fire_state: dict[str, FireRecord] = {}` (import from `.instructions`).
- In `_run_step`, immediately after `self._reporter_record(...)` (`engine.py:1171`) and
  before `if accepted:` (`:1172`), insert:

```python
if plan.instructions:
    ctx = TriggerContext(
        now_ts=time.time(),
        frame_hfr=(info.get("hfr") if isinstance(info, dict) else None),
        guide_rms=self._guide_rms(),
        frame_rejected=(not accepted),
        target_complete=False,
        active_target=target.name)
    await self._run_instructions(ctx, target, step)
```

- In `_run_scheduled`, after the step loop for a non-calibration target (`engine.py:812`),
  call `_run_instructions` with `target_complete=True`, `frame_rejected=False`.
- Add:

```python
async def _run_instructions(self, ctx, target, step):
    fired, self._fire_state = evaluate_instructions(
        self.plan.instructions, ctx, self._fire_state)
    if fired:
        await self._dispatch_actions(fired, target, step)

async def _dispatch_actions(self, fired, target, step):
    for fa in fired:
        if fa.action == "abort":
            raise SafetyAbort(fa.message or "aborted by sequence instruction")
        try:
            if fa.action == "notify":
                bus.log(fa.level, fa.message or "sequence instruction", "sequence")
            elif fa.action == "pause":
                bus.log("warning", fa.message or "paused by sequence instruction", "sequence")
                self.pause()
            elif fa.action == "refocus":
                if "focuser" in self.hub.devices:
                    await self._autofocus("triggered refocus")
                    self._frame_had_event = True
            elif fa.action == "dither":
                if self.hub.guider and self.hub.guider.connected:
                    await _bounded(self.hub.guider.dither(self.plan.dither_pixels),
                                   GUIDE_OP_TIMEOUT_S, "instruction dither")
                    self._frames_since_dither = 0
                    self._frame_had_event = True
        except SafetyAbort:
            raise
        except Exception as e:
            bus.log("warning", f"instruction action '{fa.action}' failed: {e}", "sequence")
```

Note: `abort` is raised OUTSIDE the try so it always propagates to `_run`'s SafetyAbort arm
(`engine.py:650`). `pause` reuses `self.pause()` (`:287`) — the next `_checkpoint()` (`:1128`)
blocks. The `plan.instructions` guard makes the whole path dead when empty ⇒ byte-identical.

**Step 3 — run.**
`server/.venv/Scripts/pytest.exe server/tests/test_instructions_engine.py -n0 -q`
**Expected**: `3 passed`.
Then a no-regression sweep of the engine suite:
`server/.venv/Scripts/pytest.exe server/tests/test_sequence.py server/tests/test_sequence_engine_fixes.py server/tests/test_session_engine.py -n0 -q`
**Expected**: all pass (unchanged counts) — proves the empty-path is byte-identical.

---

### Task 4 — TS types + store defaults/backfill
**Impl tier: Sonnet** (mechanical, typechecker-verified).

**Files**: `ui/src/types.ts`, `ui/src/store.ts`.

**Step 1 — impl.** Append `TriggerKind`/`ActionKind`/`Instruction` to `types.ts` (after
the `Schedule` interface, `types.ts:831-845`); add `instructions?: Instruction[];` to
`SequencePlan` (`types.ts:496`, additive/optional so pre-PRO-3 payloads type-check). In
`store.ts`, add `instructions: []` to `defaultPlan()` (`:145-175`) and backfill in
`loadPlan()` (`:181`) — the existing spread `{ ...defaultPlan(), ...parsed }` already yields
`[]` for a legacy plan, so no ids-backfill change is required (instructions carry their own
`id` optional, generated client-side on create by `defaultInstruction`).

**Step 2 — run (gate).** `cd ui && npx tsc -b`
**Expected**: exit 0, no diagnostics.

---

### Task 5 — UI pure helpers (`instructions.ts`) + tsx test
**Impl tier: Sonnet.**

**Files**: `ui/src/lib/instructions.ts` (new),
`ui/src/lib/__tests__/instructions.test.ts` (new).

**Step 1 — failing test.** `ui/src/lib/__tests__/instructions.test.ts` (mirror the
`eta.test.ts:21-43` harness — local `test`/`eq`/`assert`, `console.log` summary,
`export const result`):

```ts
import {
  defaultInstruction, describeInstruction, validateInstruction,
  triggerNeedsThreshold, triggerNeedsTime,
} from "../instructions";

test("default is a valid enabled notify rule", () => {
  const i = defaultInstruction();
  eq(i.enabled, true, "enabled");
  eq(validateInstruction(i).length, 0, "valid");
});
test("hfr rule needs a positive threshold", () => {
  const i = { ...defaultInstruction(), trigger: "on_hfr_above" as const, threshold: 0 };
  assert(validateInstruction(i).length > 0, "threshold required");
  assert(triggerNeedsThreshold("on_hfr_above"), "needs threshold");
});
test("at_time rule needs HH:MM", () => {
  const bad = { ...defaultInstruction(), trigger: "at_time" as const, at_time: "9pm" };
  assert(validateInstruction(bad).length > 0, "bad time");
  const ok = { ...defaultInstruction(), trigger: "at_time" as const, at_time: "23:30" };
  eq(validateInstruction(ok).length, 0, "good time");
  assert(triggerNeedsTime("at_time"), "needs time");
});
test("describe is human + mentions trigger and action", () => {
  const i = { ...defaultInstruction(), trigger: "on_hfr_above" as const,
              threshold: 3.5, action: "refocus" as const };
  const s = describeInstruction(i);
  assert(s.includes("3.5") && /refocus/i.test(s), `got: ${s}`);
});
```

**Step 2 — impl.** Implement the helpers per §3.0. `validateInstruction` returns problems:
threshold `<= 0` when `triggerNeedsThreshold`; `at_time` not `^\d{2}:\d{2}$` (hour<24,
min<60) when `triggerNeedsTime`; empty `message` when `action === "notify"` (a notify with
no text is useless). `describeInstruction` composes `"When HFR > 3.5 → refocus"` /
`"When guide RMS > 1.2\" → pause"` / `"When a frame is rejected → notify"` /
`"When target complete → dither"` / `"At 23:30 → abort"`, appending `" (once)"` /
`" · only M31"` when set.

**Step 3 — run.** `cd ui && npx tsx src/lib/__tests__/instructions.test.ts`
**Expected**: `instructions.test: 4/4 passed`.
Then gate: `cd ui && npx tsc -b` → exit 0.

---

### Task 6 — `InstructionsPanel` component + wire into SequenceView
**Impl tier: Sonnet** (thin render over the tested helpers; typechecker-verified).

**Files**: `ui/src/components/sequence/InstructionsPanel.tsx` (new),
`ui/src/views/SequenceView.tsx` (import + mount).

**Step 1 — impl.** New collapsible `InstructionsPanel` (match `SchedulePanel`/
`PlanLibraryPanel` shape imported at `SequenceView.tsx:8-10`). Props: `plan`, `setPlan`
(from `SequenceView`'s `useStore` selectors around `:82-88`), `canWrite`
(`useCanControlMount()`, already imported `SequenceView.tsx:23`). Each rule row renders:
trigger `<select>` (labels from `TRIGGER_LABELS`), a threshold `<input type=number>` shown
only when `triggerNeedsThreshold`, an `at_time` `<input>` shown only when
`triggerNeedsTime`, action `<select>` (`ACTION_LABELS`), a `message`/`level` input (shown
for `notify`), `once`/`cooldown_s`/`only_target` in an "advanced" disclosure, an
enabled `Toggle`, a delete `IconButton`, plus a live `describeInstruction(...)` summary
line and any `validateInstruction(...)` problems in warn tone. "Add rule" appends
`defaultInstruction()`. All edits route through `setPlan({ ...plan, instructions })` (which
persists via `store.setPlan`, `store.ts:860`). Toasts (if any) via
`useStore.getState().enqueueToast`.

**Honest-disabled (§11.8)**: when `!canWrite`, the Add/delete/inputs are dimmed + locked +
`aria-disabled` + `title={accessPhrase(...)}` (helper imported `SequenceView.tsx:23`) — never
the native `disabled` attribute. Mirror `ReadOnlyBadge` usage already in the view (`:29`).

Mount below the existing plan settings in `SequenceView` (near `SchedulePanel`), so it
reads as one more optional sub-panel.

**Step 2 — run (gate).** `cd ui && npx tsc -b`
**Expected**: exit 0, no diagnostics. (Render correctness is carried by the tested helpers
in Task 5 + the typechecker; no jsdom.)

---

## 4. Sequencing & review

Order: **T1 → T2 → T3** (backend, independently landable + CI-green) then
**T4 → T5 → T6** (UI). T2 is the review-worthy core (request a focused review on the
evaluator's edge/re-arm logic). T3's no-regression sweep is the byte-identical proof.

---

## 5. Open decisions

See the structured `openDecisions`.

---

## 6. Deferred follow-ups (explicitly out of v1)

- `run_target` / `skip_target` (target-jumping) — a real scheduler control-flow change to
  `_run_scheduled` (`engine.py:741`); large, risky. Defer.
- A dedicated `close_roof` action — v1 uses `abort` + the existing PRO-4
  `close_dome_on_unsafe` (`engine.py:665`). A first-class roof-close-without-abort is a
  follow-up.
- A rich condition grammar (AND/OR of sensor predicates) — v1 folds the one threshold into
  the trigger + `only_target`/`cooldown_s`/`once` gates.
- Client-side dry-run simulator (preview "which rules would fire" over a session's frames) —
  the backend evaluator is authoritative for v1.
- Sub-frame `at_time` precision via a timer task — v1 fires at the next frame boundary
  at/after the time (documented granularity).
- Surfacing fired instructions in the SessionReport UI beyond the existing bus-log path.
