# Dec axis saturation on the AM5N: evidence pack

Status: OPEN, not diagnosed. Written 2026-09-14 for whoever picks this up.

Rig: ZWO AM5N (strain-wave), firmware 1.8.8, native serial backend
(`server/astrodeck/devices/backends/zwo_am5.py`), Meade LX200 ASCII over USB
CDC-ACM. Native guider (not PHD2). Profile `53dbed90-1469-4659-8c38-73be34311bde`.

## The short version

The declination axis is chronically failing to deliver the motion the guider
asks of it. The guider compensates by demanding longer and longer pulses until
it hits the mount's 1000 ms ceiling, which it then hits over and over. On
2026-09-13/14 this was survivable while imaging NGC 7331 (103 of 105 frames
accepted, 2.0 to 2.5 arcsec guiding) but it broke calibration outright on
NGC 7129, which ended the night's second target.

The guider's own calibration routine diagnosed it twice, unprompted:

    Advisory: Calibration completed but RA/Dec axis angles are questionable
    and guiding may be impaired

    Advisory: Calibration completed but RA and Dec rates vary by an
    unexpected amount (often caused by large Dec backlash)

## Correction to the earlier working theory

The first read of this fault, mine, was "the Dec axis is 12x slow at
declination +66, which is why NGC 7129 could not guide while NGC 7331 at
declination +34 was fine." That framing is wrong twice over and should not be
carried forward:

1. **Dec-axis motion is not geometrically dependent on declination.** Only RA
   is, by cos(dec). A Dec pulse moves the star the same angular distance at
   +66 as at +34. Any declination dependence here would be mechanical (load,
   balance, cable drag), not geometric.

2. **The Dec problem was present on NGC 7331 too.** It is visible throughout
   the 7331 block at declination +34.4, with Dec pulse demands saturating at
   2700 ms. 7331 merely tolerated it; 7129 did not, because calibration has a
   tighter success criterion than a guiding loop that can keep limping.

So: one chronic Dec fault, present at both declinations, not a 7129-specific
problem. Treat the 7129 calibration timeout as the most legible symptom, not
as the fault itself.

## Primary evidence

Source: `C:\Users\James\AstroDeck\captures\logs\2026-09-13.jsonl` on the rig
(`astrotown`). This is the durable night log. The 200-entry in-memory ring at
`/api/logs` rolled over long ago and will not have this.

Extraction script used: `C:\Users\James\AstroDeck\pull_drift_evidence.py`.

### 1. The pulse cap, and what it implies about guide rate

Every over-length pulse logs as:

    mount: ZWO AM5 (native serial): pulse north 2683 ms capped to 1000 ms
    (one move may not exceed ~15 arcsec)

1000 ms mapping to about 15 arcsec implies the mount's guide rate is set near
**1.0x sidereal** (sidereal is 15.041 arcsec/s). Most rigs guide at 0.5x.
**Verify this on the mount before anything else** - if the configured rate and
the actual rate disagree, every number downstream is wrong, and a rate
mismatch alone could produce the entire symptom set.

### 2. Dec demand is roughly double RA demand, and it saturates

Only pulses that *exceeded* 1000 ms get logged, so this is the tail of the
distribution, not the distribution. That caveat matters: you cannot compute a
mean demand from this data. What you can read is the ceiling.

Night of 2026-09-13/14, NGC 7331, declination +34.4:

| Phase | Axis | Logged exceedances (ms) |
|---|---|---|
| Pre-flip | RA (E/W) | 1179, 1195, 1132, 1080, 1179, 1192 |
| Pre-flip | Dec (N/S) | 1176, 1229, 1468, 1485, 1710, 1786, 1814, 1896, 1919, 1953 |
| Post-flip | RA (E/W) | 1066, 1246, 1257, 1292, 1316, 1411, 1423 |
| Post-flip | Dec (N/S) | 1973, 2086, 2429, 2533, 2548, 2606, 2662, 2683, 2719, 2727, 2727 |

Three things to notice:

- RA exceedances stay in a tight 1066 to 1423 ms band all night. RA is
  basically fine.
- Dec runs about 2x RA, and **gets worse after the meridian flip**, moving
  from a 1176 to 1953 band to a 1973 to 2727 band.
- The post-flip Dec values pile up against 2727 (2727, 2727, 2719). That looks
  like a second ceiling, above the mount's. Find out whether the guider clamps
  its own demand before sending, and at what value. If it does, the loop may
  be winding up against its own limit, which is a software fault, not a
  mechanical one.

At 15 arcsec/s, a 2727 ms demand is about **41 arcsec of commanded Dec
correction**, delivered as at most 15 arcsec. The loop cannot converge.

### 3. The calibration that actually failed

2026-09-14 03:31:22 PDT (epoch 1789381882), on NGC 7129:

    guiding recovery failed: native guider: calibration timed out - 127
    pulse(s) on the go_south/south leg, the star walked 13.9px from where it
    started, and the last 0 frame(s) found no star

Read that carefully:

- **127 pulses.** At the 1000 ms cap that is up to 1905 arcsec of commanded
  travel, about 32 arcmin.
- **The star walked 13.9 px.** Convert with the guide camera's pixel scale
  (see Missing data) but no plausible scale makes 13.9 px agree with 32 arcmin
  of commanded motion.
- **"the last 0 frame(s) found no star".** The star was tracked the entire
  time. This is emphatically not a star-detection or SNR failure. The mount
  was commanded to move and did not move.
- **It is the `go_south` leg.** Calibration walks north then reverses to
  south. A failure that appears specifically on the reversal is the classic
  signature of backlash or stiction.

### 4. Timeline of the 7129 attempt

