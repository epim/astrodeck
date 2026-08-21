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
