# Photosphere simulator contract

Shared declarative conventions. Code is never shared across the boundary
between the simulator (`tools/photosphere_sim`, Python + Three.js) and the
process under test (`ui/src/next/hubs/sky/sheets`, TypeScript).

## Frames and units

- World axes: ENU, `x` east, `y` north, `z` up, metres. Azimuth is measured
  clockwise from north, altitude up from the horizontal plane:
  `sky_vector(az, alt) = [sin(az) cos(alt), cos(az) cos(alt), sin(alt)]`.
- Camera basis: three unit vectors `right`, `up`, `forward` in world
  coordinates. `right x up = -forward`; the proper rotation from camera to
  world has columns `[right, up, -forward]` (camera looks down its own -z, as
  in OpenGL). The image `x` axis is `right`, image `y` (rows increasing
  downward) is `-up`.
- Pinhole camera: `fx = fy = (short / 2) / tan(fov_short / 2)` where `short`
  is the shorter of width and height in pixels; `cx = width / 2`,
  `cy = height / 2`; pixel `(i, j)` has its centre at `(i + 0.5, j + 0.5)`.
  Projection of a camera-frame direction `(x, y, z)`, `z > 0`:
  `u = cx + fx * x / z`, `v = cy - fy * y / z`. No distortion in stage A.
- Device orientation (W3C DeviceOrientation): `R = Rz(alpha) Rx(beta) Ry(gamma)`
  maps device coordinates to world coordinates; device `x` points to the
  right of the screen, `y` to the top of the screen, `z` out of the screen.
  The rear camera at screen angle 0 has `right = R e1`, `up = R e2`,
  `forward = -R e3`. Ranges: `alpha` in `[0, 360)`, `beta` in `[-180, 180)`,
  `gamma` in `[-90, 90)`.
- Time: integer milliseconds on one virtual clock; `0` is the capture time
  of the first frame.

## Case directory

```
cache/cases/<case_id>/
  manifest.json
  input/
    frames/f000000.png ...      8-bit RGB PNG, width x height, top row first
    observations.jsonl
    actions.jsonl
  truth/
    scene.json  camera.json  reference.json  trajectory.jsonl
    landmarks.json  reference-horizon.json  holds.json
  result/                       written by the replay; scores.json and
                                report.html written by the scorer
  ideal/                        optional; `sim ideal` writes the result a
                                perfect scanner would have produced, ray cast
                                from this case's own truth, in the same shape
                                as `result/`
  corrupt/<name>/               optional; one per corruption applied, each a
                                copy of a result directory with exactly one
                                thing broken
```

`ideal/` and `corrupt/<name>/` are scored the same way `result/` is, by
pointing `sim score --result` at them; neither is an input to anything.

