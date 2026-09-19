# Photosphere simulator: the stage A baseline

Date: 2026-09-18
Status: measured baseline. This is the exit evidence for stage A of
[the simulator specification](16-photosphere-calibration-simulator.md)
(section 11.A). It records what the shipped photosphere scanner does on three
recorded cases; it does not propose or make any change to the scanner.
Related: [production handoff](14-photosphere-production-handoff.md),
[stillness review](15-photosphere-stillness-review.md),
[implementation review](17-photosphere-implementation-review.md),
`tools/photosphere_sim/CONTRACT.md`.

Every number here was re-measured from a clean cache at commit `9e3097fb`,
after the implementation review's P1 and P2 findings and issues #54 and #56
were fixed. Two earlier readings of this instrument are superseded and none of
their hashes or numbers carry over: the reading at `1a98a8d8`, taken before a
move became a swing-twist of the full attitude, and the reading at `52df7eb3`,
whose two arc cases carried the 1.5 m camera teleport at the zenith crossing
that issue #56 describes.

## 1. Purpose

Stage A of doc 16 asks for a measuring instrument, not a better scanner: an
independently rendered world, a recording of only what a phone could have
supplied, a replay of that recording through the real `PhotosphereSweep`, and a
score against truth the scanner never saw. That instrument now exists, and this
document is the honest reading from it. It measures the finished panorama's
landmark directions, the horizon boundary and each declared test obstacle, the
live overlay pose against the truth pose frame by frame, capture latency after
a hold, and coverage of an observable region defined by the scene and the
delivered frames rather than by the subset the scanner accepted. It does not
measure anything about a real device: the chart yard is noise free and
undistorted, the frames arrive on a virtual clock with no dropped or late
delivery, the lens is a pinhole, and no browser, camera driver or permission
prompt is involved. A green line here means the mathematics is right on a
recording, never that a phone will behave.

## 2. Reproduction

### 2.1 The cases

| field | chartyard-still-60 | chartyard-arc075-60 | chartyard-arc075-70 |
|---|---|---|---|
| scene | chartyard | chartyard | chartyard |
| route | still | arc075 | arc075 |
| frame width x height | 480 x 640 | 480 x 640 | 480 x 640 |
| `fov_short_deg` | 60 | 60 | 70 |
| fps | 10 | 10 | 10 |
| seed | 1 | 1 | 1 |
| expected | positive | positive | positive |
| profile | chart-noise-free | chart-noise-free | chart-noise-free |
| frames delivered | 1053 | 1053 | 1053 |
| orientation events delivered | 4414 | 4414 | 4414 |
| last frame capture time (ms) | 105200 | 105200 | 105200 |
| `finish` action (ms) | 106260 | 106260 | 106260 |
| `summary.json` `elapsed_ms` | 105260 | 105260 | 105260 |
| holds | 48 | 48 | 48 |

Both routes are built from the same 46 aims and one upward sweep. A move is one
rotation of the whole attitude, decomposed into a swing that carries the forward
direction along its great circle and a twist about that forward direction, both
on a smoothstep and both inside the same rate budget:

| route timing, both routes | value |
|---|---|
| peak attitude rate over the whole route (deg/s) | 45.0 |
| longest single move: the zenith-to-sweep transition (ms) | 6716 |
| hold length (ms) | 1200 |

That transition is long because the zenith aim's convention puts a real half
turn of roll into it: the phone's top points south when the phone is held
overhead at azimuth 0 and up again when the view returns to the horizon. Timing
it by the forward direction alone understated it, which is the implementation
review's P2 finding.

