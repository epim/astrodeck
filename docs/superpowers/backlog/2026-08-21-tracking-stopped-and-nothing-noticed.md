# The mount stopped tracking for nine minutes and four separate guards missed it

**Status:** OPEN. Recovered by hand at 01:01 on 2026-08-21. Four distinct
defects, none of them the tracking stop itself — that part is the mount doing
what it is entitled to do.

## What happened

```
00:44  the AM5 reaches its own limit near the meridian and stops tracking
00:44  a 180 s Ha frame is already open. It runs to completion, fully trailed.
00:48  the sequence starts an autofocus. It sweeps ten points over five minutes
       on a mount that is drifting at sidereal rate.
00:53  autofocus "completes": position 11132, HFR 8.40 px. Earlier sweeps the
       same night found 3.05-3.71 px. It is accepted and applied.
00:53  the meridian flip fires, issues tracking-on, and the mount answers '0'.
00:53  sequence crashes: "tracking on rejected (reply '0')"
00:53  auto-resume vetoed: "cloud cover 100% forecast" - on a night the camera
       was reading 200 bright stars at 20x noise.
01:01  recovered by hand: park, unpark, tracking on, /api/sequence/recover.
       Plate solve put it back 0.5' off target; the next autofocus found 372
       stars at the starting position against 83 on the drifting mount.
```

Cost: one ruined 180 s frame, one corrupted focus result applied to the run,
and 17 minutes of a clear night.

## Defect 1: nothing checks tracking except the light-frame gate

`_enforce_tracking` runs before a light frame. That is the only place the engine
asks. An autofocus sweep is ten exposures over five minutes and asks nobody, so
a mount that stops the moment a sweep begins is not noticed until the sweep
ends. Tonight it was worse than unnoticed: the sweep *consumed* the drift as
data and produced a focus position from it.

The fix is not "call the gate more often" — it is that **any operation which
measures the sky needs the mount to be tracking**, and autofocus is the clearest
case. A sweep should assert tracking at its start and, because it runs for
minutes, again per point.

## Defect 2: a fully trailed frame counted as good

`rejected=0` across the whole event. The 00:47:50 Ha frame has every star drawn
as a streak roughly 25 arcminutes long, and the run counted it toward its 175.
Whatever quality gate exists did not look at it, or looked and did not mind.

This is the one to fix first, because it is the last line of defence for every
other cause of trailing — cable snag, wind, guider failure, this. A frame whose
sources are all elongated in the same direction is mechanically detectable and
the detector already measures source shape for focus.

## Defect 3: the flip fired nine minutes after the limit

The flip triggered at 00:53. The mount hit its limit at 00:44. The flip is
scheduled off predicted meridian geometry; the mount's own limit is a different
and stricter boundary, and it won.

`test_the_mounts_own_limit_can_force_a_flip` exists and passes. It clearly does
not cover this path, because this is exactly that scenario and the flip was
still nine minutes late. Worth reading that test before touching the scheduler:
it may be one of the shapes in `tests-that-cannot-fail`.

## Defect 4: auto-resume was blocked by the bug that is already fixed

`9ed3286` (rain vetoes, cloud only advises) is committed and **not deployed**.
The rig is on 0.3.4. So after the crash, auto-resume refused every ten minutes
citing a 100% cloud forecast, while the sky was measurably clear. Without hand
intervention the rig would have sat idle until dawn for the second time this
week.

Nothing to fix in code. Deploy it.

## Defect 5, found afterwards, and probably the root of defects 1 and 3

**The mount's reported position diverges from reality during a guided run, and
the software already detects it and does nothing.**

At 01:24:24, twenty-two minutes after a plate solve synced the mount to
Dec +66.113, the log says:

```
field identification cleared: the mount has moved 4.74 deg since the last plate solve
```

The telescope had not moved. Two L frames 36 minutes apart (01:08 and 01:44)
show the same star field, same nebulosity, same cluster, offset only by the
dither — and every star is a round point, so tracking and guiding were both
working. What moved was the mount's *belief* about where it is.

