# A night cut short is not a night finished (#252) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A run that ends with frames still owed leaves its session `dormant`
and armed, says so in the log, and reports an end reason that is not a lie.

**Architecture:** One question — "does the ledger hold every frame the plan
asked for?" — asked in one place (`Session.owed()`), and used for both the
session's terminal status and the run's own end reason. A single new engine
flag, `_window_closed`, distinguishes a window that ran out from a target the
run set aside for other reasons.

**Tech Stack:** Python 3.12 / pydantic / pytest-asyncio server; React +
TypeScript UI. The UI has NO test framework: each `*.test.ts` is a plain tsx
script with its own assertions that exports `{ passed, failed, total }`, and
`npm test` (run-tests.mjs) runs one child process per file and exits on those
counts. Run a single file with `npx tsx <path>`, never `npx vitest`.

**Spec:** `docs/superpowers/specs/2026-08-16-dawn-cut-resume-design.md`

## Global Constraints

- No emojis anywhere — code, comments, log strings, commit messages.
- Em dashes are used throughout this codebase's log strings; keep that idiom.
- Run the server suite with xdist (`-n auto`), the project default.
- Tests must drive the real `SequenceEngine`. A pure-function test would pass
  against this defect — that is exactly how it shipped.
- `git commit -- <paths>` always, never bare `git add` (parallel-agent index race).
- Do NOT touch the rig. Rig work is step 4.4 and is the operator's call.

---

### Task 1: The ledger decides whether a session is finished

**Files:**
- Modify: `server/astrodeck/sequence/session.py` (after `remaining()`, :108-115)
- Modify: `server/astrodeck/sequence/engine.py:1071-1081`
- Create: `server/tests/test_a_short_night_stays_resumable.py`

**Interfaces:**
- Produces: `Session.owed() -> int` — frames still owed across every step,
  mode-aware, floored at 0. Task 2 calls it from the engine.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_a_short_night_stays_resumable.py`:

```python
"""#252: a run that ends with frames owed must stay resumable.

Every test here drives the REAL engine. The defect these cover shipped behind
a correct pure function (`Session.remaining()`) whose only caller asked it the
wrong question, and a pure-function test passes against it happily.

Rig evidence, 2026-08-16: session 18572134 ended `complete` with 117 of 175
frames and auto_resume set — armed, finished, and unreachable forever.
"""
import asyncio
import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Schedule, Target
from astrodeck.sequence.session import session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _target(name, count, schedule=None) -> Target:
    t = Target(name=name, ra_hours=5.5881, dec_deg=-5.3911, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=count)])
    if schedule is not None:
        t.schedule = schedule
    return t


def _plan(name, targets) -> SequencePlan:
    return SequencePlan(name=name, guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        targets=targets)


def _close_the_window(eng, target) -> None:
    """Dawn arrives UNDER a running frame loop.

    The scheduler froze this target's window at run start and will never
    re-select it, so a closure that happens while the step is shooting can only
    be seen by the per-frame boundary check. That is the shape of every real
    single-target night on this rig, and the shape no existing test had.
    """
    start, _stop = eng._frozen.get(id(target)) or (time.time() - 100, None)
    eng._frozen[id(target)] = (start, time.time() - 1)


async def test_a_dawn_cut_mid_target_leaves_the_session_resumable(sim_hub):
    plan = _plan("dawncut", [_target("A", 8)])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng._frames_done >= 2)
    _close_the_window(eng, plan.targets[0])
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    s = session_store.load(sid)
    assert s.owed() == 8 - len(s.frames)
    assert 0 < s.owed() < 8, "the cut has to land mid-target for this to test anything"
    assert s.status == "dormant", (
        "a night cut short owes frames, and 'complete' is the one status that "
        "makes them unreachable")
    armed = session_store.armed()
    assert armed is not None and armed.id == sid, (
        "armed() only ever returns a dormant session — this is the whole "
        "reason the status matters")


async def test_a_finished_run_still_completes(sim_hub):
    """The other direction. Over-correcting into false dormancy would re-arm
    every finished session and re-shoot it at the next dusk."""
    plan = _plan("short", [_target("A", 2)])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    s = session_store.load(sid)
    assert s.owed() == 0
    assert s.status == "complete"
    assert session_store.armed() is None


