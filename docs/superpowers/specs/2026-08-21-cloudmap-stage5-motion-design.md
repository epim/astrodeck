# Cloud occlusion, stage 5: when will it be in view — design

**Status:** stage 5 specified to implementation precision.

**Predecessors:** stages 1-4.

## 1. What stage 5 is

Measure how the cloud field is moving, decide whether to believe the
measurement, and use it to answer the occlusion question at a future time.

Build `server/astrodeck/cloudmap/motion.py`. Pure computation over
`GranuleWindow`s and a wind column handed in as data. No network, no clock, no
config. Needs numpy (FFT); no new dependency.

**Non-goals.** No fetching, no polling, no rendering, no API.

## 2. What is already measured, and what it forbids

Design 2 section 2.5 settled these against live data. They are constraints, not
suggestions:

- **Motion is real and measurable.** 111.0 km/h from the 2 km probability field
  against 112.9 km/h from the 10 km height field: two algorithms, two grids,
  2.3 km/h apart over nine consecutive pairs.
- **Estimate it locally**, in a window around the site. The vector varies
  across the sector: -3.07 px at the site, -0.04 px 400 km south, +0.89 px over
  the ocean. A global estimate is an average of unrelated weather.
- **Estimate ONE vector.** Per-height-band tracking gives 126 km/h for cloud
  below 2 km against a 17 km/h wind there. It does not work.
- **The horizon is about 30 minutes.** Consecutive 5-minute pairs correlate at
  0.27-0.34; the direct 40-minute pair correlates at 0.088 and returns a null
  shift. The pattern does not survive 40 minutes.

## 3. Three traps, each already paid for once

### 3.1 The nominal pixel size is wrong by a third

"2 km" is at nadir. At the site the cell is **2.95 x 2.21 km**. Converting a
correlation peak with 2.0 understated a measured speed by 33% during the work
that produced design 2. **Convert through stage 2's `pixel_size_km` at the
window's own centre cell**, north-south and east-west separately, because they
differ by 30%.

### 3.2 Correlate the probability field, not a thresholded mask

`Cloud_Probabilities` is continuous. Sub-pixel peak interpolation on a binary
mask is biased toward integer shifts and, worse, a binary field has less
structure to lock onto. Design 2's cross-product agreement was measured on the
continuous field.

### 3.3 A test that compares complements cannot fail

Recorded in design 2 section 2.7 as a live hazard for this stage. While
checking whether height bands shared a signal, the author correlated the CLEAR
mask against the ANY-CLOUD mask. They are logical complements, so their phase
correlations are **identical by construction** and the comparison returned
"identical" no matter what the sky was doing.

Any test here that correlates two fields must correlate fields that could
disagree.

## 4. API — exact signatures

```python
HORIZON_S          = 1800.0    # 30 min; beyond it, withhold
MIN_PEAK           = 0.15      # necessary, NOT sufficient - see below
MIN_PEAK_Z         = 8.0       # and the peak must stand 8 sigma above its own surface
MIN_WINDOW_PX      = 16        # a crop smaller than this is nearly all taper
MIN_SEPARATION_S   = 120.0     # two granules closer than this cannot resolve motion
MAX_SEPARATION_S   = 1200.0    # beyond 20 min the pattern has moved on
WIND_SPEED_FACTOR  = 1.6       # measured speed within this factor of some level
WIND_ANGLE_DEG     = 50.0      # and within this angle of the same level

@dataclass(frozen=True)
class Motion:
    north_kmh: float
    east_kmh: float
    speed_kmh: float
    toward_deg: float          # bearing, 0=N, 90=E
    peak: float                # correlation strength, 0..1
    dt_s: float
    corroborated: bool         # matched a level in the wind column
    column_consulted: bool     # was there a column to consult at all
    matched_level: str | None  # e.g. "250 hPa"
    reason: str

@dataclass(frozen=True)
class WindLevel:
    label: str; height_km: float; speed_kmh: float; toward_deg: float

def estimate_motion(earlier, later, site, *, half_px=100) -> Motion | None
def corroborate(motion, column) -> Motion
def forecast_at(site, alt_deg, az_deg, mask, height, motion, ahead_s,
                *, fov_deg=1.682) -> Occlusion
```

**AMENDED 2026-08-21 — `MIN_PEAK` alone is the wrong gate.** "Below this the
correlation is noise" is true only at one crop size. A phase-correlation
surface's noise floor scales with the number of cells, so an absolute threshold
is loose on a big crop and, worse, permissive on a small one: a handful of cells
is nearly all Hann taper and produces a convincing peak out of nothing.

The peak must therefore ALSO stand `MIN_PEAK_Z` = 8.0 standard deviations above
the mean of its own correlation surface, which is scale-free in a way an
absolute number cannot be, and the crop must be at least `MIN_WINDOW_PX` = 16
cells on each axis. `MIN_WINDOW_PX` existed in the implementation and was absent
from this section entirely.