| Local (PDT) | Epoch | Event |
|---|---|---|
| 09-14 00:02:11 | 1789369331 | Meridian flip attempt: mount still reports pier west, nothing flipped, calibration kept |
| 09-14 00:12:32 | 1789369952 | Flip detected: calibration + PPEC cleared, will recalibrate |
| 09-14 00:22:14 | 1789370534 | Recalibration completes with the **"large Dec backlash"** advisory |
| 09-14 02:59:45 | 1789379985 | NGC 7129 starts; autofocus ran 2.9 min unguided, re-centring first |
| 09-14 03:00:20 | 1789380020 | Guider **reuses persisted calibration** (walked on 7331 at dec +34.4) |
| 09-14 03:09:47 | 1789380587 | Reuses persisted calibration again |
| 09-14 03:13:33 | 1789380813 | `pulse west 1742 ms capped to 1000 ms` |
| 09-14 03:21:04 | 1789381264 | Persisted calibration + PPEC cleared (manual intervention) |
| 09-14 03:21:22 | 1789381282 | Fresh calibration starts |
| 09-14 03:31:22 | 1789381882 | **Calibration times out on the go_south leg** |

### 5. A separate, already-mitigated finding: 15 arcsec/min unguided drift

Logged three times on 2026-09-13:

    NGC 7331: the initial autofocus ran 2.9 min unguided - re-centring before
    guiding starts, because this rig was measured drifting about 15 arcsec/min
    with nothing holding the field

This is known and already handled by the re-centring path. Note it only so it
is not mistaken for the Dec fault. It may share a root cause (polar alignment,
balance) so it is worth keeping in view, but it is not currently costing
frames.

### 6. Unrelated failure mode in the same logs, do not conflate

2026-09-12 and early 2026-09-13 show repeated:

    guiding failed to start: native guider: no guide star found - cannot calibrate

That is the known guide-scope sensitivity problem (the guide scope only
reaches bright fields). Different fault. Ignore it for this investigation.

## Hypotheses, roughly in order of my confidence

**H1. Dec backlash or stiction in the strain-wave drive.** Supported by: the
guider's own advisory naming it; the failure landing on the direction reversal
leg; the step change after the flip, where Dec reverses sense and may sit on
the other side of the gap. Against: harmonic drives are marketed as near
zero-backlash, so a large gap would suggest a mechanical defect rather than
normal behaviour.

**H2. Guide rate mismatch.** If the mount's actual Dec guide rate is well
below what the guider assumes, every pulse under-delivers and demand inflates
until it caps. Would explain the sustained 2x ratio cleanly. Cheap to test
first, so test it first even though H1 ranks higher.

**H3. Control-loop wind-up against the cap.** The guider knows about the cap
(it logs "per-axis correction cap clamped to the mount's 1000 ms pulse cap")
but if it does not account for the truncation when computing the next
correction, error accumulates and demand runs away. The repeated 2727 ceiling
hints at a clamp somewhere in our own code. This is a software fault we would
own, and it can coexist with H1 or H2, amplifying either.

**H4. Dec load imbalance.** The AM5 has no brake, so an unbalanced payload
loads one Dec direction against gravity. Weakly supported: north demand runs
slightly above south pre-flip, but post-flip both are high, so this is not a
clean directional asymmetry. Worth a balance check since it is free.

## What to do, in order

1. **Read the mount's actual guide rate** over the serial link and compare it
   to what the guider assumes. `:Ggr#` or the equivalent in the LX200 dialect
   the AM5N speaks; see `zwo_am5.py`. Settles H2 in minutes.

2. **Measure delivered motion per pulse, per axis, in daylight.** Point at
   anything with a trackable feature, or use a star at dusk. Command a known
   pulse train on one axis, plate solve or read the guide camera centroid
   before and after, and compute arcsec delivered per second commanded.
   Compare RA against Dec. RA is the control: it is behaving, so it calibrates
   your method.

3. **Measure the backlash gap directly.** Pulse north until motion is steady,
   then reverse and count how many south pulses elapse before the centroid
   starts moving. That count times the per-pulse travel is the gap. Repeat on
   RA as a control. This is the measurement that confirms or kills H1, and
   nothing in the existing logs substitutes for it.

4. **Read our own clamp chain.** Trace a correction from the guider's computed
   error through to the serial write and find every place it is limited. Look
   specifically for a limit near 2727 ms, and for whether the post-clamp
   truncation feeds back into the next iteration's error term. That is H3.

5. **Check Dec balance** with the clutches released, both sides of the flip.

6. Only after the above: retry an NGC 7129 calibration and see whether the
   go_south leg completes.

## Missing data you will need

- **Guide camera pixel scale** (arcsec/px). Without it the 13.9 px figure
  cannot be converted. It is derivable from the guide scope focal length and
  the guide camera pixel size, both in the profile.
- **The mount's configured and actual guide rate.** See step 1.
- **The full pulse demand distribution.** The logs only record exceedances
  above 1000 ms. The guider publishes a `{t, ra, dec}` sample series over the
  API while guiding; capturing that during a run would give the real
  distribution. Note for whoever writes that capture: a previous watcher
  reported n=0 samples while 87 existed, because it could not parse that
  series. Verify your parser against live data before trusting a zero.
- **Whether the AM5N exposes a Dec backlash compensation setting** we are not
  currently using.

## A note on method

Three wrong diagnoses have already come out of this rig by reasoning from
summary statistics instead of measuring the thing directly. The 12x-slow story
at the top of this document was one of them. The logs above are enough to
locate the fault but not enough to identify it. Steps 2 and 3 are direct
measurements and they are what will actually settle this.
