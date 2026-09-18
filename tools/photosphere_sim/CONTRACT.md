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
from -10 to 90; `-10` means nothing was hit) and `"obstacles":[{"id","az_from","az_to","alt_max","min_width_deg"}]`.
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
  seconds, then holds for `hold_s`. Holds are listed in `holds.json`.
- `sweeps`: `[{"az","alt_from","alt_to","duration_s","hold_s"}]` appended
  after the aims: hold at `(az, alt_from)`, tilt linearly to `alt_to` over
  `duration_s`, hold again.
- `c_ref` is the camera centre at the first frame.
