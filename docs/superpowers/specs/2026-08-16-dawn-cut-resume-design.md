# A night cut short is not a night finished (#252)

Design, 2026-08-16. Decision taken by the operator on the semantics question;
this records what is being built and why.

## The defect

A run whose last target is set aside mid-step finalizes as `complete`, and the
session it belongs to is marked `complete` with frames still owed.
`session_store.armed()` only ever returns a `dormant` session, so the remainder
is never shot: the campaign ends silently, one night in, with the operator
holding a session that says it finished.

### Evidence, from the rig rather than from reading

Last night's log, `captures/logs/2026-08-15.jsonl`:

```
05:25:08  NGC 7129: skipped - observing window closed (stop time / max run / dawn)
05:25:08  sequence 'NGC 7129 - LRGB+SHO cycle' complete: 117 frames
```

And the session store, `captures/sessions/`:

| session                        | status   | armed | frames  | owed |
|--------------------------------|----------|-------|---------|------|
| NGC 7129 - LRGB+SHO cycle       | complete | True  | 117/175 | 58   |
| NGC 6946 SHO with a cloud dodge | complete | False | 83/90   | 9    |
| NGC 6946 Fireworks - LRGB+Ha    | complete | False | 120/180 | 60   |

Last night's session is the worst shape of the three: `auto_resume` is set, so
it believes it will come back, and `complete` means nothing will ever look at
it again. Armed and unreachable.

### The mechanism, exactly

1. `_enforce_stop_boundary` (engine.py:1435) raises `StopTarget` at a frame
   boundary once the target's frozen window has closed.
2. The scheduler catches it, marks the target skipped, and removes it from
   `remaining` (engine.py:1232-1248).
3. `remaining` is now empty, so `while remaining` exits normally. `_dawn_cutoff`
   is set ONLY in the other branch (engine.py:1259), the one reached when every
   remaining window was already closed at SELECTION time.
4. `_run` therefore takes the else at engine.py:982 - state `complete`,
   `_finalize_report("complete")`.
5. `_finalize_report` (engine.py:1077-1081) computes `unmet` only when
   `count_mode == "accepted"`. Every plan on this rig is `attempts`, so `unmet`
   is `False` by construction and the session is stamped `complete`.

Steps 3 and 5 are two independent defects that happen to line up. Either one
alone would have left the session resumable.

### Why the suite did not catch it

`test_window_dormant_then_resume_exact_remaining` and
`test_a_campaign_advances_across_nights` both close the target's window in the
PAST, so the scheduler rejects the target at selection and reaches the
`dawn_cutoff` branch, which was always correct. Neither test lets a boundary
trip mid-target - which is the only shape a single-target night can take, and
the shape of every real night this rig has run.

`_enforce_altitude_floor`'s own docstring promises the same thing and breaks the
same way: "SUSPENDED, NOT DONE ... tomorrow's resume seeds `_done` from the
frames that actually exist, finds this target short, and shoots the remainder."
True unless it fires on the last target.

The flows doctor tells operators the same thing in `to_plan.py:476-478` - "a run
that ends at its stop boundary leaves the session DORMANT with `auto_resume`
set" - and cites those two tests as evidence. The claim was false for the common
case. This design makes it true rather than editing it away.

## The decision

**In `attempts` mode, a frame not attempted before dawn is still owed.** `count`
is a total across the campaign, not a nightly budget. The session stays dormant
and armed until the ledger holds every frame the plan asked for.

Rejected: "spent at dawn" (a night is the budget), which is today's accidental
behaviour and would have required deleting the resume promises above; and
"owed but bounded" (give up after N nights), which adds a stopping rule and a
setting for a problem nobody has hit yet. If an unsatisfiable plan does start
haunting the session list, `ResumeArm`'s once-per-session "standing by ... still
owes N frames" line is where it will show up, and Abandon is the existing exit.

## The rule

**Complete means the ledger holds every frame the plan asked for.** One
question, one source of truth, asked in one place.

## Changes

### 1. `Session.owed()` - session.py

```python
def owed(self) -> int:
    """Frames still owed across every step (mode-aware, floored at 0)."""
    return sum(self.remaining().values())
```

One definition of the question, so the run's ending and the session's status
can never answer it differently.

### 2. The session terminal rule - engine.py:1076-1081

Drop the `count_mode` gate:

```python
unmet = self._session.owed() > 0
self._session.status = ("complete" if reason == "complete" and not unmet
                        else "dormant")
```

