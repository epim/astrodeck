# Photosphere simulator: the stage A baseline

Date: 2026-09-18. Refreshed: 2026-09-19.
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

**2026-09-19 refresh.** The
[photosphere issue pass](../../.superpowers/sdd/2026-09-18-photosphere-issue-pass/progress.md)
landed thirteen commits between `d4b3fe91` and `3fbd2039` (`git log --oneline
d4b3fe91..HEAD`), six of which touch what this instrument measures: `1df2b18f`
(#38, stillness bounds, no score change here), `08cc613b` (#53, the obstacle
miss rule now grades what the product can resolve), `0bcb62ca` (#59, the
overlay error is the max of three axes, not `forward` alone), `88551099` (#52,
the lens-doubt cue names the view angle, no score change), `b1e629f1` (#57,
every direction above the horizon has an aim-cone target), and `3fbd2039`
(#58, the horizon tracer finds the sky boundary by its transition rather than
a luminance threshold). The three cases were replayed and scored again at that
HEAD, unchanged inputs (same hashes, confirmed in section 2.2), and every
table below carries the 2026-09-18 numbers beside today's so a reader can see
what each commit moved. Section 4 keeps the first baseline's forensic prose
where it still describes the shipped code, marks a "what the first baseline
said" note where a mechanism has since been fixed, and section 4.6 and the
roof run-breakdown in section 4.3 are replaced with what is true now.

The whole-branch review's fix wave, `9a7f4f88` (#75, hysteresis on the
gradient floor, and six minors), landed after the tables in this document
were taken; the three cases were replayed and scored again at that HEAD and
every number was unchanged from the reading below, so it changed no number
here, only the tested build's hash.

The replaying tree carried one uncommitted change not part of this pass: the
other session's in-progress edits to `ui/src/next/hubs/sky/sheets/horizon.tsx`,
`horizonStrip.ts` and `__tests__/horizonDom.test.tsx` (`git status --porcelain`
at the time of this refresh showed exactly those three files and nothing else
under `ui/src/next/hubs/sky/sheets` or `tools/photosphere_sim`). None of the
three files the diff touches is read by `PhotosphereSweep`, the replay driver,
or the scorer -- `horizon.tsx` is the editor screen the finished panorama is
reviewed on, not the scanning path -- but `result/summary.json`'s `app_commit`
still reports `9a7f4f88d5a78d811fbcd432d0cf8a00d6c5b440-dirty` for that reason,
per `CONTRACT.md`'s rule that a bare hash on a score taken from an edited tree
would name code that was never replayed. Every number in this refresh is
therefore stamped `-dirty` against a diff that does not touch the measured
code, which is recorded here rather than left for a reader to have to
re-derive.

**2026-09-19 residuals refresh.** The
[photosphere residuals pass](../../.superpowers/sdd/2026-09-19-photosphere-residuals/progress.md)
landed four more commits that touch what this instrument measures, on top of
the `9a7f4f88` reading above: `44543a32` (#64, #68 -- the obstacle width term
now reports the longest contiguous run beside the total, and a committed
fixture pair joins the corpus), `2b964638` (#65, #70 -- the carried
registration correction is bounded in two derived regimes), `d6ee0bd7` (#71,
#74 -- the horizon tracer judges a departure against its neighbouring
columns), and `9109df9a` (#63, #76 -- a gyroscope witness is wired in, and the
zenith hold's refusal is now measured, not guessed; no score changes because
none of the three cases records a motion channel). The three cases were
replayed and scored again at this HEAD -- unchanged input hashes, confirmed
below -- and every table in this document that this pass moved now carries a
third value, today's, beside the two already there. The other session's
uncommitted diff is exactly the same three files as above
(`git status --porcelain` re-checked), so `app_commit` is
`9109df9aeae7b9b0ae9a5f53f342379e2d606e4c-dirty` for the same reason and by
the same rule: the `-dirty` suffix names an edited tree that this pass's
measured code was never part of.

