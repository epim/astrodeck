# The guide scope reaches only bright fields: what to do about it

Written 2026-08-08, after the night the guider closed its first loop on Deneb
and found nothing at RA 23h / Dec +30.

This is a proposal. Nothing here has been built.

---

## Recommendation

**Check the guide scope's focus before anything else is built, bought or
decided.** The guide star-finder has two hard size limits, both hardcoded in
Rust, and at 0.24 mm of focuser error it finds *nothing at any brightness or
exposure*, while at 0.20 mm it has lost 3 magnitudes of reach but still finds a
star as bright as Deneb. That is the reported observation, exactly, and it is
reproduced below from the shipped code without touching the rig.

The cheapest thing that settles it is a live HFR and blob-size readout on the
guide camera preview, which the app already computes for the main camera and
already has the guide frame's pixels in hand to compute. One afternoon of work,
one afternoon at the scope, no money.

---

## 1. What the code actually does

The guide star-finder is Rust, not the Python `detect_stars` that blinded
autofocus in task #112. It has the same *shape* of defect and it is worse,
because it fails to exactly zero rather than degrading.

### Limit A: the background annulus sits inside a defocused star

`native/crates/astro-guide/src/starfind.rs:313-315`

```rust
const A: i32 = 7;
const B: i32 = 12;
```

The background is the 2-sigma-clipped mean over the annulus from radius 7 px to
radius 12 px around the peak. The centroid, the mass and the SNR are then
accumulated over the disk of radius 7 (`starfind.rs:401-402`), keeping only
pixels at or above `mean_bg + 3 * sigma_bg` (`starfind.rs:196`).

Once the star is wider than about 17 px across, the annulus is measuring the
star, so the threshold rises to the star's own surface brightness and no pixel
in the 7 px disk clears it. Measured on a synthetic uniform defocus disk, sky
500 ADU, bright star:

```
 disk px   mean_bg   thresh        result      mass
      16       503      530        StarOk     35602
      17       583      878   StarLowMass         0
      20       584      840   StarLowMass         0
```

`mass < 10.0` returns `StarLowMass` at `starfind.rs:219`, and `was_found` is
false for it (`starfind.rs:76-78`), so `auto_find` discards the candidate at
`select.rs:610`. Zero pixels, zero mass, no star. This is scale-free: a star
ten magnitudes brighter fails at the same 17 px, because the threshold that
rejects it is built from the star itself.

### Limit B: a defocused blob's PSF maxima annihilate each other

`native/crates/astro-guide/src/select.rs:513-538`, `drop_search_box_conflicts`.
Any two surviving peaks within `search_region + 5` = 20 px of each other are
**both** erased unless one is at least 5x the other's significance. A defocused
blob's 9x9 matched-filter response (`select.rs:337-395`, a kernel whose positive
weights only reach radius 2.2 px) stops being single-peaked and breaks into
several near-equal maxima around the rim. They are more than 5 px apart, so
`merge_close_peaks` does not merge them, and less than 20 px apart, so the
conflict rule deletes every one of them.

This fires *earlier* than limit A and it fires during acquisition, before
anything is measured.

### The gate that looks like the size limit cannot fire

`FindParams::default()` sets `max_hfd: 20.0` (`starfind.rs:82`, enforced at
`starfind.rs:259-269`). It never fires. HFR is accumulated only over pixels
inside the radius-7 disk, so HFD cannot exceed about 14. The largest HFD any
configuration in the sweep produced was **8.95**.

This is the same shape as the `hfr > half` note in `imaging/stars.py:204-208`
("rejects 0 of 647 detections"), and it matters beyond tidiness:
`server/astrodeck/guide/native.py:1149` forwards `min_hfd` and `max_hfd` from
config to the engine as if they tuned size acceptance. `min_hfd` works, it
rejects hot pixels. `max_hfd` is a knob wired to nothing reachable. The limits
that actually bite are `A = 7`, `B = 12` and `CONFLICT_EXTRA = 5`, none of which
has a config path.

### What that costs, in magnitudes and in millimetres

Full `auto_find` pipeline (median filter, PSF convolution, local maxima, merge,
conflict prune, edge drop, `star_find`), reimplemented faithfully in Python and
run against synthetic frames. Blur disk diameter converts to focuser error as
`d_px = 50 x error_mm` at f/5 with 4.0 um pixels.

