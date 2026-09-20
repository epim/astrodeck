# Dec axis saturation on the AM5N: evidence pack

Status: **LARGELY EXPLAINED IN SOFTWARE, 2026-09-16.** The original framing
(2026-09-14) was wrong in every load-bearing claim. Read the corrections
before the evidence.

Rig: ZWO AM5N (strain-wave), firmware 1.8.8, native serial backend
(`server/astrodeck/devices/backends/zwo_am5.py`), Meade LX200 ASCII over USB
CDC-ACM. Native guider (`server/astrodeck/guide/native.py`) over the Rust
engine (`native/crates/astro-guide/`). Profile `Rig1`.

## What v1 of this document claimed, and what survived

| v1 claim | verdict |
|---|---|
| The Dec axis delivers ~12x less motion than commanded | **WRONG.** Dec runs at exactly 0.5x RA by design; it is measured hardware. |
| 127 pulses moved the star only 13.9 px, a ~9.5x shortfall | **MEANINGLESS COMPARISON.** GO_SOUTH is a recenter leg with no distance target. |
| The 2727 ms demand ceiling is an unexplained second clamp | It is ordinary `_CAL_MS_MAX` / `_ENGINE_MAX_DURATION_MS` = 2500 territory. |
| H3: control-loop wind-up against the 1000 ms cap | **DISPROVED.** `native.py:1587` already extends the step budget for truncation. |
| H1: mechanical Dec backlash is the leading hypothesis | **DEMOTED TO LAST.** Nothing observed requires it. |

## Verified facts

**1. The Dec pulse rate is half the RA rate, and that is the hardware.**
`zwo_am5.py`: `_PULSE_RA_RATE_DEG_S = 0.004178` (1.0x sidereal),
`_PULSE_DEC_RATE_DEG_S = 0.002089` (0.5x). The driver says why: "west = R2 +
Mw: measured EXACTLY -1x sid during tracking; north/south = R1 + Mn/Ms:
+/-0.5x sid (dec has no tracking to fight)". `guide_rates()` reports both
rates honestly.

**2. THE BUG.** `native.py:1546-1556` derives a single
`calibration_duration_ms` from `rates[0]` (RA) and applies it to both axes.
`rates[1]` is never read:

```python
if "calibration_duration_ms" not in engine_cfg and rates:
    ra_deg_s = abs(float(rates[0]))
    px_s = ra_deg_s * 3600.0 / scale
    cal_dist = max(25.0, math.ceil(20.0 / scale))
    ms = cal_dist / px_s / _CAL_TARGET_STEPS * 1000.0
```

On this mount every Dec calibration step is therefore sized for twice the sky
it can actually cross.

**3. It reproduces against the rig's own measurements.** At 5.5 arcsec/px (rig
log, `native.py:102`) the derived step is ~716 ms, giving RA 2.08 px/step (12
steps to cross the 25 px target, matching `_CAL_TARGET_STEPS = 12`) and Dec
~1.04 px/step (~24 steps). The rig log of 2026-08-08 recorded
`go_north 25 steps -> +27.5 px`, which is **1.1 px/step**. Predicted 1.04,
measured 1.1.

**4. GO_SOUTH is a RECENTER leg**, confirmed in `calibration.rs:512-526`:
GO_NORTH sets `recenter_pulses_left = self.dec_steps`, then GO_SOUTH counts it
down, emitting one pulse per call with **no distance target and no per-step
success test**. So an inflated GO_NORTH count is paid for twice.

**5. The 600 s timeout is the backstop, not a stall.** Calibration ran
03:21:22 to 03:31:22 on 2026-09-14 — exactly `_CAL_TIMEOUT_S = 600.0`.

**6. No step-budget extension ran.** The 2026-09-13 night log contains no
"calibration step ... clamped to the mount ... pulse cap" line, so the derived
step stayed under the 1000 ms cap and `native.py:1577-1592` never fired.
`max_steps` stayed at its default 60.