async def test_a_calibration_only_plan_still_completes(sim_hub):
    """Calibration frames reach the same ledger as lights (the rig's
    'Calib 2026-08-11' session holds 94 of them), so the ledger-truth rule must
    not strand a dark set in a nightly resume loop."""
    darks = Target(name="darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
                   center=False, autofocus_first=False,
                   steps=[ExposureStep(filter=None, exposure_s=0.05, count=2,
                                       frame_type="Dark")])
    eng = SequenceEngine(sim_hub)
    eng.start(_plan("calib", [darks]))
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    s = session_store.load(sid)
    assert s.owed() == 0
    assert s.status == "complete"
```

- [ ] **Step 2: Run it and watch the first test fail for the right reason**

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_a_short_night_stays_resumable.py -p no:randomly -x -q
```

Expected: `test_a_dawn_cut_mid_target_leaves_the_session_resumable` fails with
`AttributeError: 'Session' object has no attribute 'owed'`. Add `Session.owed()`
(step 3) and re-run: it must then fail on `assert s.status == "dormant"` with
`'complete' == 'dormant'`. Both other tests pass from the start — they are
guards, not drivers.

- [ ] **Step 3: Add `Session.owed()`**

In `server/astrodeck/sequence/session.py`, directly after `remaining()`:

```python
    def owed(self) -> int:
        """Frames still owed across every step (mode-aware, floored at 0).

        THE definition of "is this plan finished". It exists as one method
        rather than an expression at each call site because the run's ending
        and the session's status must never be able to answer it differently -
        which is exactly how #252 shipped: the session asked about unmet quota
        in accepted mode only, and the run did not ask at all.
        """
        return sum(self.remaining().values())
```

- [ ] **Step 4: Make the terminal rule read the ledger**

In `server/astrodeck/sequence/engine.py`, replace :1071-1081 (the
`# ---- session terminal transition` comment block through the `status =`
assignment) with:

```python
        # ---- session terminal transition (spec §4) ---------------------------
        # 'complete' ONLY when the run finished naturally AND the ledger holds
        # every frame the plan asked for. EVERY other cause — dawn_cutoff /
        # window_closed / max_run / incomplete / aborted / error / unsafe /
        # cooling_skip / quality — leaves unmet work -> dormant + resumable.
        #
        # THIS USED TO ASK ONLY IN ACCEPTED MODE (`quota and any(...)`), and
        # since `attempts` is the default and what every plan on the rig uses,
        # `unmet` was False by construction. On 2026-08-16 that stamped
        # 'complete' on a session holding 117 of 175 frames with auto_resume
        # set: armed, finished, and unreachable, because `armed()` only ever
        # returns a DORMANT session. Two other sessions on the same rig had
        # been closed the same way, 60 and 9 frames short.
        #
        # `owed()` is mode-aware on the session's own frozen plan, so accepted
        # mode still counts accepted frames and nothing about it changes.
        if self._session is not None:
            unmet = self._session.owed() > 0
            self._session.status = ("complete"
                                    if reason == "complete" and not unmet
                                    else "dormant")
```

- [ ] **Step 5: Run the file green, then the neighbours**

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_a_short_night_stays_resumable.py tests/test_session_resume.py tests/test_campaign_across_nights.py -p no:randomly -q
```

Expected: all pass. `test_window_dormant_then_resume_exact_remaining` and both
campaign tests finish every frame, so the stricter rule does not touch them.

- [ ] **Step 6: Commit**

```bash
git commit -m "fix(sequence): a session that owes frames is not a finished session" -- \
  server/astrodeck/sequence/session.py \
  server/astrodeck/sequence/engine.py \
  server/tests/test_a_short_night_stays_resumable.py
```

---

### Task 2: The run's own ending must agree with the ledger

**Files:**
- Modify: `server/astrodeck/sequence/engine.py:377` (`__init__` flag)
- Modify: `server/astrodeck/sequence/engine.py:487` (`start()` reset)
- Modify: `server/astrodeck/sequence/engine.py:1422-1436` (`_enforce_stop_boundary`)
- Modify: `server/astrodeck/sequence/engine.py:973-987` (the terminal branches)
- Modify: `server/tests/test_a_short_night_stays_resumable.py`

**Interfaces:**
- Consumes: `Session.owed()` from Task 1.
- Produces: engine end reasons `"dawn_cutoff"` (unchanged) and `"incomplete"`
  (new). Task 3 wires `"incomplete"` through the report, the alerts and the UI.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_a_short_night_stays_resumable.py`:

