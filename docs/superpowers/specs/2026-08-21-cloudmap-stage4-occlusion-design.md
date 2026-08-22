# Cloud occlusion, stage 4: is there cloud in the beam — design

**Status:** stage 4 specified to implementation precision.

**Predecessors:** stage 1 `ac8c72c` (geometry), stage 2 `328b69d` (ABI grid),
stage 3 (granules).

## 1. What stage 4 is

The first stage that answers the actual question. Given a look direction, return
the probability that cloud sits in the line of sight, and say where.

Build `server/astrodeck/cloudmap/occlusion.py`. Pure computation over two
`GranuleWindow`s handed in by the caller: no network, no file reading, no
config, no clock. Stage 3 fetches; stage 4 decides.

**Non-goals.** No motion, no forecast (stage 5). No API route, no rendering
(stage 6). No poller and no cache.

## 2. The model, and the trap that shapes it

Design 2 section 2.5 established there is **one cloud-top surface**, not a stack
of layers, because ACHA reports the top of the topmost cloud and nothing below
it. So the model is a height field `h(lat, lon)` and the test is: **where does
the ray cross that surface?**

Prototyped against real granules before this was written, and the prototype
found the trap:

```
alt 90 (straight up):  crossing at 4.0 km, cloud top 3.8 km,  p(cloud) = 0.124
```

**Every direction reports a crossing, including directions the mask calls
clear.** ACHA returns a height wherever its retrieval converged, and that
footprint does not agree with the 2 km mask. A crossing on its own means
nothing.

The two products answer different questions and both are needed:

- **The crossing locates the cloud** — it says which ground cell the beam
  actually passes through, which at 15 degrees altitude is up to 33 km from
  the site and is the entire reason this project exists.
- **The mask decides whether cloud is there** — `Cloud_Probabilities` at that
  pierce point is the answer.

So: **walk the ray to find the crossing, then report the mask's probability at
the crossing's pierce point.** The prototype's numbers behave: 0.124 at the
zenith, 0.576 toward the west at 25 degrees, on a night whose cloud was west and
south. Direction changes the answer, which a scalar forecast cannot do.

### 2.1 A missing height is not clear sky

If the ladder finds no crossing, **the answer is not "clear"** — thin cirrus
that ACHA cannot height is exactly the cloud that ruins a sub while the mask
still sees it.

**Measured, so the test is written knowing this: the fallback is RARE.** Over
1740 rays across the dome on a real granule pair, 98.3% found a crossing and
only **1.7%** fell through. About 17% of ACHA *pixels* carry no retrieval, but a
ray walks up to 75 rungs through many cells and needs only one of them to have a
height. An earlier draft of this section quoted the per-pixel figure next to the
per-ray case and invited exactly the wrong inference.

**AMENDED — that measurement is one overcast night at one sea-level site, and
this section turned it into a property of the module.** The fall-through rate
depends on the sky and on where you stand; 1.7% is what that granule pair gave
over that dome, not what the code does. Quote it as an observation with its
conditions attached, and do not let a test assert it.

Rarity is the argument for testing it harder, not less. A path taken by 1 ray in
60 will not be exercised into correctness by use, and the failure mode -
reporting clear sky through thin cirrus - is the one that costs frames.

For scale, the same 1740 rays put the probability at the crossing at a median of
0.306, with p10 0.124 and p90 0.541: a genuinely mixed sky with the site sitting
in a clearer part of it.

No crossing falls back to the mask probability at `FALLBACK_HEIGHT_KM`, and the
result is marked `basis="mask_only"`. Reporting clear here would repeat the
mistake design 2 section 2.5 already paid for once.

## 3. API — exact signatures

```python
FALLBACK_HEIGHT_KM = 3.0
LADDER_START_KM    = 0.2
LADDER_STEP_KM     = 0.2
LADDER_TOP_KM      = 15.0
MIN_USABLE_ALT_DEG = 5.0

@dataclass(frozen=True)
class Occlusion:
    probability: float | None   # 0..1; None when nothing could be read
    basis: str                  # "crossing" | "mask_only" | "no_data"
    crossing_km: float | None   # height where the ray met the surface
    pierce_lat_deg: float | None
    pierce_lon_deg: float | None
    downrange_km: float | None  # ground distance to the pierce point
    beam_m: float | None        # beam width there, from stage 1
    cell_km: tuple[float, float] | None   # the mask cell's true size, stage 2
    quality: int | None         # the mask DQF at that cell
    reason: str                 # one plain sentence, for a human

def occlusion_at(site, alt_deg, az_deg, mask, height, *,
                 fov_deg=1.682) -> Occlusion

def dome(site, mask, height, *, alt_step_deg=2.0, az_step_deg=4.0,
         min_alt_deg=MIN_USABLE_ALT_DEG) -> list[list[float | None]]
```

`mask` and `height` are stage 3 `GranuleWindow`s. `mask` must carry
`Cloud_Probabilities` and `DQF`; `height` must carry `HT`. Missing variables
raise `KeyError` at the call, not silently.