**7. The 1000 ms pulse cap is applied in TIME**, so it allows ~15 arcsec on RA
and only ~7.5 arcsec on Dec. The message in `zwo_am5.py._capped_ms` — "one
move may not exceed ~15 arcsec" — is correct for one axis only.

## The mechanism, end to end

`calibration_duration_ms` sized from the RA rate, so Dec steps are undersized
2x. GO_NORTH therefore needs ~24 pulses instead of 12. GO_SOUTH inherits that
count as its recenter countdown. Add CLEAR_BACKLASH and NUDGE_SOUTH and the
walk runs past the 600 s backstop, which reports the timeout against whichever
leg was running — the long tail, GO_SOUTH. "127 pulse(s) on the go_south leg"
needs no mechanical fault to explain.

## The most interesting open question

The advisory that started this investigation may be an artefact of the same
bug:

    Advisory: Calibration completed but RA and Dec rates vary by an
    unexpected amount (often caused by large Dec backlash)

The RA and Dec rates **do** vary by 2x here, because the hardware rates differ
by 2x. The advisory may be measuring correctly and attributing it to backlash
when the cause is the mount's genuine rate asymmetry. If so there is no
backlash problem at all, and the advisory must know the per-axis guide rates
before it is allowed to draw that conclusion. **UNVERIFIED** — read what the
advisory actually compares.

## Also open

**The image scale, and it is the highest-value remaining unknown.** The guider
derives `image_scale` from the profile's `guide_focal_length_mm` (confirmed
150.0) **and** `guide_camera.pixel_size_um`. If either is missing it falls back
to 1.0, and the comment at `native.py:243` claims that is harmless because
"calibration measures px/ms empirically" — but `native.py:1548` feeds the scale
into both `cal_dist` and the step duration, so it is **not** harmless. At scale
1.0 the derived step computes to ~138 ms and clamps up to the
`_CAL_MS_MIN = 300` floor, which would compound the Dec problem by a further
~2.4x.

This is unresolved, and note what does **not** answer it: the guider status
`image_scale` publishes the **Rust engine's** stats scale
(`guide/base.py:90`), not the Python `_image_scale`. Both read 0.0 / unknown
live (2026-09-16) and neither is evidence about the calibration sizing.
Settling it needs a log line or a debug endpoint, because the live device
object lives in the server process and a separate process would construct a
new hub rather than inspect the running one.

Also open: whether NUDGE_SOUTH adds materially to the walk length.

## Fix, in order

1. **Size `calibration_duration_ms` from the slower axis**, or add a per-axis
   duration to the engine. There is currently no per-axis calibration duration
   key: the engine takes one `calibration_duration_ms` (`calibration.rs`
   `CalConfig`), and per-axis keys exist only for the caps
   (`max_ra_duration_ms`, `max_dec_duration_ms`). Sizing from the slower axis
   fails safe: on this mount it gives ~1524 ms, which exceeds the 1000 ms cap,
   so the existing clamp engages and the step-budget extension lengthens the
   walk instead of failing it.
2. **Log the resolved image scale and whether it is known**, so the 1.0
   fallback stops being silent.
3. **Teach the rate-variance advisory the per-axis guide rates** before it
   blames backlash.
4. Correct the `_capped_ms` message to state the per-axis arcsec figure.
5. **Only then** look for mechanical backlash.

## Method note, worth more than the finding

Six hypotheses were formed and discarded in one session, and every wrong turn
came from computing what a value should be instead of reading what it was:

- the pixel scale was taken from a source comment, not from the rig;
- the "9.5x shortfall" compared a recenter leg against a distance target that
  leg does not have;
- a "wiring gap" in the camera pixel size was guessed at, then found to be
  correctly wired (`cameras/engine.py:72`);
- the guider status `image_scale` was read as though it were the Python scale.

Get the number, then reason. Where the number is not reachable, say so and
stop, rather than substituting arithmetic for it.