```python
async def test_the_dawn_cut_is_reported_as_a_dawn_cut(sim_hub):
    """The run's own ending has to match the session's. On 2026-08-16 the log
    read `sequence 'NGC 7129 - LRGB+SHO cycle' complete: 117 frames` over a
    night that owed 58 — the word 'complete' was the entire record of it."""
    plan = _plan("dawncut2", [_target("A", 8)])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    assert await wait_for(lambda: eng._frames_done >= 2)
    _close_the_window(eng, plan.targets[0])
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    assert eng.state.get("end_reason") == "dawn_cutoff"


async def test_a_target_set_aside_leaves_the_night_incomplete(sim_hub):
    """Not every short night is a dawn cut. A target the run set aside for its
    own reasons — here `on_missed="skip"` — still owes its frames, and
    reporting COMPLETE over it is the same lie in a smaller font."""
    past = time.strftime("%H:%M", time.localtime(time.time() - 3 * 3600))
    b = _target("B", 2, Schedule(start_mode="time", start_time=past,
                                 on_missed="skip"))
    plan = _plan("missed", [_target("A", 2), b])
    a_step, b_step = plan.targets[0].steps[0], plan.targets[1].steps[0]
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    assert eng.state.get("end_reason") == "incomplete"
    s = session_store.load(sid)
    assert s.status == "dormant"
    assert s.accepted(a_step.id) == 2
    assert s.accepted(b_step.id) == 0
    assert s.owed() == 2


async def test_a_mixed_night_owes_only_the_target_that_was_cut(sim_hub):
    """A cut on one target must not drag a finished one back into the debt."""
    plan = _plan("mixed", [_target("A", 8), _target("B", 2)])
    a_step, b_step = plan.targets[0].steps[0], plan.targets[1].steps[0]
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng._frames_done >= 2)
    _close_the_window(eng, plan.targets[0])
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    assert eng.state.get("end_reason") == "dawn_cutoff"
    s = session_store.load(sid)
    assert s.status == "dormant"
    assert s.accepted(b_step.id) == 2, "B had an open window and its own frames"
    assert s.owed() == 8 - s.accepted(a_step.id)
```

- [ ] **Step 2: Run them and confirm they fail**

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_a_short_night_stays_resumable.py -p no:randomly -q -k "dawn_cut_is_reported or set_aside or mixed_night"
```

Expected: all three fail on `end_reason`, `None != 'dawn_cutoff'` /
`None != 'incomplete'`.

**Contingency for `test_a_target_set_aside_leaves_the_night_incomplete`:** it
depends on `schedule.resolve_window` resolving a 3-hours-past `start_time` to
TODAY (it searches backward — engine.py:1128-1134). If instead the scheduler
waits for tomorrow, the test will hang to its 30 s timeout rather than fail.
In that case drive the same ending through the altitude floor instead: give the
target `Schedule(min_altitude_deg=30.0, on_floor="advance")` and
`monkeypatch.setattr(engine_module, "_frame_altitude", lambda *a, **k: 5.0)`
after the first frames land. Do not leave a hanging test in the file.

- [ ] **Step 3: Add the flag and set it where the window closes**

`engine.py:377`, beside `_dawn_cutoff`:

```python
        self._dawn_cutoff = False           # scheduler ran out of open windows
        # A RUNNING target hit its frozen stop boundary — dawn, a stop time, or
        # max_run — as opposed to the scheduler finding every window already
        # shut. Separate from `_dawn_cutoff` because that one is only ever set
        # at SELECTION, and a single-target night never reaches selection again:
        # its window closes under the frame loop, the target is dropped, the
        # queue empties, and the run used to call that "all targets complete".
        self._window_closed = False
```

`engine.py:487`, beside the `start()` reset:

```python
        self._dawn_cutoff = False
        self._window_closed = False
```

`_enforce_stop_boundary` (:1431-1436), the raise:

```python
        win = self._frozen.get(id(target))
        if not win:
            return
        stop_ts = win[1]
        if stop_ts is not None and time.time() >= stop_ts:
            # Record the CAUSE before unwinding. Every catcher of StopTarget
            # keeps the night going, so by the time the run ends nothing else
            # remembers that a window is what stopped this target.
            self._window_closed = True
            raise StopTarget("observing window closed (stop time / max run / dawn)")
