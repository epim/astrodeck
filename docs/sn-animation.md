# Supernova animation (`tools/sn_animation.py`)

Turns a fortnight of subs on one target into **one image per night**, an
**animation** of those nights in order, and a **relative light curve** of a
transient in the field. Written for SN 2026aaiv in NGC 7331.

It is an offline, daylight tool. It reads the captures directory, writes beside
it, and touches no device, no config and not the running server. Running it
while a sequence is going is safe; it is just disk and CPU.

- reduction library: `server/astrodeck/imaging/nightstack.py` (pure, unit-tested)
- CLI: `server/tools/sn_animation.py`
- tests: `server/tests/test_nightstack.py`

---

## What it does

A **night** runs local noon to local noon — the same rollover as `$$NIGHT$$` and
the night logs — so a frame stamped `2026-09-08_013000` belongs to the night of
2026-09-07.

For each night, in the chosen filter:

1. **Read** the night's subs (filenames only; no header open to bucket them).
2. **Calibrate** — master bias, per-filter master flat, and always a per-frame
   pedestal (the frame's own median), so every sub sits on a zero sky.
3. **Register** each sub to the night's *first* sub, and that first sub to the
   *first night's* first sub. Registration is phase correlation on a binned copy
   plus a full-resolution refinement pass, and **it tries the frame both ways
   round**: a meridian flip mid-night turns the field over by exactly 180
   degrees and nothing in the FITS this rig writes records it (there is no
   `PIERSIDE` card, and `ROTATANG` is the rotator's own angle, which a *mount*
   flip does not change).
4. **Combine** — sigma-clipped mean, clipped on the per-pixel median and MAD.
   The dither turns a fixed hot pixel into a per-pixel outlier, and this drops
   it. Pixels no sub reached come out **NaN** and render black.
5. **Register the night's stack** to the first night's stack for the last pixel
   or two, then **crop**.
6. **Stretch** with the *first night's* parameters — asinh, black just under the
   background, white at the 99.7th percentile. The same numbers on every night
   is what stops the animation flickering and lets a genuinely brighter night
   look brighter.
7. **Annotate** the date and (when known) a circle on the transient, and write
   `<night>.png`.

Then the GIF (one shared palette, so the sky does not shimmer), an MP4 if
`ffmpeg` is on `PATH`, and the light curve.

**Registration threshold.** A sub whose correlation score is below `2.0` is
dropped and counted in that night's log line. The score is the correlation
peak divided by the best rival peak elsewhere on the surface — deliberately not
peak-to-sidelobe, which scales with the number of pixels and so cannot carry
one threshold from a test frame to a 26-megapixel sub. Measured: correct
matches 3.8–21 (clear) and 5.8–20 (thin cloud), an unrelated star field
1.26–1.31, a blank frame 1.04–1.06. `--min-score` overrides it.

---

## Running it on the rig

`server/tools/` is **not** in the release tarball (only `server/astrodeck` is),
so copy the CLI over once:

```powershell
# from this checkout, on the workstation
scp server/tools/sn_animation.py james@astrotown:C:/Users/James/AstroDeck/tools/sn_animation.py
```

If the deployed release predates this feature, `astrodeck/imaging/nightstack.py`
will not be in it either — copy that in too, next to the release's other imaging
modules, or just wait for the next deploy:

```powershell
scp server/astrodeck/imaging/nightstack.py `
    james@astrotown:C:/Users/James/AstroDeck/releases/0.3.26/server/astrodeck/imaging/nightstack.py
```

Then, on the rig (PowerShell), with the real paths:

```powershell
$Root = "C:\Users\James\AstroDeck"
$Rel  = Join-Path $Root ("releases\" + (Get-Content (Join-Path $Root "current")))
$env:PYTHONPATH = Join-Path $Rel "server"

& (Join-Path $Root "venv\Scripts\python.exe") (Join-Path $Root "tools\sn_animation.py") `
    --captures "C:\Users\James\AstroDeck\captures\NGC 7331" `
    --filter L `
    --bias    "C:\Users\James\AstroDeck\captures\Calibration" `
    --flats   "C:\Users\James\AstroDeck\captures\Flats" `
    --sn-ra 339.27341 --sn-dec 34.409825 `
    --crop 1600x1100 --comparison 5 --frame-ms 700
```

(`--sn-ra` / `--sn-dec` are decimal **degrees**. The pair above is SN
2026aaiv's position from the Transient Name Server: 22:37:05.618
+34:24:35.37, a Type Ia at z 0.003, discovered 2026-09-01. For another
transient, substitute its own position.)

Output lands in `C:\Users\James\AstroDeck\captures\_animation\NGC 7331\`
unless `--out` says otherwise:

```
2026-09-05.png  2026-09-06.png  ...   one stack per night, cropped and labelled
animation.gif                          the nights in date order
animation.mp4                          only when ffmpeg is on PATH
lightcurve.csv                         night, n_frames, ratio, err
lightcurve.png                         the same, plotted
reference.fits                         the solved reference sub (WCS stamped in)
```

Expect a few minutes: each sub is read twice (once to register, once to stack)
and a 26-megapixel registration takes about 0.7 s, so a night of 17 subs
registers in roughly 12 seconds.

### Running it here

Identical, against any directory of frames that follow the capture template:

```
cd server
.venv/Scripts/python.exe tools/sn_animation.py --captures "<dir>" --crop 1600x1100
```

Without `--sn-ra`/`--sn-dec` it makes the animation and skips the light curve.

---

## Plate solving and the light curve

RA/Dec becomes a pixel by plate-solving with **ASTAP**, through the same
`astrodeck.solve.AstapSolver` the sequence uses (so `ASTAP_PATH`, the bundled
`vendor/astap` binary and the `ASTAP_DATA` star database all work as usual).

What gets solved is the **reference sub** — the first night's first frame,
copied to `reference.fits` in the output directory — not the stack. Every night
is registered onto that frame's pixel grid, so its WCS *is* the stack's WCS, and
it is an ordinary uint16 light of exactly the kind ASTAP already solves on this
rig every night. Nothing has to be re-encoded and no temporary stack FITS is
written.

If ASTAP is missing or the solve fails, the tool says so, writes the animation
anyway, and skips the light curve.

**Comparison stars** are chosen once, on the reference night: the brightest
unsaturated, isolated point sources in the crop, at least 40 px from the
transient and measurably no broader than the frame's own stars (which is what
keeps the *galaxy core* out of the comparison set). Because every night is
registered onto the same grid, the same pixel positions serve every night.

---

## Options

| flag | default | what it does |
| --- | --- | --- |
| `--captures` | required | the target's capture directory (holds the `Light_*.fits`) |
| `--filter` | `L` | one filter. Colour is not supported |
| `--out` | `<captures>/../_animation/<target>` | output directory |
| `--bias` | none | directory of bias frames -> master bias |
| `--flats` | none | directory of flats -> per-filter master flat (grouped on the `FILTER` header) |
| `--sn-ra`, `--sn-dec` | none | transient position in degrees; enables the light curve and centres the crop |
| `--comparison` | 5 | how many comparison stars to pick |
| `--crop` | `1600x1100` | crop size, WIDTHxHEIGHT, around the transient or the frame centre |
| `--frame-ms` | 700 | milliseconds per animation frame |
| `--aperture`, `--annulus` | 8, 14 24 | photometry radii in pixels |
| `--min-score` | 2.0 | drop a sub below this registration score |
| `--bin` | 4 | binning for the coarse correlation |
| `--min-frames` | 1 | skip a night with fewer usable subs |

Each night logs one line: subs used, subs dropped for a poor score, the score
range, how many were flipped, the night-to-night shift and the residual. A night
whose crop extends past that night's coverage gets a `WARNING` naming the
percentage — that is what a black band down one edge means, and the fix is a
smaller `--crop`.

---

## What it does NOT do

- **No dark subtraction and no dark scaling.** The rig has no darks matching the
  60 s gain-125 lights, and the 180 s narrowband darks do not scale honestly to
  them. Hot pixels are handled where they are cheap to handle: the dither moves
  them around the sky, and the sigma clip drops them.
- **No colour.** One filter per run.
- **No magnitudes.** The light curve is a **RATIO** — the transient's
  background-subtracted aperture flux divided by the summed flux of the
  comparison stars in the same crop. There is no photometric zero point, no
  colour term and no airmass correction, so the *shape* of the curve is
  meaningful and the absolute level is not. What the ratio buys is that
  transparency, sky brightness and the number of surviving subs all divide out:
  cloud that halves the transient halves the comparisons by the same factor.
- **The quoted error is a floor.** It is the sky-annulus scatter propagated
  through the aperture, with no source shot-noise term — the pipeline never
  learns the gain, so a photon-counting term would be fabricated. On a transient
  sitting on a galaxy the number is dominated by the galaxy's gradient across
  the annulus, which is honest but pessimistic.
- **No sub-pixel registration.** Shifts are whole pixels. At 0.968 arcsec/px
  that is invisible in an animation and irrelevant to aperture photometry.
- **It is not a processing suite.** These are previews and measurements, not the
  file you would take to PixInsight. The real subs are on disk, untouched.

---

## Notes for the next person

- `night_of()` matches `events.night_key()` exactly. If one changes, change both.
- `combine()` reads its frames through an indexable sequence
  (`nightstack.FitsFrames`), so a night of 26-megapixel subs costs one frame in
  memory rather than all of them. Passing `bounds` is what keeps it that way.
- `compose()` is where the flip algebra lives: a 180-degree rotation's linear
  part is `-I`, so an outer flip negates the inner translation and the flips
  compose by XOR. `test_compose_equals_placing_twice` pins it.
- The tests build their synthetic sky the shape of the real problem — galaxy,
  30 stars, a brightening point source, per-frame dithers, half of night two
  rotated 180 degrees, a 60 px cross-night offset, and a transparency swing the
  ratio has to divide away.
