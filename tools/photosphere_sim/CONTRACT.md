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
```

`manifest.json`: `{"schema":1,"case_id","seed","scene","route","camera":{width,height,fov_short_deg},"fps","expected":"positive"|"control","hashes":{"frames","observations","truth"},"versions":{"three","chromium","python","app_commit"}}`.
Hashes are hex SHA-256; `frames` is the SHA-256 of the concatenated per-file
SHA-256 hex digests in filename order.

`observations.jsonl`, one object per line, sorted by delivery time:
- `{"kind":"frame","frame_id":"f000123","t_capture_ms":12300,"t_present_ms":12360,"width":480,"height":640,"file":"frames/f000123.png"}`
- `{"kind":"orientation","t_event_ms":12280,"t_receive_ms":12300,"alpha":..,"beta":..,"gamma":..,"absolute":true}`
Delivery time is `t_present_ms` for frames and `t_receive_ms` for events.

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
- `result/columns.json`: the scanner's `columns()` (array of arrays, NaN as `null`).
- `result/events.jsonl`: one line per delivered frame:
  `{"t_ms","frame_id","compass_ready","tilt_ready","aim":<cell id|null>,"basis":{"right","up","forward"}|null,"frame_count","cue"}`.
- `result/captures.jsonl`: the scanner's capture log, one line per attempt:
  `{"at","outcome","cell"?, "basis"?, "sensor_basis"?, "adjusted"?}`.
- `result/summary.json`: `{"frames_delivered","events_delivered","frames_accepted","cells_total","cells_covered","elapsed_ms","app_commit"}`.

## Commands (from the repo root unless stated)

- Python tests: `python -m unittest discover -s tools/photosphere_sim/tests -t tools/photosphere_sim -v`
- Build a case: `python -m sim make-case <case_id>` (run inside `tools/photosphere_sim`)
- Replay through the real scanner: `node --import tsx src/next/hubs/sky/sheets/__sim__/replay.ts <abs case dir>` (run inside `ui`)
- Score: `python -m sim score <case_id>` (inside `tools/photosphere_sim`); corrupt: `python -m sim corrupt <case_id> <corruption>`

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
- For `arc`: `C(t) = pivot + [radius_m sin(az(t)), radius_m cos(az(t)), height_m + lift_m * max(0, alt(t)) / 90]`
  and the orientation is `look_basis(az(t), alt(t), 0)`: the camera is carried
  around the body at arm's length and rises as the view tilts up.
- `aims`: ordered `[az, alt]` targets. Between consecutive aims the view moves
  along the shorter azimuth arc with a smoothstep profile over `move_s`
  seconds, then holds for `hold_s`. Holds are listed in `holds.json`. A move
  whose great-circle angle exceeds 30 degrees times `move_s` takes `angle /
  30` seconds instead, at the same smoothstep profile, so no move averages
  more than 30 degrees per second.
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
            "obstacles":[{"id","truth_alt_peak","deficit_median","deficit_p95","width_missed_deg","min_width_deg","missed"}],
            "missed_obstructions":[ids],"north_offset_deg"},
 "overlay":{"samples","missing_fraction","frames_over_gate",
            "moving":{"median_deg","p95_deg","max_deg"},"settled":{"median_deg","p95_deg","max_deg"}},
 "capture":{"holds","holds_with_capture","latency_ms":{"p95","max"},"accepted_frames"},
 "coverage":{"panorama_alpha_fraction","observable_fraction_covered","cells_covered_fraction"},
 "gates":{"landmarks_p95_lt_0_5","landmarks_p99_lt_1","no_omissions","no_duplicates","horizon_p95_lt_1","no_missed_obstructions","no_unresolved_boundary",
          "overlay_settled_p95_lt_0_5","overlay_moving_p95_lt_1","capture_p95_le_1500","every_hold_captured","coverage_ge_0_95","pass"}}

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
  `R sin(acos(0.99))` wide survives, and the expected area is scaled by
  `min(1, R sin(acos(0.99)) / radius_m)`. Without it the chart yard's `T1`,
  a 0.08 m disc on a 0.25 m trunk, is measured against a model it can fill
  only 44 per cent of and clears the filter by seven per cent.
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
  `deficit_p95` summarise it, `width_missed_deg` is 0.1 degrees times the
  number of those bins whose deficit exceeds 1.0, and `min_width_deg` is the
  width the scene declares the obstacle must be found at. `missed` is
  `deficit_median > 1.0`, or true for an obstacle that is visible but has no
  resolved bin at all. The median, not the minimum: a bin at the edge of an
  obstacle straddles a coarse measured bin and swings either way without the
  obstacle being lost. `missed_obstructions` is the ids of the missed ones.
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

- Overlay error is the angle between an `events.jsonl` line's `basis.forward`
  and that frame's truth `forward`. A frame is `moving` when its truth
  `angular_rate_deg_s` exceeds 2, else `settled`. `missing_fraction` is the
  lines that could not be scored over all lines: a null basis, and a
  `frame_id` the case never delivered, which is a sample with no pose and
  cannot be dropped from the denominator either. `max_deg` and
  `frames_over_gate` (samples above their own class's gate, 1.0 moving and 0.5
  settled) are reported beside the percentiles, because one frame in a
  thousand pointing three degrees wrong moves no percentile at all.
- Holds are walked in `from_ms` order and each takes the first
  `captures.jsonl` record with `outcome == "accepted"` and
  `from_ms <= at <= to_ms + 1500` that no earlier hold has claimed; the
  latency is `at - from_ms`. The claim matters: the 1500 ms grace makes
  consecutive windows overlap by most of a hold, so without it one accepted
  record answers for two holds. `accepted_frames` is every accepted record.
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
of the observable region. `pass` is the AND of every other gate.

A gate over no evidence fails where evidence was expected: a blank panorama
fails the landmark gates, a wholly uncertain boundary fails the horizon gates,
and no capture log fails `every_hold_captured`. A gate is vacuously true only
where the route itself produced no such group, which `overlay.samples` and
`capture.holds` distinguish.
