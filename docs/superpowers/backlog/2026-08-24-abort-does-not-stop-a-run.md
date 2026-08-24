# Abort reports success and auto-resume restarts the run

Observed on the rig 2026-08-24 00:30-00:40 PDT, release 0.3.7, session
`9af6ab0d1b5a40d5a2ce2a29d7a7d022` ("Two-target - 6946 then M31").

## What happens

`POST /api/sequence/abort` returns `{"aborted": true}`, the engine publishes
state `aborted` with `running: false`, and the watcher sees a clean stop. Within
about 45 seconds auto-resume restarts the SAME session and imaging continues.

Three consecutive aborts, three restarts:

```
00:30:00  state=aborted running=False :: sequence aborted
00:33:53  saved Light_NGC 6946_L_2026-08-24_003353_0816.fits
00:36:38  state=aborted running=False :: sequence aborted
00:37:2x  auto-resume: 'Two-target - 6946 then M31' resumed
00:38:39  state=aborted running=False :: sequence aborted     <- after disarming
00:39:25  state=running  running=True  :: slewing to NGC 6946
```

The third abort was preceded by `PATCH /api/sessions/<id> {"auto_resume": false}`,
which returned `200` with `auto_resume: False` in the body. It made no
difference. Throughout all of this `GET /api/sequence/resume-arm` returned
`{"armed": null, "hold": null}`.

So: the run could not be stopped through any documented control, and the two
endpoints that describe the arming state both disagreed with what the resume
tick actually did.

## Why it matters

An abort is a human decision, not a fault. Auto-resume exists to survive a
crash, a power blip or a restart -- it should not second-guess an operator who
deliberately stopped a run. Tonight that meant:

* The mount could not be left parked (see below).
* A warm ramp was cancelled twice by the resurrected run re-asserting -10 C
  (`camera warm ramp stopped - cooling was requested`).
* `deploy_037.ps1`'s "refuse to deploy over a running sequence" guard could
  never be satisfied, so the box was undeployable until `deploy_038.ps1`
  aborted-and-killed in one breath to win the race.

## The second-order bug: recovery resurrects an aborting run

At 00:31:11, with the run already reporting `aborted`, a manual
`POST /api/mount/park` succeeded (`mount parked`). Seventeen seconds later the
sequence layer's tracking-refusal recovery fired:

```
00:31:28  the mount refused to resume tracking (ZWO AM5: tracking on refused
          - mount is parked; unpark first (AM5 e14))            [sequence]
00:31:28  NGC 6946: the mount refuses to track - parking, unparking and
          re-acquiring the target, once                         [sequence]
00:33:18  NGC 6946: recovered in 25s - the mount is tracking again and the
          target is re-centred
```

The recovery path unparked the mount and re-acquired the target for a run that
was not supposed to be running. This is the unguarded tracking refusal noted
during the meridian-flip work; it needs the same `_aborting` check the rest of
the wind-down has.

This also explains an unexplained observation from earlier the same night: at
21:36, with `state: idle` and `running: False`, the mount was found tracking at
a target's coordinates. Same mechanism, earlier run.

## Suspected cause

`session.auto_resume` is not what the resume tick consults. There is a
module-level armed-session reference (the singleton behind the
"resume never armed on night two" defect) that the PATCH does not clear and
that `/api/sequence/resume-arm` does not report. Needs tracing from the tick
backwards to whatever it actually reads.

## What a fix has to do

1. An operator abort must disarm auto-resume for that session, permanently --
   distinguish "stopped by a person" from "stopped by a crash". The engine
   already knows which it was; `_aborting` is set on the operator path.
2. Recovery paths must not run once `_aborting` is set. Audit every watchdog
   that can issue motion, not just the tracking one.
3. `/api/sequence/resume-arm` must report what the tick reads, or it is
   worse than no endpoint -- it actively misled the diagnosis tonight.
4. A test that aborts a running engine and asserts nothing restarts it within
   several tick intervals. None of the existing abort tests run the tick.

## Not affected

Frame data is fine: 60 accepted frames of NGC 6946 L, 0 rejected, and the
meridian flip worked correctly (including its own failed-first-attempt retry).
