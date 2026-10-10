# Committed fixture cases

Three case directories that are IN the repository, so the replay-backed tests
run on a clean checkout and in CI: two for the legacy scanner and one for the
panorama scanner (the last section). The full-length recordings under `cache/`
are git-ignored (`.gitignore`, `tools/photosphere_sim/cache/`) and are about
42 MB each, which is why they are not; issue #68 is that every test standing
on them therefore ran on one machine and nowhere else.

| case | scene | route | frames | camera | `fov_short_deg` | bytes |
|---|---|---|---|---|---|---|
| `chartyard-shortpan-60` | chartyard | shortpan | 95 | 240 x 320, 5 fps | 60 | 1 575 845 |
| `chartyard-shortpan-70` | chartyard | shortpan | 95 | 240 x 320, 5 fps | 70 | 1 669 521 |
| `treeline-panshort-41` | treeline | panshort-p23 | 103 | 90 x 160, 12 fps | 41.14 | 1 287 894 |

3 245 366 bytes for the first pair, of which 2.69 MB is the 190 frame PNGs,
0.41 MB the two `truth/` directories and 0.14 MB the observation streams. The
third is described under its own heading below.

## How they were sized

Four choices, each one deliberate:

- **A short route.** `routes/shortpan.json` is six aims: three along the
  horizon at azimuth 0, 24 and 48, then three at altitude 35 over sky the
  first three have already painted, which is where the overlap test has
  something to disagree with. 18.9 seconds against the recorded routes' 105.
- **A small frame.** 240 x 320 is exactly the size of the scanner's own
  analysis canvas for a 3:4 frame (`canvas.height = 320`), so the replay's
  resampler neither shrinks nor magnifies here and the frames are a quarter
  of the recordings' 480 x 640. The resampler's own behaviour is graded by
  the two `resample` cases in `photosphereReplay.test.ts`.
- **Few frames.** 5 fps rather than 10. The scanner grabs at most every
  350 ms, so halving the frame rate left every capture outcome in the replay
  byte for byte what it was at 10 fps, for half the bytes. That was measured,
  not assumed.
- **Long holds.** `hold_s` is 2.4 s rather than the recorded routes' 1.2, so
  each hold affords three grab attempts instead of one. That is what carries
  the wrong-lens case past `LENS_DOUBT_AFTER` (9 consecutive refusals against
  a threshold of 6) in a route a tenth of the recorded one's length.

## What they are for

The pair differs in exactly one field, the camera's short-axis field of view:
70 degrees against the 60 the scanner assumes. That is issue #52's experiment.
On this route the wrong lens earns a run of 9 consecutive refusals to match
and the cue names the camera view angle; the right lens earns none and the cue
never mentions the lens. "Refusals to match" is two outcomes since issue #95,
`overlap-wait` and `carry-too-large`, and the run is counted over both
(`extendsOverlapRun`) exactly as the scanner counts it. `photosphereReplay.test.ts` grades both,
and those cases do not skip.

The three cases that replay the full-length recordings keep their loud skip:
they carry the scoring the baseline document quotes
(`docs/ui-rebuild/18-photosphere-simulator-baseline.md`) and one case these
fixtures cannot reach, a refusal run being broken by an outcome that ends it.

## Rebuilding them

```
cd tools/photosphere_sim
python -m sim make-case chartyard-shortpan-60 --out fixtures
python -m sim make-case chartyard-shortpan-70 --out fixtures
```

That is the same renderer the recordings were made with, and it reproduces
these directories byte for byte on the same machine (checked: the manifest
hashes and the total byte count came back identical after a rebuild). A
different Chromium or Three.js version may not, which is why the frames are
committed rather than generated in CI -- and why `manifest.json` records
both versions.

`truth/` is here although nothing in the test suite reads it: it makes each
of these a complete case directory in `CONTRACT.md`'s sense, so
`python -m sim score chartyard-shortpan-60 --cases fixtures` (from
`tools/photosphere_sim`, and against a `result/` a replay has written
somewhere it owns) can be run without re-rendering anything. It costs 203 KB
a side.

The bytes are pinned through git by `.gitattributes`
(`tools/photosphere_sim/fixtures/** -text`). Without it this repository's
`core.autocrlf = true` would check the JSON and JSONL out with CRLF on
Windows and every hash in `manifest.json` -- and `input_hash` in any
`scores.json`, which the scorer copies from the manifest rather than
recomputing -- would name bytes that are not on disk. Measured: a commit and
fresh checkout with `autocrlf=true` and no rule returns 9 of the 21 text
files as CRLF and breaks `hashes.observations` and `hashes.truth`; with the
rule, a fresh clone reproduces all three hashes for both cases.

## What this pair does NOT grade

Worth being plain about, because "the simulator runs in CI now" would
overstate it:

- **Nothing scores these cases.** `shortpan` appears in no `sim score`
  invocation and no Python test. Every accuracy quantity the full-length
  recordings carry -- landmark p95 and p99, omissions and duplicates, horizon
  p95, `false_open_sr`, the obstacle verdicts, overlay alignment, coverage
  fraction, capture latency -- is ungraded here. A regression that degrades
  registration accuracy without stopping the chain passes all four cases.
- **The route is azimuth 0 to 60 at two altitudes.** No 360 degree sweep, no
  zenith, no north wrap. The test's `horizon.points.length === bins` is a
  shape check and not a horizon grade.
- **Every scanner-decided quantity in the tests is a threshold, not a count**
  (deliberately: a later targeting change must not redden a case about
  whether the chain runs). That is the same choice that keeps these cases
  from catching accuracy drift.
- **The refusal-run reset case stays on the full-length recording**: the
  wrong-lens run here is never broken by an outcome that ends one.