The two routes differ only in whether the camera translates. `still` holds the
camera at `c_ref` for the whole timeline; `arc075` carries it around a body
pivot at arm's length and lifts it as the view tilts up. The carried azimuth is
interpolated continuously along the shorter arc, separately from the
swing-twist orientation, so the camera centre sweeps the half circle at the
zenith crossing instead of jumping across the body (issue #56):

| route | max displacement from `c_ref` (m) | median displacement (m) | max frame-to-frame step (m) |
|---|---|---|---|
| still | 0.000 | 0.000 | 0.000 |
| arc075 | 1.518 | 1.115 | 0.059 |

### 2.2 Input hashes and build

| case | `hashes.frames` | `hashes.observations` | `hashes.truth` |
|---|---|---|---|
| chartyard-still-60 | `a140d0f6f5d971658bbd489eee5b895c88612dd2fd8d71debbd51fb9f46af878` | `56c7a1298b354bbb10dc561984a874d6bc1a25aebfbcd90b7ec0056eec6883d7` | `32197463726a07a5e1918eedbbe4ef6187712f62b16abb4b033740ec219bfda7` |
| chartyard-arc075-60 | `166a04222cfae03a5337f3c55581c026d46518160d06a89007570c4d470fa15a` | `56c7a1298b354bbb10dc561984a874d6bc1a25aebfbcd90b7ec0056eec6883d7` | `ef4e73395cc2e65031576232c9b40abfcfaea6201c3fd03f197a54b51336c524` |
| chartyard-arc075-70 | `83103ac1d33932d32fd084a6cd8783a70111d1a03a4dd85328ebf07f538afc31` | `56c7a1298b354bbb10dc561984a874d6bc1a25aebfbcd90b7ec0056eec6883d7` | `a3841065e2b88b67f13c4f1d2f0cece3d6ad94157ea13ed79e50e4109cd75481` |

The three cases share an `observations` hash because the route, the clock and
the frame identities are the same in all three; only the pixels and the camera
intrinsics differ.

| case | `scores.json` `input_hash` |
|---|---|
| chartyard-still-60 | `39af21e582ba52a3d9fc0bdd5b95cd92933c37af53ef501e1c97268b59ea0be9` |
| chartyard-arc075-60 | `74e8a4610b713aeae99342bdb2117b3f0fa4e8f569c433b18d41c0af6719185e` |
| chartyard-arc075-70 | `e7185b24f4454dde46d9305d793c539f4f800dcf61f0cdbc7d7723268f239d4d` |

The tested build is `app_commit` `9e3097fb2e1c7308eb36e7bdfe1ddcb3eb837683` on
branch `feat/photosphere-production`, read from `result/summary.json`, which the
replay driver stamps from the working tree. The manifest's own
`versions.app_commit` is `null`: a case is built by the simulator and does not
belong to an application build.

**Determinism.** After the three cases were built, replayed and scored,
`chartyard-still-60` was built, replayed and scored a second time into a
separate directory, from the same source tree and with no shared state:

```text
# inside tools/photosphere_sim
python -m sim make-case chartyard-still-60 --out cache/scratch-determinism
# inside ui
node --import tsx src/next/hubs/sky/sheets/__sim__/replay.ts \
    C:/Users/bear/astro/tools/photosphere_sim/cache/scratch-determinism/chartyard-still-60
# inside tools/photosphere_sim
python -m sim score chartyard-still-60 --cases cache/scratch-determinism
```

| compared between the two independent builds | result |
|---|---|
| `manifest.json` `hashes.frames` | identical |
| `manifest.json` `hashes.observations` | identical |
| `manifest.json` `hashes.truth` | identical |
| `result/scores.json` | byte identical (12931 bytes) |
| `result/panorama.png` | byte identical |
| `result/horizon.json` | byte identical |
| `result/events.jsonl` | byte identical |
| `result/captures.jsonl` | byte identical |
| `result/summary.json` | byte identical |

The only difference anywhere in the two runs is the path `sim score` prints for
the report it wrote. Renderer, recorder, truth, jsdom replay and scorer are
therefore reproducible end to end on this machine.

### 2.3 The commands

From a clean cache, in this order. `<id>` is each of the three case ids in the
order below.

```text
rm -rf tools/photosphere_sim/cache/cases/chartyard-*

# inside tools/photosphere_sim
python -m sim make-case <id>

# inside ui
node --import tsx src/next/hubs/sky/sheets/__sim__/replay.ts \
    C:/Users/bear/astro/tools/photosphere_sim/cache/cases/<id>

# inside tools/photosphere_sim
python -m sim score <id>
```

The instrument's own floor is built and scored separately:

```text
# inside tools/photosphere_sim
python -m sim ideal chartyard-still-60
python -m sim score chartyard-still-60 --result cache/cases/chartyard-still-60/ideal
```

| step | chartyard-still-60 | chartyard-arc075-60 | chartyard-arc075-70 |
|---|---|---|---|
| `make-case` wall time (s) | 47.8 | 46.6 | 46.9 |
| `make-case` exit code | 0 | 0 | 0 |
| `replay.ts` wall time (s) | 5.7 | 6.7 | 6.5 |
| `replay.ts` exit code | 0 | 0 | 0 |
| `score` wall time (s) | 3.8 | 3.8 | 4.0 |
| `score` exit code | 1 | 1 | 1 |

| step | wall time (s) | exit code |
|---|---|---|
| `ideal chartyard-still-60` | 2.4 | 0 |
| `score --result .../ideal` | 4.2 | 0 |
| determinism `make-case --out` | 47.1 | 0 |
| determinism `replay.ts` | 5.7 | 0 |
| determinism `score --cases` | 3.8 | 1 |

The eleven commands of the baseline take about 3 minutes; the determinism pass
adds about a minute. `score` exits 1 when the gate verdict is FAIL and 0 when
it is PASS, so the exit codes above are the verdicts in section 3.1 and not
errors. Exit 2 is reserved for "I could not run".

| case | case directory | `input/` | `truth/` | `result/` | `ideal/` |
|---|---|---|---|---|---|
| chartyard-still-60 | 51 MiB | 48 MiB | 510 KiB | 937 KiB | 873 KiB |
| chartyard-arc075-60 | 42 MiB | - | - | - | not built |
| chartyard-arc075-70 | 43 MiB | - | - | - | not built |

### 2.4 Versions

| component | version |
|---|---|
| three | 0.186.0 |
| Chromium (Playwright, headless, SwiftShader) | 149.0.7827.55 |
| Playwright | 1.61.0 |
| Python | 3.12.10 |
| numpy | 2.5.0 |
| Pillow | 12.3.0 |
| Node | 24.14.0 |
| tsx | 4.23.1 |
| jsdom | 29.1.1 |

## 3. Results

Every number in this section is a field of the case's `result/scores.json`,
except the capture outcome histogram, which is a count over
`result/captures.jsonl`.

### 3.1 Gates

| gate | still-60 | arc075-60 | arc075-70 | ideal (still-60) |
|---|---|---|---|---|
| `landmarks_p95_lt_0_5` | PASS | PASS | FAIL | PASS |
| `landmarks_p99_lt_1` | PASS | FAIL | FAIL | PASS |
| `no_omissions` | PASS | FAIL | FAIL | PASS |
| `no_duplicates` | PASS | PASS | FAIL | PASS |
| `horizon_p95_lt_1` | FAIL | FAIL | FAIL | PASS |
| `no_missed_obstructions` | FAIL | FAIL | FAIL | PASS |
| `no_unresolved_boundary` | PASS | PASS | FAIL | PASS |
| `overlay_settled_p95_lt_0_5` | PASS | PASS | FAIL | PASS |
| `overlay_moving_p95_lt_1` | PASS | PASS | FAIL | PASS |
| `overlay_max_lt_10` | PASS | PASS | PASS | PASS |
| `no_duplicate_frames` | PASS | PASS | PASS | PASS |
| `capture_p95_le_1500` | PASS | PASS | PASS | PASS |
| `every_hold_captured` | FAIL | FAIL | FAIL | PASS |
| `coverage_ge_0_95` | PASS | PASS | FAIL | PASS |
| `pass` | FAIL | FAIL | FAIL | PASS |

| case | gates failing, of the fourteen that `pass` is the AND of |
|---|---|
| chartyard-still-60 | 3 |
| chartyard-arc075-60 | 5 |
| chartyard-arc075-70 | 11 |
| ideal (still-60) | 0 |

The ideal column is the same case's `ideal/` directory: the result a perfect
scanner would have written, built from the case's own truth by ray casting. It
is in the table so that every other column can be read against the instrument's
own floor rather than against zero.

### 3.2 Landmarks

| | still-60 | arc075-60 | arc075-70 | ideal |
|---|---|---|---|---|
| expected (observable) | 40 | 40 | 40 | 40 |
| found | 40 | 34 | 28 | 40 |
| omitted | 0 | 6 | 10 | 0 |
| duplicated | 0 | 0 | 2 | 0 |
| slivers | 0 | 0 | 2 | 2 |
| spurious | 0 | 3 | 8 | 0 |
| error median (deg) | 0.059 | 0.051 | 4.099 | 0.030 |
| error p95 (deg) | 0.150 | 0.148 | 7.700 | 0.084 |
| error p99 (deg) | 0.225 | 2.508 | 7.929 | 0.209 |
| error max (deg) | 0.264 | 3.663 | 7.986 | 0.266 |

`per_landmark` carries all 52 entries of `landmarks.json`; the twelve that are
not observable from `c_ref` are outside `expected` and outside the percentiles.

Omitted and duplicated landmarks, with their direction from `c_ref`:

| id | az (deg) | alt (deg) | host | status |
|---|---|---|---|---|
| W1 | 95.7 | 9.0 | wall-east | omitted in arc075-60 and arc075-70 |
| W2 | 59.9 | -9.8 | wall-east | omitted in arc075-60 and arc075-70 |
| RF1 | 161.1 | 39.0 | roof-south | omitted in arc075-60 and arc075-70 |
| RF2 | 195.0 | 23.4 | roof-south | omitted in arc075-60 and arc075-70 |
| R3K1 | 103.0 | 55.0 | background | omitted in arc075-60 and arc075-70 |
| R4K1 | 120.0 | 75.0 | background | omitted in arc075-60 |
| T1 | 39.6 | 3.7 | trunk | omitted in arc075-70 |
| R1K4 | 219.0 | 15.0 | background | omitted in arc075-70 |
| R1K5 | 282.0 | 15.0 | background | omitted in arc075-70 |
| R2K4 | 236.0 | 35.0 | background | omitted in arc075-70 |
| R3K4 | 253.0 | 55.0 | background | omitted in arc075-70 |
| R2K1 | 86.0 | 35.0 | background | duplicated in arc075-70 |
| R2K6 | 331.0 | 35.0 | background | duplicated in arc075-70 |

The two duplicates are chartyard-arc075-70's alone, and they are the reason it
fails `no_duplicates` where the other two cases pass: a wrong lens scale puts
two copies of the same disc in the panorama far enough apart that both clear
the blob filter and both fall inside the match radius.

### 3.3 Horizon

| | still-60 | arc075-60 | arc075-70 | ideal |
|---|---|---|---|---|
| truth bins | 3600 | 3600 | 3600 | 3600 |
| measured bins | 30 | 30 | 30 | 3600 |
| measured resolution (deg) | 12.0 | 12.0 | 12.0 | 0.1 |
| signed error median (deg) | +0.95 | +7.47 | +35.35 | 0.00 |
| absolute error p95 (deg) | 75.90 | 75.90 | 72.95 | 0.00 |
| absolute error max (deg) | 76.75 | 76.75 | 73.05 | 0.00 |
| false open (sr) | 0.1419 | 0.1544 | 0.0058 | 0.0000 |
| false blocked (sr) | 0.8208 | 1.5277 | 0.4391 | 0.0000 |
| unresolved (sr) | 0.0000 | 0.0000 | 5.2360 | 0.0000 |
| unresolved bins | 0 | 0 | 25 | 0 |
| north offset (deg) | -1.3 | -7.0 | -10.0 | 0.0 |

`north_offset_deg` is searched over the range -10 to +10 in steps of 0.1, so
the value for chartyard-arc075-70 is the edge of the search and a lower bound
on the true shift, not a measurement of it.

Obstacles, each scored against its own first-hit silhouette and never against
the envelope:

| obstacle | truth peak (deg) | min width (deg) | | still-60 | arc075-60 | arc075-70 | ideal |
|---|---|---|---|---|---|---|---|
| pole-near | 63.20 | 2.0 | deficit median (deg) | 56.15 | 56.15 | null | 0.00 |
| | | | deficit p95 (deg) | 56.20 | 56.20 | null | 0.00 |
| | | | width missed (deg) | 2.2 | 2.2 | null | 0.0 |
| | | | verdict | MISSED | MISSED | MISSED | found |
| pole-far | 12.45 | 0.25 | deficit median (deg) | -74.55 | -74.55 | -33.55 | 0.00 |
| | | | deficit p95 (deg) | -74.55 | -74.55 | -33.55 | 0.00 |
| | | | width missed (deg) | 0.0 | 0.0 | 0.0 | 0.0 |
| | | | verdict | found | found | found | found |
| roof-south | 75.15 | 10 | deficit median (deg) | -0.85 | -5.00 | null | 0.00 |
| | | | deficit p95 (deg) | 2.35 | 2.95 | null | 0.00 |
| | | | width missed (deg) | 16.6 | 40.8 | null | 0.0 |
| | | | verdict | MISSED | MISSED | MISSED | found |
| wall-east | 25.60 | 10 | deficit median (deg) | -34.35 | -55.40 | null | 0.00 |
| | | | deficit p95 (deg) | 25.25 | 21.60 | null | 0.00 |
| | | | width missed (deg) | 12.0 | 12.0 | null | 0.0 |
| | | | verdict | MISSED | MISSED | MISSED | found |
| trunk | 21.50 | 1.0 | deficit median (deg) | -24.55 | -23.55 | null | -24.35 |
| | | | deficit p95 (deg) | -24.50 | -23.50 | null | -24.30 |
| | | | width missed (deg) | 0.0 | 0.0 | null | 0.0 |
| | | | verdict | found | found | MISSED | found |

A `null` deficit means the obstacle is visible and has no resolved measured bin
at all, which `missed` reads as missed; four of chartyard-arc075-70's five
obstacles are missed that way, because its boundary is unresolved over most of
the azimuths they occupy. The ideal's negative deficit on the trunk is the
instrument, not a fault: the ideal writes the envelope, and over the trunk's
azimuths the envelope is the canopy standing above it.

### 3.4 Overlay

| | still-60 | arc075-60 | arc075-70 | ideal |
|---|---|---|---|---|
| samples | 1053 | 1053 | 1053 | 1053 |
| missing fraction | 0.000 | 0.000 | 0.000 | 0.000 |
| duplicate frame ids | 0 | 0 | 0 | 0 |
| frames over their class gate | 0 | 0 | 994 | 0 |
| moving median (deg) | 0.000 | 0.000 | 5.382 | 0.000 |
| moving p95 (deg) | 0.000 | 0.000 | 7.842 | 0.000 |
| moving max (deg) | 0.095 | 0.095 | 8.369 | 0.000 |
| settled median (deg) | 0.029 | 0.029 | 3.840 | 0.000 |
| settled p95 (deg) | 0.075 | 0.075 | 7.328 | 0.000 |
| settled max (deg) | 0.174 | 0.174 | 8.372 | 0.000 |

The two 60 degree cases report the same overlay figures because the overlay
pose is built from the orientation stream, which is identical across the three
recordings; only chartyard-arc075-70's wrong lens moves it. Their moving
medians and p95s are not zero but smaller than a thousandth of a degree: at
those instants the last orientation reading the scanner received was emitted at
the frame's own capture time, so its reconstructed pose equals the truth pose
to floating-point precision. The figure is therefore a property of where the
sensor's emission instants fall relative to the 100 ms frame grid, and it has
moved between route revisions with no change to the scanner. Read the maxima,
not the medians, on those two rows.

### 3.5 Capture

A hold is satisfied by the first unclaimed record in its window whose outcome
is `accepted` or `already-captured`. The split is reported so that a hold the
scanner declined because it already held that dome cell is visible as what it
is (issue #54).

| | still-60 | arc075-60 | arc075-70 | ideal |
|---|---|---|---|---|
| holds | 48 | 48 | 48 | 48 |
| holds satisfied | 47 | 41 | 11 | 48 |
| of which captured | 21 | 20 | 9 | 48 |
| of which already covered | 26 | 21 | 2 | 0 |
| latency p95 (ms) | 859.0 | 859.0 | 859.5 | 700.0 |
| latency max (ms) | 860 | 860 | 860 | 700 |
| accepted frames | 21 | 20 | 9 | 48 |

Latency is measured to the satisfying record whatever its outcome, and every
satisfied hold is answered well inside its own window:

| | still-60 | arc075-60 | arc075-70 |
|---|---|---|---|
| hold length (ms) | 1200 | 1200 | 1200 |
| lowest satisfying latency (ms) | 710 | 710 | 808 |
| satisfying records below 840 ms | 32 | 28 | 2 |
| satisfying records at 840 ms or above | 15 | 13 | 9 |

Outcome histogram over `result/captures.jsonl`:

| outcome | still-60 | arc075-60 | arc075-70 | ideal |
|---|---|---|---|---|
| alignment-wait | 183 | 191 | 187 | 0 |
| too-soon | 32 | 30 | 23 | 0 |
| already-captured | 26 | 21 | 2 | 0 |
| accepted | 21 | 20 | 9 | 48 |
| no-target | 2 | 0 | 18 | 0 |
| overlap-wait | 0 | 2 | 25 | 0 |
| stale-image | 0 | 0 | 0 | 0 |
| frame-already-captured | 0 | 0 | 0 | 0 |
| total records | 264 | 264 | 264 | 48 |
| records with `adjusted` true | 0 | 0 | 1 | 0 |

`stale-image` and `frame-already-captured` are the two refusals the
implementation review's P1 finding added: a capture now needs a delivered image
on every path, and one delivered frame is captured once. Neither fires on any
of these cases, which is the expected result. The replay drives the frame
callback, where each callback is its own delivery, so the new requirement is
satisfied by construction; the outcomes are listed with their zeros so that a
future run which does trip them is visible as a change rather than as a new
row.

### 3.6 Coverage

| | still-60 | arc075-60 | arc075-70 | ideal |
|---|---|---|---|---|
| panorama alpha fraction | 0.9941 | 0.9882 | 0.7698 | 1.0000 |
| observable fraction covered | 0.9941 | 0.9882 | 0.7698 | 1.0000 |
| cells covered fraction | 0.9890 | 0.9670 | 0.5714 | 1.0000 |
| cells covered / cells total | 90 / 91 | 88 / 91 | 52 / 91 | 1 / 1 |

The ideal's `cells_covered_fraction` is not comparable with the other three:
its `summary.json` declares a single cell, because the ideal panorama is ray
cast rather than assembled from dome cells.

## 4. What the numbers say

Six mechanisms account for every failing gate. Each is stated only as far as
the evidence in the case directories supports it.

### 4.1 The boundary is thirty bins wide, the truth is thirty-six hundred

`PhotosphereSweep` is constructed with thirty azimuth bins and writes one
altitude per bin; the truth is sampled every tenth of a degree. A single number
per bin cannot describe a boundary that changes inside the bin, and the chart
yard was built with obstacles that do exactly that. The comparison below is
computed from `truth/reference-horizon.json` and `result/horizon.json` for
chartyard-still-60 (it is not a `scores.json` field):

| bin | azimuth range (deg) | truth max in bin (deg) | truth mean in bin (deg) | measured (deg) |
|---|---|---|---|---|
| 4 | 48 to 60 | 44.00 | 33.18 | 38.0 |
| 6 | 72 to 84 | 25.50 | 25.08 | 0.0 |
| 8 | 96 to 108 | 49.30 | 27.66 | 57.0 |
| 21 | 252 to 264 | 49.30 | 5.24 | 13.0 |
| 25 | 300 to 312 | 63.20 | 15.42 | 7.0 |
| 26 | 312 to 324 | 63.00 | 9.71 | 9.0 |

Bins 25 and 26 are where `pole-near` stands: a pole a couple of degrees wide
reaching far above everything else that shares its bin. Its deficit in the
obstacle table is the height of the pole above what the bin reports, and its
missed width is the width of the pole itself. This is the failure section 9 of
the spec asks the chart profile to expose, and the instrument exposes it.

### 4.2 Three bins near north report a canopy where the truth has a low ridge

The largest single contribution to the horizon percentiles is not the binning.
`traceSkyCoverage` works on one luminance value per raster row, calls the
eightieth percentile of the top rows the sky level, and reads the first row
below seven tenths of that level as the top of an obstruction. The chart yard's
palette and its background were chosen before that tracer was measured against
them:

| quantity, chartyard-still-60 | value |
|---|---|
| sky level the tracer computed | 122.0 |
| the tracer's obstruction threshold | 85.4 |
| background value-noise grey range in the scene | 70 to 150 |
| palette colours whose luminance is below the threshold | 8 of 24 |

The consequence, in the bins that report a boundary higher than the tallest
thing their own azimuths contain:

| bin | azimuth range (deg) | truth max in bin (deg) | measured (deg) | first row below threshold |
|---|---|---|---|---|
| 0 | 0 to 12 | 11.15 | 87.0 | 4 |
| 1 | 12 to 24 | 12.45 | 87.0 | 4 |
| 2 | 24 to 36 | 45.50 | 86.0 | 5 |
| 5 | 60 to 72 | 24.50 | 76.0 | 15 |
| 7 | 84 to 96 | 25.60 | 72.0 | 19 |

The two upper rings of the chart are what those bins are reading. Near the
zenith a disc of small angular radius covers a wide band of azimuth, so it
occupies much of a row average rather than a few pixels of it:

| ring | discs | altitude (deg) | angular radius (deg) | top reached (deg) | azimuth span at that altitude (deg) |
|---|---|---|---|---|---|
| R5 (cap) | 6 | 85 | 1.5 | 86.5 | 17.2 |
| R4 | 8 | 75 | 1.0 | 76.0 | 3.9 |

The luminance of the row that trips the threshold, read from
`result/columns.json`, says which of the two it is in each bin:

| bin | tripping row luminance | altitude of that row (deg) | what is there |
|---|---|---|---|
| 0 | 76.2 | 87 | within about a degree of the top of the cap ring |
| 1 | 76.2 | 87 | the same |
| 2 | 76.2 | 86 | the same |
| 5 | 38.3 | 76 | `R4K0`, colour `(128, 0, 0)`, at the top of the R4 ring |
| 7 | 85.0 | 72 | no disc: the value-noise sky at its own dark end |

Bin 7 is the second half of the artefact. Its column falls gradually from the
top of the frame and crosses the threshold with no landmark involved at all,
because the background grey floor is itself below the threshold.

This is a chart artefact, ruled as such in the stage A ledger, and it is
recorded here as an instrument limitation rather than filed against the
scanner. A luminance-only tracer cannot tell a dark cloud from a canopy, and
the scene hands it dark sky and dark discs above the horizon. The fix belongs
to the scene, in stage B: restrict the palette above altitude 60 to colours
brighter than the tracer's threshold, and lift the background grey floor above
it. The effect on the numbers is visible in the table:

| chartyard-still-60 horizon error, recomputed from the two files | median (deg) | p95 (deg) | max (deg) |
|---|---|---|---|
| all bins (matches `scores.json`) | +0.95 | 75.90 | 76.75 |
| with bins 0, 1, 2, 5 and 7 removed | +0.75 | 25.45 | 56.20 |

Removing them does not rescue the gate. It moves the failure from one cause to
another, which is the point of separating them.

### 4.3 The roof and the wall are missed on width, not on height

`roof-south` and `wall-east` both have a deficit median below zero in the two
60 degree cases, and both are MISSED, on the width term alone:

| case | obstacle | deficit median (deg) | width missed (deg) | min width (deg) |
|---|---|---|---|---|
| still-60 | roof-south | -0.85 | 16.6 | 10 |
| still-60 | wall-east | -34.35 | 12.0 | 10 |
| arc075-60 | roof-south | -5.00 | 40.8 | 10 |
| arc075-60 | wall-east | -55.40 | 12.0 | 10 |

A negative median means the measured boundary is above the obstacle's own
silhouette over most of its span, which a thirty-bin boundary produces by
holding each bin's maximum. The width term counts the tenth-of-a-degree bins
where the measured boundary falls more than one degree below the obstacle, and
those bins are at the obstacle's edges, where a coarse bin straddles the
boundary. A roof spanning most of a quadrant can lose a chunk wider than its
declared minimum width and still have a median of about zero, which is exactly
what the two rows for `roof-south` show. An obstacle is found when it is found,
not when most of it is.

### 4.4 The arc moves the camera, and only the near things move with it

The two arc cases carry the camera metres away from `c_ref` while the still
case never moves it at all (section 2.1). Parallax follows the inverse
distance, and the landmark errors follow parallax. The six surface landmarks
are painted on objects at known distances; the background landmarks are
effectively at infinity.

| landmark | host | distance from `c_ref` (m) | still-60 error (deg) | arc075-60 error (deg) |
|---|---|---|---|---|
| RF1 | roof-south | 2.38 | 0.052 | omitted |
| W1 | wall-east | 2.54 | 0.109 | omitted |
| W2 | wall-east | 2.93 | 0.264 | omitted |
| RF2 | roof-south | 3.78 | 0.149 | omitted |
| T1 | trunk | 9.18 | 0.142 | 3.663 |
| H1 | hill | 120.14 | 0.041 | 0.041 |

| group, found landmarks only | still-60 median (deg) | still-60 max (deg) | arc075-60 median (deg) | arc075-60 max (deg) |
|---|---|---|---|---|
| background (effectively infinite) | 0.054 | 0.163 | 0.051 | 0.163 |
| surface (on objects) | 0.126 | 0.264 | 1.852 | 3.663 |

The background population is barely changed between the two cases and its
maximum is identical. The hill landmark, farthest away, is unchanged to three
decimal places. The trunk landmark moves by degrees. The four landmarks on the
wall and the roof, the nearest surfaces in the scene, are not merely displaced
but omitted, because their painted position is far enough from truth that the
scorer finds no blob of their colour within its match radius. That is the
signature of translation, not of a rotation or a north error: a global rotation
would move the background discs by the same amount, and they did not move.

`cache/cases/chartyard-arc075-60/result/panorama.png` shows the same thing
directly. The roof, a box a few metres overhead, is painted as a large brown
fan across the upper middle of the equirectangular frame, several times the
solid angle it subtends from `c_ref`, with an orange surface disc on it. The
nearby wall is a tall tan slab at the left, with the canopy sphere and the thin
trunk beside it. The background sky, its value noise and its stripe great
circles are crisp and unsmeared behind them, and the distant hill sits at the
correct height at both ends of the frame. Two small unpainted rectangles sit at
the bottom edge.

The landmark error map from the same report puts a green ring on every
background disc it found, six white rings for the six omissions (two of them
high on the roof fan, where the smeared roof is painted over two background
discs, and four on the surface landmarks), grey rings on the twelve landmarks
that are not observable, and exactly one red ring, at the trunk landmark's
truth direction, with the green disc it should contain sitting clearly to the
west of it, together with the whole trunk.

### 4.5 The assumed lens on chartyard-arc075-70

The scanner starts with a short-axis field of view of 60 degrees and no stored
calibration, and `chartyard-arc075-70` differs from `chartyard-arc075-60` in
that field alone. The registration step fits a rigid rotation against the
existing mosaic, and a scale error is not something a rotation can absorb, so
the frames are refused rather than misplaced:

| | arc075-60 | arc075-70 |
|---|---|---|
| overlap-wait outcomes | 2 | 25 |
| no-target outcomes | 0 | 18 |
| accepted captures | 20 | 9 |
| dome cells covered | 88 | 52 |
| panorama alpha fraction | 0.9882 | 0.7698 |
| unresolved horizon bins | 0 | 25 |
| unresolved solid angle (sr) | 0.0000 | 5.2360 |
| landmarks found | 34 | 28 |
| landmarks duplicated | 0 | 2 |
| overlay settled p95 (deg) | 0.075 | 7.328 |
| gates failing | 5 | 11 |

This is issue #52. The part of it that matters is not the accuracy cost but the
cue: the overlap-wait path tells the user to fix their aim and never names the
camera view angle control that would fix the actual problem. The scanner
records which gate fired on every refusal, in the same log this table is
counted from.

### 4.6 Holds without a capture or a cell

`every_hold_captured` fails on all three cases, and the latency gate passes on
all three. Captures, when they happen, happen promptly (section 3.5). Since
issue #54 was fixed the gate no longer blames the scanner for declining to
photograph a cell it already holds, so what remains is genuine. Classifying
each hold by the best outcome recorded inside its own window:

| what happened during the hold | still-60 | arc075-60 | arc075-70 |
|---|---|---|---|
| a capture was accepted | 21 | 20 | 9 |
| refused: the cell was already captured | 26 | 21 | 2 |
| refused: overlap-wait | 0 | 2 | 25 |
| refused: no-target | 1 | 0 | 10 |
| refused: alignment-wait | 0 | 5 | 2 |

The first two rows are the satisfied holds. What is left is one hold on
chartyard-still-60, seven on chartyard-arc075-60 and thirty-seven on
chartyard-arc075-70.

| case | unsatisfied holds | what they are |
|---|---|---|
| still-60 | 1 | the last hold of the route, the top of the upward sweep, where no dome cell lies within the aim cone, so the scanner reports `no-target` for the whole hold (issue #57) |
| arc075-60 | 7 | five `alignment-wait` and two `overlap-wait`, the near-field mismatch of section 4.4 reaching the registration step |
| arc075-70 | 37 | the lens error of section 4.5 |

Only the still case's single remaining failure is about the geometry of the
dome rather than about the scan.

## 5. Known limits of the instrument

These bound what any number above can mean.

| limit | why it matters |
|---|---|
| The chart yard is noise free, evenly lit and has no lens distortion. | Feature localisation is easier here than on any real frame. A p95 measured here is a floor for the same algorithm on a phone, never a prediction of it. |
| Frames arrive on a virtual clock at exactly 10 fps, none dropped, none late. | Capture faults, clock injection and provider faults are stage B. Nothing here exercises them. |
| The grab gate samples at 350 ms against a 10 fps stream, so it lands on every fourth frame. | The effective sampling interval is 400 ms, which is why each case logs 264 grab attempts over a timeline of about 105 s. Capture latency is quantised by that grid rather than by anything in the scanner's decision. |
| The replay drives the `requestVideoFrameCallback` path. | The interval fallback, the only path Firefox Android takes, is never executed by any case here, so neither the new `stale-image` refusal on that path nor issue #48's stillness timestamping is measured. A browser whose MediaStream `currentTime` does not advance now loses capture there, not only the stillness witness. |
| The overlay error on the two 60 degree cases is set by sampling phase. | Section 3.4. Where a reading was emitted at a frame's own capture time the reconstructed pose matches truth exactly, so the moving median and p95 collapse to floating-point noise and have moved between route revisions with no scanner change. The maxima are the informative figures on those rows. |
| The raster is 1080 x 300 and both the result and the ideal are read from it. | The ideal column of section 3.2 is the quantisation floor: a perfect scanner scores p95 0.084 and max 0.266 degrees on this raster, so a difference below that is the raster, not the algorithm. |
| The `min_width_deg` values were chosen when they only labelled a row. | The width term of `missed` was added afterwards, and those declared widths are now the difference between found and missed on the narrow obstacles. They have not been re-derived since they became load bearing (issue #53). |
| The horizon tracer meets a dark sky and dark discs. | Section 4.2. A scene change belongs in stage B; until then the horizon percentiles on the chart yard carry an artefact the scanner is not responsible for. |
| `begin` is offered repeatedly until the scanner accepts it. | The recorded `begin` action is an earliest time, not the instant the scan starts: `PhotosphereSweep.begin()` refuses silently until the compass is ready, and the production Start button is behind the same gate. |
| Determinism is established on this machine, not across machines. | Section 2.2: two independent builds of the still case agree byte for byte through the renderer, the recorder, the truth, the replay and the scorer. Spec section 8 asks for documented numerical tolerances rather than bit-identical rendering across GPUs, and no second GPU has been tried. |

## 6. Stage B entry point

Stage A's exit condition in section 11.A of the spec is "the current
implementation has an honest measured baseline. Do not adjust scenes until it
passes." This document is that baseline. Section 11.B is the next stage:
retain complete source frames and add provider and clock injection, reproduce
and fix the review 15 regressions, implement multiview calibration with
qualification and invalidation including translation and sparse finite-depth
reconstruction, and expand the camera models and motion cases.

The three regressions from
[the stillness review](15-photosphere-stillness-review.md) become permanent
simulator cases, as section 10 of the spec requires:

1. Stop the orientation events, move the image, then settle in a different
   view. New stillness must not certify the old direction without a valid
   relocalization.
2. Freeze the physical video frame while fallback timer callbacks continue. The
   scanner must not accumulate fresh visual evidence from a frozen frame.
3. Approach by ten degrees over a hundred milliseconds and then hold with
   change-driven silence. A valid quick approach must recover rather than stay
   permanently rejected because of an old jump.

Two scanner findings came out of these runs and are open against the scanner,
not against the simulator:

| issue | what it is | where it came from |
|---|---|---|
| #57 | No dome cell lies within the aim cone at altitude 85, so a hold there can never capture: the zenith cap's cone is narrower than the gap to the next ring, the aim dot shows no target, and every frame is declined with no cue that says why. Widen the cap's cone or add a ring so every altitude has a target, and say in the cue when a direction has none. | Section 4.6 |
| #52 | The assumed lens. A short-axis field of view ten degrees from the truth costs most of the dome, and the cue on the refusal path tells the user to fix their aim instead of naming the camera view angle control. Read the refusal history that is already in the capture log. | Section 4.5 |

Also carried into stage B from this baseline:

| item | where it came from |
|---|---|
| A scene change so the horizon tracer is not handed dark sky and dark discs above altitude 60, and a background grey floor above the tracer's threshold. | Section 4.2 |
| Re-derive the declared `min_width_deg` values now that they gate (issue #53). | Section 5 |
| Exercise the interval fallback path, and answer the device questions in issue #48: the camera pipeline delay on Firefox Android, and whether `video.currentTime` advances with frame delivery for a MediaStream-backed element there. That question is now a release gate, because capture itself depends on it and not only the stillness witness. | Section 5 |
| Decouple the overlay measurement from the sampling phase, so the moving percentiles measure the scanner rather than where the emission instants fall. | Section 3.4 |