Corroborated by its own arithmetic: at 01:01:45 it reported altitude 61.2, which
is right for Dec 66.1 near the meridian; by 01:31 it reported 56.1, which is
right for Dec 70.6. Its altitude and azimuth stay perfectly self-consistent with
its own declination the whole way — computed 55.607 against a reported 55.6 —
so this is not a status-reporting bug. The internal model itself has walked.

**The detection is the damning part.** Something already computes "the mount has
moved 4.74 deg since the last plate solve". Its entire response is to clear a
field-identification label. It does not re-solve, does not re-sync, does not
warn, does not hold. A number that large means one of two things — the mount is
lost, or the mount is lying — and both are worth more than forgetting a caption.

**Why this is probably the root of tonight.** The meridian flip is scheduled
from the mount's coordinates. If those are several degrees out, the flip is
computed for the wrong hour angle and fires at the wrong time, which is exactly
what happened: the flip came nine minutes after the mount had already hit its
own limit, because the scheduler thought there was time left. The same applies
to any altitude or horizon gate.

It also fits the earlier, vaguer note that mount position drift "appears only
when the guider drives the mount", and the emulated pulse guide is the obvious
suspect — `:Mn#`/`:Ms#`/`:Mw#` rate moves plus the tracking-suspend for east,
none of which the AM5 may account for in its own model. **That mechanism is
suspected, not proven**, and proving it needs a bench test: park, sync, drive a
known number of pulses in each direction with the guider otherwise idle, and
read the coordinate back.

**CORRECTED an hour later, by measuring for longer.** This paragraph first said
the divergence "moves in steps tied to some event rather than creeping", on the
strength of 80 seconds of sampling in which declination held to about 4
arcseconds. That was too short a window to conclude anything from. Sampled every
10 s for 26 minutes instead — 151 points spanning ten captured frames —
declination fell smoothly from 70.6656 to 70.5914, a steady **-10.3 arcsec per
minute**, with no step at any frame boundary, dither or filter change. It creeps.

Which sharpens the puzzle rather than solving it, and the sharpened version is
the useful thing to hand the bench test: there are **two regimes**. Between
01:02 and 01:24 the reported declination moved +4.74 degrees, about +12.9
arcminutes per minute. Between 01:47 and 02:13 it moved -10.3 arcseconds per
minute — seventy-five times slower and in the opposite direction. The fast
window contains the two autofocus runs and the guider's calibration and start;
the slow window is nothing but frames and dithers. So whatever drives the fast
regime is plausibly tied to the guider starting or to the mount tracking
unguided through a sweep, and is NOT the per-frame dithering.

At the slow rate the error reaches about 25 arcminutes over a remaining night,
which is a re-centre and no more. At the fast rate it reaches degrees in
minutes, which is what breaks the flip.

**Two fixes, in order.** First, the flip and every other pointing decision
should be computed from the TARGET's solved coordinates and the clock, not from
the mount's readout; the target's hour angle is exactly knowable and the mount's
opinion is not needed. Second, the 4.74 deg detector should trigger a re-solve
and re-sync rather than a label change — the rig already plate-solves on demand
and the correction is free.

## What I checked and ruled out on the way

Recorded because each of these looked right for a while:

- **Not a spurious status read.** `get_tracking` is `:GAT#` and `_get` *raises*
  on a link error rather than returning a falsy default, and the serial link is
  mutex'd with cancel-safe orphan joining. A failed read cannot present as
  "not tracking".
- **Not the guide-pulse tracking suspend.** `pulse_guide` really does issue
  `:Td#` for an east pulse and restore with `:Te#` in a `finally`, so a sampled
  `tracking: false` during guiding is expected and benign — but that window is
  sub-second and this was fourteen consecutive reads over four and a half
  minutes, with RA advancing and Dec drifting the whole time.
- **Not a pointing-model error.** A 5 degree altitude discrepancy looked alarming
  until it turned out to be me comparing against NGC 7129's catalogue
  declination while the mount was reporting where it had drifted to. Computed
  altitude from the configured site matched the mount's to 0.012 degrees, and
  the best-fit latitude came back [SITE-LAT] against [SITE-LAT] configured.

The thing that settled it was pulling the frame and looking at it. Six
hypotheses, five of them wrong, and one 3 KB thumbnail.