```

- [ ] **Step 4: Pick the ending from the ledger**

Replace `engine.py:973-987` with:

```python
            # WHICH ENDING IS THIS? Two facts decide, and they answer different
            # questions. `owed` says whether the PLAN is unfinished; the flags
            # say what STOPPED it. A run that ends owing nothing is complete
            # however its last target left the queue.
            owed = self._session.owed() if self._session is not None else 0
            if self._dawn_cutoff or (self._window_closed and owed):
                # the scheduler ran out of open windows, or a running target hit
                # its frozen stop boundary and left work behind (§1.9-C).
                self._set_state(state="complete", detail="stopped at dawn (windows closed)",
                                end_reason="dawn_cutoff", schedule=None, session=None)
                bus.log("info", f"sequence '{plan.name}' stopped at dawn: "
                                f"{self._shortfall_phrase(owed)}", "sequence")
                self._finalize_report("dawn_cutoff")
            elif owed:
                # The run did everything it was told to do and the plan is still
                # short: a target set aside by its altitude floor, a missed
                # start, or a skip instruction. Reporting that as "complete" is
                # the same lie as the dawn case, in a smaller font.
                self._set_state(state="complete",
                                detail="targets set aside — frames still owed",
                                end_reason="incomplete", schedule=None, session=None)
                bus.log("info", f"sequence '{plan.name}' ended with targets set "
                                f"aside: {self._shortfall_phrase(owed)}", "sequence")
                self._finalize_report("incomplete")
            else:
                self._set_state(state="complete", detail="all targets complete",
                                schedule=None, session=None)
                bus.log("info", f"sequence '{plan.name}' complete: {self._frames_done} frames"
                                + (f", {self._rejected} flagged" if self._rejected else ""),
                        "sequence")
                self._finalize_report("complete")
```

Add `_shortfall_phrase` next to `_finalize_report`:

```python
    def _shortfall_phrase(self, owed: int) -> str:
        """The sentence that was missing at 05:25 on 2026-08-16, when the whole
        record of a 58-frame shortfall was the word "complete".

        Counts are CAMPAIGN-wide, not tonight's: `_frames_done` is seeded from
        the ledger on a resume, so night two of a 175-frame plan honestly reads
        "150 of 175" rather than starting over at zero.
        """
        total = self.plan.total_frames() if self.plan else 0
        return (f"{self._frames_done} of {total} frames, {owed} still owed — "
                f"the session stays armed and resumes when the window opens")
```

- [ ] **Step 5: Run the file, then the engine suites**

```
cd server && ./.venv/Scripts/python.exe -m pytest tests/test_a_short_night_stays_resumable.py tests/test_session_resume.py tests/test_campaign_across_nights.py tests/test_sequence_engine_fixes.py tests/test_engine_safety.py tests/test_engine_timeouts_escalation.py -p no:randomly -q
```

Expected: all pass. If any test asserts the old `detail` string
`"all targets complete"` on a short night, that assertion was encoding the bug —
read it before changing it.

- [ ] **Step 6: Commit**

```bash
git commit -m "fix(sequence): a night that owes frames does not get to say it is complete" -- \
  server/astrodeck/sequence/engine.py \
  server/tests/test_a_short_night_stays_resumable.py
```

---

### Task 3: Make `incomplete` legible everywhere it surfaces

**Files:**
- Modify: `server/astrodeck/sequence/report.py:101`
- Modify: `server/astrodeck/alerting.py:272-277`
- Modify: `ui/src/types.ts:590`
- Modify: `ui/src/lib/reportChart.ts:102-116`
- Modify: `ui/src/lib/__tests__/reportChart.test.ts`

**Interfaces:**
- Consumes: end reason `"incomplete"` from Task 2.

**The chip word is "UNFINISHED", not "INCOMPLETE", for two reasons.** In a list
of reports, COMPLETE and INCOMPLETE differ by one glanced-over prefix, and these
two chips mean opposite things. And `endReasonMeta`'s fallback arm already
uppercases any unknown reason, so a case returning "INCOMPLETE" would be dead
code and its test could not fail — the word has to be one the fallback cannot
produce or there is nothing to assert. The reason ID stays `incomplete`
server-side; only the display word differs.

- [ ] **Step 1: Write the failing UI test**

In `ui/src/lib/__tests__/reportChart.test.ts`, alongside the existing
`endReasonMeta` cases:

```ts
test("a night that owes frames is UNFINISHED, and never reads as complete", () => {
  // Not the uppercase fallback: that would render "INCOMPLETE", which differs
  // from "COMPLETE" by a prefix nobody reads at a glance. These two chips mean
  // opposite things and must not look alike.
  expect(endReasonMeta("incomplete")).toEqual({ word: "UNFINISHED", tone: "warn" });
  // contrast: a dawn cut is nobody's fault and needs no attention
  expect(endReasonMeta("dawn_cutoff")).toEqual({ word: "DAWN CUTOFF", tone: "good" });
});
```

- [ ] **Step 2: Run it and confirm it fails**

```
cd ui && npx tsx src/lib/__tests__/reportChart.test.ts
```

Expected: FAIL — `expected { word: 'INCOMPLETE', tone: 'warn' } to deeply equal
{ word: 'UNFINISHED', tone: 'warn' }`. That failure is the proof the assertion
has teeth: the fallback produces the wrong word.

- [ ] **Step 3: Add the case**

`ui/src/lib/reportChart.ts`, in `endReasonMeta`, after the `dawn_cutoff` case:

```ts
    case "incomplete":   return { word: "UNFINISHED", tone: "warn" };