`dome` returns a grid indexed `[alt_index][az_index]`, alt ascending from
`min_alt_deg`, az ascending from 0. It exists so stage 6 renders one call, and
so this stage is measured on cost.

## 4. Algorithm — exact

### 4.1 `occlusion_at`

```
if alt_deg < MIN_USABLE_ALT_DEG:  Occlusion(None, "no_data", ..., reason=...)

prev_gap = None
for h in [0.2, 0.4, ... 15.0]:                    # LADDER_START..TOP by STEP
    p    = pierce_point(site, alt_deg, az_deg, h)     # stage 1
    top  = height value at p, in km                   # stage 3 value_at, /1000
    if top is NaN:
        prev_gap = None                               # a gap resets the walk
        continue
    gap = top - h
    if prev_gap is not None and prev_gap > 0 >= gap:
        return crossing result at h, using the MASK at p
    prev_gap = gap

# no crossing anywhere on the ladder
p = pierce_point(site, alt_deg, az_deg, FALLBACK_HEIGHT_KM)
return mask-only result at p
```

**AMENDED 2026-08-21 — the literal ladder above breaks every elevated
site.** Stage 1's `pierce_point` refuses a layer at or below the observer, so a
first rung of 0.2 km MSL raises `ValueError: layer_msl_km 0.2 is at or below the
observer at 1.624 km MSL` for anywhere above 200 m — which is Boulder, this
series' own first golden point, and most western-US observing. The module does
not misreport those sites; it raises on the first rung and never answers at all.

The ladder therefore starts at the first rung **above `site.elev_km`**, and
`FALLBACK_HEIGHT_KM` is 3.0 km only where that is above the site, otherwise the
first rung above it, with `no_data` reserved for a site above the whole ladder
(Mauna Kea is 4.2 km and is a real place). A rung below the observer is sky the
beam is already past; there is nothing there to cross.

**AMENDED — the four fall-through causes must not share one sentence.** The
pseudocode's single "no crossing anywhere on the ladder" exit produced one
`reason` for four different situations: no retrieval along the beam, a pierce
point outside the fetched window, outside the sector, and behind the limb. They
are different facts about what is known and a human reading one needs to know
which.

**`prev_gap = None` on a NaN is not an optimisation.** Without it, a ray that
leaves one cloudy region and enters another registers a false crossing at the
boundary between them, because the gap appears to flip sign across a stretch of
sky that had no surface at all.

**The comparison is `prev_gap > 0 >= gap`.** Strict above, inclusive below, so a
ray that lands exactly on the top counts as crossing it. The reverse
(`>= 0 >`) makes a ray grazing the top at exactly the rung height miss.

A ray already above the surface at the first rung — fog, or a top below
0.2 km — never satisfies the condition and correctly falls through to the
mask-only branch: the beam is above that cloud, not through it.

### 4.2 Reading the two grids

The mask is 2 km and the height is 10 km. They share a projection but not a
sampling, so **each lookup goes through its own `GridSpec`**. Never reuse an
index between them; that is a five-times error and it will look plausible.

Both lookups are `lonlat_to_index` on the window's own spec, then
`GranuleWindow.value_at`. A pierce point outside the window is not an error —
it means the caller asked for a direction the fetched window does not cover.
Return `basis="no_data"` with a reason naming the shortfall, so stage 6 can draw
the edge of knowledge rather than a confident blank.

### 4.3 The honesty fields

`beam_m` is
`beam_footprint_km(slant_to_layer_km(alt, h, site.elev_km), fov_deg) * 1000`.

**AMENDED 2026-08-21 — the line this section shipped had two errors in it,
and the honesty field was the thing it corrupted.** It read
`beam_footprint_km(fov_deg, slant_to_layer_km(alt, h, 0))`.

Stage 1 declares `beam_footprint_km(slant_km, fov_deg)`, so the arguments were
reversed — and the call does not fail. It quietly computes `2*fov*tan(slant/2)`
with the angle in degrees, which is nearly right where anyone would check it
(1.043 km against a true 1.011 km at a 34 km slant, 3%) and wrong where the beam
actually matters, raising only past a 180 km slant.

The slant must also start at the **site's** elevation, not sea level. To a 4 km
deck at 30 degrees the slant is 7.992 km from sea level and 4.749 km from
Boulder — a **68% overstatement of the beam**, which is precisely the number
the UI shows to argue the satellite pixel dwarfs it. An error in the direction
of flattering our own resolution is the worst direction available. `cell_km` is `pixel_size_km` from stage 2 at the mask cell.

They exist to be shown together. At 30 degrees the beam is 35-530 m across and
the cell is 2.95 x 2.21 km — **over a thousand times the area**. Any UI that
prints a probability without that ratio nearby is claiming a determination the
data cannot support (design 1 section 3).

## 5. Cost

`dome` at 2 x 4 degrees over alt 5-90 is 43 x 90 = 3870 rays, each walking up
to 75 rungs with two grid lookups: about 580,000 lookups worst case. Pure
Python at roughly 1 microsecond a lookup is under a second, and the walk exits
early on the first crossing, which the prototype hit by rung 20 in every
direction.

