# Abort reported success and auto-resume restarted the run

FIXED 2026-08-24. Observed on the rig 00:30-00:40 PDT on release 0.3.7,
session `9af6ab0d1b5a40d5a2ce2a29d7a7d022` ("Two-target - 6946 then M31").

## What happened

`POST /api/sequence/abort` returned `{"aborted": true}`, the engine published
state `aborted` with `running: false`, and within about 45 seconds auto-resume
restarted the SAME session. Three times:

```
00:30:00  state=aborted running=False :: sequence aborted
00:33:53  saved Light_NGC 6946_L_2026-08-24_003353_0816.fits
00:36:38  state=aborted running=False :: sequence aborted
00:38:39  state=aborted running=False :: sequence aborted     <- after a PATCH
00:39:25  state=running  running=True  :: slewing to NGC 6946
```

Consequences: the mount could not be left parked, a camera warm ramp was
cancelled twice by the resurrected run re-asserting -10 C, and the box was
undeployable because `deploy_037.ps1` refuses to restart the server under a
running sequence and the sequence would not stay stopped. `deploy_038.ps1`
therefore aborts and kills in one breath; that workaround should be reverted
to a plain guard now that this is fixed.

## Root cause

A closed loop with no exit:

* `engine.start` sets `session.auto_resume = True` unconditionally
  (engine.py, "ARMED BY DEFAULT") -- including on a start issued by the resume
  tick itself.
* Nothing disarmed it on an abort. `_finalize_report` set the session
  `dormant`, which still owes frames and is correct.
* `session_store.armed()` is satisfied by exactly `dormant AND auto_resume`.

So: abort -> dormant -> armed -> resume -> re-armed -> abort -> ...

`PATCH /api/sessions/<id> {"auto_resume": false}` returned `200` with
`auto_resume: False` and did not help, because the engine holds its own
`self._session` reference captured at `start()` and saves that copy at
finalize, clobbering the store's.

## The fix

`_finalize_report` disarms when an operator stopped the run:

```python
if reason == "aborted" and self._aborting:
    self._session.auto_resume = False
```

Keyed on `_aborting`, NOT on the reason string alone. A process teardown
cancels the run task without going through `abort()` and lands on the same
`except CancelledError -> _finalize_report("aborted")` arm; disarming there
would retire resume-after-restart, the feature whose whole purpose is the 2am
reboot. `_aborting` is set only by `abort()`, whose four callers are all
deliberate human actions (`/api/sequence/abort`, `/api/disconnect`, profile
`apply` and `activate`), and it is still True at finalize because `abort()`
clears it in a `finally` that runs after the awaited task.

Re-arming stays a UI action, which is what `test_resume_arm.py`'s
`_dormant_armed` helper has always modelled -- it aborts and then explicitly
sets `auto_resume = True`.

Tests: `server/tests/test_abort_stays_aborted.py`. Two of the three failed
against the old code with the exact rig symptom
("the resume tick restarted a deliberate abort"); the third pinned the
shutdown case before the fix and still passes.

## Two things I claimed here first that were wrong

Recorded because both were plausible and both would have caused wasted work.

**"Recovery resurrects an aborting run."** At 00:31:11 a manual park succeeded
and 17 s later the tracking-refusal recovery unparked the mount and re-acquired
the target. I read that as recovery running during a wind-down. It was not:
that recovery lives inside the engine's run loop and cannot execute unless a
run is active, so its firing proves auto-resume had ALREADY restarted the run.
It was behaving correctly inside a live run. No `_aborting` guard is needed on
it, and the earlier "mount tracking under state idle at 21:36" has the same
single explanation -- a resumed run, not an orphaned watchdog.

**"`/api/sequence/resume-arm` misreports."** It returned `{"armed": null}`
while resume was plainly arming. The route uses `session_store.armed()`, the
same predicate as the tick, which requires status `dormant`. I sampled it while
the run was RUNNING, when no session is dormant and "armed and waiting" is
legitimately empty. The endpoint was right.

## Not affected

60 accepted frames of NGC 6946 L, 0 rejected. The meridian flip worked,
including its own failed-first-attempt detection and retry.