```

And extend the docstring's reason list at :102-104 to include `incomplete`,
noting that its word is deliberately not a near-twin of COMPLETE.

- [ ] **Step 4: Extend the type union**

`ui/src/types.ts:590`:

```ts
  end_reason?: "complete" | "aborted" | "error" | "unsafe" | "dawn_cutoff" | "cooling_skip" | "quality" | "incomplete";
```

- [ ] **Step 5: Fix the alert wording**

`server/astrodeck/alerting.py:272-277`. `f"Run {state}: {reason}"` would emit
"Run complete: incomplete", which contradicts itself. The state is `complete`
for dawn cuts too, so this reads oddly today and nonsensically after:

```python
            elif state in ("complete", "aborted", "error"):
                reason = data.get("end_reason") or state
                # PHRASED FROM THE REASON, not the state. `state` is "complete"
                # for a dawn cutoff and for a night that ended owing frames, so
                # "Run complete: incomplete" is what the old wording produced.
                alert = AlertEvent("run_end", "info" if state == "complete" else "error",
                                   f"Run ended: {reason}",
                                   plan=data.get("plan_name", ""),
                                   extra={"end_reason": reason})
```

- [ ] **Step 6: Extend the report's reason comment**

`server/astrodeck/sequence/report.py:101`:

```python
    end_reason: str | None = None        # complete|incomplete|aborted|error|unsafe|dawn_cutoff
```

- [ ] **Step 7: Run both suites' affected files**

```
cd ui && npx tsx src/lib/__tests__/reportChart.test.ts && npx tsc --noEmit
cd ../server && ./.venv/Scripts/python.exe -m pytest tests/test_alerting.py -p no:randomly -q
```

Expected: all pass. No existing test asserts the `Run {state}: {reason}` wording
(the alerting tests construct `AlertEvent` messages by hand).

- [ ] **Step 8: Commit**

```bash
git commit -m "fix(reports): a night that owes frames reads INCOMPLETE, not COMPLETE" -- \
  server/astrodeck/sequence/report.py server/astrodeck/alerting.py \
  ui/src/types.ts ui/src/lib/reportChart.ts ui/src/lib/__tests__/reportChart.test.ts
```

---

### Task 4: Prove it, then decide about the rig

**Files:** none modified unless a sabotage survives.

- [ ] **Step 1: Sabotage each change and read the OUTPUT**

Three reverts, one at a time, each followed by
`pytest tests/test_a_short_night_stays_resumable.py -p no:randomly -q`:

| Revert | Test that must go red |
|---|---|
| `unmet = self._session.owed() > 0` back to the `quota and ...` form | `..._leaves_the_session_resumable` |
| `self._window_closed = True` line in `_enforce_stop_boundary` | `..._is_reported_as_a_dawn_cut` |
| the `elif owed:` branch | `..._set_aside_leaves_the_night_incomplete` |

A revert that leaves the suite green means the test is not testing the change.
Read the failure text, not the exit code. Restore each change before the next.

- [ ] **Step 2: Full server suite**

```
cd server && ./.venv/Scripts/python.exe -m pytest -n auto -q
```

Expected: 6095 + 6 new passed, 0 failed.

- [ ] **Step 3: Full UI suite + build**

```
cd ui && npm test && npx tsc --noEmit && npx vite build
```

Expected: 2351 + 1 green, tsc and vite clean.

- [ ] **Step 4: The rig repair — OPERATOR'S CALL, do not do this unprompted**

The fix is forward-only. Session `18572134...` on astrotown is `complete` with
58 owed and `auto_resume` already set; flipping its `status` to `"dormant"` in
`captures/sessions/<id>.json` makes tonight resume it. Safe while the rig is
idle — `SessionStore` reads from disk on every access and holds no cache.

**This only sticks if 0.2.80 ships first.** astrotown runs 0.2.79, which still
carries the defect: a repaired session cut short again tomorrow morning gets
re-stamped `complete` and stranded a second time. Deploy, then repair.