| blur disk | focuser error | blur on sky | faintest star still found | reach lost |
|---|---|---|---|---|
| 0 to 2 px | up to 0.04 mm | 11" | baseline | 0 |
| 4 px | 0.08 mm | 22" | 1.6x brighter | 0.5 mag |
| 6 px | 0.12 mm | 33" | 2.5x brighter | 1.0 mag |
| 8 px | 0.16 mm | 44" | 6.3x brighter | 2.0 mag |
| 10 px | 0.20 mm | 55" | 16x brighter | 3.0 mag |
| 12 px and beyond | 0.24 mm | 66" | **nothing, at any brightness** | all of it |

The tolerance is 0.24 mm of focuser travel. The critical focus zone at f/5 is
about 0.027 mm. So the software is roughly 9x more forgiving than visual focus,
and still nine times tighter than a helical focuser nobody has ever checked is
likely to be sitting at.

## 2. Why this matches the night exactly

Three independent things line up.

**The exposure ladder that was tried could not have worked.** 2 s at gain 100
through 6 s at gain 80 binned 2x2 spans roughly 1 to 2 magnitudes of depth. At
0.20 mm of focus error the deficit is 3 magnitudes, which needs 16x the signal;
at 0.24 mm no exposure works at all, because the failing gates are ratios and
thresholds built from the star, not photometric floors. 4 s at gain 400
saturated the sky, which closes that route. A pure sensitivity shortfall would
have improved steadily with exposure. It did not move.

**Deneb bounds the error from the other side.** Deneb was found, calibrated and
guided at SNR 340. Past 0.24 mm nothing is found at any brightness, Deneb
included. So the guide scope is not wildly out. It is somewhere in the band
where a mag 1.25 star is untroubled and an ordinary field is invisible, which is
a band about 0.1 mm wide.

**The field was not empty.** 1920 x 1080 px at 5.5 arcsec/px is 2.93 x 1.65
degrees, about 4.8 square degrees. Standard all-sky counts put roughly 15 to 20
stars brighter than V=9 and around a hundred brighter than V=11 in a field that
size at that galactic latitude. The main camera plate-solved it to 0.4 arcmin,
so the sky was clear and the pointing was right. Finding zero is not a sky
problem.

## 3. What this analysis is, and what it is not

Stated plainly, because a confident number has cost this rig a night before.

- The limits at `starfind.rs:313-315`, `starfind.rs:401-402` and
  `select.rs:513-538` are **read directly from the shipped source**. The
  `max_hfd` gate being unreachable follows from the same source. Those claims do
  not depend on any model.
- The magnitude table depends on a **Python reimplementation** of the Rust, run
  against a **synthetic** star: a uniform disk convolved with a 1.2 px Gaussian,
  on a 121 px frame, Gaussian noise, one star. Real optics have aberration, real
  frames have many stars, and both change the conflict-prune and the top-100 cap.
  Treat the millimetre and magnitude figures as the right order and the right
  shape, not as calibration.
- **Nothing here has been checked against a real guide frame.** No guide frame
  from that night is in the repo. That is the single largest gap and the fix for
  it is option 1.
- Defocus is the hypothesis that fits. It is not proven. Dew on the guide scope,
  a mis-seated camera, or an aperture obstruction would all produce a milder
  version of the same thing and would all be visible in the same afternoon.

## 4. The options, ranked

### 1. Guided manual focus for the guide camera (do this first)

*Diagnosis and treatment in one.*

Live guide-camera frames with a large, readable size number and a
you-are-at-best-focus verdict, so the owner can turn the ring by hand and watch
it fall.

The measurement stack already exists and is already wired for the main camera.
`hub.py` attaches `hfr`, `stars`, `defocus_r80`, `defocus_snr` and a Bahtinov
verdict to every main-camera preview (`server/astrodeck/hub.py:2860-2876`). The
guide preview path already holds the raw array: `_guide_preview_defect(data,
cam.name)` at `server/astrodeck/hub.py:1470` is handed the numpy frame and today
only checks whether it is constant. That line is the seam.