**Measure it in a test and fail above 3 seconds.** If it needs to be faster, the
answer is to vectorise the ladder with numpy, not to coarsen the grid — the
resolution is what makes the picture honest.

## 6. Domain errors

**AMENDED — "raises ValueError" does not specify a guard.** Several of these
inputs raise a `ValueError` from two modules away whether or not this module
checks them, so a test asserting only the exception TYPE passes with the guard
deleted. Each guard must be pinned on its **message**, or on behaviour only the
local check produces (an early raise before any grid work, say). Sabotage found
four guards deleteable with the suite green for exactly this reason.

`ValueError` for: `alt_deg` outside `0 < alt <= 90`; `az_deg` not finite;
a non-positive ladder step; `alt_step_deg`/`az_step_deg` outside `(0, 45]`.
`KeyError` for a required variable absent from a window. Nothing here returns a
plausible number for a bad input.

## 7. Tests

`server/tests/test_cloudmap_occlusion.py`. Fixtures are synthetic
`GranuleWindow`s built in the test — a `GridSpec` from design 2 section 9.1 and
small numpy arrays — so every scenario is exactly constructed.

1. `test_a_ray_through_a_flat_deck_crosses_at_the_deck_height` — uniform top at
   4.0 km, ray at 90 degrees, crossing within one ladder step of 4.0.
2. `test_the_crossing_moves_downrange_as_the_altitude_drops` — same deck at
   alt 90, 45, 20: `downrange_km` strictly increases, and at 20 degrees it
   exceeds 8 km. Pins that the geometry is actually in the loop.
3. `test_the_probability_comes_from_the_mask_at_the_pierce_point_not_overhead`
   — build a mask that is clear overhead and cloudy 15 km north; a ray at low
   altitude toward the north returns the cloudy probability. **This is the
   whole project in one test.**
4. `test_a_crossing_in_a_clear_cell_reports_the_clear_probability` — the trap
   from section 2: a height surface everywhere, a mask reading 0.02. The result
   is 0.02, not "occluded".
5. `test_no_retrieval_anywhere_falls_back_to_the_mask_and_says_so` — all-NaN
   height, `basis == "mask_only"`, probability from the mask at
   `FALLBACK_HEIGHT_KM`, and the probability is NOT None and NOT zero.
6. `test_a_gap_in_the_height_field_does_not_fake_a_crossing` — cloudy at 2 km,
   NaN in the middle, cloudy again at 9 km along the ray; assert the crossing
   is the real one and not the boundary. Sabotage target: removing the
   `prev_gap = None` reset must fail this.
7. `test_a_ray_grazing_the_top_exactly_counts_as_crossing` — pins
   `prev_gap > 0 >= gap`.
8. `test_a_cloud_top_below_the_first_rung_is_not_a_crossing` — top at 0.1 km,
   result is `mask_only`; the beam is above it.
9. `test_the_two_grids_are_never_confused` — mask and height windows whose
   specs differ by the real 5x sampling; place a feature so that using the
   wrong spec picks a cell 5 cells away, and assert the right one is read.
10. `test_a_pierce_point_outside_the_window_says_no_data` — and the reason
    names the window, not a number.
11. `test_the_beam_and_the_cell_are_reported_together` — both non-None on a
    crossing, and `cell_km` area exceeds the beam area by more than 100x at
    alt 30, which is the ratio the UI must show.
12. `test_altitude_below_the_floor_is_refused_not_guessed` —
    `basis == "no_data"`.
13. `test_domain_errors` — parametrised over section 6.
14. `test_a_missing_variable_raises_rather_than_defaulting` — a mask window
    without `Cloud_Probabilities`.
15. `test_the_dome_is_indexed_altitude_then_azimuth` — shape matches the steps,
    `[0]` is the lowest altitude, and a feature placed due east appears at the
    az-90 column, not az-270. Pins the azimuth convention end to end.
16. `test_the_dome_completes_within_its_budget` — full 2 x 4 degree dome under
    3 seconds.

## 8. Invariants

- No network, no file, no clock, no config in this module.
- The mask grid and the height grid are indexed through their own specs.
- A missing height never reports clear.
- `probability` is `None` only when `basis == "no_data"`.
- Every returned `Occlusion` carries a `reason` a human can read.
- Pure `math` plus numpy for array access. **The "no h5py import" this
  originally claimed is unkeepable as the design is written**: section 3
  annotates two parameters as `GranuleWindow`, and importing stage 3 to name the
  type pulls h5py in behind it. Either the annotation is a string under
  `TYPE_CHECKING`, or the invariant is dropped. Do not restate it in the module
  docstring where it will read as verified.

## 9. Seams for stage 5

Stage 5 takes two `mask` windows at different times, estimates one local motion
vector (design 2 section 2.5: local, single, cross-checked against the model
wind column), and offers `occlusion_at(..., at=now + dt)` by shifting the pierce
point against that vector. It must respect the 30-minute horizon of design 2
section 2.6 and withhold rather than extrapolate past it.
