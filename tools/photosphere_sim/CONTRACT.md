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
- `{"kind":"motion","t_event_ms":12290,"t_receive_ms":12300,"rate":{"alpha":..,"beta":..,"gamma":..}}`
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

`motion` is the SECOND witness (issue #105). The scanner gained a gyroscope
witness for the view the camera cannot judge (issue #63): on a featureless sky
the video vouches for nothing, so a reading is held by a `rotationRate` that
stays quiet. Before this channel existed no recording carried one, the
gyroscope was absent in every replay and always refused, and a hold the
recordings scored as missed for want of a pose might have been a hold a real
phone would have captured - `every_hold_captured` could not settle it either
way.

Unlike `orientation`, `motion` is NOT change-driven: it is emitted on every
tick of its 60 Hz grid whether or not anything moved, because that is what a
`devicemotion` stream does and it is the whole reason the witness works. A
phone holding still keeps producing these while the orientation stream goes
silent.

The three numbers are the rate of turn about the DEVICE's own axes, in degrees
per second, which is not the rate of change of the Euler angles: d(alpha)/dt
diverges near the poles while the phone turns perfectly steadily, and these
routes end at the zenith. They are taken from the rotation of the device frame
between the attitudes half a period either side of the sample, so the vector's
magnitude is the trajectory's own `angular_rate_deg_s` - which is the only
quantity the scanner reads, since `MotionStability` compares
`hypot(alpha, beta, gamma)` against a threshold. Measured against
`truth/trajectory.jsonl` over the arc route: median error 0.0001 deg/s,
and exactly 0 through a hold.

A case declares `gyro_noise_deg_s`, Gaussian per axis. The default is 0,
which makes a hold read EXACTLY zero - and the scanner discards an exact zero
triple as synthetic (`MotionStability.observe`, issue #106: a real MEMS gyro has
a noise floor and never reports one twice). So an exact stream does not model a
quiet gyro, it models a dead one, and at every held attitude the witness goes
stale. Every chart-yard case therefore sets 0.05 deg/s, a phone gyro's floor at
60 Hz: a sample's magnitude near 0.09 against `QUIET_RATE_DEG_S` 0.5, and a
random-walk drift near 0.007 degrees a second against `QUIET_DRIFT_DEG` 0.5. A
case that wants a gyro too noisy to vouch for anything raises it; issue #76 is
the measurement that forced this (arc075-60, 43 to 46 of 48 holds).

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
- `result/horizon.json`: `{"bins":N,"points":[{"az","alt"}],"uncertain_bins":[..]}` (`alt` 0..90; the scanner's own output). A result with `"version":2` is the pan scanner's 720-bin profile and polyline, described under "Version 2".
- `result/columns.json`: the scanner's centre-ray columns, `centreColumns()`
  (array of arrays, NaN as `null`) - one 101-row column per azimuth bin, read
  at the bin's centre, unchanged in content from before issue #58's fix. The
  tracer itself now reads more than this file holds: `BIN_SAMPLES` columns
  across each bin, from the same panorama, the centre one first.
- `result/events.jsonl`: one line per delivered frame:
  `{"t_ms","frame_id","compass_ready","tilt_ready","aim":<cell id|null>,"basis":{"right","up","forward"}|null,"frame_count","cue"}`.
- `result/captures.jsonl`: the scanner's capture log, one line per attempt:
  `{"at","outcome","cell"?, "basis"?, "sensor_basis"?, "adjusted"?, "wait"?,
  "separation"?, "anchor"?, "gap"?, "overlap_term"?, "correlation"?,
  "feature_correlation"?, "samples"?, "searched"?}`. The last five belong to
  `overlap-wait` (issue #130): which term of the conflict refused
  (`brightness`, `edges` or `both`), the two correlations it decided on, the
  sample count, and whether registration searched before refusing. `wait`, `separation` and `anchor` belong to
  `alignment-wait` and were added for issue #76, because one outcome name
  covered three different refusals and a log of them said only that a hold did
  not capture. `separation` is written on one other outcome:
  `carry-too-large`, where it carries the size of the carried correction the
  bound refused, measured on the refusing frame (issue #95). `wait` is
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
- Replay through the real scanner: `node --import tsx src/next/hubs/sky/sheets/__sim__/replay.ts <abs case dir> [--scanner legacy|pano]` (run inside `ui`; `legacy` is the default, see "Version 2")
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
  they differ. When this rule landed it read 41.8 degrees of total in runs of
  13.3, 10.2, 7.2, 5.7 and 5.4, and was MISSED on the longest run alone
  against a 12 degree threshold. Three commits later the tracer changed what
  it finds there (issues #71 and #74), and the same obstacle now reads 29.8
  degrees of total with a longest run of 10.2, which is under the threshold,
  so it is NOT missed and the case's `no_missed_obstructions` passes. Both
  changes are needed for that: the rule reading a stretch, and the tracer
  finding more of the roof. A worked example dated to the commit that wrote
  it is how #89 happened; this one carries both readings on purpose.
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
  `resolvable_width_deg` is `max(min_width_deg', 360 / product_bins)`, where
  `min_width_deg'` is the declared label floored at `EDITOR_MIN_WIDTH_DEG`
  (1.5577 degrees, issue #53: the owner's ruling that the narrowest obstruction
  the planner honours is the closest two dots can be placed in the horizon
  editor, at its finest zoom). It is reported
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
  `resolvable_width_deg` falls back to the scene's declared `min_width_deg`,
  floored at `EDITOR_MIN_WIDTH_DEG` (`null` where the scene declares none), no bin is resolved so a visible
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

## Version 2: the horizon panorama scanner

Everything above describes the legacy scanner and stays true of it. Version 2
adds a second scanner, a single-ring live panorama behind a flag that is off by
default (spec `docs/ui-rebuild/19-photosphere-panorama.md`), and the files,
gates and corruptions to grade it. The legacy scanner stays the default and its
results are scored exactly as before: a case is scored the version 2 way only
when its route is a pan, its `horizon.json` is version 2, or it carries a
`grading` block or a `truth/visibility.json`. Every other case takes the legacy
path and writes the `scores.json` keys it always did.

### Motion: which axis is which

`rotationRate` in the W3C specification, and in Chromium, reports
`alpha` = the rate about device x, `beta` = about device y and `gamma` = about
device z, in degrees per second (rounded to 0.1 on Chromium). The `motion`
record's `rate` is `{"alpha", "beta", "gamma"}` in exactly that order.

- A portrait phone at pitch `p` turning on the spot at `omega` reports
  `beta = omega cos p` and `|gamma| = omega sin p`, with `alpha` about 0. A
  pure pitch lands in `alpha`.
- The simulator's code has always written x, y, z in that order
  (`sim/sensors.py`, the `alpha`, `beta`, `gamma` of `motion_events`). Only the
  docstring of `motion_events` said otherwise (it called them the rates about
  z, x and y), and it is corrected to match the code.
  Issue #897 proposed changing the code to `alpha = rate[2], beta = rate[0],
  gamma = rate[1]`; its premise is inverted, the code was right and the
  docstring wrong, and that proposal is a mutant the sensor tests must fail.
- The scanner does not depend on this ruling: `PoseTrack` fits the mapping
  (permutation, sign and unit) at run time against the orientation stream. The
  W3C mapping is only the default until that fit confirms or replaces it.

### Seeding, and the legacy rule

A case with a `realism` block draws every random source from
`np.random.SeedSequence(case seed).spawn(10)`, in this fixed order: 0 relative
stream, 1 absolute stream, 2 motion, 3 tremor, 4 frame noise, 5 EIS, 6
auto-exposure, 7 to 9 reserved. The same seed reproduces byte-identical
observations and a different seed does not (#901).

**Legacy rule:** a case with no `realism` block keeps today's bytes. Its gyro
noise is drawn from `default_rng(0)` through `gyro_noise_deg_s`, as it always
was, and the committed fixtures' `hashes.observations` must rebuild unchanged.
`grading`, `realism` and `scanner_options` are copied into `manifest.json` only
when the case definition has them.

### `input/observations.jsonl`, version 2

One JSON object per line. Times are ms from the case start. Lines are merged by
delivery time as today (`mergeObservations`): readings before frames at equal
times, and equal kinds in file order.

```
{"kind":"frame","frame_id":"f000123","t_capture_ms":12300,"t_present_ms":12360,"width":180,"height":320,"file":"frames/f000123.png"}
{"kind":"frame","frame_id":"f000124","t_capture_ms":null,"t_present_ms":12393,"width":180,"height":320,"file":"frames/f000124.png"}
{"kind":"orientation","event":"deviceorientation","t_event_ms":12280,"t_receive_ms":12285,"alpha":12.3,"beta":67.0,"gamma":-1.2,"absolute":false}
{"kind":"orientation","event":"deviceorientationabsolute","t_event_ms":12280,"t_receive_ms":12285,"alpha":101.4,"beta":67.0,"gamma":-1.2,"absolute":true}
{"kind":"motion","t_event_ms":12290,"t_receive_ms":12295,"rate":{"alpha":0.1,"beta":18.4,"gamma":-7.8}}
{"kind":"screen","t_event_ms":15000,"t_receive_ms":15000,"angle":90}
```

- `event` absent means `deviceorientationabsolute`, the legacy files.
- `t_capture_ms: null` means the frame metadata carries no `captureTime`.
- Orientation angles may be null (Brave's blocked shape).
- `rate` may be null. Its components are deg/s about device x, y and z, in that
  order.
- `screen` is optional.

### Case definition additions

```json
{
  "case_id": "chartyard-pan1-41", "scene": "chartyard", "route": "pan1-p23",
  "camera": {"width": 180, "height": 320, "fov_short_deg": 41.14}, "fps": 30, "seed": 7,
  "expected": "positive", "profile": "realism-v1",
  "grading": {"still_pivot": true, "fully_observable": false, "daylight": true, "north_graded": true, "expect_closure": true},
  "scanner_options": {"focal_prior_scale": 1.1, "sensor_only": false, "declination_deg": 0},
  "realism": {
    "gyro_scale_err": 0.02,
    "relative": {"yaw_zero_deg": "random", "drift_deg_min": 2.0, "noise_deg": 0.05, "quant_deg": 0.1,
                 "threshold_deg": 0.1, "pump_hz": 60, "latency_ms": 5, "spikes": null},
    "absolute": {"bias_deg": 0, "declination_deg": 0, "sinusoid": {"amp_deg": 0, "phase_deg": 0},
                 "noise": {"sigma_deg": 3.0, "tau_s": 0.3}, "steps": [], "quant_deg": 0.1,
                 "threshold_deg": 0.1, "pump_hz": 60, "latency_ms": 5, "spikes": null},
    "motion": {"bias_deg_s": 0.05, "noise_deg_s": 0.03, "round_deg_s": 0.1, "pump_hz": 60, "latency_ms": 5},
    "streams": "both",
    "gyro": true,
    "capture_time": {"mode": "delivery", "lag_ms": 50},
    "frames": {"...": "see Frame realism"}
  }
}
```

Defaults (reset from the device facts by T31):

- `gyro_scale_err` 0.02;
- relative drift 2 deg/min and noise 0.05;
- absolute noise sigma 3 degrees with `tau_s` 5.0, sinusoid amplitude 2 at
  phase 40, no bias;
- `north_graded` cases use `tau_s` 0.3 and amplitude 0;
- spikes `{"rate_hz": 0.2, "size_deg": 15}`, only on the slow case;
- capture time `delivery` with a 50 ms lag.

Rules:

- `steps`: `[{"t_s": 8.0, "deg": 4.0}]`, a heading step at `t_s`.
- `spikes`: single samples displaced by plus or minus `size_deg`.
- `streams`: `both`, or `absolute-only` in Chromium's shape: each absolute
  record is also emitted as `event: "deviceorientation"` with `absolute: true`,
  the same values and times, and no relative stream.
- `capture_time`: `delivery` (`t_capture_ms` = exposure time + `lag_ms`,
  Chromium's I420 semantics), `sensor` (the exposure time) or `none` (null).
  `t_present_ms = max(exposure + 60, t_capture_ms + 10)` when `t_capture_ms` is
  not null, else exposure + 60.
- `grading` flags, read by the scorer from `manifest.json`: `still_pivot`,
  `fully_observable`, `daylight`, `north_graded` (white-noise compass, no bias)
  and `expect_closure`. An absent flag is false.

### Route kind `pan`

```json
{"schema": 1, "name": "pan1-p23", "kind": "pan", "pivot": [0, 0, 0], "radius_m": 0, "height_m": 1.4,
 "reference": "axis", "start_hold_s": 1.0, "end_hold_s": 1.0, "rate_cap_deg_s": 45,
 "pans": [{"alt": 22.98, "from_az": 0, "turn_deg": 385, "speed_deg_s": 20, "ramp_s": 0.5, "direction": "cw"}],
 "tremor": {"yaw_deg": 0.3, "pitch_deg": 0.3, "roll_deg": 0.3, "band_hz": [0.5, 3.0]}}
```

- Camera centre: `pivot + [r sin az, r cos az, height_m]`, with `r = radius_m`;
  0 is a still pivot. The optical axis has the segment's azimuth `az(t)` and
  altitude `alt`, with roll 0 plus tremor.
- Segments run back to back, each with a smoothstep speed ramp of `ramp_s` at
  each end. `cw` means azimuth increasing. `from_az` is optional after the
  first segment.
- Holds come only at the start and the end.
- Tremor: per axis, the sum of 5 sinusoids log-spaced in `band_hz`, with phases
  and weights from SeedSequence child 3, scaled to the given rms.
- `rate_cap_deg_s`: the build raises if the total angular rate, tremor
  included, would exceed it.
- `reference: "axis"`: `c_ref = pivot + [0, 0, height_m]`. Otherwise the first
  frame's position is used, as today.

### Object materials

```json
"material": {"kind": "flat"}
"material": {"kind": "noise", "seed": 11, "cells": 24, "octaves": 4, "mod": [0.6, 1.3]}
"material": {"kind": "stripes", "count": 40, "duty": 0.5, "colour2": [60, 52, 40]}
```

- `noise`: the texel factor is `mod[0] + (mod[1] - mod[0]) x valueNoise(u, v) /
  1.875`, using the lattice recipe of the Scene schema on the texture's (u, v).
  The object colour is multiplied by it and clamped.
- `stripes`: `count` stripes along u. `duty` is the fraction in the object's
  colour; the rest is `colour2`.
- Textures are 256 x 256, mapped with THREE's default geometry UVs.
- Python truth ignores `material`.

### Frame realism (`realism.frames`)

```json
"frames": {"exposure_ms": 4, "subframes": 4,
  "ae": {"min_gain": 0.6, "max_gain": 1.6, "tau_s": 0.5, "target_luma": 118},
  "noise_sigma": 2.0, "rolling_shutter_ms": 25,
  "eis": {"tau_s": 0.2, "margin_deg": 1.8, "jitter_deg": 0.05},
  "aliasing": null}
```

Dusk cases use `exposure_ms: 33` and fps 15. The alias case uses
`"aliasing": {"factor": 4}`. Cases without a `frames` block render as today.

### `input/scanner.json` and the `--scanner` flag

```json
{"focal_prior_scale": 1.0, "sensor_only": false, "declination_deg": null}
```

Every field is optional, and an absent file means these defaults. A non-null
`declination_deg` makes the replay call `setDeclination(az => az + d)`. The
replay reads nothing else from outside `input/`.

The replay driver takes the scanner by name:

    node --import tsx src/next/hubs/sky/sheets/__sim__/replay.ts <abs case dir> [--scanner legacy|pano]

(run inside `ui`). `legacy` is the default and writes exactly the files it
always wrote; `pano` writes the result files below. The flag selects an entry of
the replay's `SCANNERS` registry, and the registry, not the flag, is what knows
which scanners exist.

### Result files (replay to scorer)

- `panorama.png`: unchanged.
- `horizon.json`: version 1 for `legacy`, version 2 for `pano`:
  `{"version":2,"interpolation":"linear-wrap","profile_bins":720,"profile":[720],"profile_traced":[720, null where none],"profile_state":[720],"points":[{"az","alt"}],"tau","bins":720,"uncertain_bins":[..]}`.
  `profile` is the published altitude per bin and `profile_traced` the traced
  one. `profile_state` is the `BinState` of each bin: 0 Measured, 1 Low,
  2 Unknown, 3 Tall, 4 Overhead (reserved), 5 Edited, 6 Kept. `uncertain_bins`
  lists the bins in Low, Unknown or Tall. Bin `i` covers
  `[i / 2, (i + 1) / 2)` degrees. `points` is the published polyline, linear
  between points and wrapped at 360.
- `columns.json`: legacy only.
- `events.jsonl`: the legacy fields
  `{t_ms, frame_id, compass_ready, tilt_ready, aim, basis, frame_count, cue}`,
  plus `keyframe` and `cls` for `pano`. `basis` is the live corrected pose, in
  the scan frame.
- `captures.jsonl` (`pano`): the snake_case form of `CaptureRecord`:
  `{at, frame_id, outcome, detail?, kf?, step_deg?, rate_deg_s?, psr?, zncc?, innovation_deg?, w_yaw?, extrapolated_ms?}`.
  `frame_id` is the observation's id.
- `summary.json`: as today, and unchanged for `legacy`. For other scanners,
  `cells_total` and `cells_covered` are null, and
  `"cost_ms": {"callback": Stats, "callback_ref": Stats, "ref_unit_ms": number}`
  is added.
- `first_seen.bin`: raw little-endian Uint16, 1080 x 300 row-major (the
  panorama's raster), in deciseconds since Begin; 0 means never seen.
- `diagnostics.json`: the `Diagnostics` type, keys verbatim:
  `{version:1, scanner:'pano', sensor_only, begin_ms, finish_ms, predictor_mode, mode_changes:[{t_ms,from,to}], axis_mapping:{perm,sign,unit,fit,confirmed}|null, tau_ms, tau_sigma_ms, tau_pairs, tau_applied, focal:{state,f_norm,sd_pct,ratios,short_fov_deg}, loop:{closed,method,pre_deg,post_deg,match:{early_kf,late_kf}|null,unwrapped_deg}, north:{offset_deg,sigma_deg,spread_deg,samples,n_eff,stable,source}|null, declination_applied, keyframes:[{id,frame_id,t_ms,q,cls,sigma_deg}], keyframe_ms, readback_ms, stale_refusals, extractor:'tracer'}`.
  `keyframes[].q` is the final world pose, camera to world, `[w, x, y, z]`: the
  camera frame is x right, y up, looking along -z, so the rotation matrix of `q`
  has the columns `right`, `up`, `-forward`.
- `live.jsonl`: one `LiveSnapshot` per keyframe.
- `Stats` in JSON: `{"n", "p50", "p95", "max"}`.

### `truth/visibility.json` and `truth/route.json`

```json
{"bins": 720, "noise_sigma": 2.0,
 "contrast_sigma": [720 numbers or null], "visible": [720 booleans], "footprint_top_deg": [720 numbers or null]}
```

For each bin, with A the truth boundary (the maximum of the 0.1-degree truth
over the bin):

- `contrast_sigma` is the maximum, over delivered frames whose claimed footprint
  contains (the bin centre, A) with 2 degrees of footprint above A, of
  `|mean luma in [A + 0.5, A + 1.5] - mean luma in [A - 1.5, A - 0.5]| /
  noise_sigma`. Luma is sampled from the post-processed frame along the
  bin-centre azimuth.
- `visible` is `contrast_sigma >= 3`.
- `footprint_top_deg` is the highest altitude of the footprint region at the
  bin.

`truth/route.json` is the route definition as built. It is written for pan
routes only, and its `kind: "pan"` is what makes a case a pan case.

### Recording file

JSON Lines, UTF-8, the output of `Recorder.finish`:

1. `RecordingHeader`;
2. then, in delivery order: observation lines as above, where frame lines carry
   `"mime"` and `"data"` (base64) in place of `"file"`; and
   `{"kind": "action", "t_ms": ..., "action": "begin" | "finish"}` lines;
3. finally `{"kind": "report", "report": ScanReport}`, when a report was passed.

Times are ms since the camera opened. Frame ids are `f%06d` by delivery count,
kept even when frames are decimated. The file contains photos of the
surroundings. It stays on the phone unless the owner shares it, and it is never
committed without approval.

### Scan report, version 2

The `ScanReport` type defines it. No coordinates, no declination, no raw photo
key, no user-agent string, no wall-clock time. `buildReport` drops any field the
type does not declare. `slices` are up to 16 JPEG data URLs of kept strips,
spread evenly over the keyframes, and `captureLog` is the full `CaptureRecord`
list (#902).

### Scoring version 2

Definitions the gates share:

- **Pan route**: `truth/route.json` exists with `kind: "pan"`.
- **Delivery window**: the frames whose `t_capture_ms` lies between the `begin`
  and `finish` actions of `input/actions.jsonl`. They come from the case, never
  from anything the scanner reports about itself, so a scanner that began late
  or stopped writing lines does not shrink what it is asked about. Without an
  actions file every frame is in the window.
- **Claimed footprint** of a delivered frame: the directions whose camera-frame
  coordinates under the truth pose and truth intrinsics satisfy
  `|x / z| <= tan 3 deg` and `|y / z| <= 0.9 tan(long / 2)`, in front of the
  camera (`y` is the long axis of the portrait image, so the half-extent is
  `cy / fy`). The footprint region is the union over the window's frames, on the
  panorama raster. Parallax is ignored.
- **Measured mask**: the bins whose `profile_state` is 0.
- **Expected bins**: the visible bins whose A + 6 degrees is at or below
  `footprint_top_deg`. A perfect scanner measures these. Where
  `visibility.json` is absent nothing is excused: every bin counts as visible
  for the empty-Measured rule, and the gates that need the file fail.
- **The polyline** is read as the planner reads a stored horizon, linear
  between points and wrapped at 360, and is sampled at the truth's own
  0.1-degree azimuths.

| gate | rule | applies to |
|---|---|---|
| `horizon_p95_lt_1` | p95 of the absolute polyline-minus-truth error at the truth azimuths inside Measured bins is below 1.0 (the limit less 1e-9, so a line shifted exactly one degree does not pass on the last bit of an interpolation). An empty Measured set fails when 36 or more bins are visible, and passes when fewer are | every case |
| `no_missed_obstructions` | the legacy obstacle rules on the interpolated polyline over Measured bins, rounded to 1e-9. An obstacle is only expected where it is inside the expected bins: one wholly dark or wholly above the footprint has nothing to miss | every case |
| `never_below_profile` | the polyline is at or above `profile` at both ends of every bin and at every vertex inside one (1e-6) | every version 2 horizon |
| `measured_is_honest` | p99 of the same error is below 2.0, and the same empty rule | every version 2 horizon |
| `measured_share` | at least 85 % of the expected bins are Measured or Edited | `daylight` and `still_pivot` |
| `unknown_where_unobservable` | at most 5 % of the not-visible bins are Measured | every case with `visibility.json` |
| `no_unresolved_boundary` | zero Low, Unknown or Tall bins; the legacy rule (zero unresolved solid angle) for a version 1 horizon | `fully_observable` for version 2; every case for version 1 |
| landmark gates | the legacy gates; a landmark is expected if the legacy `observable` flag says so AND its disc, grown by 1.5 degrees, lies inside the footprint region. The region comes from the route's frusta and the truth poses, never from the scanner's alpha. `per_landmark` rows gain `in_footprint` | every case; footprint rule on pan routes |
| `overlay_moving_p95_lt_1` | after removing one global yaw (the circular mean of reported-minus-truth forward heading over every frame with a basis), the p95 of the largest of the `forward`, `right` and `up` angular errors over moving frames is below 1.0 | pan routes |
| `overlay_complete` | frames in the window with a non-null basis are at least 95 % of the frames in the window (#898) | pan routes |
| `overlay_settled_p95_lt_0_5`, `overlay_max_lt_10` | retired: absent from `gates`, still reported in `overlay` | pan routes |
| `loop_residual_lt_0_25` | the angle of the rotation carrying `qinv(q_early) q_late` onto the same relative rotation of the truth poses, for the `loop.match` keyframes (through their `frame_id`), is below 0.25 degrees. Fails when `loop.closed` is false, the method is not `image`, or there is no match | pan, `expect_closure` |
| `focal_err_lt_0_5pct` | `abs(f_norm x short px - truth fx) / truth fx` is below 0.5 %. Reported, not gated, on arc cases | pan, `still_pivot` |
| `north_err_lt_1` | the circular mean over the diagnostics keyframes of `heading(q)` minus the truth heading of that keyframe's frame is below 1 degree in magnitude | pan, `north_graded` |
| `north_sigma_honest` | that error is at most 2.5 x the reported `sigma_deg`; true when no north is reported | pan, no compass bias (`realism.absolute.bias_deg` 0) |
| `live_fill_p95_le_1000` | per raster cell: from the first window frame whose central 2-degree slit contains the cell to `begin_ms + first_seen x 100`; p95 at most 1000 ms. A cell the scan entered and never reported counts 10 000 ms. Under the frozen replay clock this grades keyframe-selection latency only | pan routes |
| `coverage_ge_0_95` | painted alpha over the footprint region is at least 0.95 on pan routes; the legacy frustum rule elsewhere | every case |
| `capture_p95_le_1500`, `every_hold_captured` | present and true on pan routes (`capture` still reports what was logged); unchanged elsewhere | non-pan routes |
| `no_duplicate_frames`, the other landmark and overlay gates | unchanged | every case |

A gate that does not apply to a case is absent from `gates`, so the table of
gates is exactly what graded the case. Cost is reported (`cost_ms`, from
`summary.json`) and gated by nothing until D0 calibrates it.

`scores.json` gains, on a version 2 case only:

- `route` `{"kind"}`, `flags` (the five grading flags, absent ones false),
  `visibility` `{"present"}` and `cost_ms`;
- in `horizon`: `version`, `states` (a count per state name), `visible_bins`,
  `empty_measured`, `error_samples`, `unresolved_bins`, `below_profile_bins`,
  `polyline_points`, `tau`, `measured_share` `{expected, settled, share}`,
  `unknown_where_unobservable` `{not_visible, measured, share}`, and `p99` in
  `signed_error_deg`; the legacy keys keep their meaning, computed over Measured
  bins at the truth's own azimuths, and `measured_bins` is 720;
- in `overlay` on a pan route: `yaw_removed_deg`, `frames_in_window`,
  `frames_with_basis`, `complete_fraction`;
- in `coverage` on a pan route: `footprint_fraction_covered`, beside the
  frustum figure `observable_fraction_covered`. A perfect slit painter on a
  41.14 lens at a pitch of 22.98 degrees scores 0.9397 of the frustum, which is
  why the frustum cannot be the rule, and at least 0.999 of the footprint;
- on a pan route: `loop` `{closed, method, match, residual_deg, note}`, `focal`
  `{f_norm, short_px, truth_fx, state, error_fraction}`, `north`
  `{error_deg, keyframes, reported, sigma_deg}` and `live_fill`
  `{cells, never, p95_ms, median_ms, max_ms, note}`.

### Version 2 corruptions

The corruptions that touch the boundary, the poses or the north have a version
2 form (`yaw` and `north-wrap` turn the profile arrays by whole bins, the
polyline's vertices by the same whole number of half degrees and every
keyframe pose exactly; `mirror`, `focal`, `remove-section` and
`erase-horizon-strip` edit the profile, its states and the polyline together).
The eleven below act on version 2 results and refuse a result that lacks the
file they act on, because a corruption that did not happen would read as a
scorer that cannot fail.

| name | parameters | what it does | gate it must fail |
|---|---|---|---|
| `shift-line-down-1` | `deg` | lowers the polyline by `deg` (1.0) | `horizon_p95_lt_1` (also `never_below_profile`, by construction: the line was built to sit on the profile) |
| `dent-one-bin` | `bin_index`, `deg` | pulls the polyline `deg` (2.0) under the profile across one bin, with held vertices one bin out each side | `never_below_profile` |
| `relabel-unknown-measured` | | calls every Unknown bin Measured, published altitude unchanged | `unknown_where_unobservable` (also `measured_is_honest`, and `horizon_p95_lt_1` once those bins are over 5 % of the graded azimuths, by construction) |
| `relabel-measured-low` | `fraction`, `az0` | calls a contiguous run of `fraction` (0.2) of the Measured bins Low at 90, polyline lifted | `measured_share` |
| `mark-bins-unknown` | `count`, `az0` | calls `count` (20) contiguous Measured bins Unknown at 90, polyline lifted | `no_unresolved_boundary` |
| `drop-poses-90pct` | `keep_every` | nulls the basis on all but every `keep_every`-th (10th) event line | `overlay_complete` |
| `yaw-ramp` | `deg` | a yaw error growing linearly from 0 to `deg` (3.0) across the poses of `events.jsonl` | `overlay_moving_p95_lt_1` |
| `inflate-closure` | `deg` | turns the late keyframe of `loop.match` by `deg` (0.5) about the vertical | `loop_residual_lt_0_25` |
| `scale-focal-1.01` | `scale` | multiplies `diagnostics.focal.f_norm` by `scale` | `focal_err_lt_0_5pct` |
| `shrink-north-sigma` | `sigma_deg` | sets `diagnostics.north.sigma_deg` to `sigma_deg` (0.1) | `north_sigma_honest`, on a result whose north error is over 0.25 degrees: an exact result has an error of 0 and cannot be made dishonest |
| `delay-first-seen-2s` | `deciseconds` | adds `deciseconds` (20) to every nonzero `first_seen.bin` cell | `live_fill_p95_le_1000` |

The lifted polylines (`relabel-measured-low`, `mark-bins-unknown`,
`remove-section`, `erase-horizon-strip`) only ever raise the line, and put a
vertex at the line's own height one bin either side of the change, so the
damage is the changed bins and one shoulder bin each side whatever the
polyline looked like.

### The sensor-only control

The scanner with `sensor_only: true` (kept as an option) is replayed on
`chartyard-pan1-41` under realism. It must fail at least one of:
`landmarks_p95_lt_0_5`, `no_duplicates`, `loop_residual_lt_0_25`,
`overlay_moving_p95_lt_1`. If it passes them all, the realism is too weak to
grade stitching: the realism blocks get stronger before any stitching gate is
believed. The control is re-run after the realism defaults are reset from the
device facts.

### The ideal result on a pan route

`python -m sim ideal <case_id>` on a pan case writes a version 2 result from the
truth: `horizon.json` with a state per bin (Unknown where the visibility file
says not visible, Tall where A + 6 is above the footprint top, Measured
elsewhere) and the tightest polyline that clears both bins at every shared edge
(a flat line between equal bins and a climb of 0.01 degrees either side of each
step); `diagnostics.json` whose keyframes are the truth poses every 4 degrees of
turn, the loop match the last keyframe against the earlier one that looks most
nearly the same way (at least 330 degrees of turn apart), the truth focal
length, a north error of 0 and a sigma of 2; `first_seen.bin` from the claimed
footprint; `events.jsonl` with the truth basis on every delivered frame, plus
`keyframe` and `cls`; a `captures.jsonl` with one accepted record per keyframe;
and a `summary.json` with null cell counts and a zero `cost_ms`. It needs
`truth/visibility.json`. A legacy route keeps the result it always had.