**AMENDED — `HORIZON_S`'s justification conflates two different quantities.**
Design 2's 40-minute pair correlating at 0.088 bounds where a MEASUREMENT is
still possible. It says nothing about where a FORECAST is still useful, which is
a separate and shorter question, and 1800 s was set as though the two were the
same. The constant stays at 1800 s for now: a verifier measured the forecast
becoming worse than silence before that and proposed 1200 s, but the measurement
did not reproduce, and a safety bound does not move on a number that cannot be
re-derived. Settle it with the calibration data stage 6 collects.

`estimate_motion` returns `None` — never a zero vector — when the pair is
unusable: separation outside `[MIN_SEPARATION_S, MAX_SEPARATION_S]`, peak below
`MIN_PEAK`, or either window lacking `Cloud_Probabilities`. A zero vector is a
claim about the sky; `None` is the absence of one.

## 5. Algorithm — exact

### 5.1 `estimate_motion`

```
a = earlier window's Cloud_Probabilities, NaN -> 0.0
b = later   window's Cloud_Probabilities, NaN -> 0.0
both cropped to the same shape, centred on the site cell

A = a - mean(a);  B = b - mean(b)
w = outer(hanning(rows), hanning(cols))          # taper, or the frame edge dominates
R = conj(rfft2(A*w)) * rfft2(B*w)
R /= abs(R) + 1e-12                              # phase only
c = fftshift(irfft2(R, s=A.shape))
pk = argmax(c);  di = pk_row - rows//2;  dj = pk_col - cols//2
```

Sub-pixel by parabolic fit on the three points either side of the peak in each
axis, skipped when the peak is on the array border:

```
dsi = 0.5*(c[i-1,j] - c[i+1,j]) / (c[i-1,j] - 2c[i,j] + c[i+1,j])
```

Then, and only then, to physical units:

```
ns_km, ew_km = pixel_size_km(spec, centre_row, centre_col)     # stage 2
north_kmh = -(di + dsi) * ns_km / dt_s * 3600      # row increases SOUTHWARD
east_kmh  =  (dj + dsj) * ew_km / dt_s * 3600
```

**The minus sign on north is the likely bug.** Stage 2 fixes row 0 as north, so
a positive row shift is southward motion. A test pins it.

### 5.2 `corroborate`

The measurement is believed only if it resembles the wind at *some* level:

```
for level in column:
    speed_ok = 1/WIND_SPEED_FACTOR <= motion.speed_kmh / level.speed_kmh <= WIND_SPEED_FACTOR
    angle_ok = wrapped |motion.toward_deg - level.toward_deg| <= WIND_ANGLE_DEG
    if speed_ok and angle_ok:  corroborated
```

**Do not use a vector difference.** The measurement that this whole design
rests on — 111.0 km/h toward 359 — sits **45.3 km/h** from its nearest level
(250 hPa, 105.4 km/h toward 23) while agreeing on *speed* to 5%. A vector-
difference threshold tight enough to be meaningful would have thrown that away.
Speed ratio and bearing angle, separately, is the test that passes a good
measurement and still rejects nonsense: a correlator locked to an artifact
gives a speed no level carries, a direction no level carries, or both.

An empty column returns `corroborated=False` with a reason saying the column
was unavailable — **not** an assertion that the motion is wrong.

**AMENDED — `corroborated=False` was carrying two different facts.** "No wind
column was available" and "a column was consulted and no level matched" are
different states, and only the second is evidence against the measurement. A
single boolean reports the first as though it were the second, which is the
"unreadable is not a verdict" mistake this codebase has paid for elsewhere.
Hence `column_consulted`, and hence three corroboration states in the forecast's
reason (section 5.3), not two.

**AMENDED — report the CLOSEST matching level, not the first.** A column arrives
in whatever order its source lists it, so a first-match rule makes
`matched_level` an artifact of that ordering. It changes no verdict; it changes
what the operator is told, which is the field's only purpose. Score each level
by the hypot of its two gate residuals, each normalised by its own gate, so
neither speed nor bearing is privileged, the point of section 5.2 being that
those two must not collapse into one distance.

An uncorroborated `Motion` is still returned. Stage 6 decides what to show; this
stage does not silently discard a measurement it merely cannot confirm.

### 5.3 `forecast_at`

Cloud moving with velocity **v** puts the cloud currently at `P - v*dt` at the
pierce point `P` at time `t + dt`. So:

```
if ahead_s < 0 or ahead_s > HORIZON_S:  Occlusion(None, "no_data", reason=...)
if motion is None:                      Occlusion(None, "no_data", reason=...)

run stage 4's ray walk to get the pierce point P and crossing height
offset P BACKWARD along the motion vector by v * ahead_s
sample the mask at the offset point
```