What the four cases do grade: the chain runs end to end on real rendered
frames, the #52 cue fires on the wrong lens and does not fire on the right
one, and a test run writes nothing into the repository.

## Do not write into them

`replayCase` empties and rewrites `<case>/result/`. Every test here copies
`input/` to a temporary directory and replays there, and one case asserts
that no `result/` appeared inside these directories afterwards. Pointing a
replay at a case directory it does not own is how a recursive delete reaches
the recording it was measuring.

## `treeline-panshort-41`: the panorama scanner's fixture

The horizon panorama scanner's committed case (SPEC-v2 7.5, T26), replayed with
`--scanner pano` by
`ui/src/next/hubs/sky/sheets/pano/__tests__/panoFixtureReplay.test.ts` on a
clean checkout. It is the treeline scene (`scenes/treeline.json`) panned
through 120 degrees at 20 deg/s on `routes/panshort-p23.json`: a still pivot, a
1 s hold at each end, 0.3 degrees rms of tremor. The case definition is
`cases/treeline-panshort-41.json`. It carries the 13.2 sensor realism (a
relative and an absolute stream, a motion stream, delivery capture time with a
50 ms lag) with a synthetic declination of 7.25 degrees in both the absolute
stream and `input/scanner.json`, whose focal prior is 10 % wrong
(`focal_prior_scale` 1.1). It has no grading flags: it is replayed and checked
for shape, and is not scored, though `truth/` is complete (including
`visibility.json` and `route.json`) and `python -m sim score` can score a
replay of it.

| | |
|---|---|
| frames | 103 PNGs, 998 517 bytes (9.5 KB each) |
| camera | 90 x 160, 12 fps, `fov_short_deg` 41.14 |
| `input/` other than frames | 183 435 bytes (1 405 observations: 791 orientation, 511 motion, 103 frame) |
| `truth/` | 104 445 bytes |
| whole directory | 116 files, 1 287 894 bytes (the budget is 1.5 MB) |

### How it was sized

SPEC-v2 asks for "120 degrees, about 100 frames at 180 x 320" in about 1.2 MB
and no more than 1.5 MB. The three cannot all hold, and the cap is the one that
was kept. A PNG of a treeline frame is 27.5 KB at 180 x 320 whatever the frame
rate, so 100 frames at that size is 2.8 MB. Each row is a build of this route
and a replay with the pano scanner, measured:

| camera | fps | frames | per frame | directory | ring band painted |
|---|---|---|---|---|---|
| 180 x 320 | 30 | 256 | 27.5 KB | 7.56 MB | 0.989 |
| 180 x 320 | 15 | 128 | 27.5 KB | 3.90 MB | 0.988 |
| 90 x 160 | 15 | 128 | 9.5 KB | 1.54 MB | 0.988 |
| 90 x 160 | 12 | 103 | 9.5 KB | 1.29 MB | 0.987 |
| 90 x 160 and frame noise 2.0 | 12 | 103 | 17.6 KB | 2.14 MB | 0.987 |

Three choices, each forced by the arithmetic above:

- **Half the resolution.** 90 x 160 is the size of the scanner's own live
  source (SPEC-v2 4.2). The replay harness scales a frame to the 180 x 320
  analysis canvas, as it does for the 45 x 80 frames of the synthetic scans in
  `scannerV0.test.ts`, so the scanner sees the same canvas with softer detail.
- **12 fps.** The 15 fps row is 3 % over the cap, and at 12 fps the pan is still
  well sampled (1.7 degrees between frames against a 6 degree wide slit).
  The scanner's speed limit is 12 deg/s at 12 fps (4.3), under the 20 deg/s of
  the pan, so the cue reads "Too fast" on 72 of the 103 frames and the keyframes
  are classed `blurred` (4.3 classes a fast keyframe, it does not refuse it).
  The arc is painted all the same, and the 12 fps row is the measurement. A
  later tracker that refuses a keyframe above the limit would thin this arc, and
  the fixture test would say so, and the fix then belongs to the fixture (a
  slower route, or a higher frame rate within the size cap), not to the 0.9 bar.
- **No frame noise.** `frames.noise_sigma` is 0, where the graded cases use
  2.0. Noise is what a PNG cannot compress: it adds 8 KB to every frame. The
  fixture therefore does not exercise noise robustness. The 17 graded cases do,
  and they need the cache.

The other 13.5 effects stay at their defaults: 4 ms exposure in 4 sub-frames,
auto-exposure, a 25 ms rolling shutter and the stabiliser.

### What the test asserts

Every result file of 13.7 exists and parses to its shape, and `columns.json`
(legacy only) does not exist. The ring band is painted: the band is raster
altitudes 0 to 45, the arc is what is left of the ring once the largest
unpainted run is removed, and the fraction is the share of band pixels in the
arc with alpha 255. Measured 0.987 over an arc of 126.0 degrees (120 degrees of
pan and a 3 degree half-slit at each end). The test file defines the measure
and checks that it reads a hole in the arc as a hole. Nothing here grades
accuracy: a registration regression that leaves the arc painted passes. The
graded cases do that, and so does the control of SPEC-v2 7.7.

### Rebuilding it

```
cd tools/photosphere_sim
python -m sim make-case treeline-panshort-41 --out fixtures
```

Two builds on one machine gave 116 files, all byte identical (compared by
SHA-256, `manifest.json` included), so the hashes in `manifest.json` are
stable. As with the first pair, a different Chromium or Three.js may not
reproduce it, which is why the frames are committed. The bytes are pinned
through git by the same `.gitattributes` rule, which covers everything under
`fixtures/`. `tests/test_manifest_hashes.py` reads every directory here, so a
changed byte in any frame of this one turns it red.