The right metric is `imaging/defocus.py::measure_blob`, not `median_hfr`.
`measure_blob` sizes its aperture from the source with a 1200 px window and has
no ceiling, so it reads a number at every focuser position from wildly out down
to sharp. `median_hfr` saturates around 5 px and would go flat exactly where the
owner needs to know which way to turn. `HANDOVER_R80_PX = 12.0` is already the
repo's "small enough for real star detection to take over" boundary and is
almost exactly the 12 px wall found above, which is a pleasant coincidence worth
using rather than re-deriving.

- Cost: an afternoon of code, an afternoon at the scope. No money. Daylight
  works: a distant terrestrial object gives a stable target, and any bright star
  works after dark.
- Buys: the answer to the actual question, plus a permanent tool. The guide
  scope will drift with temperature and will be knocked; this makes re-checking
  it a two-minute job forever.
- Does not fix: nothing about faint fields if the scope turns out to be in
  focus. Then go to option 2 and the answer is instrumental, not software.
- Known to have worked when: the blob size bottoms out and holds, and a
  subsequent slew to an ordinary mag 9 field finds a guide star. That second
  half is the real test. Do not accept "the number went down" as proof.

### 2. Measure the guide scope's actual reach, once, after focus

*Diagnosis. Do this second, before writing any catalog code.*

Slew to three or four ordinary fields and record how many candidates
`auto_find` returns and the SNR of the best. That number decides whether options
4 and 7 are needed at all. Today nobody knows the guide scope's limiting
magnitude, and every argument about catalogs and hardware is being had without
it.

- Cost: half a night, no code beyond a way to read the candidate count.
- Buys: the fact that makes every remaining decision cheap.

### 3. Widen the star-finder's defocus tolerance

*Treatment for the code defect, worth doing regardless of what focus turns out
to be.*

Three concrete changes, in increasing order of risk:

- Make `max_hfd` mean something, or delete it. A config knob wired to a gate that
  cannot fire is this repo's dominant defect class and is already documented as
  such in `broken-promises-bug-class`.
- Scale the background annulus and the centroid disk with the measured source
  instead of pinning them at 7 and 12. This is precisely the fix
  `imaging/stars.py` already made for `star_size` and its rationale is written
  out at `imaging/stars.py:252-312`. The guide engine is PHD2-parity code and
  this is a deliberate divergence from it, which needs saying out loud in the
  commit.
- Reconsider the conflict prune killing both peaks. Upstream's reasoning is that
  two stars in one search box make an ambiguous lock. A fragmented single blob
  is not two stars, and the current rule cannot tell them apart.

- Cost: real work, on parity-audited code with a golden-vector test suite.
- Buys: a guider that degrades instead of falling off a cliff, and that says
  "your guide scope is 0.3 mm out" instead of "no star found".
- Does not fix: a genuinely out-of-focus scope. Guiding on a 20 px blob is
  possible but the centroid is worse. This buys diagnosis, not performance.
- Known to have worked when: the sweep table in section 1 flattens out.

### 4. Target selection that steers toward a guide star

*Treatment. Only worth building if option 2 shows the scope is genuinely
shallow after focusing.*

What the app has: `catalog/brightstars.py` is cut at **V <= 4.00, 241 rows**,
which is a naked-eye list for "point at something obvious", not a guide-star
catalog. `catalog/data/ngc.tsv` is deep-sky. There is **no catalog in this repo
with stars in the magnitude 8 to 12 range a guide scope needs.** ASTAP's star
database is vendored beside the solver (`solve/astap.py:76-95`) and does reach
that depth, but nothing here reads its binary format and it exists to solve
plates, not to answer queries.

So the honest choices are:

- **Measure, do not predict.** After the slew and before the sequence starts,
  take one guide exposure, run `auto_find`, and if it returns nothing say so and
  offer a small dither of the framing. This needs no catalog, describes the
  actual instrument on the actual night through the actual haze, and is far
  cheaper than the alternative. Strongly preferred.
- **Vendor a catalog.** A Tycho-2 extract cut at V <= 10 is roughly 430,000 rows
  and a few MB; cut at V <= 11.5 it is about 2.5 million rows and tens of MB.
  This buys planning before the slew, which the measure-first approach cannot
  do, and costs a real dependency and a real provenance obligation. The warning
  in `brightstars.py`'s docstring about provenance claims nothing in the repo can
  re-run applies here with force.