`remaining()` is already mode-aware: attempts mode counts recorded frames,
accepted mode counts accepted ones. Verified that `_record_frame`
(engine.py:3645-3652) ledgers rejected frames too, so an attempts-mode step that
shot its full count is never falsely short; and that calibration frames reach
the same ledger (the rig's "Calib 2026-08-11 bias+darks" session holds 94), so a
calibration plan can still complete.

This is the backstop. Whatever new way a future run finds to end early, a
session that owes frames cannot be stamped finished.

### 3. The run's own ending must agree - engine.py:971-987

`_enforce_stop_boundary` sets `self._window_closed = True` where it raises
(reset alongside `_dawn_cutoff` in `start()`). The natural-exit path then picks
its ending from two facts rather than one:

```python
owed = self._session.owed() if self._session is not None else 0
if self._dawn_cutoff or (self._window_closed and owed):
    -> dawn_cutoff
elif owed:
    -> incomplete
else:
    -> complete
```

`owed` is the substantive test - it is what says the plan is unfinished.
`_window_closed` only chooses the LABEL, separating a window that ran out from a
target the run set aside for its own reasons.

The conjunction is defensive rather than load-bearing, and the honest reason to
write it is that I could not construct a night where `_window_closed` is true
and nothing is owed: a target cut at a frame boundary is by definition short of
its count. Writing it as a conjunction means that if some later path does
produce one, it is reported as the complete night it is, instead of a cutoff.

### 4. A new end reason: `incomplete`

The run did everything it was told to do, and the plan still owes frames. Its
causes are the altitude floor (`on_floor="advance"`), a missed start
(`on_missed="skip"`), and an operator's `skip_target` instruction. Reporting
those as `complete` is the same lie in a smaller font.

- `report.py:101` - extend the reason comment.
- `ui/src/types.ts:590` - add to the `end_reason` union.
- `ui/src/lib/reportChart.ts:105` - `case "incomplete": { word: "INCOMPLETE",
  tone: "warn" }`. Warn, not good: `dawn_cutoff` is the sky running out, which
  is nobody's fault and needs no attention; this one means the run set targets
  aside and the operator may want to know why.
- `alerting.py:275` - `f"Run {state}: {reason}"` would emit "Run complete:
  incomplete". Reword to `f"Run ended: {reason}"`, which reads correctly for
  every existing reason too. No test asserts the current wording.

`health.ts` needs nothing: it only reacts to `seqState` `aborted`/`error`, and
these endings keep `state="complete"`.

### 5. The terminal log line carries the shortfall

Last night the whole record of a 58-frame shortfall was the word `complete`. The
three endings become:

- `sequence 'X' stopped at dawn: 117 of 175 frames, 58 still owed - the session
  stays armed and resumes when the window opens`
- `sequence 'X' ended with targets set aside: 117 of 175 frames, 58 still owed -
  the session stays armed and resumes when the window opens`
- `sequence 'X' complete: 175 frames` (unchanged)

## Deliberately not changed

- **`ResumeArm.armed()`** stays dormant-only. With the session status honest,
  dormant is the correct filter; widening it would re-arm abandoned sessions.
- **`end_reason` for a dawn cut** stays `dawn_cutoff`. It is accurate about the
  cause and already understood by the UI.
- **The flows doctor note and the altitude-floor docstring** stay as written.
  They describe the behaviour this change delivers.
- **The other two stranded sessions on the rig.** Fixed forward only; no
  migration code. See rollout.

## Tests

New file `server/tests/test_a_short_night_stays_resumable.py`. Every test drives
the real engine - the last bug of this class hid behind a correct pure function
whose only caller was broken, and a pure-function test would pass against this
one too.

1. **A dawn cut mid-target leaves the session resumable.** Single target, a
   frozen window whose stop passes after the first frames. Assert:
   `end_reason == "dawn_cutoff"`, `session.status == "dormant"`,
   `session.owed()` equals the shortfall, and `session_store.armed()` returns
   it. This is last night, reproduced.
2. **The altitude floor twin.** `on_floor="advance"` fires on the only target:
   `end_reason == "incomplete"`, session dormant, owed matches.
3. **A genuinely complete run still completes.** Guards against over-correcting
   into false dormancy - `end_reason == "complete"`, status `complete`,
   `armed()` returns None.
4. **A calibration-only plan still completes.** The ledger-truth rule must not
   strand calibration sessions in a nightly resume loop.
5. **A mixed night.** Target A is cut at its stop boundary, target B then runs
   to its full count. The night is a `dawn_cutoff`, the session is dormant, and
   `owed()` equals A's shortfall alone - B is not dragged back into it.

Then sabotage: revert each of changes 2, 3 and 4 individually and confirm the
matching test goes red. A test that passes against the reverted code is not
testing the change.

Existing tests that must stay green unmodified:
`test_session_resume.py::test_window_dormant_then_resume_exact_remaining`,
`test_campaign_across_nights.py` (both), and the `end_reason` assertions in
`test_engine_safety.py` / `test_engine_timeouts_escalation.py`.

## Rollout

The fix is forward-only. One session gets repaired by hand: NGC 7129, `complete`
with 58 owed and `auto_resume` already set, flipped back to `dormant` in
`captures/sessions/<id>.json` so it resumes tonight. Safe to edit while the rig
is idle - `SessionStore` reads from disk on every access and holds no cache.

**Order matters.** The repair only sticks if 0.2.80 ships first: astrotown runs
0.2.79, which still carries this bug, so a repaired session cut short again
tomorrow morning would be re-stamped `complete` and stranded a second time.

The other two sessions (60 and 9 frames owed) are left as they are.

## Risks

- **A plan that can never be satisfied stays dormant and armed forever** - a
  target that no longer rises this season, a step the operator lost interest in.
  Accepted with the semantics decision above; visible through ResumeArm's
  standing-by line, exits through Abandon.
- **Pruning.** `SessionStore._prune` only reclaims `complete`/`abandoned`
  sessions, so more sessions staying dormant means the 200-session soft cap is
  reached sooner. Nineteen sessions exist after three months of use; not a
  problem now, worth remembering.
- **A failed ledger write** (`engine.py:3653` logs and continues) makes a step
  look short, so the session goes dormant and shoots a few extra frames on the
  next night. Self-correcting, and the right way to be wrong.