Two numbers changed gate verdicts today. `chartyard-arc075-60`'s
`no_missed_obstructions` flips FAIL to PASS, and it needs BOTH of two
commits, not one: task 2 (#64) keys the miss rule on the longest contiguous
run rather than the total, and task 5 (#71/#74) shortens that run from 13.3
to 10.2 degrees, under the 12.0 degree threshold. The roof is still
under-reported by 29.8 degrees of total width; the gate passes because the
rule now reads a stretch, not because the roof is fully found.
`chartyard-arc075-70`'s `overlay_max_lt_10` flips FAIL to PASS
(task 4, #70: the carried correction is now bounded, so the combined overlay
maximum falls from 10.038 to 1.741 degrees). Section 3.1 has both, and every
other section below that either number touches.

Section 3.2 (landmarks) and the overlay and horizon rows of 3.3/3.4 for
`chartyard-arc075-70` moved again today for the same structural reason
section 3.2 already states about the earlier pass: task 4's bound changes
which frames register and how the mosaic is painted, which moves the panorama
the landmark blobs, the horizon boundary and the overlay pose are all read
from, even though none of landmark scoring, horizon tracing or the overlay
metric itself was touched by that task. The detailed per-landmark-id table in
section 3.2 is not re-derived id by id for this refresh past what the summary
counts already say; the summary counts are current.

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

The tested build for the 2026-09-18 reading was `app_commit`
`9e3097fb2e1c7308eb36e7bdfe1ddcb3eb837683` on branch
`feat/photosphere-production`, read from `result/summary.json`, which the
replay driver stamps from the working tree. The manifest's own
`versions.app_commit` is `null`: a case is built by the simulator and does not
belong to an application build.

**2026-09-19 refresh.** All three cases were replayed and scored again without
rebuilding them -- the manifest and `input_hash` values above are unchanged,
confirmed byte for byte against this reading -- at `app_commit`
`9a7f4f88d5a78d811fbcd432d0cf8a00d6c5b440-dirty` (the `-dirty` suffix and why
it is present despite no measured file being edited are explained in the
refresh note at the top of this document). This is the pass's final HEAD,
after the whole-branch review's fix wave (`9a7f4f88`); replaying and scoring
at that HEAD changed no number from the reading recorded below, which was
taken one commit earlier at `3fbd2039`.

**2026-09-19 residuals refresh.** All three cases were replayed and scored a
third time, again without rebuilding -- `manifest.json`'s hashes are unchanged
from the table above -- at `app_commit`
`9109df9aeae7b9b0ae9a5f53f342379e2d606e4c-dirty`, this pass's final HEAD. All
three `scores.json` files carry that exact string; there is no case where the
`-dirty` suffix is absent or names a different tree. `chartyard-still-60`'s
ideal (`cache/cases/chartyard-still-60/ideal`) was rescored the same way and
is unchanged from the 2026-09-18 reading in every figure, as expected --
`sim/ideal.py` is untouched by this pass too. The two committed fixture cases
(`fixtures/chartyard-shortpan-60` and `-70`) were replayed into a scratch copy
outside the repository (per `fixtures/README.md`'s own rule not to write into
them) and scored there for the first time; section 3.7 below records what
that showed, replacing the fixture's own summary.json app_commit with the same
`9109df9a...-dirty` string, confirmed by inspecting the replay driver's
printed output before scoring.

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

**2026-09-19 refresh.** Determinism was re-checked at the pass's HEAD at the
time (`3fbd2039`, one commit before the fix wave described above), on the
replay and scoring half of the pipeline (this refresh did not rebuild any case
with `make-case`, since the corpus is unchanged and re-hashed as such above):
`chartyard-still-60`'s `input/` and `truth/` were copied to a second, separate
directory with no `result/` of their own, replayed independently, and
compared against the result already on record.

| compared between the two independent replays | result |
|---|---|
| `result/summary.json` | byte identical |
| `result/panorama.png` | byte identical |

Both carry `app_commit` `3fbd2039053ed3709574af8661602aff55cbed33-dirty`. The
replay and scoring pipeline is therefore still reproducible on this machine at
this HEAD; the renderer half of determinism (make-case-to-make-case) was
established at the 2026-09-18 reading above and was not re-run, since nothing
in this pass touches `sim/render.py`, `sim/truth.py` or `sim/scene.py`.

**2026-09-19 residuals refresh.** Determinism was re-checked again at this
pass's final HEAD (`9109df9a`), the same way: `chartyard-still-60`'s `input/`,
`truth/` and `manifest.json` were copied to a second, separate directory with
no `result/` of its own, replayed independently through the same
`replay.ts`, and compared against the result already on record in
`cache/cases/chartyard-still-60/result` (itself freshly replayed and scored
for this refresh). Nothing under `tools/photosphere_sim/cache` was deleted,
moved or renamed by this check; the second copy lived entirely under this
session's scratch directory.

| compared between the two independent replays | result |
|---|---|
| `result/summary.json` | byte identical |
| `result/panorama.png` | byte identical (82 445 bytes) |

Both carry `app_commit` `9109df9aeae7b9b0ae9a5f53f342379e2d606e4c-dirty`. The
replay and scoring pipeline is reproducible on this machine at this HEAD too;
the renderer half was not re-run, for the same reason as the 2026-09-19
refresh above.

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

Each cell below is `before the issue pass (2026-09-18) -> after it
(2026-09-19) -> after the residuals pass (2026-09-19, this refresh)`. A gate
with no arrow did not change on that case in either pass; the task/commit
column names what changed it where one did. Cases where only the residuals
pass moved something read `PASS` / `FAIL` unchanged through the middle step.

| gate | still-60 | arc075-60 | arc075-70 | ideal (still-60) | changed by |
|---|---|---|---|---|---|
| `landmarks_p95_lt_0_5` | PASS | PASS | FAIL | PASS | -- |
| `landmarks_p99_lt_1` | PASS | FAIL | FAIL | PASS | -- |
| `no_omissions` | PASS | FAIL | FAIL | PASS | -- |
| `no_duplicates` | PASS | PASS | FAIL | PASS | -- |
| `horizon_p95_lt_1` | FAIL | FAIL | FAIL | PASS | -- (numbers moved a lot, verdict did not: task 12, #58, `3fbd2039`) |
| `no_missed_obstructions` | FAIL -> PASS | FAIL -> **PASS** | FAIL -> PASS | PASS | task 10+12 (#53/#58) closed still-60 and arc075-70; **today, arc075-60 needs BOTH task 2 and task 5 together**: task 2 (#64, `44543a32`) keys the miss rule on the longest contiguous run rather than the total, and task 5 (#71/#74, `d6ee0bd7`) shortens that run from 13.3 to 10.2 degrees, under the 12.0 degree `resolvable_width_deg` threshold -- the gate passes because the rule now reads a stretch, not because the roof is fully found: it is still under-reported by 29.8 degrees of total width (section 3.3, 4.3) |
| `no_unresolved_boundary` | PASS | PASS | FAIL | PASS | -- |
| `overlay_settled_p95_lt_0_5` | PASS | PASS | FAIL | PASS | -- |
| `overlay_moving_p95_lt_1` | PASS [P] | PASS [P] | FAIL | PASS | -- |
| `overlay_max_lt_10` | PASS | PASS | PASS -> FAIL -> **PASS** | PASS | task 13 (#59) then task 11 (#57) pushed the combined maximum to 10.038 (issue #70); **today, task 4 (#70, `2b964638`) bounds the carried correction and the maximum falls to 1.741** |
| `no_duplicate_frames` | PASS | PASS | PASS | PASS | -- |
| `capture_p95_le_1500` | PASS | PASS | PASS | PASS | -- |
| `every_hold_captured` | FAIL -> PASS | FAIL | FAIL | PASS | task 11 (#57, `b1e629f1`): the zenith aim cone now covers every direction above the horizon. Task 6 (#63/#76, `9109df9a`) measured why the two arc cases still fail rather than fixing it: hold 47 refuses `alignment-wait` with `wait=no-pose` on both, the `separation` term of the gate fires zero times in the whole scan (issue #104, dead code), and the featureless-view cue (issue #63) is what is actually refusing the frame -- a gyro witness now exists in the scanner, but neither recording carries a `devicemotion` channel to drive it with (issue #105), so the gate stays red for a reason the simulator cannot exercise, not because the fix failed |
| `coverage_ge_0_95` | PASS | PASS | FAIL | PASS | -- |
| `pass` | FAIL | FAIL | FAIL | PASS | still FAIL on all three after both passes; still-60 fails on exactly one gate, arc075-60 now on four, arc075-70 now on ten (of the fourteen) |

[P] Phase-locked, not measured: the moving median is still of order 1e-14
degrees on both 60 degree cases, because the sensor emitted a reading at the
frame's own capture instant and the scanner's interpolation was never
exercised (section 3.4). This did not change in the pass. The cell says PASS;
it does not say the scanner tracked a moving view to within a degree.

| case | gates failing, of the fourteen that `pass` is the AND of | before the issue pass (2026-09-18) | after it (2026-09-19) | after the residuals pass (2026-09-19, this refresh) |
|---|---|---|---|---|
| chartyard-still-60 | now | 3 | **1** | 1 (unchanged today) |
| chartyard-arc075-60 | now | 5 | 5 (same set: task 12's re-review confirmed "arc075-60 gates unchanged") | **4** -- today's tasks 2+5 (#64 and #71/#74 together) close `no_missed_obstructions` |
| chartyard-arc075-70 | now | 11 | 11 (same count, different set: `no_missed_obstructions` joined the passing side, `overlay_max_lt_10` left it) | **10** -- today's task 4 (#70) closes `overlay_max_lt_10` |
| ideal (still-60) | now | 0 | 0 | 0 (unchanged today) |

`chartyard-still-60` now fails only `horizon_p95_lt_1` (p95 20.56 degrees at
the 2026-09-19 issue-pass reading, unchanged again today at the same measured
20.56, section 3.3): the two gates issues #57 and #58 targeted are both fixed
on this case, and the remaining failure is the coarse-bin mechanism section
4.1 describes, now the dominant one instead of the smallest of three. Neither
of today's two commits touches this case's horizon or its gate count.

`chartyard-arc075-60` now fails `landmarks_p99_lt_1`, `no_omissions`,
`horizon_p95_lt_1` and `every_hold_captured` -- down from five because task 5
(#71/#74) resolved bin 20's roof and pulled the longest missed run under
`resolvable_width_deg` (section 3.3). `chartyard-arc075-70` still fails ten of
fourteen: task 4 (#70) closed `overlay_max_lt_10` by bounding the carried
correction, but the wrong assumed lens (section 4.5) and the coarse-bin/tracer
mechanisms (sections 4.1-4.3) are untouched by either fix and still cost it
most of the corpus.

The ideal column is the same case's `ideal/` directory: the result a perfect
scanner would have written, built from the case's own truth by ray casting. It
is in the table so that every other column can be read against the instrument's
own floor rather than against zero. It was rebuilt and rescored for this
refresh; every figure below is identical to 2026-09-18's, as expected, since
`sim/ideal.py` is untouched by this pass.

### 3.2 Landmarks

No task in the 2026-09-18 issue pass changed landmark scoring or the camera
model, so at that reading `chartyard-still-60` and `chartyard-arc075-60` were
unchanged to three decimal places from the 2026-09-18 baseline.
`chartyard-arc075-70`'s figures moved at every subsequent reading, because
whichever commit changes which frames the scanner captures and how the
mosaic is painted also changes the panorama the landmark blobs are read from,
even though no landmark-scoring code has ever been touched by any of these
passes. Today, task 4 (#70, `2b964638`) is that commit: bounding the carried
correction changes which of `chartyard-arc075-70`'s frames register and moves
every figure on that case again, while `chartyard-still-60` and
`chartyard-arc075-60` stay unchanged (confirmed character-identical in task
4's own before/after measurement). Each cell is `before the issue pass ->
after it -> after today's residuals pass`; no arrow means unchanged at that
step.

| | still-60 | arc075-60 | arc075-70 | ideal |
|---|---|---|---|---|
| expected (observable) | 40 | 40 | 40 | 40 |
| found | 40 | 34 | 28 -> 26 -> **29** | 40 |
| omitted | 0 | 6 | 10 -> 11 -> **5** | 0 |
| duplicated | 0 | 0 | 2 -> 3 -> **6** | 0 |
| slivers | 0 | 0 | 2 -> 1 -> **2** | 2 |
| spurious | 0 | 3 | 8 -> 10 -> **3** | 0 |
| error median (deg) | 0.059 | 0.051 | 4.099 -> 4.980 -> **3.106** | 0.030 |
| error p95 (deg) | 0.150 | 0.148 | 7.700 -> 7.390 -> **4.978** | 0.084 |
| error p99 (deg) | 0.225 | 2.508 | 7.929 -> 7.805 -> **5.110** | 0.209 |
| error max (deg) | 0.264 | 3.663 | 7.986 -> 7.933 -> **5.146** | 0.266 |

The ideal's landmark figures are unchanged (`sim/ideal.py` is untouched by
either pass); its 2 slivers are the same ones the 2026-09-18 reading found.
`chartyard-arc075-70`'s error statistics all move toward the ideal today
without approaching it: the wrong assumed lens (section 4.5) is unchanged by
task 4's fix, which bounds the overlay's carried correction, not the
registration scale error that still refuses most of this case's frames.

`per_landmark` carries all 52 entries of `landmarks.json`; the twelve that are
not observable from `c_ref` are outside `expected` and outside the percentiles.

Omitted and duplicated landmarks, with their direction from `c_ref`. The
still-60 and arc075-60 sets are unchanged from 2026-09-18. chartyard-arc075-70's
set is different in both membership and count from the 2026-09-18 reading,
which is included alongside it because no task explicitly targeted this case's
landmark set and the change is otherwise easy to mistake for noise:

| id | az (deg) | alt (deg) | host | status, 2026-09-18 | status, 2026-09-19 |
|---|---|---|---|---|---|
| W1 | 95.7 | 9.0 | wall-east | omitted in arc075-60 and arc075-70 | omitted in arc075-60 and arc075-70 |
| W2 | 59.9 | -9.8 | wall-east | omitted in arc075-60 and arc075-70 | omitted in arc075-60 and arc075-70 |
| RF1 | 161.1 | 39.0 | roof-south | omitted in arc075-60 and arc075-70 | omitted in arc075-60 and arc075-70 |
| RF2 | 195.0 | 23.4 | roof-south | omitted in arc075-60 and arc075-70 | omitted in arc075-60 and arc075-70 |
| R3K1 | 103.0 | 55.0 | background | omitted in arc075-60 and arc075-70 | omitted in arc075-60 and arc075-70 |
| R4K1 | 120.0 | 75.0 | background | omitted in arc075-60 | omitted in arc075-60 |
| T1 | 39.6 | 3.7 | trunk | omitted in arc075-70 | found in arc075-70 |
| R1K4 | 219.0 | 15.0 | background | omitted in arc075-70 | found in arc075-70 |
| R1K5 | 282.0 | 15.0 | background | omitted in arc075-70 | found in arc075-70 |
| R2K4 | 236.0 | 35.0 | background | omitted in arc075-70 | omitted in arc075-70 |
| R3K4 | 253.0 | 55.0 | background | omitted in arc075-70 | omitted in arc075-70 |
| R2K1 | 86.0 | 35.0 | background | duplicated in arc075-70 | omitted in arc075-70 |
| R2K6 | 331.0 | 35.0 | background | duplicated in arc075-70 | duplicated in arc075-70 |
| R1K2 | 135.0 | 15.0 | background | found in arc075-70 | omitted in arc075-70 |
| R1K6 | 314.0 | 15.0 | background | found in arc075-70 | omitted in arc075-70 |
| R2K5 | 299.0 | 35.0 | background | found in arc075-70 | omitted in arc075-70 |
| R2K7 | 17.0 | 35.0 | background | found in arc075-70 | duplicated in arc075-70 |
| R3K0 | 51.0 | 55.0 | background | found in arc075-70 | duplicated in arc075-70 |

The two duplicates on 2026-09-18 and the three on 2026-09-19 are
chartyard-arc075-70's alone, and they are the reason it fails `no_duplicates`
where the other two cases pass: a wrong lens scale puts two copies of the same
disc in the panorama far enough apart that both clear the blob filter and both
fall inside the match radius. Which discs are duplicated moved along with
everything else in this table; the mechanism (the wrong assumed lens, section
4.5) did not.

**Today's residuals pass moves this table a third time, on `arc075-70` only.**
Task 4's fix (#70) changes the case's accepted frames from 10 to 9 and the
mosaic they paint, which moves the specific landmark set again (this document
does not reproduce all eighteen rows a third time; the summary counts above
are current). Omitted today: `RF1`, `RF2`, `W1`, `W2` (unchanged from both
earlier readings) and `R3K4` (unchanged). `R2K4`, `R2K1`, `R1K2`, `R1K6` and
`R2K5` -- omitted at the 2026-09-19 issue-pass reading -- are all found today.
Duplicated today (6, up from 3): `R2K6`, `R2K7` and `R3K0` carry over, and
`R0K5`, `R1K3` and `R1K4` are newly duplicated -- `R1K4` was itself omitted at
the 2026-09-18 reading and found at the 2026-09-19 one, so this is its third
distinct status in three readings. The mechanism is unchanged: a wrong lens
scale, not a landmark-scoring change.

### 3.3 Horizon

The measured bin count and resolution are unaffected by either pass (still 30
bins, still 12.0 degrees each): none of the commits changes `bins` on
`PhotosphereSweep`. Every other row below moved during the issue pass, on
every case, because task 12's tracer rewrite (#58, `3fbd2039`) changed how
`traceSkyCoverage` finds the boundary on every column, not just the ones its
own fix targeted; most rows moved again in today's residuals pass, for the
reasons in the "changed by" column added below. Cells read
`before the issue pass -> after it (2026-09-18/19) -> after the residuals
pass (this refresh)`.

| | still-60 | arc075-60 | arc075-70 | ideal | changed today by |
|---|---|---|---|---|---|
| truth bins | 3600 | 3600 | 3600 | 3600 | -- |
| measured bins | 30 | 30 | 30 | 3600 | -- |
| measured resolution (deg) | 12.0 | 12.0 | 12.0 | 0.1 | -- |
| signed error median (deg) | +0.95 -> +1.60 -> **+1.80** | +7.47 -> +2.95 -> **+3.20** | +35.35 -> +4.40 -> **+7.50** | 0.00 | still-60/arc075-60: task 5 (#71/#74, bins 5/6/17/21 and 4/20 changed); arc075-70: task 4 (#70, a different frame set registers) |
| absolute error p95 (deg) | 75.90 -> 20.56 -> **49.00** | 75.90 -> 56.50 -> 56.40 | 72.95 -> 40.75 -> **16.51** | 0.00 | still-60: task 5 (bin 21, 0 -> 49 against a 47.90 truth maximum, is now the largest single deficit in the case); arc075-60: task 5, within rounding of no change; arc075-70: task 4 |
| absolute error max (deg) | 76.75 -> 58.35 -> 58.35 | 76.75 -> 62.05 -> **58.35** | 73.05 -> 41.35 -> **39.80** | 0.00 | arc075-60: task 5; arc075-70: task 4; still-60 unchanged today |
| false open (sr) | 0.1419 -> 0.0261 -> **0.0091** | 0.1544 -> 0.2487 -> **0.0522** | 0.0058 -> 0.0395 -> **0.0122** | 0.0000 | still-60 and arc075-60: task 5 (#71/#74); arc075-70: task 4 (#70), with task 5's own net effect on this case measured as unchanged (see the false_open/false_blocked note below) |
| false blocked (sr) | 0.8208 -> 0.3791 -> **0.5202** | 1.5277 -> 0.7098 -> **0.7411** | 0.4391 -> 0.2936 -> **0.2050** | 0.0000 | same as the row above; read against each case's own ideal, not one number -- see below |
| unresolved (sr) | 0.0000 | 0.0000 | 5.2360 -> 3.9794 | 0.0000 | unchanged today on every case |
| unresolved bins | 0 | 0 | 25 -> 19 | 0 | unchanged today (confirmed: `unresolved_sr` is bit-for-bit the same figure) |
| north offset (deg) | -1.3 -> -0.9 -> **-0.2** | -7.0 -> -10.0 -> **-1.3** | -10.0 -> **-0.2** | 0.0 | today's tracer/mosaic changes (task 5 on the 60-degree cases, task 4 on arc075-70) moved every case's best-fit shift off the search boundary it had been pinned against |

`north_offset_deg` was searched over the range -10 to +10 in steps of 0.1 at
every reading. At the 2026-09-19 issue-pass reading, chartyard-arc075-70
(both readings) and chartyard-arc075-60 sat at the edge of that search, a
lower bound on the true shift rather than a measurement of it. Today none of
the three cases is pinned against that boundary any more: the tracer and
mosaic changes in this pass moved every case's horizon profile enough that a
real interior optimum exists again.

`false_open_sr` fell on `still-60` and `arc075-70` at the issue-pass reading
-- the two cases where task 12's fix removed dark sky and dark discs reading
as an obstruction (section 4.2) -- and *rose* on `arc075-60` at that same
reading, which is issue #71: a genuinely open patch of roof read as blocked
there under that tracer's persistence rule. Today's residuals pass (task 5,
#71/#74) is the fix for exactly that: `false_open_sr` falls again on
`still-60` (0.0261 to 0.0091) and on `arc075-60` (0.2487 to 0.0522, the case
issue #71 was filed against), and is unchanged on `arc075-70` (0.0395 to
0.0122 is task 4's doing, not task 5's -- task 5's own net effect on this
case, measured independently of task 4's frame-set change, is exactly zero).

Obstacles, each scored against its own first-hit silhouette and never against
the envelope. Task 10 (#53, `08cc613b`) added the `resolvable` /
`visible_width_deg` / `resolvable_width_deg` fields between the two readings:
an obstacle narrower than one product bin (30 bins, 12 degrees each) cannot be
represented by the product at all, and is no longer graded as missed for that
reason alone (`CONTRACT.md`, "Issue #53"). The two poles and the trunk are
never resolvable at this product's resolution (visible widths 2.2, 0.3 and 3.0
degrees); the roof and the east wall are, at their declared 10 degrees
notwithstanding, because their true visible extent is 146.6 and 84.0 degrees.
`verdict` below is `missed` read back as `MISSED`/`found`; an obstacle that is
not resolvable is always `found` by construction, whatever its deficit says,
which is why the poles and the trunk read `found` throughout even where the
deficit is large.

| obstacle | truth peak (deg) | min width (deg) | resolvable | | still-60 (2026-09-18 -> 09-19) | arc075-60 (09-18 -> 09-19) | arc075-70 (09-18 -> 09-19) | ideal |
|---|---|---|---|---|---|---|---|---|
Each cell below now carries a third arrow target, today's residuals-pass
reading, in bold where it differs from the issue-pass "after" value already
recorded. Unbolded values are unchanged today.

| pole-near | 63.20 | 2.0 | no | deficit median (deg) | 56.15 -> -0.85 -> -0.85 | 56.15 -> 54.15 -> 54.15 | null -> null -> null | 0.00 |
| | | | | deficit p95 (deg) | 56.20 -> 37.95 -> 37.95 | 56.20 -> 54.20 -> 54.20 | null -> null -> null | 0.00 |
| | | | | width missed: total (deg) | 2.2 -> 0.3 -> 0.3 | 2.2 -> 2.2 -> 2.2 | null -> null -> null | 0.0 |
| | | | | width missed: longest run (deg) | -- -> 0.3 -> 0.3 | -- -> 2.2 -> 2.2 | -- -> null -> null | 0.0 |
| | | | | verdict | MISSED -> found (unchanged) | MISSED -> found (unchanged) | MISSED -> found (unchanged) | found |
| pole-far | 12.45 | 0.25 | no | deficit median (deg) | -74.55 -> -1.55 -> -1.55 | -74.55 -> -1.55 -> -1.55 | -33.55 -> -34.55 -> **-2.55** | 0.00 |
| | | | | deficit p95 (deg) | -74.55 -> -1.55 -> -1.55 | -74.55 -> -1.55 -> -1.55 | -33.55 -> -34.55 -> **-2.55** | 0.00 |
| | | | | width missed: total (deg) | 0.0 | 0.0 | 0.0 | 0.0 |
| | | | | width missed: longest run (deg) | -- -> 0.0 -> 0.0 | -- -> 0.0 -> 0.0 | -- -> 0.0 -> 0.0 | 0.0 |
| | | | | verdict | found (unchanged) | found (unchanged) | found (unchanged) | found |
| roof-south | 75.15 | 10 | yes | deficit median (deg) | -0.85 -> -1.50 -> -1.50 | -5.00 -> -4.50 -> **-5.95** | null -> -17.60 -> **-10.45** | 0.00 |
| | | | | deficit p95 (deg) | 2.35 -> -0.25 -> -0.30 | 2.95 -> 55.43 -> **2.15** | null -> -16.79 -> **15.28** | 0.00 |
| | | | | width missed: total (deg) | 16.6 -> 2.6 -> 1.3 | 40.8 -> 41.8 -> **29.8** | null -> 0.0 -> **1.3** | 0.0 |
| | | | | width missed: longest run (deg) | -- -> 1.3 -> 1.3 | -- -> 13.3 -> **10.2** | -- -> 0.0 -> **1.3** | 0.0 |
| | | | | verdict | MISSED -> found (unchanged) | MISSED -> MISSED -> **found** | MISSED -> found (unchanged) | found |
| wall-east | 25.60 | 10 | yes | deficit median (deg) | -34.35 -> -2.05 -> -2.05 | -55.40 -> -56.40 -> -56.40 | null -> -5.43 -> **-9.40** | 0.00 |
| | | | | deficit p95 (deg) | 25.25 -> -0.40 -> -0.40 | 21.60 -> -5.40 -> **-18.40** | null -> -4.40 -> **-7.55** | 0.00 |
| | | | | width missed: total (deg) | 12.0 -> 0.0 -> 0.0 | 12.0 -> 0.0 -> 0.0 | null -> 0.0 -> 0.0 | 0.0 |
| | | | | width missed: longest run (deg) | -- -> 0.0 -> 0.0 | -- -> 0.0 -> 0.0 | -- -> 0.0 -> 0.0 | 0.0 |
| | | | | verdict | MISSED -> found (unchanged) | MISSED -> found (unchanged) | MISSED -> found (unchanged) | found |
| trunk | 21.50 | 1.0 | no | deficit median (deg) | -24.55 -> -25.55 -> -25.55 | -23.55 -> -25.55 -> -25.55 | null -> -20.55 -> **null** | -24.35 |
| | | | | deficit p95 (deg) | -24.50 -> -25.50 -> -25.50 | -23.50 -> -25.50 -> -25.50 | null -> -20.50 -> **null** | -24.30 |
| | | | | width missed: total (deg) | 0.0 | 0.0 | null -> 0.0 -> **null** | 0.0 |
| | | | | width missed: longest run (deg) | -- -> 0.0 -> 0.0 | -- -> 0.0 -> 0.0 | -- -> 0.0 -> **null** | 0.0 |
| | | | | verdict | found (unchanged) | found (unchanged) | MISSED -> found (unchanged; not resolvable, so found by construction regardless of the null deficit) | found |

Two verdicts move today. `chartyard-arc075-60`'s `roof-south` flips from
MISSED to **found**, and it takes both of today's commits together: task 5
(#71/#74) shortens the longest run (13.3 to 10.2 degrees, under the 12.0
degree `resolvable_width_deg` threshold) and shrinks the total (41.8 to
29.8), and task 2 (#64) is what made the verdict key on that run rather than
the total in the first place -- without it, 29.8 degrees of total
under-reporting would still exceed 12.0 and the row would still read MISSED.
Together they close this case's `no_missed_obstructions` gate (section 3.1);
the roof itself is still under-reported by 29.8 degrees, just no longer in
one run 12 degrees or wider. `chartyard-arc075-70`'s
`trunk` deficit goes from a measured `-20.55` to `null`: today's task 4
(#70) changed which of the nine accepted frames register and how the mosaic
is painted, and the bins over the trunk's azimuth are no longer resolved at
all on this case. The verdict does not move, because `trunk` was never
`resolvable` at this product's bin width and is `found` by construction
either way (its visible width, 3.0 degrees, is under one 12-degree bin).

The two width rows are issue #64, closed on 2026-09-19 after the readings
above were taken. The scorer reported one width figure until then, the TOTAL
of every under-reported tenth-degree bin, under the name `width_missed_deg`;
the contract's rule had always been "a stretch at least this wide". The figure
of that name is now the longest CONTIGUOUS under-reported run and the total
keeps its own name, `width_missed_total_deg`. A `--` above is a field that did
not exist at the earlier reading; the `ideal` column's runs are 0.0 by
construction, since a run cannot exceed a total and every ideal total is 0.0.

**The split changed no verdict and no gate on any of the three cases.** It
changes two numbers: the roof's figure on `still-60` (2.6 total, longest run
1.3) and on `arc075-60` (41.8 total, longest run 13.3). Section 4.3 has the
runs behind both. `arc075-60` stays MISSED because its longest run alone
clears the 12.0 degree `resolvable_width_deg`, and the review that measured it
found that run to be the whole of product bin 20 (azimuth 240 to 252) plus a
1.3 degree spill into bin 21: one entirely lost product bin, which is the
quantity the threshold is defined as.

`missed_obstructions`, at the 2026-09-19 issue-pass reading: still-60 `[]`
(was implicitly `pole-near, roof-south, wall-east` under the pre-#53 envelope
rule); arc075-60 `["roof-south"]`; arc075-70 `[]` (was `pole-near, pole-far`
under the same old rule, both of which the resolvability fix alone would have
already cleared). `no_missed_obstructions` was therefore PASS on still-60 and
arc075-70 and FAIL only on arc075-60, where the roof was genuinely, resolvably
missed by 41.8 of its 146.6 degrees.

**Today's residuals pass closes it, and it takes both of today's tracer/rule
commits together.** `missed_obstructions` is `[]` on all three cases now: task
2 (#64, `44543a32`) keys the miss rule on the longest contiguous run instead
of the total, and task 5 (#71/#74, `d6ee0bd7`) shortens arc075-60's roof's
longest run from 13.3 to 10.2 degrees, under the 12.0 degree
`resolvable_width_deg` threshold. Neither alone would flip it: at the old
total-based rule the roof's total (29.8 degrees today, itself down from 41.8)
still exceeds 12.0, and at today's run-based rule without task 5's fix the
run would still be 13.3. So `no_missed_obstructions` is PASS on every case in
the corpus today, but on arc075-60 that means the rule now reads a stretch
rather than the whole obstacle -- the roof is still under-reported by 29.8
degrees of total width, it is simply no longer one contiguous run 12 degrees
or wider. It is the only obstacle-verdict change of this pass; every other
obstacle's verdict in the table above is unchanged from the issue-pass
reading.

`wall-east` is the header result of issue #58: it is **found on every case**
now, where it was MISSED on every case in the 2026-09-18 reading. That is the
specific defect section 4.2 files against the scanner (a tracer with no
contrast sign reading a bright wall as open sky) and it is closed.

A `null` deficit meant the obstacle was visible and had no resolved measured
bin at all in the 2026-09-18 reading; four of chartyard-arc075-70's five
obstacles were `null` that way, because its boundary was unresolved over most
of the azimuths they occupy. Only `pole-near` is still `null` today, because
it sits inside the 19 bins (down from 25) that remain unresolved on that case
(section 4.5). The ideal's negative deficit on the trunk is the instrument,
not a fault: the ideal writes the envelope, and over the trunk's azimuths the
envelope is the canopy standing above it.

### 3.4 Overlay

Task 13 (#59, `0bcb62ca`) changed what the overlay error IS: the maximum of
the three angles between the reported and truth `forward`/`right`/`up`, not
`forward` alone (a `right`/`up` rotated about an untouched `forward` -- a pure
roll error -- was invisible to the old metric). `CONTRACT.md`'s `scores.json`
schema grew three fields as a result: `max_forward_deg`, `max_right_deg`,
`max_up_deg`, reported beside the combined maximum on both the moving and the
settled block, because the combined figure alone does not say which axis
moved. Cells read `before the issue pass -> after it -> after today's
residuals pass`.

| | still-60 | arc075-60 | arc075-70 | ideal |
|---|---|---|---|---|
| samples | 1053 | 1053 | 1053 | 1053 |
| missing fraction | 0.000 | 0.000 | 0.000 | 0.000 |
| duplicate frame ids | 0 | 0 | 0 | 0 |
| frames over their class gate | 0 | 0 | 994 -> 1025 -> **417** | 0 |
| moving samples | 460 | 460 | 460 | 460 |
| moving median (deg) | 0.000 (unchanged) | 0.000 (unchanged) | 5.382 -> 9.539 -> **0.000** | 0.000 |
| moving p95 (deg) | 0.000 | 0.000 | 7.842 -> 10.032 -> **1.734** | 0.000 |
| moving max (deg) | 0.095 -> 0.116 (unchanged today) | 0.095 -> 0.116 (unchanged today) | 8.369 -> 10.038 -> **1.741** | 0.000 |
| moving max, forward axis (deg) | field added by task 13 -> 0.095 (unchanged today) | -> 0.095 (unchanged today) | -> 10.038 -> **1.741** | -> 0.000 |
| moving max, right axis (deg) | -> 0.116 (unchanged today) | -> 0.116 (unchanged today) | -> 10.038 -> **1.734** | -> 0.000 |
| moving max, up axis (deg) | -> 0.114 (unchanged today) | -> 0.114 (unchanged today) | -> 10.037 -> **1.558** | -> 0.000 |
| settled median (deg) | 0.029 -> 0.063 (unchanged today) | 0.029 -> 0.063 (unchanged today) | 3.840 -> 9.612 -> **0.074** | 0.000 |
| settled p95 (deg) | 0.075 -> 0.092 (unchanged today) | 0.075 -> 0.092 (unchanged today) | 7.328 -> 10.102 -> **1.387** | 0.000 |
| settled max (deg) | 0.174 (unchanged) | 0.174 (unchanged) | 8.372 -> 10.118 -> **1.734** | 0.000 |
| settled max, forward/right/up (deg) | field added by task 13 -> 0.174 / 0.174 / 0.093 (unchanged today) | -> 0.174 / 0.174 / 0.093 (unchanged today) | -> 10.108 / 10.118 / 10.102 -> **1.635 / 1.734 / 1.576** | -> 0.000 / 0.000 / 0.000 |

The two 60 degree cases still report the same overlay figures as each other,
for the same structural reason as before: the overlay pose is built from the
orientation stream, which is identical across the three recordings, and
only `chartyard-arc075-70` moves. What moved on the two 60 degree cases at
the 2026-09-19 issue-pass reading was the metric itself, not the scanner: the
old moving maximum (0.095) is exactly `max_forward_deg`, and the combined
maximum (0.116) is carried entirely by the `right` axis, which the old metric
never looked at. Likewise the settled median rose from 0.029 to 0.063 and the
settled p95 from 0.075 to 0.092 purely because a genuine, small roll
contribution the old forward-only metric could not see is now counted
(section 4.4's parallax discussion is about a different, larger effect and is
unaffected). Today's residuals pass moves nothing on these two cases at all:
every overlay figure above is character-identical to the issue-pass reading,
confirmed by task 4's own controls measurement, because the bound task 4
adds only engages `if (registration.adjusted && rawBasis)` and neither
control case ever produces an adjusted registration.

**`chartyard-arc075-70` is the one case this pass's overlay work is about,
and it moves by roughly a factor of six today.** At the issue-pass reading its
moving and settled statistics all sat at or above 9.5 degrees against a gate
of 1.0/0.5: task 11's targeting change (#57) altered which frames that case
captures, and combined with the already-wrong assumed lens (section 4.5) the
registered pose disagreed with truth by nearly the full `overlay_max_lt_10`
budget, which no percentile could reach -- the gate this case newly failed
(issue #70). Today, task 4 (#70, `2b964638`) bounds the carried correction at
a derived limit (`carriedCorrectionMax`, 3.0 degrees on this recording's
uncalibrated 60 degree default) instead of the old flat 10 degrees, so a fit
too large for the lens is refused (`overlap-wait`) rather than worn into
every later pose. Every accepted frame on this case now carries a correction
of exactly 0.0000 (`captures.jsonl`, `sensor_basis` against `basis`), against
6.81 to 10.04 degrees before, and the overlay maxima fall from 10.038/10.118
to 1.741/1.734 -- a real fit still measured (the recording's two fitted
corrections were 6.81 and 9.94 degrees, matching the derivation's estimate of
a lens about 5.26 degrees wrong), just no longer carried once it exceeds what
the assumed lens can honestly absorb. `overlay_max_lt_10` flips FAIL to PASS
(section 3.1). The cost is one fewer accepted frame (10 to 9) and
`frames_over_gate` collapsing from 1025 to 417 of 1053 -- fewer frames now
disagree with truth by more than the settled gate, even though coverage in
painted area is slightly BETTER (section 3.6): the frames that remain are
placed where the sensor says they are. Not every number improves -- duplicated
landmarks rise 3 to 6 and slivers 1 to 2 (section 3.2), and the horizon's
signed median worsens 4.40 to 7.50 degrees while its p95 more than halves
(40.75 to 16.51, section 3.3) -- and the case still fails ten of fourteen
gates, because the wrong assumed lens itself (section 4.5) is untouched.
Issue #94, filed against this same fix, is the reason the anchor still moves
the overlay at all on this case: the bound limits how LARGE a carried
correction may be, not how LONG it is carried, and a 1.74 degree correction
set by a frame that was then refused is still worn through the rest of the
scan.

The still-60 and arc075-60 moving median and p95 are still far smaller than a
thousandth of a degree, for the same reason as the 2026-09-18 reading: the
orientation sampler runs on a 10 ms grid and the frames arrive on a 100 ms
one, so during a move a reading is emitted at the frame's own capture instant,
the scanner is then handed the truth pose and returns it, and the
interpolation between two readings -- the thing this gate is supposed to grade
-- is never exercised at all. That is a property of where the sensor's
emission instants fall relative to the frame grid, not of the scanner. The
`overlay_moving_p95_lt_1` cells for those two cases in section 3.1 are marked
accordingly. Read the maxima, not the medians, on those two rows.

The 2026-09-18 reading recomputed, outside `scores.json`, how many of the 460
moving samples on each 60 degree case were exactly zero (443, 96.3%) using a
`forward`-only dot-product-arccos over `result/events.jsonl` and
`truth/trajectory.jsonl`. That computation is numerically unstable near
`cos(angle) = 1` (the derivative of `arccos` diverges there), and a
same-shaped attempt for this refresh, extended to all three axes, produced an
internally inconsistent median (about 1e-6 degrees, against `scores.json`'s
own reported 3e-14) that does not survive its own sanity check. Rather than
publish a number known to be wrong, this refresh does not restate an
exactly-zero count: `scores.json`'s `moving.median_deg` (about 3e-14 degrees
on both cases, effectively floating-point noise) is the evidence that the
phase-locking mechanism is unchanged, and a corrected recount is left for
whoever next touches this section rather than guessed at here.

### 3.5 Capture

A hold is satisfied by the first unclaimed record in its window whose outcome
is `accepted` or `already-captured`. The split is reported so that a hold the
scanner declined because it already held that dome cell is visible as what it
is (issue #54). Task 11 (#57, `b1e629f1`) was the only commit of the issue
pass that touched capture eligibility -- it widened the zenith aim cone and
added nearest-cell targeting -- and its effect showed only on `still-60`'s
and `arc075-70`'s rows below; `arc075-60`'s capture numbers were unchanged by
it and are unchanged again today. Today's residuals pass adds one more mover:
task 4 (#70, `2b964638`) bounds the carried correction, which refuses one
frame on `arc075-70` that the issue-pass reading had accepted. Cells read
`before the issue pass -> after it -> after today's residuals pass`.

| | still-60 | arc075-60 | arc075-70 | ideal |
|---|---|---|---|---|
| holds | 48 | 48 | 48 | 48 |
| holds satisfied | 47 -> 48 (unchanged today) | 41 (unchanged) | 11 -> 12 -> **11** | 48 |
| of which captured | 21 (unchanged) | 20 (unchanged) | 9 -> 10 -> **9** | 48 |
| of which already covered | 26 -> 27 (unchanged today) | 21 (unchanged) | 2 (unchanged) | 0 |
| latency p95 (ms) | 859.0 | 859.0 | 859.5 (unchanged throughout) | 700.0 |
| latency max (ms) | 860 | 860 | 860 | 700 |
| accepted frames | 21 (unchanged) | 20 (unchanged) | 9 -> 10 -> **9** | 48 |

`still-60` satisfies all 48 holds, up from 47 at the issue-pass reading
(unchanged today): the last hold of the route, at azimuth 180 altitude 85, is
the one issue #57 named directly (section 4.6), and it is answered. Today's
residuals pass touches no capture number on `still-60` or `arc075-60` at all.

Latency is measured to the satisfying record whatever its outcome, and every
satisfied hold is answered well inside its own window:

| | still-60 | arc075-60 | arc075-70 |
|---|---|---|---|
| hold length (ms) | 1200 | 1200 | 1200 |
| lowest satisfying latency (ms) | 710 | 710 | 808 -> 795 (unchanged today) |
| satisfying records below 840 ms | 32 -> 33 (unchanged today) | 28 | 2 -> 3 (unchanged today) |
| satisfying records at 840 ms or above | 15 | 13 | 9 (unchanged today) |

Outcome histogram over `result/captures.jsonl`. Cells read
`before the issue pass -> after it -> after today's residuals pass`; no
arrow means unchanged.

| outcome | still-60 | arc075-60 | arc075-70 | ideal |
|---|---|---|---|---|
| alignment-wait | 183 | 191 | 187 -> 189 (unchanged today) | 0 |
| too-soon | 32 -> 33 (unchanged today) | 30 | 23 -> 30 (unchanged today) | 0 |
| already-captured | 26 -> 27 (unchanged today) | 21 | 2 (unchanged) | 0 |
| accepted | 21 | 20 | 9 -> 10 -> **9** | 48 |
| no-target | 2 -> 0 (unchanged today) | 0 | 18 -> 0 (unchanged today) | 0 |
| overlap-wait | 0 | 2 | 25 -> 33 -> **34** | 0 |
| stale-image | 0 | 0 | 0 | 0 |
| frame-already-captured | 0 | 0 | 0 | 0 |
| total records | 264 | 264 | 264 | 48 |
| records with `adjusted` true | 0 | 0 | 1 -> 2 -> **0** | 0 |

The clean result of task 11 is the `no-target` row: it dropped to zero on
every case at the issue-pass reading, from 2 (still-60) and 18 (arc075-70),
and stays zero today. `arc075-70`'s `overlap-wait` count rises again today
(33 to 34) and its `records with adjusted true` falls to zero: today's task 4
(#70) refuses the one frame whose correction exceeded the new bound rather
than adjusting and accepting it, which is also why accepted frames fall from
10 back to 9 and `already_captured`/`too-soon`/`alignment-wait` are all
unchanged -- the one frame this bound catches moves from `accepted` to
`overlap-wait` and nowhere else.

`stale-image` and `frame-already-captured` are the two refusals the
implementation review's P1 finding added: a capture now needs a delivered image
on every path, and one delivered frame is captured once. Neither fires on any
of these cases, which is the expected result and unchanged by this pass. The
replay drives the frame callback, where each callback is its own delivery, so
the new requirement is satisfied by construction; the outcomes are listed with
their zeros so that a future run which does trip them is visible as a change
rather than as a new row.

### 3.6 Coverage

Task 11's targeting change (#57) was the only commit of the issue pass that
touched how frames are accepted into the panorama, so `still-60` and
`arc075-60` -- where that change had no headline effect -- were unchanged at
that reading, and `arc075-70` improved because more of its aim attempts
landed on a real target instead of being refused outright (section 4.5).
Today, task 4 (#70) moves `arc075-70` a second time, by a smaller amount and
in the painted-area row only. Cells read `before the issue pass -> after it
-> after today's residuals pass`.

| | still-60 | arc075-60 | arc075-70 | ideal |
|---|---|---|---|---|
| panorama alpha fraction | 0.9941 | 0.9882 | 0.7698 -> 0.8008 -> **0.8134** | 1.0000 |
| observable fraction covered | 0.9941 | 0.9882 | 0.7698 -> 0.8008 -> **0.8134** | 1.0000 |
| cells covered fraction | 0.9890 | 0.9670 | 0.5714 -> 0.6374 (unchanged today) | 1.0000 |
| cells covered / cells total | 90 / 91 | 88 / 91 | 52 / 91 -> 58 / 91 (unchanged today) | 1 / 1 |

`chartyard-arc075-70` still fails `coverage_ge_0_95` by a wide margin (0.81
against 0.95): the improvement from task 11 narrowed the gap the wrong lens
opens (section 4.5) without closing it, and today's improvement from task 4
narrows it further still without closing it either -- one fewer accepted
frame (10 to 9, section 3.5) but the ones that remain are placed where the
sensor says they are, which is a net gain in painted area and no change in
which of the 91 dome cells are covered at all.

The first two rows are equal on every case for a structural reason, not a
coincidence: on these three routes the observable region is the WHOLE raster.
Both routes sweep the dome, so every one of the 1080 x 300 cells projects
inside at least one delivered frame, and `observable_fraction_covered` and
`panorama_alpha_fraction` are then the same fraction over the same set of
cells. Nothing in these numbers can therefore fail on the observable region
being computed wrongly, which is why the mask is graded directly by a test
(`tests/test_score.py`, `ObservableRegion`) on a trajectory that looks in one
direction only.

The ideal's `cells_covered_fraction` is not comparable with the other three:
its `summary.json` declares a single cell, because the ideal panorama is ray
cast rather than assembled from dome cells.

### 3.7 The committed fixture pair

Today's residuals pass (task 2, #64/#68, `44543a32`) committed a second,
much smaller pair of case directories into the repository itself:
`tools/photosphere_sim/fixtures/chartyard-shortpan-60` and
`-70`, 3 245 366 bytes for the pair. Unlike `cache/cases/`, which is
git-ignored and about 42 MB a case, these are tracked (`.gitattributes` pins
`tools/photosphere_sim/fixtures/** -text` so a Windows checkout with
`core.autocrlf=true` cannot silently corrupt the JSON and JSONL and break
every hash in `manifest.json`), so a clean checkout -- CI included -- can
replay them without rendering anything first. Issue #68, "the recorded cases
are git-ignored, so every replay-backed test skips in CI," is half closed by
their existence.

**What they are.** The same chart-yard scene and profile as the three cases
above, on a short six-aim route (`routes/shortpan.json`: three aims along the
horizon at azimuth 0, 24 and 48, then three at altitude 35 over sky the first
three have already painted, 18.9 s against the recorded routes' 105.3) at a
smaller frame (240 x 320, exactly the scanner's own analysis canvas size for
a 3:4 frame, so the replay's resampler neither shrinks nor magnifies) and a
lower frame rate (5 fps; measured to produce the identical 35 capture records
in the identical order as 10 fps, because the scanner grabs at most every
350 ms either way). The pair differs in exactly one field, `fov_short_deg`
(60 against 70), which is issue #52's own experiment: on this route the
wrong lens earns a run of 9 consecutive `overlap-wait` refusals against
`LENS_DOUBT_AFTER` = 6, and the cue names the camera view angle; the right
lens earns 0 and the cue never mentions the lens. `photosphereReplay.test.ts`
grades both directions and neither case skips.

**What they grade.** The chain runs end to end on real rendered frames (the
same Three.js renderer the recordings use, not a token file): frames in,
readings in, events out with every contract key, a capture log whose every
outcome is one the scanner can return with at least one `accepted`, dome
cells covered, a panorama with painted pixels, a `horizon.json` whose points
match its declared bins. Issue #52's cue fires on the wrong lens and does not
fire on the right one. A test run writes nothing into the repository -- both
fixtures still have no `result/` directory after being replayed, which this
refresh's own replay (below) confirms by having run entirely outside
`fixtures/`, per the README's explicit rule not to write into them.

**What they do NOT grade, on the fixture's own README's own words.** "Nothing
scores these cases. `shortpan` appears in no `sim score` invocation and no
Python test." Every accuracy quantity the three full-length recordings carry
-- landmark p95 and p99, omissions and duplicates, horizon p95,
`false_open_sr`, the obstacle verdicts, overlay alignment, coverage fraction,
capture latency -- is therefore ungraded by CI on this pair: "a regression
that degrades registration accuracy without stopping the chain passes all
four cases." The route is azimuth 0 to 60 at two altitudes only -- no 360
degree sweep, no zenith, no north wrap -- so the replay test's
`horizon.points.length === bins` is a shape check, not a horizon grade.
Every scanner-decided quantity the tests assert is a threshold, not a count,
by deliberate choice, which is the same choice that keeps the fixture from
catching accuracy drift. The refusal-run-reset case (a run of refusals broken
by an outcome that ends it) stays on the full-length recording, because the
fixture's wrong-lens run is never broken by anything.

**Scored anyway, for this refresh, so the document records what CI actually
grades versus what a reader might assume it grades.** Nothing in
`photosphereReplay.test.ts` calls `sim score` on this pair, so this is new
measurement, not a repeat of an existing CI check. Each fixture was replayed
into a scratch copy outside the repository (the README's rule against writing
into `fixtures/` directly) and scored with `python -m sim score
chartyard-shortpan-<60|70> --cases <scratch dir>`, at `app_commit`
`9109df9aeae7b9b0ae9a5f53f342379e2d606e4c-dirty`:

| | chartyard-shortpan-60 | chartyard-shortpan-70 |
|---|---|---|
| frames delivered / accepted | 95 / 4 | 95 / 2 |
| cells covered / 91 | 21 | 11 |
| landmarks found / omitted (of 40 expected) | 11 / 29 | 7 / 33 |
| landmarks error median / p95 (deg) | 0.040 / 0.164 | 2.676 / 5.446 |
| horizon | unresolved over the whole boundary (`unresolved_sr` 6.28, the full sphere's worth over an unswept route); `missed_obstructions`: `["roof-south", "wall-east"]`, both `resolvable`, neither seen by this route at all | same |
| holds satisfied / captured (of 6) | 6 / 4 | 2 / 2 |
| coverage (`observable_fraction_covered`) | 0.951 | 0.491 |
| `pass` | false | false |

This is the expected shape, not a regression: a six-aim, two-altitude route
was never meant to sweep the dome, resolve the horizon or find every
landmark, and the README says so. The only thing this pair is FOR --
end-to-end chain health and issue #52's cue in both directions -- is exactly
what `photosphereReplay.test.ts` grades, and this refresh's replay reproduced
the frame and event counts task 2's report records (95 frames, 407 readings
in, both cases) and logged 48 capture records apiece.

## 4. What the numbers say

Seven mechanisms, in the six sections below, account for every failing gate at
the first baseline (1a98a8d8, then re-measured unchanged at 9e3097fb on
2026-09-18). (Section 4.2 carries two: a tracer threshold met from both
sides.) Each is stated only as far as the evidence in the case directories
supports it.

**2026-09-19 refresh.** Task 12 (#58, `3fbd2039`) rewrote `traceSkyCoverage`
from a luminance-threshold tracer to one that finds the sky boundary by its
transition, which removes both of section 4.2's mechanisms outright -- they
described a component that no longer exists in the shipped code. Section 4.2
is kept below as a "what the first baseline said" record, because it explains
why the 2026-09-18 numbers were what they were, followed by the current
status. Section 4.1's mechanism (coarse binning) is untouched by this pass and
is, if anything, more load-bearing now: with 4.2's artefact gone, bin 25's
single-valued read of the near pole is the single largest contributor to
`still-60`'s remaining horizon error (section 3.3). Section 4.3's roof
narrative is partly superseded -- `still-60`'s roof is no longer missed -- and
its worked "coarse-bin failure" example is replaced with today's numbers.
Sections 4.4 and 4.5 are updated where the pass changed their evidence.
Section 4.6 is replaced in full.

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

**What the first baseline found, and what the same six bins say now
(2026-09-19, after task 12's tracer rewrite, `3fbd2039`).** The mechanism --
one number per 12 degree bin cannot describe a boundary that moves inside the
bin -- is untouched by this pass; only the tracer's per-column reading
changed, so four of these six bins now land close to their truth maxima:

| bin | azimuth range (deg) | truth max in bin (deg) | measured, 2026-09-18 (deg) | measured, 2026-09-19 issue pass (deg) | measured, 2026-09-19 residuals pass (deg) |
|---|---|---|---|---|---|
| 4 | 48 to 60 | 44.00 | 38.0 | 44 | 44 (unchanged) |
| 6 | 72 to 84 | 25.50 | 0.0 | 26 | 26 (unchanged) |
| 8 | 96 to 108 | 49.30 | 57.0 | 27 | 27 (unchanged) |
| 21 | 252 to 264 | 49.30 | 13.0 | 0 | **49** |
| 25 | 300 to 312 | 63.20 | 7.0 | 64 | 64 (unchanged) |
| 26 | 312 to 324 | 63.00 | 9.0 | 25 | 25 (unchanged) |

**Today's residuals pass (task 5, #71/#74) recovers bin 21.** Its measured
value moves from 0 to 49 against a 49.30 degree truth maximum -- almost
exact, and the same bin that carried the 252.0-to-253.3 azimuth sliver
section 4.3 discusses as the coarse-bin failure landing on the roof's own
edge. Confirmed directly from `truth/reference-horizon.json` and
`result/horizon.json` at this pass's HEAD, the same way the two columns
before it were built. This is also the bin behind `still-60`'s absolute
error p95 rising from 20.56 to 49.00 degrees (section 3.3): the new
tracer's azimuth-support rule finds a floating, dark departure at rows 42-47
of that column and correctly promotes it, but the single bin still holds one
value for its whole 12 degrees, so the same accurate reading is now reported
across azimuth this bin does not occupy at that altitude, which is the
mechanism this section is about, landing on a different bin than before.

Bin 25 went from badly *undershooting* the pole (7.0 against a 63.20 degree
peak) to reading it almost exactly (64), which is the coarse-bin failure this
section describes in its purest form: the new tracer correctly finds the top
of the pole, and because the bin holds one value for its whole 12 degrees,
that one accurate reading is now reported as the boundary for the five to six
degrees of open sky the pole does not occupy. This single bin is the largest
contributor to `chartyard-still-60`'s remaining horizon error (its deficit
against the true open-sky altitude nearby carries the case's 58.35 degree
absolute-error maximum, section 3.3). Bin 21 moved the other way, from an
already-wrong 13.0 to 0: that bin and its neighbour are where section 4.3's
roof edge now sits, addressed there. Bins 4, 6 and 8 improved because the
tracer no longer needs to cross a fixed luminance line partway up a gradual
sky (section 4.2).

### 4.2 One threshold, no contrast sign: dark sky reads as canopy, a bright wall as sky

**What the first baseline said (1a98a8d8 / 9e3097fb, 2026-09-18).** This
section is kept in full below because it is the record of the defect and the
evidence that led to fixing it. Task 12 (#58, `3fbd2039`) replaced the
threshold tracer this section describes with one that finds the boundary by
its transition; neither mechanism below exists in the shipped code any more,
which the current-status paragraph at the end of this section confirms with
today's numbers.

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
| palette colours whose luminance is below the threshold | 7 of 24 |

The seven are palette indices 0, 1, 2, 8, 9, 10 and 16, computed from
`sim/palette.py` under the scanner's own `luminance`. The nearest colour above
the threshold is index 3, `(0, 128, 128)`, at 89.73, four degrees of grey
clear of it.

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
brighter than the tracer's threshold, lift the background grey floor above it,
and, for the converse below, require every object colour to sit a stated
margin BELOW the sky level, so that a surface the tracer is supposed to find
cannot read as sky. The effect on the numbers is visible in the table:

| chartyard-still-60 horizon error, recomputed from the two files | median (deg) | p95 (deg) | max (deg) |
|---|---|---|---|
| all bins (matches `scores.json`) | +0.95 | 75.90 | 76.75 |
| with bins 0, 1, 2, 5 and 7 removed | +0.75 | 25.45 | 56.20 |

Removing them does not rescue the gate. It moves the failure from one cause to
another, which is the point of separating them.

**The converse, and this one is the scanner's.** `wall-east` is MISSED for the
same reason with the sign reversed, and it is not a chart artefact. Its 12.0
degrees of missed width on chartyard-still-60 is not an accumulation of
edges: it is one whole INTERIOR bin, bin 6, azimuth 72 to 84 -- one contiguous
run, so the same 12.0 under either reading of issue #64 -- while the wall
spans azimuth 48.0 to 132.0. There `result/horizon.json` reports altitude 0.0
against a truth profile of 24.5 to 25.5 degrees. The reason is in
`result/columns.json`: the wall's colour is `(120, 100, 80)`, luminance 103.7,
which is ABOVE the tracer's threshold of 85.4, and it fills the column to the
bottom of the frame -- the last 36 of the column's 101 samples are exactly
103.7. The whole column's minimum is 87.0, at index 45, in the sky above the
wall; no sample in it is below the threshold at all. `traceSkyCoverage`
therefore finds no drop and returns open sky over a solid wall.

The tracer has no contrast sign. It looks for a DARKENING, so any surface
brighter than seven tenths of the sky level is not an obstruction to it,
whichever way round the scene is lit. That is the converse of the dark-disc
artefact above: the same threshold, the same missing sign, once with the sky
too dark and once with the obstacle too bright. A scanner that reports open
sky over a wall it can see is wrong about the sky, not about the chart, so
this one is filed against the scanner: issue #58. Restricting the scene's
object colours (the stage B item above) removes it from these cases; it does
not remove it from a white wall in daylight.

chartyard-arc075-60 loses a different single bin to the same mechanism, bin 4,
azimuth 48 to 60, where its column's minimum is 103.7 exactly -- the wall's own
luminance, with nothing darker anywhere in the column.

**`false_blocked_sr` against a single number is the wrong comparison, and
today's residuals pass corrects it.** A 30-bin boundary that takes each bin's
maximum truth altitude necessarily over-blocks part of every bin an
obstruction only partly occupies, so `false_blocked_sr` has a non-zero floor
even for a perfect scorer -- and that floor is different on every case,
because it depends on which bins are resolved at all. Task 5's review
recomputed each case's own floor by writing its own binned truth maximum into
a scratch result directory, scored over that SAME case's own `unresolved`
mask rather than over the full boundary a fully-resolved case would have:

| case | own ideal `false_blocked_sr` | own ideal `false_open_sr` | own ideal horizon p95 (deg) |
|---|---|---|---|
| chartyard-still-60 | 0.5963 | 0.0000 | 54.35 |
| chartyard-arc075-60 | 0.5963 | 0.0000 | 54.35 |
| chartyard-arc075-70 | **0.1278** | 0.0000 | 24.05 |

`chartyard-arc075-70`'s own floor is far below the other two, not because its
tracer is better, but because 19 of its 30 bins are unresolved and excluded
from the mask both the measurement and this floor are scored over: a bin that
contributes nothing to either side cannot inflate either one. A single number
(0.5963, the still-60/arc075-60 figure, which an earlier round of this same
task's own report used for all three cases before its reviewer caught it)
overstates how much room `arc075-70` has to over-block, by more than 4.5x.
Read against its own floor, today's measured `false_blocked_sr` of 0.2050 on
`arc075-70` is 0.0772 OVER its own ideal; `still-60`'s 0.5202 is 0.0761 UNDER
its own ideal (it now under-blocks slightly, most of the case landing at or
below the honest per-bin answer); `arc075-60`'s 0.7411 is 0.1448 over its own
ideal, the largest excess of the three, and it is the residual issue #71's fix
does not close -- bin 21 of that case (azimuth 252 to 264, truth 0.00 to
49.30) still reads 0 and is not anchored to any self-standing run within
reach, recorded as a known limit below rather than hidden.

**Current status (2026-09-19, after `3fbd2039`).** Both mechanisms above are
gone. Re-reading the same five dark-disc bins against the new tracer's output
on `chartyard-still-60`:

| bin | truth max in bin (deg) | truth mean in bin (deg) | measured, 2026-09-18 (deg) | measured, 2026-09-19 (deg) |
|---|---|---|---|---|
| 0 | 11.15 | 11.09 | 87.0 | 12 |
| 1 | 12.45 | 10.67 | 87.0 | 14 |
| 2 | 45.50 | 42.12 | 86.0 | 46 |
| 5 | 24.50 | 23.61 | 76.0 | 25 |
| 7 | 25.60 | 25.57 | 72.0 | 26 |

Every one of the five now lands within about a degree of its truth mean,
where before it overshot by 45 to 76 degrees: the new tracer does not read the
zenith cap and R4 rings as a 90 degree obstruction. The converse is closed
too: `wall-east` is `found` (not `MISSED`) on all three cases in section 3.3's
obstacle table, where it was `MISSED` on all three at this reading's start.
Issue #58 is closed. What section 4.1 already flagged as the coarse-binning
mechanism -- now visible without the threshold artefact sitting on top of it
-- is the largest remaining source of horizon error on the cases that still
fail `horizon_p95_lt_1`.

### 4.3 The roof is missed on width, not on height

**What the first baseline said (1a98a8d8 / 9e3097fb, 2026-09-18).**
`roof-south` had a deficit median below zero in both 60 degree cases and was
MISSED in both, on the width term alone. (`wall-east` was MISSED on the width
term too, but for the different reason section 4.2 set out: one interior bin
where the tracer read the wall as sky.)

| case | obstacle | deficit median (deg) | width missed: total (deg) | min width (deg) |
|---|---|---|---|---|
| still-60 | roof-south | -0.85 | 16.6 | 10 |
| arc075-60 | roof-south | -5.00 | 40.8 | 10 |

Both figures in that column are totals. The scorer had no longest-run figure
at that reading; issue #64 added one afterwards, and the runs behind these
totals are enumerated below.

A negative median means the measured boundary was above the obstacle's own
silhouette over most of its span, which a thirty-bin boundary produces by
holding each bin's maximum. The width term counts the tenth-of-a-degree bins
where the measured boundary falls more than one degree below the obstacle.
Those bins come in runs, and every run on chartyard-still-60 sat against a
measured bin boundary -- an azimuth that is a multiple of 12 -- because that
is where a single-valued bin is furthest from a boundary that slopes across
it. The five runs summed to the 16.6 degrees above, four of them ordinary
straddles of three to five degrees at one end of a bin, and a fifth --
azimuth 252.0 to 253.3, the last 1.3 degrees of the roof falling inside bin 21
where the rest of the bin was open sky and the bin reported 13.0 against a
34.4 to 36.3 degree deficit -- called out as "the coarse-bin failure of
section 4.1 landing on an obstacle's own edge rather than a pole's."
`chartyard-arc075-60` lost 40.8 degrees to three wider, shallower runs
instead, which the parallax of the arc route rather than a bin edge produced.

**Current status (2026-09-19, after task 12's tracer rewrite, `3fbd2039`).**
`chartyard-still-60`'s roof is no longer missed: the total fell from 16.6 to
2.6, under the 12 degree `resolvable_width_deg` threshold, and the longest run
-- which is what `width_missed_deg` names since issue #64 -- is 1.3, so the
row is found under either reading. Four of the
five old runs are gone -- the new tracer's transition detection reads the
roof's own edge at most azimuths instead of overshooting or undershooting it
by several degrees -- and one new, narrower one appeared where the old
mechanism did not have a run at all:

| run | azimuth (deg) | width (deg) | measured bin | the bin reports (deg) | the roof's own silhouette there (deg) | worst deficit (deg) |
|---|---|---|---|---|---|---|
| 1 | 106.7 to 108.0 | 1.3 | 8 | 27.0 | 47.40 to 49.30 | 22.3 |
| 2 | 252.0 to 253.3 | 1.3 | 21 | 0.0 | 47.40 to 49.30 | 49.3 |

Run 2 is the same edge as the old baseline's fifth run, at the same azimuth
(252.0 to 253.3, the last 1.3 degrees of the roof inside bin 21): the coarse-bin
failure of section 4.1 landing on this obstacle's own edge is still exactly
there, and its worst deficit is now larger (49.3 against 36.3), because bin 21
itself moved from 13.0 to 0 under the new tracer (section 4.1's addendum).
Run 1 is new: bin 8 (96 to 108) now reads 27, close to the bin's own truth
mean, but 22.3 degrees short of the roof's silhouette in the 1.3 degrees where
the roof itself reaches into that bin. Both runs are the same shape --
1.3 degrees at a bin edge where the roof's silhouette and the bin's single
value disagree -- and together they cost 2.6 degrees, comfortably under the
resolvable width, which is why the obstacle now reads found.

`chartyard-arc075-60`'s roof was still missed at that reading, at a similar
total cost (41.8 against the previous 40.8 degrees), but in a different
shape: five runs instead of three, four of them narrow straddles at
consecutive bin boundaries (azimuth 192.0 to 233.4, bins 16 through 19, worst
deficits 1.85 to 3.35 degrees) and a fifth that spanned two whole bins, 20
and 21 (azimuth 240.0 to 253.3), where both bins reported 0 against a
silhouette of 47.4 to 62.05 -- the roof's own edge together with the same
252.0-to-253.3 sliver `still-60` also loses, but here the bin immediately
before it was wrong too, which the arc's parallax rather than a single edge
produced. That fifth run was 13.3 degrees, and it was what carried the
verdict under issue #64: the five runs were 13.3, 10.2, 7.2, 5.7 and 5.4, so
the longest on its own cleared the 12.0 degree threshold and the row stayed
MISSED without the other four.

**Today's residuals pass closes it, and it takes both of today's commits
together.** Section 3.3's own bin-change table records the direct cause of
the run shrinking: bin 20 (azimuth 240 to 252, truth 49.45 to 62.05) moves
from 0 to 64, an azimuth-support promotion of a floating, dark departure task
5's new tracer (#71/#74, `d6ee0bd7`) finds and anchors against the roof's own
self-standing run in the neighbouring bin -- this is #71's own worked
example, the case the issue was filed against. That removes bin 20 from the
run that used to span bins 20 and 21 together, leaving only the
252.0-to-253.3 sliver inside bin 21 (still reading 0, the same coarse-bin
failure section 4.1 describes) as the case's longest remaining run: total
falls from 41.8 to 29.8 degrees and the longest run from 13.3 to **10.2**.
But the run shrinking is only half of why the row flips: it is task 2 (#64,
`44543a32`) that made the verdict key on the longest run at all rather than
the total, earlier today. Without that rule change, this case's total of 29.8
degrees would still exceed the 12.0 degree `resolvable_width_deg` threshold
and the row would still read MISSED under the pre-#64 rule -- the roof is not
fully found, it is under-reported by 29.8 degrees in total, just no longer in
one contiguous stretch 12 degrees or wider. `chartyard-arc075-60`'s deficit
median is -5.95 today (was -4.50), still close to zero: the mechanism this
section is named for -- an obstacle can lose a chunk wider than its declared
minimum width and still keep a near-zero median, because an obstacle is found
when it is found and not when most of it is -- does not depend on which side
of the threshold the case lands, and today it lands on the found side by 1.8
degrees of run, while still losing more than double that in total width.

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

No task in this pass touches the camera model or landmark scoring, and this
section's numbers are unchanged in the 2026-09-19 refresh (section 3.2, 3.4):
translation parallax is a still-open item, not one this pass addressed.

### 4.5 The assumed lens on chartyard-arc075-70

The scanner starts with a short-axis field of view of 60 degrees and no stored
calibration, and `chartyard-arc075-70` differs from `chartyard-arc075-60` in
that field alone. The registration step fits a rigid rotation against the
existing mosaic, and a scale error is not something a rotation can absorb, so
the frames are refused rather than misplaced. Task 9 (#52, `88551099`) named
the view angle in the refusal cue without changing any of these numbers; task
11 (#57, `b1e629f1`) changed which frames get through at all by widening the
aim cone, which moved most of the rows below at the issue-pass reading.
Today's task 4 (#70, `2b964638`) adds a second, smaller move: bounding the
carried correction refuses one frame that used to be accepted, and the
overlay row is the one this task exists for. Cells read `before the issue
pass -> after it -> after today's residuals pass`.

| | arc075-60 | arc075-70 |
|---|---|---|
| overlap-wait outcomes | 2 (unchanged throughout) | 25 -> 33 -> **34** |
| no-target outcomes | 0 (unchanged) | 18 -> 0 (unchanged today) |
| accepted captures | 20 (unchanged) | 9 -> 10 -> **9** |
| dome cells covered | 88 (unchanged) | 52 -> 58 (unchanged today) |
| panorama alpha fraction | 0.9882 (unchanged) | 0.7698 -> 0.8008 -> **0.8134** |
| unresolved horizon bins | 0 (unchanged) | 25 -> 19 (unchanged today, confirmed via `unresolved_sr`) |
| unresolved solid angle (sr) | 0.0000 (unchanged) | 5.2360 -> 3.9794 (unchanged today) |
| landmarks found | 34 (unchanged) | 28 -> 26 -> **29** |
| landmarks duplicated | 0 (unchanged) | 2 -> 3 -> **6** |
| overlay settled p95 (deg) | 0.075 -> 0.092 (unchanged today) | 7.328 -> 10.102 -> **1.387** |
| gates failing | 5 -> **4** (today, task 5, #71/#74) | 11 -> **10** (today, task 4, #70) |

Issue #52's cue half closed at the issue-pass reading and is unaffected by
today's pass. The remaining accuracy cost was smaller than before but still
large at that reading: eliminating every `no-target` refusal (section 3.5)
let 6 more dome cells get covered and pulled panorama alpha fraction up
three points, but `chartyard-arc075-70` still failed 11 of 14 gates and
still failed `coverage_ge_0_95` by 0.15. Today, bounding the carried
correction (issue #70) closes `overlay_max_lt_10` and pulls panorama alpha
fraction up a further 1.3 points at the cost of one accepted frame, but
`chartyard-arc075-70` still fails 10 of 14 gates and still fails
`coverage_ge_0_95`, now by 0.14. The wrong assumed lens is still the reason a
scale error is not something the registration step's rigid rotation can
absorb; neither widening the aim cone nor bounding the carried correction
changes whether a mismatched scale lets a frame register, only which frames
are attempted and which corrections are worn once they do.

### 4.6 Holds without a capture or a cell

**What the first baseline said (1a98a8d8 / 9e3097fb, 2026-09-18).**
`every_hold_captured` failed on all three cases, and the latency gate passed
on all three. Classifying each hold by the best outcome recorded inside its
own window (accepted or already-captured if either satisfied it, otherwise
overlap-wait ahead of no-target ahead of alignment-wait, the most diagnostic
refusal present):

| what happened during the hold | still-60 | arc075-60 | arc075-70 |
|---|---|---|---|
| a capture was accepted | 21 | 20 | 9 |
| refused: the cell was already captured | 26 | 21 | 2 |
| refused: overlap-wait | 0 | 2 | 25 |
| refused: no-target | 1 | 0 | 10 |
| refused: alignment-wait | 0 | 5 | 2 |

The first two rows were the satisfied holds. What was left was one hold on
`chartyard-still-60`, seven on `chartyard-arc075-60` and thirty-seven on
`chartyard-arc075-70`. `chartyard-still-60`'s single unsatisfied hold was hold
47, azimuth 180 altitude 85, the last hold of the route at the top of the
upward sweep, open from 104066 to 105266 ms: its window held `alignment-wait`
at 104460, then `no-target` at 104860 and 105260. The hold did not read
`no-target` throughout -- the first attempt was still waiting on alignment,
and only once the aim settled did the scanner discover there was nothing at
altitude 85 to aim at -- which mattered for issue #57, because it was the
settled attempts that proved the cone was empty rather than merely unaimed.

**Current status (2026-09-19, after task 11's aim-cone fix, `b1e629f1`).**
`every_hold_captured` now passes on `chartyard-still-60` and still fails on
the other two. Re-classifying every hold the same way:

| what happened during the hold | still-60 | arc075-60 | arc075-70 |
|---|---|---|---|
| a capture was accepted | 21 | 20 | 9 -> 10 |
| refused: the cell was already captured | 26 -> 27 | 21 | 2 |
| refused: overlap-wait | 0 | 2 (unchanged) | 25 -> 33 |
| refused: no-target | 1 -> 0 | 0 | 10 -> 0 |
| refused: alignment-wait | 0 | 5 (unchanged) | 2 -> 3 |

`chartyard-still-60` now has zero unsatisfied holds, down from one: hold 47
(azimuth 180, altitude 85) is now satisfied by `already-captured` at 104860
ms, inside the same window as before. The zenith cell it targets was already
photographed by an earlier aim once the widened cone and nearest-cell
targeting gave that hold a real target to check against, where before the
cone held nothing at all and every attempt fell straight to `no-target`.
`chartyard-arc075-60`'s seven unsatisfied holds are the *same* seven holds
(3, 4, 36, 37, 38, 39 and 47) with the *same* classification (five
`alignment-wait`, two `overlap-wait`) as the first baseline -- task 11's fix
does not touch this case's failure mode, which section 4.4's parallax
discussion already explains. `chartyard-arc075-70`'s thirty-six unsatisfied
holds (down from thirty-seven) lose their `no-target` outcomes entirely, same
as `still-60`; the vacated attempts mostly become `overlap-wait` (25 to 33)
because the mismatched lens (section 4.5) still keeps registration from
completing once an attempt has something to aim at.

`chartyard-arc075-60` and `chartyard-arc075-70` share one holdout hold 47
itself: unlike `still-60`, both arc cases' hold 47 windows are
`alignment-wait` on all three of their capture attempts, at the same instants
(104460, 104860, 105260 ms) as the first baseline. The aim never settles
during that hold on either arc case, so the scanner never reaches the point
of checking whether a target exists there at all; the aim-cone fix cannot help
a hold that alignment itself never clears. At the issue-pass reading this was
a different failure from the one issue #57 named, not yet filed as its own
issue, and guessed above to be "consistent with, and may be the same
mechanism as, the near-field parallax section 4.4 describes reaching the
registration step on these two routes."

**Today's residuals pass (task 6, #63/#76, `9109df9a`) measured hold 47
directly instead of guessing, and the guess above was wrong.** It is not a
registration or parallax problem: every one of hold 47's three `alignment-wait`
records on both arc cases now carries `wait: 'no-pose'`, and the `separation`
term -- the one the guess above and issue #76's own hypothesis both pointed
at -- fires **zero times in the entire scan on both cases** (also filed as
issue #104: the term is reached by no test and no recording, dead code with a
guard's shape). The real mechanism is issue #63: from 104360 ms to the end of
the hold, both arc cases' per-frame cue reads "The sky here has nothing to
track, so nothing is being captured" -- the view is featureless, because from
the arc route's camera position at the zenith the frame has no gradient to
judge a shift by (the three cases' orientation streams are byte-identical;
only the camera position differs). At 104460 ms the silence is 374 ms, past
the strict rule's 250 ms freshness window and short of the 500 ms silent-settle
threshold, so all three cases -- including `still-60` -- record `no-pose`
there; that instant is a dead zone, not the defect. At 104860 ms the silence
is 774 ms and the vouched path is available: `still-60` takes it and records
`already-captured`, but the arc routes cannot, because a featureless frame
yields no continuity (`evidence.view` is null), so `forFrame` falls back to
the strict rule, which the 774 ms silence fails. This is not confined to hold
47: seven of `arc075-60`'s unsatisfied holds, including the whole altitude-70
band (holds 36-39), spend most of their window on the same featureless cue.

A gyro witness (`MotionStability`, the same shape as the video's own
`VisualStability`) is now wired into the scanner to cover exactly this gap --
consulted only while the video answers `featureless`, never over a genuine
measurement of motion or a stalled camera -- but neither recording carries a
`devicemotion` channel (issue #105), so `captures.jsonl` is byte-identical
before and after the change on all three cases and `every_hold_captured`
stays red on both arc cases for a reason this simulator cannot exercise: the
apparatus has no gyro to drive the fix with, not that the fix does not work.
Section 3.1 and the gate table above carry the same note.

Only `chartyard-still-60`'s (closed) failure was ever about the geometry of
the dome rather than about the scan; the two arc cases' remaining failure was
never about geometry either, and is now known to be about a view the camera
cannot judge rather than about parallax or registration.

## 5. Known limits of the instrument

These bound what any number above can mean.

| limit | why it matters |
|---|---|
| The chart yard is noise free, evenly lit and has no lens distortion. | Feature localisation is easier here than on any real frame. A p95 measured here is a floor for the same algorithm on a phone, never a prediction of it. |
| Frames arrive on a virtual clock at exactly 10 fps, none dropped, none late. | Capture faults, clock injection and provider faults are stage B. Nothing here exercises them. |
| The harness clock does not advance inside a frame callback. | The replay sets the virtual clock to a frame's delivery time and then runs the callback, so registration, mosaic painting and column extraction all cost zero milliseconds. Every timing figure in this document is measured on that clock: the 350 ms sample gate, the 600 ms registration gate and every capture latency in section 3.5. A scanner taking 300 ms per frame on a phone would produce numerically identical figures here. These are the instrument's timings, not the scanner's cost. |
| `pass` is the stage A gate set, not the spec's section 9. | `tools/photosphere_sim/CONTRACT.md`, "What stage A does not evaluate", names the rows of section 9 this instrument does not grade: the noise-free calibration row (FoV error, ray error), the qualified-noisy row, the calibration-confidence corpus, the loop-mismatch clause, and the no-fabricated-heartbeat clause of the capture row. `false_open_sr` is reported and not gated, so the safety-relevant row is covered only by the width term of an obstacle's `missed`. A green `pass` above is a claim about fourteen thresholds and nothing else. |
| The grab gate samples at 350 ms against a 10 fps stream, so it lands on every fourth frame. | The effective sampling interval is 400 ms, which is why each case logs 264 grab attempts over a timeline of about 105 s. Capture latency is quantised by that grid rather than by anything in the scanner's decision. |
| The replay drives the `requestVideoFrameCallback` path. | The interval fallback, the only path Firefox Android takes, is never executed by any case here, so neither the new `stale-image` refusal on that path nor issue #48's stillness timestamping is measured. A browser whose MediaStream `currentTime` does not advance now loses capture there, not only the stillness witness. Still open after the 2026-09-19 refresh (issue #48's device item; task 5, `512d1478`, closed everything else #48 asked for). |
| The overlay error on the two 60 degree cases is set by sampling phase. | Section 3.4. Where a reading was emitted at a frame's own capture time the reconstructed pose matches truth exactly, so the moving median and p95 collapse to floating-point noise and have moved between route revisions with no scanner change. The maxima are the informative figures on those rows. Unaffected by this pass, though task 13 (#59) changed what "the reconstructed pose matches truth exactly" is checked against, from `forward` alone to all three axes (section 3.4). |
| The raster is 1080 x 300 and both the result and the ideal are read from it. | The ideal column of section 3.2 is the quantisation floor: a perfect scanner scores p95 0.084 and max 0.266 degrees on this raster, so a difference below that is the raster, not the algorithm. Unchanged by this pass; the ideal was rebuilt for the 2026-09-19 refresh and reports the same figures. |
| The `min_width_deg` values were chosen when they only labelled a row. | The width term of `missed` was added afterwards, and those declared widths are now the difference between found and missed on the narrow obstacles. Task 10 (#53, `08cc613b`) closed the first half of this -- an obstacle is now graded by its actual visible width against the product's own resolution, not by its declared `min_width_deg`, which is why the two poles and the trunk read `resolvable: false` and can no longer be marked missed at all (section 3.3) -- but the declared values themselves have still not been re-derived from anything (issue #53, part 2, open). |
| The horizon tracer meets a dark sky and dark discs. | Section 4.2. **Closed by task 12 (#58, `3fbd2039`).** The tracer rewrite that fixed the bright-wall-as-sky defect also removed the dark-sky-and-dark-discs artefact this row described: section 4.2's current-status note shows the same five bins now landing within about a degree of truth. A stage B scene change is no longer needed to remove this specific artefact from the chart yard, though issue #71 shows the new tracer's own persistence rule can still misread a genuinely open patch as blocked on `arc075-60`, which is a new and different limit, not a survival of this one. |
| `begin` is offered repeatedly until the scanner accepts it. | The recorded `begin` action is an earliest time, not the instant the scan starts: `PhotosphereSweep.begin()` refuses silently until the compass is ready, and the production Start button is behind the same gate. |
| Determinism is established on this machine, not across machines. | Section 2.2: two independent builds of the still case agree byte for byte through the renderer, the recorder, the truth, the replay and the scorer. Spec section 8 asks for documented numerical tolerances rather than bit-identical rendering across GPUs, and no second GPU has been tried. Re-confirmed for the replay and scoring half of the pipeline at `3fbd2039` on 2026-09-19, and again at `9109df9a` for today's residuals pass (`chartyard-still-60`'s `summary.json` and `panorama.png` byte-identical between two independent replays); the renderer half was not re-run at either reading, since nothing in either pass touches it. |
| The new tracer's persistence rule can read a genuinely open patch as blocked. | Issue #71. At the 2026-09-19 issue-pass reading (task 12 fix round 1, section 3.3), `arc075-60`'s `false_open_sr` rose from 0.1544 to 0.2487 sr even though the same commit removed the dark-sky artefact on the other two cases. **Closed by today's residuals pass** (task 5, `d6ee0bd7`): comparing departures against the mosaic's own median, anchored against a self-standing neighbour, falls `arc075-60`'s `false_open_sr` to 0.0522 and flips `no_missed_obstructions` to PASS on that case (section 3.3). One residual is explicitly not fixed: `arc075-60` bin 21 (azimuth 252 to 264) still reads 0 and is not anchored to any self-standing run within `AZ_SLOP_ROWS` reach, worth at most 0.017 sr of the case's remaining 0.0522. |
| A soft sky-to-obstruction edge over about ten rows can still walk the tracer's local sky model into the obstruction. | Issue #74. **Closed for edges up to 16 rows by today's residuals pass** (task 5, `d6ee0bd7`, section 3.3): a wall behind an edge too soft for one column to notice leaving the sky is now found across the mosaic, exact to a 16-row edge (against 10 for a column alone) and under-reported rather than lost past that. A grey wall less than about 15 per cent darker than its local sky is still read as an exposure change (issue #102 names the residual precisely: a steep sky gradient confined to part of the compass can still publish a fabricated horizon), and a floating obstruction brighter than its own sky still reads as open sky (issue #99). |

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

Three scanner findings came out of these runs and were, at the 2026-09-18
reading, open against the scanner, not against the simulator. All three are
now **closed**, by the [photosphere issue pass](../../.superpowers/sdd/2026-09-18-photosphere-issue-pass/progress.md)
whose commits this refresh measures:

| issue | what it was | where it came from | status, 2026-09-19 |
|---|---|---|---|
| #57 | No dome cell lies within the aim cone at altitude 85, so a hold there can never capture: the zenith cap's cone is narrower than the gap to the next ring, the aim dot shows no target, and every frame is declined with no cue that says why. Widen the cap's cone or add a ring so every altitude has a target, and say in the cue when a direction has none. | Section 4.6 | **Closed**, commit `b1e629f1`. Every direction above the horizon now has a target; `chartyard-still-60` satisfies all 48 holds (section 3.5, 4.6). |
| #52 | The assumed lens. A short-axis field of view ten degrees from the truth costs most of the dome, and the cue on the refusal path tells the user to fix their aim instead of naming the camera view angle control. Read the refusal history that is already in the capture log. | Section 4.5 | **Closed** (cue half), commit `88551099`. The accuracy cost of the wrong lens itself is unchanged and is not what the issue asked for (section 4.5); the committed UI's own view-angle control exposure was filed separately (issue #69). |
| #58 | `traceSkyCoverage` reports open sky over a wall brighter than seven tenths of the sky level. The tracer looks for a darkening and has no contrast sign, so a bright surface filling the frame to its bottom edge is read as nothing at all. | Section 4.2 | **Closed**, commit `3fbd2039`. `wall-east` is `found` on every case (section 3.3); the tracer rewrite that fixed this also removed the dark-sky/dark-disc artefact section 4.2 filed as an instrument limit, not a scanner bug. |

Today's residuals pass closes six more issues this document measures, plus
half of a seventh:

| issue | what it was | where it came from | status, today |
|---|---|---|---|
| #64 | The obstacle width term counted scattered tenth-degree bins rather than requiring them to form a stretch, deciding `roof-south`'s verdict on close calls. | Section 3.3, 4.3 | **Closed**, commit `44543a32`. `width_missed_deg` is now the longest contiguous run and `width_missed_total_deg` the total; the verdict reads the run. Moved no gate on these three cases by itself (the row it was filed against had already been carried by the issue pass's tracer rewrite), but the split's numbers are what today's task 5 fix then moves further (section 3.3). |
| #68 | The recorded cases are git-ignored, so every replay-backed test skips in CI. | Section 3.7 (new) | **Half closed**, commit `44543a32`. A committed fixture pair now runs the replay chain and issue #52's cue on a clean checkout; nothing scores it (section 3.7). |
| #65 | `begin()` requires `compassReady`, so a browser with no `DeviceOrientationEvent` can never reach the manual overhead press #42 item 3 was supposed to reopen. | -- | **Closed**, commit `2b964638`. The refusal stays (there is no honest way to aim a scan without a bearing); the panel now says so plainly instead of asking the user to keep moving the phone. Does not change any case's score. |
| #70 | The carried visual anchor was clamped at the overlay gate's own 10 degrees, so a mis-set lens sat against the clamp and the whole overlay went with it. | Section 3.1, 3.4, 4.5 | **Closed**, commit `2b964638`. The clamp is now two derived regimes (uncalibrated: a fixed fraction of the lens's own field of view; calibrated: the registration fitter's own search ceiling), so a correction too large for the lens is refused rather than worn. `chartyard-arc075-70`'s `overlay_max_lt_10` flips FAIL to PASS, moving maximum 10.038 to 1.741 (section 3.4). Residual: issue #94, the anchor is bounded in size but not in how long it is carried. |
| #71 | A floating obstruction under 12 degrees tall with clear sky beneath it read as open sky under the tracer's persistence floor. | Section 3.3, section 5 | **Closed**, commit `d6ee0bd7`. `chartyard-arc075-60`'s `false_open_sr` falls 0.2487 to 0.0522, and its roof's longest missed run falls from 13.3 to 10.2 degrees. That alone does not flip `no_missed_obstructions` -- it takes task 2's #64 (which keyed the verdict on the run rather than the total) landing the same day for the shortened run to be what the gate reads. Residual: bin 21 of that same case (azimuth 252 to 264) still reads 0, worth at most 0.017 sr, not anchored to any self-standing neighbour within reach. |
| #74 | A soft sky-to-obstruction edge over about ten rows could still walk the tracer's local sky model into the wall. | Section 5 | **Closed for edges to 16 rows**, commit `d6ee0bd7`. A wall behind a soft edge is now found across the mosaic, exact to 16 rows (against 10 for a single column). Residuals #99, #100, #102 name what is still not fixed (a floating obstruction brighter than its sky, a bin far from the mosaic's pooled seed, a steep local sky gradient). |
| #63 | The featureless-hold witness kept only a reading the video already vouched for, so a phone that comes to rest on blank sky still read the compass as lost at 2 seconds. | Section 4.6 (new) | **Closed**, commit `9109df9a`. A gyro witness (`MotionStability`) now covers exactly this gap, consulted only while the video is featureless. No score changes on any of the three cases: none records a `devicemotion` channel (issue #105), so the fix is unexercised here, not inert. |

Also carried into stage B from the 2026-09-18 baseline, with what this pass
did and did not do about each:

| item | where it came from | status, 2026-09-19 |
|---|---|---|
| A scene change so the horizon tracer is not handed dark sky and dark discs above altitude 60, and a background grey floor above the tracer's threshold; and, for the converse, object colours held a stated margin below the sky level so a surface the tracer must find cannot read as sky. | Section 4.2 | Superseded for the dark-sky half: #58's tracer rewrite removed that artefact from the chart yard without a scene change (section 4.2's current-status note). Still open in spirit as a general scene-robustness item. Issue #71's own false-open failure mode (a floating obstruction under 12 degrees tall) is **closed by today's residuals pass** (`d6ee0bd7`); issues #99, #100 and #102 name the ones that remain (a floating obstruction brighter than its sky, a bin far from the mosaic's pooled seed, a steep local sky gradient). |
| Advance the virtual clock by each frame callback's measured cost, so that the sample gate, the registration gate and the capture latencies are timed against work that took time. Without it the instrument cannot tell a scanner that fits in a frame budget from one that does not. | Section 5 | Open. No task in this pass touches the harness clock. |
| Re-derive the declared `min_width_deg` values now that they gate (issue #53). | Section 5 | Half closed: task 10 (#53, `08cc613b`) made resolvability depend on an obstacle's actual visible width rather than its declared `min_width_deg`, which is why the two poles and the trunk are `resolvable: false` and can no longer be marked missed regardless of that declared value (section 3.3). The declared values themselves are still not re-derived from anything -- issue #53 part 2, open. |
| Exercise the interval fallback path, and answer the device questions in issue #48: the camera pipeline delay on Firefox Android, and whether `video.currentTime` advances with frame delivery for a MediaStream-backed element there. That question is now a release gate, because capture itself depends on it and not only the stillness witness. | Section 5 | Half closed: task 5 (#48, `512d1478`) fixed the vouching margin to one observation interval on the fallback path in the scanner's own logic. The device question itself -- what a real Firefox Android phone's camera pipeline and `video.currentTime` actually do -- was not and cannot be answered by this simulator; issue #48's device item stays open, and this instrument still never exercises the interval fallback path at all (section 5). |
| Decouple the overlay measurement from the sampling phase, so the moving percentiles measure the scanner rather than where the emission instants fall. | Section 3.4 | Open. Task 13 (#59, `0bcb62ca`) changed what is measured (the max of three axes, not `forward` alone) but not when it is sampled; the phase-locking mechanism itself is unchanged (section 3.4). |

**Open residuals, checked against GitHub at the time of this refresh** (`gh
issue view <n> --json state,title` on each; every issue below returned
`OPEN`, and every issue that returned `CLOSED` -- #57, #58, #52, #63, #64,
#65, #68 (half), #70, #71, #74 -- is covered in the tables above instead
rather than repeated here). Filed as findings surfaced during implementation
and review, per this project's rule that every defect found gets an issue at
the time it is found:

| issue | what it is | where it surfaces here |
|---|---|---|
| #48 (device item) | Whether a real Firefox Android phone's camera pipeline delay and `video.currentTime` advance the way the interval fallback path assumes. | Section 5: this instrument has no device and cannot answer it; a prior pass closed everything else #48 asked for in the scanner's own logic. |
| #53 part 2 | The declared `min_width_deg` values have never been re-derived from anything now that they are load bearing; task 10 (an earlier pass) fixed only which obstacles they can decide (resolvability), not what the numbers themselves should be. | Section 3.3, section 5. |
| #76 | The zenith hold on the arc routes refuses `alignment-wait` for its whole window while the still route captures it. | Section 4.6. Today's task 6 measured it directly and refuted its own filed hypothesis (the `separation` term of the alignment gate, suspected of misreading near-pole geometry): that term fires zero times in the whole scan on both arc cases (issue #104). The real mechanism is #63 (closed) reaching capture -- a featureless view denies the vouch path -- and a gyro witness now exists for exactly this gap, but stays unexercised because neither recording carries a `devicemotion` channel (issue #105). **Stays open, subject changed**, blocked on #105. |
| #90 | The stillness noise subtraction (#62's fix) can zero a real sample-scale witness on texture near the sampling grid's own resolution; the numbers claiming this does not matter in production come from a scratch simulation, not a measurement on a device. | Not measured by this instrument (noise-free chart yard, section 5); a real-device item, filed by task 3's reviewer. |
| #94 | The carried visual anchor (#70's fix) is bounded in size but never expires, so a mis-set lens still moves the whole overlay through a run of refusals that has already concluded the lens is wrong. | Section 3.4, section 4.5: `chartyard-arc075-70`'s overlay still reads 1.741 degrees where the controls read 0.174, entirely from a carried anchor set by a since-refused frame. |
| #95 | A refusal caused by the carried-correction bound on a CALIBRATED lens reaches the user as an aim instruction ("Return to a green patch..."), and `captures.jsonl` cannot tell that refusal from a view that genuinely did not match, because both record the same `overlap-wait` outcome. | Not exercised by any of the three cases (none has a calibrated lens); filed against the calibrated regime task 4's fix round 2 added. |
| #96 | Mutation testing's restore-from-copy step can intermittently fail with `OSError: [Errno 22]` on this machine, leaving a MUTATED source file in the working tree until the next md5 check catches it. | A tooling defect in this pass's own method, not in the scanner; hit at least twice (tasks 5 and 6) and now guarded by a retry and an entry/exit md5 check. |
| #98 | An ordinary azimuth-to-azimuth brightness difference -- an auto-exposure step that is not identical at every azimuth, or a patch of open sky merely dimmer than the rest of the compass -- publishes a fabricated horizon: 46 degrees at an exposure seam, 90 degrees for a dimmer arc. | Filed by task 5's reviewer against the round-0 tracer (comparing absolute levels between columns). Task 5's own round-1 fix (comparing departures instead of levels, section 3.3) appears to be exactly this fix on inspection of both the issue and the code, but the issue carries no closing comment and this refresh did not re-verify it against the issue's own repro script; left open here rather than asserted closed on inference. |
| #99 | A floating obstruction 6 to 11 rows tall that is BRIGHTER than its sky is permanently read as open sky, by the azimuth support's own dark-only rule (deliberate: the chart yard's sky has bright bands the same shape as a real floating obstruction). | Section 3.3's known-limits list ("What is not fixed"): open before today's pass and unchanged by it. |
| #100 | A bin whose sky is more than 32 per cent from the mosaic's pooled seed never seeds its own local model and publishes a blocked dome, certain -- pre-existing in the tracer this pass's task 5 replaced, measured in both directions, not introduced by it. | Section 5's known-limits table; open before today's pass and unchanged by it. |
| #102 | A steep sky gradient confined to part of the compass (roughly 25 per cent per 12 rows or more, near the horizon) publishes a fabricated 30-degree horizon -- the conservative direction, but still open. | Section 5's known-limits table, `EXPOSURE_TOLERANCE`'s own documented residual. |
| #104 | The `separation` term of `grabFrame`'s alignment gate is reached by no test and no recording -- one instance of "a harness that cannot reach the branch." | Section 4.6: found while measuring #76; the term is dead code with a guard's shape, before and after today's pass. |
| #105 | The simulator's cases record no `devicemotion` channel, so the second (gyro) witness built for #63 cannot be measured by replay at all. | Section 3.1, 4.6: this is why `every_hold_captured` stays red on both arc cases today even though the mechanism it was failing on (#63) is closed. |
| #106 | The gyro witness read a `rotationRate` of literal zeros as a still phone, so a stuck gyro (or an emulator/WebView that reports exact zeros rather than nulls for "no gyro") would vouch for a heading the phone has already left. | Found and closed within today's pass (task 6 fix round 1, before this document's HEAD): an exact zero triple is now treated as no measurement at all. Left in this list because it was filed as an issue and #48's device pass still needs to say whether any target browser spells "no gyro" this way. |