- Known to have worked when: a plan whose target has no guide star is refused or
  adjusted before the mount moves, and the refusal is right.

### 5. Multi-star guiding

*Already built. It will not help here, and it is worth saying so before someone
spends a week on it.*

`max_stars` exists, defaults to 1 (`engine.rs:181,204`) and is threaded through
config (`guide/native.py:1149-1151`), so it is one config line away from being
on. But it cannot reach a field a single-star threshold rejects:

- `multi_star_candidates` (`select.rs:643-656`) **raises** the bar. It rejects
  everything below `af_min_snr` = 6.0, which single-star mode never applies to
  the candidate list at all.
- Calibration uses `measured.first()` only (`engine.rs:642`). Secondaries do not
  exist yet.
- In guiding, secondaries are re-measured as offsets from the primary. There is
  no stacked centroid and no summed SNR anywhere in the engine.

Multi-star buys robustness, surviving a cloud or a satellite crossing the
primary. It does not buy depth. Turn it on after focus is fixed, for what it is
actually for.

### 6. Guiding on the main camera

*Treatment. Real, and it costs imaging time.*

The main camera reached that field at 0.4 arcmin. Two shapes:

- **Dither and recentre only.** Plate-solve between subs and correct pointing,
  no closed loop. Cheap, and the app already has every piece. It corrects drift
  but not periodic error, so it works for short subs and an accurate polar
  alignment and not otherwise.
- **A real main-camera guide mode.** Every guide exposure is an exposure not
  spent on the target. At a 2 s cadence this is most of the night.

The honest note: the AM5's pulse-guide is emulated over serial (east is
tracking-suspend, west is R2+Mw, north/south are timed R1 moves) and that is
already the least validated link in the chain. Adding a second unproven layer on
top of it is not where to start.

### 7. Hardware

*Last. Do not spend money before the free experiment has been run.*

If option 2 shows the scope is genuinely shallow at correct focus:

- **Off-axis guider on the main scope.** Puts the guide sensor behind 530 mm of
  focal length (`config.py:70`) and the main aperture. At 4.0 um that is about
  1.56 arcsec/px, and the collecting area is much larger. It also removes
  differential flexure between two tubes, which is a separate problem this rig
  has not yet met. Costs: an OAG, a night of adjusting back-focus, and it has its
  own focus adjustment, so option 1 is needed either way. Note the main scope's
  **aperture is not recorded in config**, only its focal length, so the gain
  cannot be computed from what the repo knows.
- **A larger or faster guide scope.** A 50 mm f/4 roughly triples the collecting
  area. Cheapest hardware answer, does nothing about flexure.
- **A more sensitive guide camera.** Least likely to be the binding constraint.
  At 5.5 arcsec/px the star lands in about one pixel and the sky is 15.8 mag per
  pixel at suburban brightness, so this configuration is not read-noise limited.

None of these is worth ordering while the working hypothesis is that a focus ring
is a quarter of a millimetre out.

## 5. Practical notes for the next attempt

- Exposure is not capped at 6 s. `config.py:457` allows up to 15 s, gain to 1000,
  binning to 4. The UI preset ladder stops at 10 s
  (`ui/src/components/ui/CameraPickers.tsx:33`). Neither was the limit that
  night; the analysis above says no exposure in that range could have worked.
- Binning 2x2 halves the blur disk in pixels, which moves a 12 px wall to 6 px of
  unbinned blur. That is a genuine, free widening of the tolerance and it costs
  image scale the guider does not need at 5.5 arcsec/px. Worth trying on the
  night as a stopgap, but it is a workaround for a defect, not a fix.
- Changing binning is refused while a session is live (`guide/native.py:385-391`)
  because the calibration is in the current binning's pixels. Set it before
  calibrating.

## 6. What is not known

- Whether the guide scope is actually out of focus. Nothing in this document
  establishes it. It is the hypothesis that fits every reported symptom, and it
  is untested.
- The guide scope's limiting magnitude at correct focus. Never measured.
- How the star-finder behaves on a real defocused frame from this instrument. No
  such frame exists in the repo.
- Whether the fields that failed contained a star the finder *should* have found.
  This needs a catalog cross-match the repo cannot currently do.
- Whether anything other than defocus (dew, obstruction, a mis-seated camera)
  contributes. All would be visible in the same afternoon at the scope.