**Backward, not forward.** Offsetting forward answers "where will the cloud
that is over me now have gone", which is the wrong question and gives an answer
that is wrong by twice the displacement.

The returned `Occlusion` carries `basis="forecast"` and a reason naming the lead
time and whether the motion was corroborated.

## 6. Tests

`server/tests/test_cloudmap_motion.py`. Synthetic windows throughout.

1. `test_the_correlator_recovers_a_shift_it_was_given` — roll a real-looking
   random field by (+3, -5) and recover exactly that. **Run this first; without
   it every other number here is unverifiable.**
2. `test_sub_pixel_shifts_come_back_fractional` — shift by 2.5 px via
   interpolation, recover within 0.2 px.
3. `test_a_southward_shift_reports_negative_north` — pins the sign of section
   5.1. Sabotage target.
4. `test_pixels_become_km_through_the_real_cell_size_not_the_nominal_one` —
   the same pixel shift on the ACMC spec at the site returns a speed at least
   30% above what 2.0 km/px would give.
5. `test_north_and_east_use_their_own_cell_dimensions` — a pure-east shift and
   a pure-north shift of equal pixel count give speeds differing by the
   2.95/2.21 ratio.
6. `test_a_flat_field_returns_none_not_a_zero_vector` — no structure, peak
   below `MIN_PEAK`, result `None`.
7. `test_a_pair_too_close_together_in_time_is_refused`.
8. `test_a_pair_too_far_apart_in_time_is_refused` — pins `MAX_SEPARATION_S`
   against the 40-minute decorrelation.
9. `test_nan_becomes_zero_and_does_not_poison_the_fft` — a field with NaNs
   still returns a finite vector.
10. `test_the_measurement_is_local_to_the_window` — put different motion in the
    centre and the corner; the centred window reports the centre's.
11. `test_a_measurement_matching_the_jet_is_corroborated` — the real numbers:
    111.0 km/h toward 359 against the measured column, matches 250 hPa.
12. `test_a_vector_difference_test_would_have_rejected_it` — assert the vector
    difference to the best level exceeds 40 km/h while `corroborate` still
    passes. Pins section 5.2's reasoning so nobody "simplifies" it back.
13. `test_nonsense_is_not_corroborated` — 400 km/h, and separately a vector
    180 degrees from every level.
14. `test_an_empty_column_is_not_a_refutation` — `corroborated=False`, reason
    names the missing column, and the motion is still returned.
15. `test_the_forecast_offsets_backward_along_the_motion` — cloud 10 km north
    moving south at 60 km/h; at `ahead_s=600` the pierce point samples the
    cloud. Offsetting forward would sample clear sky, so this pins the
    direction.
16. `test_beyond_the_horizon_the_forecast_is_withheld` — `ahead_s` of 2400
    gives `basis="no_data"`, not an extrapolation.
17. `test_a_negative_lead_time_is_refused`.
**AMENDED — test 18 as written below is FALSE, and falls into a neighbouring
version of the hazard it warns about.** Mean-subtracting `1 - p` gives exactly
`-A`, so the whitened cross-spectrum is -1 at every frequency and the surface is
a NEGATIVE delta: peak 0.0 against 1.0, argmax at float noise, and
`estimate_motion` refuses the pair. Written literally, the test fails.

The construction that genuinely cannot fail — and the one design 2 section 2.7
actually fell into — is complementing BOTH frames of the pair: correlating
clear-vs-clear against cloudy-vs-cloudy is bit-identical by construction. Assert
that. A warning about tests that cannot fail is worth little if the warning is
wrong about which construction cannot fail.

**The eighteen tests below also leave the module's refusals largely unkept.**
The suite now carries thirty; the additions pin the behind-the-limb and
out-of-sector refusals, the missing-variable case, the different-grid refusal,
the `half_px` floor, the calm-level skip, the lower half of the speed gate, the
ordering of the look-direction and lead-time checks, and the no-coordinates rule
on every reason string rather than only the forecast's.

18. `test_no_test_here_correlates_a_field_with_its_own_complement` — a
    meta-test: assert that correlating `p` against `1 - p` returns the same
    peak and shift as correlating `p` against `p`, which is why such a
    comparison proves nothing. Documents the hazard of section 3.3 in
    executable form.

## 7. Invariants

- `estimate_motion` returns `None`, never a zero vector, when it cannot measure.
- Pixels become kilometres only through `pixel_size_km`.
- Row increases southward, everywhere.
- An uncorroborated motion is returned, not discarded.
- No forecast is offered past `HORIZON_S`.
- No network, no clock, no config.

## 8. Seams for stage 6

Stage 6 renders `dome()` now and at `+15` and `+30` minutes, shows whether the
motion was corroborated and against which level, and never draws a forecast past
the horizon. It also owns calibration: joining each captured frame's own cloud
verdict to its pierce point, which the rig has been generating nightly for free.
