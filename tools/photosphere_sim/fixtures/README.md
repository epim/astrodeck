# Committed fixture cases

Two case directories that are IN the repository, so the replay-backed tests
run on a clean checkout and in CI. The full-length recordings under `cache/`
are git-ignored (`.gitignore`, `tools/photosphere_sim/cache/`) and are about
42 MB each, which is why they are not; issue #68 is that every test standing
on them therefore ran on one machine and nowhere else.

| case | scene | route | frames | camera | `fov_short_deg` | bytes |
|---|---|---|---|---|---|---|
| `chartyard-shortpan-60` | chartyard | shortpan | 95 | 240 x 320, 5 fps | 60 | 1 575 845 |
| `chartyard-shortpan-70` | chartyard | shortpan | 95 | 240 x 320, 5 fps | 70 | 1 669 521 |

3 245 366 bytes for the pair, of which 2.69 MB is the 190 frame PNGs, 0.41 MB
the two `truth/` directories and 0.14 MB the observation streams.

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
On this route the wrong lens earns a run of 9 consecutive `overlap-wait`
refusals and the cue names the camera view angle; the right lens earns none
and the cue never mentions the lens. `photosphereReplay.test.ts` grades both,
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