`cache/` is git-ignored: a full-length case is about 42 MB, almost all of it
frame PNGs. A case directory of the same shape may also live in `fixtures/`,
which IS committed, and `--out fixtures` is how `make-case` writes one there.
A fixture case is short and small enough to carry in the repository so that a
replay-backed test runs on a clean checkout and in CI (issue #68);
`fixtures/README.md` says what the committed pair is and how it was sized.
Nothing writes a `result/` into a committed case: a replay copies `input/` to
a temporary directory and writes there, because `replayCase` empties and
rewrites the `result/` of whatever directory it is given.

`manifest.json`: `{"schema":1,"case_id","seed","scene","route","camera":{width,height,fov_short_deg},"fps","expected":"positive"|"control","profile","hashes":{"frames","observations","truth"},"versions":{"three","chromium","webgl_renderer","python","numpy","pillow","app_commit"}}`.
Hashes are hex SHA-256; `frames` is the SHA-256 of the concatenated per-file
SHA-256 hex digests in filename order. `profile` is the case definition's own,
carried here so a result names the profile of the case directory it sits in
rather than of whatever `cases/` holds on the machine doing the scoring.
`versions` names every library on the path from a pose to a PNG:
`webgl_renderer` is the `GL_RENDERER` string the rendering page itself
reports, which is the only one of them no package manifest records, and it is
`null` for the flat renderer, as `three` and `chromium` are.

`observations.jsonl`, one object per line, sorted by delivery time:
- `{"kind":"frame","frame_id":"f000123","t_capture_ms":12300,"t_present_ms":12360,"width":480,"height":640,"file":"frames/f000123.png"}`
- `{"kind":"orientation","t_event_ms":12280,"t_receive_ms":12300,"alpha":..,"beta":..,"gamma":..,"absolute":true}`
Delivery time is `t_present_ms` for frames and `t_receive_ms` for events.

At an equal delivery time the two files disagree on purpose, and both are
right. The generator's sort is stable over the frame records followed by the
event records, so `observations.jsonl` lists the FRAME first; it is a file
ordering, and the recording is not claiming an arrival order it did not
measure. The replay dispatches the READING first, because that is what a
browser does: an orientation event lands in the task queue, which is drained
before the rendering steps, and `requestVideoFrameCallback` runs with the
rendering steps. A replay that took the file's order instead would hand the
scanner a pose history one sample short of the one a page would have had. So
a reader of `observations.jsonl` must not infer the delivery order within a
millisecond from the line order, and a driver must sort the merged stream with
readings ahead of frames at equal delivery times.

There is no `devicemotion` record, and no case can produce one. The scanner
gained a gyroscope witness for the view the camera cannot judge (issue #63):
on a featureless sky the video vouches for nothing, so a reading is held by a
`rotationRate` that stays quiet. Every recording predates that channel and
carries only frames and orientation, so in a replay the gyroscope is always
absent and always refuses. A hold that the recordings score as missed for
want of a pose may therefore be a hold a real phone would have captured, and
`every_hold_captured` cannot settle it either way until a recording carries
motion (issue #105). Issue #76's zenith holds on the arc routes are exactly
that case.

`actions.jsonl`: `{"t_ms":0,"action":"begin"}` and `{"t_ms":<end>,"action":"finish"}`.

`truth/camera.json`: `{"width","height","fov_short_deg","fx","fy","cx","cy","distortion":null}`.
`truth/reference.json`: `{"c_ref":[e,n,u]}` (metres), the position the finished
panorama describes: the camera centre at the first frame.
`truth/trajectory.jsonl`: per frame `{"frame_id","t_capture_ms","position":[e,n,u],"right":[..],"up":[..],"forward":[..],"az","alt","angular_rate_deg_s"}`.
`truth/landmarks.json`: `[{"id","colour":[r,g,b],"az","alt","observable":true|false,"kind":"background"|"surface"}]`
with `az`,`alt` the direction from `c_ref`.
`truth/reference-horizon.json`: `{"bins":3600,"alt_max":[3600 floats]}` (the highest
altitude at which a ray from `c_ref` hits any object, sampled every 0.05 deg
from -10 to 90; `-10` means nothing was hit) and
`"obstacles":[{"id","az_from","az_to","alt_max","min_width_deg","profile":[3600 floats]}]`.
`profile[b]` is the highest altitude at which THAT object is the FIRST thing
hit in bin `b`, and `-10` where it never is; `alt_max` is its maximum and
`az_from`/`az_to` the arc it is non-empty over. The per-object profile is what
makes an obstacle scoreable on its own: the envelope `alt_max` is the highest
of everything, so an obstacle standing under a taller one (the chart yard's
trunk under its canopy) never appears in it, and a boundary describing only
the canopy is indistinguishable from one that also found the trunk.
`truth/holds.json`: `[{"index","az","alt","from_ms","to_ms"}]`.

## Result directory (written by the replay, read by the scorer)

- `result/panorama.png`: 1080 x 300 RGBA, column `x` is azimuth `(x + 0.5) / 1080 * 360`,
  row `y` is altitude `90 - y / 299 * 100`; alpha 255 where the scanner
  painted, 0 elsewhere.
- `result/horizon.json`: `{"bins":N,"points":[{"az","alt"}],"uncertain_bins":[..]}` (`alt` 0..90; the scanner's own output).
- `result/columns.json`: the scanner's centre-ray columns, `centreColumns()`
  (array of arrays, NaN as `null`) - one 101-row column per azimuth bin, read
  at the bin's centre, unchanged in content from before issue #58's fix. The
  tracer itself now reads more than this file holds: `BIN_SAMPLES` columns
  across each bin, from the same panorama, the centre one first.
- `result/events.jsonl`: one line per delivered frame:
  `{"t_ms","frame_id","compass_ready","tilt_ready","aim":<cell id|null>,"basis":{"right","up","forward"}|null,"frame_count","cue"}`.
- `result/captures.jsonl`: the scanner's capture log, one line per attempt:
  `{"at","outcome","cell"?, "basis"?, "sensor_basis"?, "adjusted"?, "wait"?,
  "separation"?, "anchor"?}`. The last three belong to `alignment-wait` and
  were added for issue #76, because one outcome name covered three different
  refusals and a log of them said only that a hold did not capture. `wait` is
  `no-pose` (nothing could place the frame at all), `unsettled` (a pose was
  worn but the settle test found none) or `separation` (both poses exist and
  differ by more than 1.5 degrees); `separation` carries the degrees that last
  term measured, and `anchor` carries the size of the carried visual
  correction - the max-axis separation of the anchor PAIR itself, the raw sensor
  basis it was set from against the fitted basis it was set to, 0 where there is
  none. It is not the separation that transfer produces on this frame's pose,
  which is close but not equal. Every field is written only where the
  scanner set it, so a record that measured nothing claims nothing.
- `result/summary.json`: `{"frames_delivered","events_delivered","frames_accepted","cells_total","cells_covered","elapsed_ms","app_commit"}`.
  `app_commit` is the replaying tree's `git rev-parse HEAD`, with `-dirty`
  appended when `git status --porcelain` is not empty, and `null` when git
  cannot answer. The suffix is not decoration: a bare hash on a score taken
  from an edited tree names code that was never replayed, and that is the one
  error a reader cannot catch, because the hash resolves and the diff is gone.

## Resampling

The scanner draws the video into a canvas smaller than the frame, and the
replay's canvas stub resamples the frame to whatever size it is asked for by
exact area averaging: each destination pixel is the mean of the source
pixels under it, weighted by how much of each one falls inside it.

This is the simulator's idealisation and not a claim about any browser. What
a real `drawImage` into a smaller canvas does is implementation-defined --
the filter, whether it is separable, whether it runs on the GPU and in what
precision, all vary by engine and by scale factor -- so a replay measures the
scanner against a stated downscale, and the same scanner on a phone will see
slightly different pixels. The property that matters is that the downscale
AVERAGES rather than PICKS: a nearest-neighbour choice of one source pixel in
fifteen would make a still view flicker between neighbouring samples, and a
stillness grid built from single pixels would read sensor noise as movement.
A browser that picked would break the scanner; one that averages differently
moves the numbers a little.

## Commands (from the repo root unless stated)

Every `python -m sim` command runs inside `tools/photosphere_sim`.

- Python tests: `python -m unittest discover -s tools/photosphere_sim/tests -t tools/photosphere_sim -v`
- Build a case: `python -m sim make-case <case_id>`
- Replay through the real scanner: `node --import tsx src/next/hubs/sky/sheets/__sim__/replay.ts <abs case dir>` (run inside `ui`)
- Score: `python -m sim score <case_id>`, which also writes `report.html`
- The instrument's own floor: `python -m sim ideal <case_id>` writes `ideal/`,
  scored with `python -m sim score <case_id> --result <case dir>/ideal`
- Corrupt: `python -m sim corrupt <case_id> <corruption> [--param KEY=VALUE ...]`
- Re-render a page from a `scores.json` already on disk, without scoring
  again: `python -m sim report <case_id>`

`score` exits 0 when `pass` is true, 1 when it is false, and 2 for "I could
not run" -- a missing case, a missing result, or a truth the scorer cannot
read. A failing case and a broken invocation must not look alike on the way
out.

## Scene schema (`scenes/<name>.json`)

- `schema` 1, `name`, `seed` (int).
- `background`: `{"kind":"directional","distance_m":4000,"texture":{"kind":"value-noise","seed":int,"octaves":4,"cells":16,"grey":[70,150],"stripes":{"count":11,"tilt_deg":23,"grey":170,"width_deg":1.5}}}`.
  Texture recipe, on an equirectangular image of `W x H` (column `x` is
  azimuth `(x+0.5)/W*360`, row `y` is altitude `90 - (y+0.5)/H*180`):
  value noise = sum over octave `o` in `0..octaves-1` of `0.5^o` times bilinear
  interpolation of a lattice of `cells * 2^o` columns (wrapping in azimuth)
  by `cells * 2^(o-1) + 1` rows (clamped in altitude) whose value at column
  `i`, row `j` is `lattice(seed, o, i, j)`, an integer hash in `[0, 1)`
  that Python and JavaScript compute identically in uint32 arithmetic:
  `h = seed ^ (o * 0x9E3779B1) ^ (i * 0x85EBCA77) ^ (j * 0xC2B2AE3D)`;
  `h ^= h >> 16; h *= 0x7FEB352D; h ^= h >> 15; h *= 0x846CA68B; h ^= h >> 16`
  (every product truncated to 32 bits; JavaScript uses `Math.imul` and `>>> 0`);
  `lattice = h / 4294967296`. The octave sum lies in `[0, 1.875)`; divide by
  1.875 and map linearly to `grey[0]..grey[1]`. Stripes: `count`
  great circles through the points `(az = k * 360 / count, alt = 0)` tilted
  by `tilt_deg` from the vertical; every pixel whose angular distance to a
  stripe's great circle is below `width_deg / 2` is painted `grey`.
- `landmarks`: `[{"id","palette":int,"az","alt","radius_deg"}]` discs on the
  background, colour `PALETTE[palette]`, painted last (over stripes).
- `objects`: `[{"id","kind":"plane","z","colour":[r,g,b]}` | `{"id","kind":"box","min":[e,n,u],"max":[e,n,u],"colour"}` |
  `{"id","kind":"cylinder","base":[e,n,u],"radius","height","colour"}` | `{"id","kind":"sphere","centre":[e,n,u],"radius","colour"}]`.
  Object colours are never palette colours and have channel spread below 60.
- `surface_landmarks`: `[{"id","palette":int,"object":id,"centre":[e,n,u],"normal":[e,n,u],"radius_m"}]`
  flat discs lying on an object face, painted in the palette colour.
- `test_obstacles`: `[{"id","object":id,"min_width_deg"}]` the obstacles the
  horizon scorer must find individually.

## Route schema (`routes/<name>.json`)

`{"schema":1,"name","kind":"still"|"arc","pivot":[e,n,u],"radius_m","height_m","lift_m","aims":[...],"move_s","hold_s","sweeps":[...]}`.
- The camera centre for `still` is `pivot + [0, radius_m, height_m]` for the
  whole route (the phone is held where the first aim would put it and never
  translates).
- For `arc`: `C(t) = pivot + [radius_m sin(az(t)), radius_m cos(az(t)), height_m + lift_m * max(0, alt(t)) / 90]`;
  the camera is carried around the body at arm's length and rises as the
  view tilts up. At a hold or during a sweep's tilt, `az(t)`/`alt(t)` and the
  orientation are both simply the aim's own values, `look_basis(az, alt, 0)`.
  During a move between aims, the orientation follows the swing-twist
  rotation the `aims` bullet below describes (not `look_basis` applied to a
  moving `az(t)`/`alt(t)`), while `az(t)` for THIS formula's `sin`/`cos` is a
  separate, always-continuous azimuth that interpolates the two endpoint
  azimuths along the shorter arc under the same smoothstep fraction; `alt(t)`
  for the lift term is the orientation's own (forward-derived) altitude,
  which has no equivalent discontinuity to avoid.
- `aims`: ordered `[az, alt]` targets. Between consecutive aims the view moves
  as one rotation from the start's `look_basis(az, alt, 0)` to the end's,
  decomposed into a swing (the minimal rotation carrying the start's forward
  direction to the end's along their great circle) and a twist (the roll,
  about the resulting forward, needed to reach the end's exact right/up),
  both driven by the same smoothstep profile over the move's duration, then
  holds for `hold_s`. Holds are listed in `holds.json`. A move's duration is
  `move_s`, unless `theta = sqrt(swing_deg^2 + twist_deg^2)` exceeds `30 *
  move_s` degrees, in which case it takes `theta / 30` seconds instead, at
  the same smoothstep profile, so the combined rotation -- forward and roll
  together -- never averages more than 30 degrees per second. That duration
  is rounded UP to a whole millisecond, never to the nearest one: rounding
  down would shorten the move below what `theta` needs and put the route a
  fraction over its own rate budget. `hold_s` and a sweep's `duration_s` and
  `hold_s` round to the nearest millisecond, having no budget to breach.
  Every segment boundary is therefore an integer millisecond, which is what
  lets a later comparison of times be exact.
- `sweeps`: `[{"az","alt_from","alt_to","duration_s","hold_s"}]` appended
  after the aims: hold at `(az, alt_from)`, tilt linearly to `alt_to` over
  `duration_s`, hold again.
- `c_ref` is the camera centre at the first frame.

## Texture conventions (both implementations)

The Python truth (`sim/truth.py`) and the JavaScript renderer paint the
same background from the scene JSON. The recipe in the Scene schema leaves
four choices open; both implementations make them this way:

1. An octave's lattice has `columns = cells * 2^o` columns and
   `rows = columns / 2 + 1` rows, so its cells are square with row 0 at
   the zenith and row `rows - 1` at the nadir. A direction samples it at
   `u = az / 360 * columns` (wrapping: the column after the last is column
   0) and `v = (90 - alt) / 180 * (rows - 1)` (clamped to the last row),
   with bilinear interpolation between the four surrounding lattice values.
2. The grey value is truncated, not rounded:
   `floor(grey[0] + sum / 1.875 * (grey[1] - grey[0]))` where `sum` is the
   weighted octave sum and 1.875 is `2 - 0.5^(octaves - 1)` for four
   octaves.
3. A stripe is the great circle through `(az_k, 0)` with `az_k = k * 360 /
   count`, tilted by `tilt_deg` from the vertical; its pole is
   `n = [cos(az_k) cos(tilt), -sin(az_k) cos(tilt), -sin(tilt)]`, and a
   direction `d` is on the stripe when `|d . n| < sin(width_deg / 2)`.
   Every stripe leans the same way.
4. Where two landmark discs overlap, the EARLIER disc in file order wins in
   the truth; the texture therefore paints discs from the last declared to
   the first, so the painter's "later covers earlier" agrees with the ray
   caster. The chart yard's discs do not overlap (a test keeps it so);
   the rule exists for scenes that do.

The uint32 lattice hash is pinned by integer test values in
`tests/test_truth.py` (for example `lattice(7, 0, 0, 0) = 2492178918`); a
port must reproduce those integers, not merely a similar picture, because
dropping the final shift changes the picture by less than one grey level.

## Manifest hashes

`frames` is defined in the Case directory section. `observations` is the
SHA-256 of `input/observations.jsonl`'s bytes. `truth` is the SHA-256 of
the concatenated per-file hex digests of every file under `truth/`, in
filename order, the same construction as `frames`.

## scores.json

{"schema":1,"case_id","input_hash","app_commit","profile",
 "panorama":{"width","height","mode","note"|null},
 "landmarks":{"expected","found","omitted":[ids],"duplicated":[ids],"slivers","spurious","errors_deg":{"median","p95","p99","max"},
              "per_landmark":[{"id","observable","truth":{"az","alt"},"measured":{"az","alt"}|null,"error_deg"|null,"expected_px",
                               "status":"found"|"omitted"|"duplicate"|"sliver"|"not_observable"}]},
 "horizon":{"truth_bins":3600,"measured_bins","measured_resolution_deg","signed_error_deg":{"median","p95","max"},
            "false_open_sr","false_blocked_sr","unresolved_sr","note",
            "obstacles":[{"id","truth_alt_peak","deficit_median","deficit_p95",
                         "width_missed_deg","width_missed_total_deg","min_width_deg",
                         "visible_width_deg","resolvable","resolvable_width_deg","missed"}],
            "missed_obstructions":[ids],"north_offset_deg"},
 "overlay":{"samples","missing_fraction","frames_over_gate","duplicate_frame_ids",
            "moving":{"median_deg","p95_deg","max_deg","max_forward_deg","max_right_deg","max_up_deg"},
            "settled":{"median_deg","p95_deg","max_deg","max_forward_deg","max_right_deg","max_up_deg"}},
 "capture":{"holds","holds_satisfied","holds_captured","holds_already_covered","holds_with_capture",
            "latency_ms":{"p95","max"},"accepted_frames"},
 "coverage":{"panorama_alpha_fraction","observable_fraction_covered","cells_covered_fraction"},
 "gates":{"landmarks_p95_lt_0_5","landmarks_p99_lt_1","no_omissions","no_duplicates","horizon_p95_lt_1","no_missed_obstructions","no_unresolved_boundary",
          "overlay_settled_p95_lt_0_5","overlay_moving_p95_lt_1","overlay_max_lt_10","no_duplicate_frames",
          "capture_p95_le_1500","every_hold_captured","coverage_ge_0_95","pass"}}

Angles are degrees, areas are steradians, times are milliseconds. Every
percentile is `numpy`'s linear interpolation. A statistic with nothing to
average over is `null`, never 0: an absent measurement and a measurement of
zero are different claims.

`input_hash` is the SHA-256 of the manifest's `frames` and `observations`
hex digests concatenated, the same construction as those hashes themselves.
`app_commit` comes from `result/summary.json`, falling back to the manifest's
`versions.app_commit`. `profile` comes from the case definition.

### Landmarks

Blobs of each palette colour are decoded from `panorama.png` and turned into
directions by the raster mapping above. A blob is a landmark's when it is the
nearest landmark of that colour within 8 degrees; a landmark with one blob is
`found`, with two or more `duplicate`, with none `omitted`, and a blob that
is near no landmark of its colour is `spurious`.

- One raster cell spans `cell_az = 0.3333 * cos(alt)` degrees of true angle in
  azimuth and `cell_alt = 0.3344` in altitude, so a disc of angular radius `r`
  should cover `pi r^2 / (cell_az * cell_alt)` cells. A blob below 40 per cent
  of that is ignored as noise. The expected area is taken from the smallest
  landmark sharing the blob's colour, because the filter runs before the
  match: it must never be able to discard a landmark it has not identified. A
  background landmark's area is `pi radius_deg^2`; a surface landmark's is the
  flat disc's projected solid angle, `pi radius_m^2 |n . d| / D^2`, since
  leaving the foreshortening out would claim a grazing disc must be several
  times the size it can possibly be.
- A disc on a cylinder or a sphere is clipped again, because the truth paints
  a surface landmark only where the hit face's normal is within
  `acos(0.99)` of the declared one: on a host of radius `R` only a band
  `R sin(acos(0.99))` wide survives. A cylinder curves in one direction, so
  the expected area is scaled by `min(1, R sin(acos(0.99)) / radius_m)`; a
  sphere curves in both, so the same limit applies twice and the factor is
  `min(1, (R sin(acos(0.99)) / radius_m)^2)`. Without the clip the chart
  yard's `T1`, a 0.08 m disc on a 0.25 m trunk, is measured against a model
  it can fill only 44 per cent of and clears the filter by seven per cent.
- `per_landmark` carries all 52 landmarks in `landmarks.json` order, each with
  its `observable` flag and `expected_px`, the raster cells its disc should
  cover at its own altitude. Its `status` reconciles with the aggregates
  exactly: `found`, `omitted` and `duplicate` are the observable landmarks and
  are counted by `found`, `omitted` and `duplicated`; `sliver` is a
  non-observable landmark a blob was matched to and is counted by `slivers`;
  `not_observable` is the rest. `expected`, `found`, `omitted`, `duplicated`
  and `errors_deg` cover only the observable landmarks.
- A landmark whose centre is occluded can still show a clipped sliver of its
  disc, whose centroid is not its direction. That sliver is neither credited
  nor blamed and it is not `spurious`; `slivers` is how many there were.
- Omissions are reported as omissions and never dropped from `expected`.
- `panorama` records what was read. A file that is not a 1080 x 300 raster
  with an alpha channel is scored as EMPTY and `panorama.note` says which:
  converting an alpha-less image to RGBA would invent alpha 255 everywhere and
  hand a scanner that wrote the wrong format a perfect coverage score.

### Horizon

The truth is `reference-horizon.json`'s `alt_max` clipped to `[0, 90]`, which
`horizon.note` records: the scanner's floor is 0, and -10 in the truth means
no ray hit anything. Measured bin `i` of `N` covers
`[i * 360 / N, (i + 1) * 360 / N)`, addressed by index rather than by its
`az`, and each truth bin takes the measured bin its centre falls in.

- Bins in `uncertain_bins` are UNRESOLVED. They are excluded from
  `signed_error_deg`, `false_open_sr` and `false_blocked_sr`; their area is
  `unresolved_sr`, the cos-weighted solid angle of those azimuths over
  altitudes 0 to 90; and `no_unresolved_boundary` fails on them. Unknown
  counts separately and cannot satisfy coverage.
- `signed_error_deg.median` is the median of the signed errors, so it shows
  bias; `p95` and `max` are of the absolute error, because that is the
  quantity the gate is stated in.
- `false_open_sr` sums `cos(alt) * (0.1 deg)^2` over the 0.1 x 0.1 degree
  cells that the truth blocks and the measured profile leaves open;
  `false_blocked_sr` is the converse. Spherical area, not equirectangular
  pixels.
- `obstacles` carries every declared test obstacle, scored against its own
  `profile` and never against the envelope. Over the bins where the object is
  the first thing hit and the measurement is resolved,
  `deficit = clip(profile, 0, 90) - measured`; `deficit_median` and
  `deficit_p95` summarise it, and `min_width_deg` is the width the scene
  declares the obstacle must be found at. A bin is UNDER-REPORTED when its
  deficit exceeds 1.0, and the two width figures are both counts of those
  bins, 0.1 degrees each: `width_missed_deg` is the longest CONTIGUOUS RUN of
  them and `width_missed_total_deg` is all of them wherever they fall. The
  median rather than the minimum, because a bin at the edge of an obstacle
  straddles a coarse measured bin and swings either way without the obstacle
  being lost; and the width beside it, because the median cannot see a notch.
  The chart yard's roof spans 146 degrees, so cutting the declared 10 degree
  minimum width out of it leaves 1366 of 1466 bins right and the median at
  zero. An obstacle is found when it is found, not when most of it is. The
  width term costs a correct boundary nothing: a measured profile at or above
  the envelope has every deficit at or below zero, so both width figures are
  0.0 at every resolution. `missed_obstructions` is the ids of the missed ones.
- Issue #64: the verdict reads the RUN and never the total. "A stretch at
  least this wide" is what the rule has always said, and a stretch is what the
  product would hand a planner: the profile is interpolated between
  neighbouring azimuth bins, so a contiguous under-reported stretch is a
  false-open a route would be planned through, while a scatter of
  tenth-degree bins along a silhouette is edge jitter -- a different defect,
  which must not be able to add up into the first one's verdict. Azimuth
  wraps, so a run through north is one run; a bin that is not under-reported
  breaks a run, and so does a bin with no resolved measurement over it, since
  an unresolved bin is not evidence of a miss. `width_missed_total_deg` is
  reported beside the run so the jitter is still in the score, and
  `chartyard-arc075-60`'s roof-south is what the two figures look like when
  they differ: 41.8 degrees of total in runs of 13.3, 10.2, 7.2, 5.7 and 5.4,
  MISSED on the longest run alone against a 12 degree threshold.
- Issue #53: an obstacle narrower than one product bin is a width the product
  cannot represent at all, whatever the scanner does -- the scanner reports
  the horizon in a fixed number of azimuth bins (`PhotosphereSweep`'s own
  `bins`, 30 by default, 12 degrees each) and the planner interpolates that
  same resolution, so the measured profile at that azimuth describes the
  whole bin, not the obstacle's own narrow stretch of it. What decides this
  is the obstacle's own VISIBLE extent, `visible_width_deg` (the count of
  truth bins the object is the first thing hit in, times the truth's own
  step), never its declared `min_width_deg`: the chart yard's roof-south and
  wall-east both declare 10 while spanning 146.6 and 84.0 degrees, so gating
  on the label would call two wide, genuinely scoreable obstacles
  unresolvable. `product_bins` is the number of points in THIS result's own
  `result/horizon.json` (whatever the scanner that produced it used, not a
  constant); `resolvable` is `visible_width_deg >= 360 / product_bins`.
  `resolvable_width_deg` is `max(min_width_deg, 360 / product_bins)`, reported
  on every row regardless of `resolvable`, and it still sets the verdict's
  width threshold once an obstacle IS resolvable: the declared value can
  still hold a wide obstacle to a wider minimum than one bin, it just cannot
  be the thing that makes an otherwise-wide obstacle unresolvable. `missed`
  is `resolvable and (deficit_median > 1.0 or width_missed_deg >=
  resolvable_width_deg)`, and, for a resolvable obstacle, true also when it
  is visible but has no resolved bin at all. An obstacle that is NOT
  resolvable is always `missed: false`: `deficit_median`, `deficit_p95` and
  both width figures are still computed and reported, but stay informational,
  because they describe a comparison the product's own resolution makes
  meaningless, not a fault in the scanner. On the chart yard the two poles
  and the trunk (visible widths 2.2, 0.3 and 3.0 degrees) are never
  resolvable at the product's 12 degree bin; the roof and the east wall are,
  regardless of their declared 10. Issue #58 was one instance of the width
  term firing on a resolvable obstacle: on `chartyard-still-60` the wall's
  measured bin centred at azimuth 78 (covering 72 to 84) reported open sky
  against a wall the truth puts at about 25 degrees there, a whole product
  bin lost in one contiguous run (`width_missed_deg` 12.0 at
  `resolvable_width_deg` 12.0), and `no_missed_obstructions` was correctly
  false on that case. The tracer fix `3fbd2039` closed it: `wall-east` is
  found on all three cases today, with both width figures 0.0 on that case,
  and the paragraph is kept as the worked example of a real width miss.
- A result with NO boundary at all -- no `result/horizon.json`, or one whose
  `points` array is empty -- has `product_bins` 0, and there is then no
  product resolution to defer to. Nothing is excused: `measured_bins` is 0,
  `measured_resolution_deg` is `null`, every obstacle is `resolvable: true`,
  `resolvable_width_deg` falls back to the scene's declared `min_width_deg`
  (`null` where the scene declares none), no bin is resolved so a visible
  obstacle's two deficit and two width figures are all `null`, and every
  VISIBLE obstacle is `missed` (an obstacle visible in no bin at all keeps
  its 0.0 widths and is not missed, exactly as at any other resolution).
  A scanner that reported no boundary has not found the obstacles,
  and suppressing the verdict for want of a bin width would let a silent
  scanner score better than a wrong one.
- Scoring an obstacle against the envelope scores whatever is tallest at those
  azimuths. The chart yard's trunk stands under its canopy, so the envelope
  over the trunk's span is 45.8 degrees against the trunk's own 21.5: a
  boundary that reports 21.5 has found the trunk, and the envelope rule calls
  it missed, while a boundary that reports only the canopy has not found the
  trunk at all and the envelope rule calls it present.
- `north_offset_deg` is the shift in `[-10, 10]` degrees, in 0.1 steps,
  minimising the mean absolute signed error, positive when the measured
  profile is turned east. Ties go to the smaller shift.

### Overlay, capture, coverage

- Overlay error is the MAXIMUM of the three angles between an `events.jsonl`
  line's `basis.forward`/`right`/`up` and that frame's truth
  `forward`/`right`/`up`. Issue #59: `forward` alone cannot see roll -- a
  basis whose `right` and `up` are rotated about `forward` by any amount,
  with `forward` itself untouched, is a perfect match under a forward-only
  metric, which is exactly what `sim.corrupt`'s `roll` produces and the
  production registration never fits. `max_forward_deg`, `max_right_deg` and
  `max_up_deg` report each axis's own worst angle beside the combined
  maximum, because the combined figure alone does not say which axis moved.
  A frame is `moving` when its truth `angular_rate_deg_s` exceeds 2, else
  `settled`. `missing_fraction` is the lines that could not be scored over the
  lines considered: a null basis, and a `frame_id` the case never delivered,
  which is a sample with no pose and cannot be dropped from the denominator
  either. `max_deg` and `frames_over_gate` (samples above their own class's
  gate, 1.0 moving and 0.5 settled, read on the combined maximum) are
  reported beside the percentiles, because one frame in a thousand pointing
  three degrees wrong moves no percentile at all. `frames_over_gate` is
  informational; the gate over a single sample is `overlay_max_lt_10`.
- A `frame_id` on more than one line is delivered more than once. The FIRST
  line for an id is the one scored and the later ones are not considered at
  all: they are not samples, and they are not in `missing_fraction`'s
  denominator. `duplicate_frame_ids` is how many ids appeared more than once,
  and `no_duplicate_frames` reads it. Counting a repeat as another sample
  would let a scanner raise its own sample count by reprocessing a frame, and
  scoring the later line would let a second answer overwrite the answer
  already given for that frame.
- Holds are walked in `from_ms` order and each takes the first
  `captures.jsonl` record with `from_ms <= at <= to_ms + 1500` that no earlier
  hold has claimed and whose `outcome` is `accepted` OR `already-captured`;
  the latency is `at - from_ms`, to the satisfying record whatever its
  outcome. The claim matters: the 1500 ms grace makes consecutive windows
  overlap by most of a hold, so without it one record answers for two holds.
  `holds_captured` and `holds_already_covered` split the satisfied holds by
  which outcome satisfied them, `holds_satisfied` is their sum, and
  `holds_with_capture` is that same sum under its original name, which is
  what the gate reads. `accepted_frames` is every accepted record in the log.
- `already-captured` satisfies a hold because the route revisits directions.
  An aim can land on a dome cell an earlier aim already photographed, and a
  scanner that declines to photograph it a second time is behaving correctly.
  The gate `every_hold_captured` therefore asks that every hold ended in a
  photograph or in a cell already photographed, not that every hold produced
  a new frame. Counting only `accepted` failed 26 of the real still case's
  holds for correct behaviour (issue #54).
- `panorama_alpha_fraction` is the cos-weighted fraction of raster cells with
  alpha 255. `observable_fraction_covered` is the same fraction over the
  observable region: the cells whose direction from `c_ref` projects inside at
  least one frame of `trajectory.jsonl` under `camera.json`, ignoring
  parallax, and not below altitude -10. The observable region comes from the
  scene and the delivered frames, never from the subset the scanner accepted.
  `cells_covered_fraction` is `summary.json`'s `cells_covered / cells_total`.

### Gates

Thresholds are the provisional gates of
`docs/ui-rebuild/16-photosphere-calibration-simulator.md` section 9 and are
held fixed for comparability: landmarks p95 below 0.5 and p99 below 1.0
degrees; horizon p95 below 1.0; overlay p95 below 0.5 settled and 1.0 moving;
capture p95 at most 1500 ms with every hold captured; coverage at least 0.95
of the observable region. Two gates are this simulator's own rather than
section 9's, because section 10 requires the corruptions they catch to
produce a failing result: no overlay sample 10 degrees or more out
(`overlay_max_lt_10`, which no percentile can reach) and no frame delivered
twice (`no_duplicate_frames`). `pass` is the AND of every other gate; there
are fifteen names in all.

A gate over no evidence fails where evidence was expected: a blank panorama
fails the landmark gates, a wholly uncertain boundary fails the horizon gates,
and no capture log fails `every_hold_captured`. A gate is vacuously true only
where the route itself produced no such group, which `overlay.samples` and
`capture.holds` distinguish.

**What stage A does not evaluate.** `pass` is the AND of the fourteen gates
above and means the stage A gates only. It is not the spec's section 9, and
these rows of that table are not evaluated here at all:

- *Noise-free, well-observed calibration chart* (FoV error at most 0.25
  degrees, p95 ray error at most 0.1 degree). Stage A produces no calibration
  at all: the scanner runs on its assumed lens, nothing estimates a field of
  view, and there is no per-ray residual to take a percentile of. This is the
  row `chartyard-arc075-70` would be measured against, and all that case can
  say today is what an uncorrected ten-degree error costs downstream.
- *Qualified noisy handheld scene*. The chart yard is noise free and
  undistorted and there is no noisy profile to run.
- *Calibration confidence qualification*. A corpus gate: at least 100
  independently seeded held-out scans with an interval-coverage figure.
  Stage A has three cases and no intervals.
- The loop-mismatch clause of *Loop closure and qualified horizon*. The
  horizon half of that row is gated (`horizon_p95_lt_1`,
  `no_missed_obstructions`, `no_unresolved_boundary`); loop mismatch is not
  measured.
- The "no fabricated orientation heartbeat" clause of *Capture after a valid
  hold*. The latency half is gated (`capture_p95_le_1500`); nothing here
  checks that a capture was not certified by a reading the scanner invented.
- *Safety-relevant confidence* is covered only in part. `false_open_sr` is
  computed and reported and NOT gated; what stands in for that row is the
  width term of an obstacle's `missed`, which fails a positive case when a
  RESOLVABLE declared test obstacle is lost over one CONTIGUOUS stretch of at
  least its own `resolvable_width_deg` (issue #64; scattered under-reported
  bins are reported in `width_missed_total_deg` and gate nothing). A
  false-open area that falls on no declared
  obstacle passes every gate here while being reported, and so does one that
  falls only on an obstacle whose own visible silhouette never reaches one
  product bin (issue #53): on the chart yard that is the two poles and the
  trunk, not the roof or the east wall, which are wide enough to be graded
  regardless of their declared width.

A green `pass` on a stage A case therefore says the mathematics is right on
that recording, against those fourteen thresholds. It says nothing about
calibration, noise, loop closure, heartbeat honesty, or false-open area away
from a declared obstacle.

## Corruptions

`python -m sim corrupt <case_id> <name> [--param KEY=VALUE ...]` copies a
result directory, breaks exactly one thing in the copy, and leaves everything
else byte for byte, so the difference between the two scores is the
corruption and nothing else. The copy does not carry `scores.json` or
`report.html` over: those describe the result that was corrupted. The names
and their parameters, spec section 10:

| name | parameters | what it does |
|---|---|---|
| `yaw` | `deg` | turns raster, boundary and overlay east by `deg` |
| `roll` | `deg` | rotates every event's `right`/`up` about its own `forward` by `deg`, `forward` untouched |
| `north-wrap` | | `yaw` with `deg = 350`, which is 10 degrees west |
| `focal` | `scale` | every altitude to `atan(scale tan alt)` |
| `flip-vertical` | | reverses the raster's rows |
| `mirror` | | reverses the columns, `az -> 360 - az` |
| `duplicate-section` | `az0`, `width` | copies a wedge over the next one |
| `remove-section` | `az0`, `width` | unpaints a wedge, its bins uncertain |
| `wrong-reference` | `offset_m` | re-renders the panorama from `c_ref + offset` |
| `blur` | `radius_px` | box blur of the colours, alpha untouched |
| `erase-landmarks` | | paints every palette-coloured pixel grey |
| `empty` | | alpha 0 everywhere and an empty capture log |
| `erase-horizon-strip` | `alt_max` | unpaints below `alt_max`, all bins uncertain |
| `brightness` | `gain` | scales the colours: the geometry-preserving control |
| `substitute-pose` | `back`, `index` | one event carries an earlier event's basis |
| `duplicate-frame` | `index` | one `frame_id` on two event lines |

`report.html` is written beside `scores.json` by `sim score`, and
`python -m sim report <case_id>` re-renders it from a `scores.json` that is
already there. It is one self-contained file: inline base64 PNGs, inline SVG,
one style block, no script and nothing to fetch.
