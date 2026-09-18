# Photosphere simulator, stage A (the measuring instrument) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stage A of `docs/ui-rebuild/16-photosphere-calibration-simulator.md` section 11: an independently rendered 3D chart yard, a handheld 0.75 m trajectory with an upward sweep plus a zero-translation control, realistic change-driven sensor observations, frame export, an independent truth evaluator, a replay of the recorded inputs through the REAL `PhotosphereSweep`, a numeric scorer with the provisional gates, corruption tests that prove the scorer detects wrong geometry and missing coverage, and an honest measured baseline of the current implementation.

**Architecture:** Two independent halves that share only declarative JSON. The Python side (`tools/photosphere_sim/`) renders images with Three.js in headless Chromium, generates sensor observations from a physical trajectory through its own W3C conversion, computes truth by CPU ray intersection, scores results and corrupts them. The TypeScript side (`ui/src/next/hubs/sky/sheets/__sim__/`) drives the production scanner under jsdom with injected frames, events and a virtual clock, reading only `input/`. Nothing in `tools/photosphere_sim` imports production code; nothing in the replay reads `truth/`.

**Tech Stack:** Python 3.12 system interpreter (`python`: numpy 2.5, Pillow 12.3, Playwright with Chromium 149, SwiftShader WebGL); stdlib `unittest` for the Python tests (no pytest in that interpreter and nothing is to be installed into it). Three.js 0.186 as a devDependency of `tools/photosphere_sim/package.json` (npm registry reachable). Node 24 with `tsx` and `jsdom` from `ui/node_modules` for the replay; UI tests hand-rolled (`node --import tsx <file>` from `ui`, each exports `result`).

**Spec:** `docs/ui-rebuild/16-photosphere-calibration-simulator.md` (all of it; stage A is section 11.A, the gates are section 9, the corruption list is section 10). Also `docs/ui-rebuild/14-photosphere-production-handoff.md` section 4.2 (frame and timing contract) and 4.3 (diagnostic recorder).

## Global Constraints

- Work only in `C:\Users\bear\astro` on branch `feat/photosphere-production`. The checkout holds much unrelated modified and untracked work (another agent's uncommitted `horizon.tsx`, `horizonStrip.ts`, `horizonDom.test.tsx` among it): never touch those, never broadly stage, reset, clean or revert. Commit with explicit pathspecs only (`git commit -F msg -- <paths>`). Implementers do not commit; the controller commits.
- No telescope motion, no deployment, no change to rig authentication or safety configuration. Nothing here touches `astrotown`.
- Independence (spec section 4): no file under `tools/photosphere_sim/` may import from `ui/` or reimplement by copying `photosphereGeometry.ts`; the replay driver must not open anything under `truth/`; the process under test receives only `input/` (images, observations, actions). A test in Task 1 greps for these and every later task keeps it green.
- Determinism: every random choice is seeded from the case seed; the same case definition yields byte-identical `input/` and `truth/` on the same machine (hashes in the manifest).
- The world is a fictional ENU frame in metres with no geodetic coordinates anywhere. The site's real latitude, longitude and label must never appear in any file. No emojis in code, docs or commit messages. Files are UTF-8 without BOM and LF line endings (write with the Write/Edit tools, not PowerShell `Set-Content`; in Python, `pathlib.write_text` on Windows silently writes CRLF, so open with `newline="\n"`; issue #40).
- Cached renders and results live under `tools/photosphere_sim/cache/` (git-ignored in Task 1). Nothing large is committed: case definitions, scenes, routes and code only.
- Python tests run with: `python -m unittest discover -s tools/photosphere_sim/tests -t tools/photosphere_sim -v` from the repo root. Tests that need Chromium are named `test_render_*` and must skip with an explicit reason if Playwright cannot launch Chromium, never silently pass.
- UI: `npx tsc -b` from `ui` stays green; `npm.cmd test` from `ui` keeps only the three pre-existing #36 failures (`r7Css` x2, `r7Disabled` x1).
- Angles are degrees in every JSON file and every public Python/TS signature; radians only inside function bodies. Times in JSON are integer milliseconds on one virtual clock starting at 0 at the first frame's capture.
- Report file and brief file paths are given in the dispatch; write the full report to the report file.

---

### Task 1: Package skeleton, the coordinate contract, and the orientation conversions

**Files:**
- Create: `tools/photosphere_sim/CONTRACT.md` (the shared contract every later task reads; content below)
- Create: `tools/photosphere_sim/README.md` (how to run: the four commands from CONTRACT.md's "Commands" section)
- Create: `tools/photosphere_sim/package.json`, run `npm install` there (creates `node_modules/` and `package-lock.json`)
- Create: `tools/photosphere_sim/sim/__init__.py` (empty), `tools/photosphere_sim/sim/geometry.py`
- Create: `tools/photosphere_sim/tests/__init__.py` (empty), `tools/photosphere_sim/tests/test_geometry.py`, `tools/photosphere_sim/tests/test_independence.py`
- Modify: `.gitignore` (append two lines: `tools/photosphere_sim/cache/` and `tools/photosphere_sim/node_modules/`)

**Interfaces:**
- Produces (every later task uses these exact names; all vectors are `numpy.ndarray` of shape `(3,)` or `(N,3)`, float64; degrees everywhere):
  - `sky_vector(az_deg, alt_deg) -> ndarray(3)` and `sky_angles(v) -> (az_deg, alt_deg)`
  - `look_basis(az_deg, alt_deg, roll_deg=0.0) -> Basis` where `Basis` is a dataclass `Basis(right, up, forward)`; `Basis.matrix_cam_to_world()` returns the 3x3 with columns `[right, up, -forward]` (a proper rotation, determinant +1)
  - `Camera(width, height, fov_short_deg)` with attributes `fx, fy, cx, cy`, methods `project(dir_cam: ndarray(N,3)) -> ndarray(N,2)` (pixel coordinates `u` right, `v` down; returns NaN for directions with `z <= 0`) and `ray(u, v) -> ndarray(3)` (unit direction in CAMERA coordinates for pixel coordinates; pixel centres are at integer + 0.5), plus `to_camera(basis, dir_world) -> dir_cam` and `to_world(basis, dir_cam) -> dir_world`
  - `device_orientation(basis, screen_angle_deg=0) -> (alpha, beta, gamma)` and `basis_from_device_orientation(alpha, beta, gamma, screen_angle_deg=0) -> Basis`
  - `angle_between(a, b) -> degrees` for unit vectors, computed as `atan2(|a x b|, a . b)` (a clamped `acos` loses six digits near zero and fails the 1e-7 round-trip gate) and `wrap_deg(x)` to `[-180, 180)`

**CONTRACT.md content** (write it verbatim, then keep it true; later tasks edit only their own sections):

```markdown
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
```

**geometry.py, the orientation conversion (derive from the contract, do not look at `photosphereGeometry.ts`):**
```python
def device_orientation(basis, screen_angle_deg=0.0):
    # Undo the screen rotation first: the rear camera's right/up at screen
    # angle s are the device x/y rotated by s about the device z axis.
    s = math.radians(screen_angle_deg)
    x = basis.right * math.cos(s) + basis.up * math.sin(s)   # device x in world
    y = -basis.right * math.sin(s) + basis.up * math.cos(s)  # device y in world
    z = -basis.forward                                      # device z in world
    R = np.column_stack([x, y, z])                          # world <- device
    sb = R[2, 1]
    cb_mag = math.hypot(R[0, 1], R[1, 1])
    if cb_mag < 1e-9:
        # beta is +-90: alpha and gamma are not separable; put it all in alpha.
        beta = math.degrees(math.atan2(sb, 0.0))
        gamma = 0.0
        alpha = math.degrees(math.atan2(R[1, 0], R[0, 0]))
    else:
        best = None
        for sign in (1.0, -1.0):
            cb = sign * cb_mag
            beta = math.degrees(math.atan2(sb, cb))
            gamma = math.degrees(math.atan2(-R[2, 0] * sign, R[2, 2] * sign))
            alpha = math.degrees(math.atan2(-R[0, 1] * sign, R[1, 1] * sign))
            if -90.0 <= gamma < 90.0:
                best = (alpha, beta, gamma)
                break
        alpha, beta, gamma = best
    return (alpha % 360.0, wrap_deg(beta), gamma)
```
and `basis_from_device_orientation` builds `R = Rz(alpha) @ Rx(beta) @ Ry(gamma)` with the standard right-handed rotation matrices, takes device axes as its columns, applies the screen rotation, and returns `Basis(right=x, up=y, forward=-z)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_geometry.py`, using `unittest`:
```python
import math, unittest
import numpy as np
from sim.geometry import (sky_vector, sky_angles, look_basis, Camera, device_orientation,
                          basis_from_device_orientation, angle_between, wrap_deg)

class SkyVectors(unittest.TestCase):
    def test_cardinal_directions_are_hand_specified(self):
        np.testing.assert_allclose(sky_vector(0, 0), [0, 1, 0], atol=1e-12)
        np.testing.assert_allclose(sky_vector(90, 0), [1, 0, 0], atol=1e-12)
        np.testing.assert_allclose(sky_vector(180, 0), [0, -1, 0], atol=1e-12)
        np.testing.assert_allclose(sky_vector(0, 90), [0, 0, 1], atol=1e-12)
    def test_angles_round_trip(self):
        for az, alt in [(0, 0), (37.5, 12.25), (270, -9.9), (359.9, 89.5)]:
            a, h = sky_angles(sky_vector(az, alt))
            self.assertAlmostEqual((a - az + 180) % 360 - 180, 0, places=9)
            self.assertAlmostEqual(h, alt, places=9)

class LookBasis(unittest.TestCase):
    def test_north_level_basis(self):
        b = look_basis(0, 0)
        np.testing.assert_allclose(b.forward, [0, 1, 0], atol=1e-12)
        np.testing.assert_allclose(b.right, [1, 0, 0], atol=1e-12)
        np.testing.assert_allclose(b.up, [0, 0, 1], atol=1e-12)
    def test_proper_rotation(self):
        for az, alt, roll in [(0, 0, 0), (123, 45, 0), (200, -5, 30), (0, 89, 0)]:
            m = look_basis(az, alt, roll).matrix_cam_to_world()
            self.assertAlmostEqual(np.linalg.det(m), 1.0, places=9)
            np.testing.assert_allclose(m.T @ m, np.eye(3), atol=1e-9)

class Pinhole(unittest.TestCase):
    def test_hand_specified_projection_portrait_60(self):
        cam = Camera(480, 640, 60.0)
        self.assertAlmostEqual(cam.fx, 240 / math.tan(math.radians(30)), places=6)
        # 30 deg right of forward in the horizontal plane lands on the right edge.
        d = np.array([[math.sin(math.radians(30)), 0, math.cos(math.radians(30))]])
        u, v = cam.project(d)[0]
        self.assertAlmostEqual(u, 480.0, places=6); self.assertAlmostEqual(v, 320.0, places=6)
        # 20 deg up lands above the centre: v = 320 - fx * tan(20).
        d = np.array([[0, math.sin(math.radians(20)), math.cos(math.radians(20))]])
        u, v = cam.project(d)[0]
        self.assertAlmostEqual(u, 240.0, places=6)
        self.assertAlmostEqual(v, 320 - cam.fx * math.tan(math.radians(20)), places=6)
    def test_behind_the_camera_is_nan(self):
        cam = Camera(480, 640, 60.0)
        self.assertTrue(np.isnan(cam.project(np.array([[0, 0, -1.0]]))).all())
    def test_ray_inverts_project_at_pixel_centres(self):
        cam = Camera(480, 640, 70.0)
        for u, v in [(0.5, 0.5), (240.5, 320.5), (479.5, 639.5), (100.5, 17.5)]:
            d = cam.ray(u, v)
            np.testing.assert_allclose(cam.project(d[None, :])[0], [u, v], atol=1e-9)

class DeviceOrientation(unittest.TestCase):
    def test_upright_phone_looking_north(self):
        # Screen faces south, camera looks north: alpha 0, beta 90, gamma 0.
        b = basis_from_device_orientation(0, 90, 0)
        np.testing.assert_allclose(b.forward, [0, 1, 0], atol=1e-12)
        np.testing.assert_allclose(b.right, [1, 0, 0], atol=1e-12)
        np.testing.assert_allclose(b.up, [0, 0, 1], atol=1e-12)
    def test_alpha_270_looks_east(self):
        np.testing.assert_allclose(basis_from_device_orientation(270, 90, 0).forward, [1, 0, 0], atol=1e-12)
    def test_beta_110_tilts_the_camera_20_degrees_up(self):
        az, alt = sky_angles(basis_from_device_orientation(0, 110, 0).forward)
        self.assertAlmostEqual(alt, 20.0, places=9); self.assertAlmostEqual(az % 360, 0.0, places=9)
    def test_round_trip_over_a_grid_including_the_upright_singularity(self):
        for az in range(0, 360, 45):
            for alt in (-10, 0, 20, 60, 85):
                for roll in (0, -15, 15):
                    b = look_basis(az, alt, roll)
                    a, be, g = device_orientation(b)
                    self.assertTrue(0 <= a < 360); self.assertTrue(-180 <= be < 180); self.assertTrue(-90 <= g < 90)
                    c = basis_from_device_orientation(a, be, g)
                    self.assertLess(angle_between(b.forward, c.forward), 1e-7)
                    self.assertLess(angle_between(b.up, c.up), 1e-7)
    def test_change_of_0_1_degree_is_visible_in_the_euler_angles(self):
        a0 = device_orientation(look_basis(10.0, 20.0))
        a1 = device_orientation(look_basis(10.1, 20.0))
        self.assertGreaterEqual(max(abs(wrap_deg(x - y)) for x, y in zip(a0, a1)), 0.09)
```

`tests/test_independence.py`:
```python
import pathlib, re, unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
class Independence(unittest.TestCase):
    def test_simulator_never_imports_production_code(self):
        bad = []
        for p in list(ROOT.glob("sim/**/*.py")) + list(ROOT.glob("renderer/**/*.js")) + list(ROOT.glob("renderer/**/*.html")):
            text = p.read_text(encoding="utf-8")
            if re.search(r"ui[\\/]src|photosphereGeometry|photosphere\.ts|from ['\"]\.\./\.\./ui", text):
                bad.append(str(p))
        self.assertEqual(bad, [], "simulator files reference production code")
    def test_replay_never_reads_truth(self):
        replay = ROOT.parents[1] / "ui" / "src" / "next" / "hubs" / "sky" / "sheets" / "__sim__"
        if not replay.exists():
            self.skipTest("replay driver not written yet")
        for p in replay.glob("*.ts"):
            self.assertNotIn("truth", p.read_text(encoding="utf-8"), f"{p} mentions truth/")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m unittest discover -s tools/photosphere_sim/tests -t tools/photosphere_sim -v`
Expected: import errors (`sim.geometry` missing).

- [ ] **Step 3: Write `sim/geometry.py`, CONTRACT.md, README.md, package.json; `npm install`; append the two `.gitignore` lines**

`package.json`: `{"name":"photosphere-sim-renderer","private":true,"type":"module","devDependencies":{"three":"0.186.0"}}`. Run `npm install` inside `tools/photosphere_sim` (needs the registry; it was reachable at plan time). Confirm `node_modules/three/build/three.module.js` exists.

- [ ] **Step 4: Run the tests to verify they pass**

Expected: all `test_geometry` cases pass; `test_replay_never_reads_truth` skips (driver not written yet); `test_simulator_never_imports_production_code` passes.

- [ ] **Step 5: Report**

Write the report to the path in the dispatch. List the files created and the `npm install` result. Do not commit.

---

### Task 2: The chart yard, and the truth evaluator that ray-casts it

**Files:**
- Create: `tools/photosphere_sim/scenes/chartyard.json`
- Create: `tools/photosphere_sim/sim/scene.py`, `tools/photosphere_sim/sim/truth.py`, `tools/photosphere_sim/sim/palette.py`
- Create: `tools/photosphere_sim/tests/test_scene.py`, `tools/photosphere_sim/tests/test_truth.py`
- Modify: `tools/photosphere_sim/CONTRACT.md` (add the "Scene schema" section below, verbatim)

**Interfaces:**
- Consumes: `sim.geometry` (`sky_vector`, `sky_angles`, `angle_between`).
- Produces:
  - `palette.PALETTE: list[tuple[int,int,int]]` of 24 colours: every channel in `{0, 128, 255}`, the three greys excluded, in a fixed documented order; `palette.nearest(rgb) -> (index, distance)` using the max per-channel absolute difference.
  - `scene.load(path) -> Scene` (a dataclass holding `background`, `landmarks`, `objects`, `surface_landmarks`, `test_obstacles`, `seed`)
  - `truth.intersect(scene, origin, dirs) -> Hit` with arrays `t` (metres, inf for background), `object_id` (str array, `"background"` for the sky), `colour` (N,3 uint8), `landmark_id` (str array, `""` if none)
  - `truth.horizon(scene, c_ref, bins=3600, alt_step=0.05) -> dict` matching `reference-horizon.json`
  - `truth.landmark_directions(scene, c_ref) -> list[dict]` matching `landmarks.json`
  - `truth.ideal_panorama(scene, c_ref, width=1080, height=300) -> ndarray(height,width,4) uint8` (alpha 255 everywhere)
  - `truth.background_texture(scene, width, height) -> ndarray(height,width,3) uint8`: the equirectangular background image (noise + stripes + landmark discs) that the renderer will ALSO generate from the same declarative spec; both implementations follow the recipe in the schema so they agree pixel for pixel up to resampling.

**Scene schema (append to CONTRACT.md verbatim):**
```markdown
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
```

**chartyard.json** (write exactly these numbers; the reference camera centre for both stage-A routes is `[0, 0.75, 1.4]`):
- background: distance 4000, texture `{"kind":"value-noise","seed":7,"octaves":4,"cells":16,"grey":[70,150],"stripes":{"count":11,"tilt_deg":23,"grey":170,"width_deg":1.5}}`
- landmarks: five rings at `alt` `5, 15, 35, 55, 75`; ring `r` (0-based) has 8 discs at `az = (k * 45 + r * 17 + (k * k * 7) % 45) % 360` for `k = 0..7`, `radius_deg` 1.0, `palette` index `(r * 8 + k) % 24`, id `f"R{r}K{k}"`; plus a cap ring `r = 5` at `alt` 85 with 6 discs at `az = (k * 60 + 17) % 360` for `k = 0..5`, `radius_deg` 1.5, palette `(40 + k) % 24`, id `f"R5K{k}"`. This gives 46 background landmarks. (Earlier text put ring 0 at -5, where the ground plane hides it from a camera 1.4 m up, and eight 1.5 degree discs at alt 87, where the small circle is only 18.85 degrees around and they overlap; issue #45. At alt 85 with 60 degree spacing adjacent cap centres are 5.0 degrees apart on the sphere, so 3 degree discs do not touch.)
- objects:
  - `ground`: plane `z` 0, colour `[60,55,45]`
  - `wall-east`: box min `[2.5,-1.5,0]` max `[2.8,3.0,2.6]`, colour `[120,100,80]`
  - `roof-south`: box min `[-1.5,-4.0,2.9]` max `[1.5,0.3,3.1]`, colour `[90,70,60]`
  - `pole-near`: cylinder base `[-1.2,1.8,0]` radius 0.03 height 4.5, colour `[40,40,40]`
  - `pole-far`: cylinder base `[8.0,20.0,0]` radius 0.05 height 6.0, colour `[45,45,50]`
  - `trunk`: cylinder base `[6,8,0]` radius 0.25 height 7, colour `[80,60,40]`
  - `canopy`: sphere centre `[6,8,7.5]` radius 2.5, colour `[50,90,40]`
  - `hill`: box min `[-300,120,0]` max `[300,160,25]`, colour `[70,80,90]`
- surface_landmarks: `W1` palette 3 on `wall-east` centre `[2.5,0.5,1.8]` normal `[-1,0,0]` radius 0.05; `W2` palette 11 on `wall-east` centre `[2.5,2.2,0.9]` normal `[-1,0,0]` radius 0.05; `RF1` palette 19 on `roof-south` centre `[0.6,-1.0,2.9]` normal `[0,0,-1]` radius 0.06; `RF2` palette 5 on `roof-south` centre `[-0.9,-2.6,2.9]` normal `[0,0,-1]` radius 0.06; `T1` palette 13 on `trunk`, on the face nearest `c_ref`: the horizontal unit vector from the trunk axis `(6, 8)` toward `(0, 0.75)` is `d = [-0.6375, -0.7704, 0]`, so centre `[5.841, 7.807, 2.0]` (axis plus `0.25 * d` at height 2.0), normal `d`, radius 0.08. `H1` palette 21 on `hill` centre `[10,120,12]` normal `[0,-1,0]` radius 1.2.
- test_obstacles: `pole-near` min width 2.0, `pole-far` min width 0.25, `roof-south` 10, `wall-east` 10, `trunk` 1.0.

**Truth evaluator rules:**
- `intersect` uses analytic ray/plane, ray/slab (box), ray/finite vertical cylinder (side and both caps) and ray/sphere; nearest positive `t` wins; ties go to the earlier object in file order. Background is hit at `distance_m` when nothing closer; its colour is sampled from `background_texture` at 4096x2048 with nearest-neighbour lookup, and a direction within `radius_deg` (angular distance) of a background landmark returns that landmark's palette colour and id. A surface landmark is hit when the hit object is its object, the hit point is within `radius_m` of `centre` and the face normal at the hit dots with `normal` above 0.99.
- `horizon(scene, c_ref)`: for each of `bins` azimuths at bin centres, cast rays at altitudes from -10 to 90 step `alt_step`; `alt_max` is the highest altitude whose hit is an object (not background); -10 when none. `obstacles`: for each `test_obstacles` entry, the azimuth range (from, to, wrapping allowed) over which that object is the FIRST hit at some altitude, and `alt_max` restricted to that object.
- `landmark_directions`: direction from `c_ref` to the disc centre (background: the disc's `(az, alt)` itself); `observable` true when the ray from `c_ref` in that direction reports `landmark_id == id`.
- `ideal_panorama`: one ray per pixel centre; colour from `intersect`; alpha 255.

- [ ] **Step 1: Write the failing tests**

`tests/test_scene.py`: loading `chartyard.json` gives 48 background landmarks with distinct `(palette, ring)` pairs, 6 surface landmarks, 8 objects, 5 test obstacles; every object colour has channel spread below 60 and is not within 40 of any palette colour; palette has 24 entries, all distinct, greys absent.

`tests/test_truth.py` (all from `c_ref = [0, 0.75, 1.4]`):
```python
def test_open_sky_north_is_background_and_the_hill_is_where_the_arithmetic_says(self):
    # az 0: the hill's near face is at y=120, 119.25 m away, top at z=25 -> alt = atan((25-1.4)/119.25)
    h = horizon(scene, C_REF, bins=360, alt_step=0.05)   # coarse for speed in the test
    expected = math.degrees(math.atan2(25 - 1.4, 120 - 0.75))
    self.assertAlmostEqual(h["alt_max"][0], expected, delta=0.06)
def test_the_roof_edge_is_at_the_hand_computed_altitude(self):
    # az 180: roof near edge y=0.3 is 0.45 m south, its underside z=2.9 is 1.5 m up
    expected = math.degrees(math.atan2(2.9 - 1.4, 0.75 - 0.3))
    self.assertAlmostEqual(alt_max_at(h, 180.0), expected, delta=0.06)
def test_the_near_pole_has_its_declared_angular_width_and_height(self):
    obs = next(o for o in h["obstacles"] if o["id"] == "pole-near")
    d = math.hypot(-1.2 - 0, 1.8 - 0.75)
    self.assertAlmostEqual(o_width(obs), math.degrees(2 * math.atan2(0.03, d)), delta=0.15)
    self.assertAlmostEqual(obs["alt_max"], math.degrees(math.atan2(4.5 - 1.4, d)), delta=0.1)
def test_the_far_pole_is_at_least_the_minimum_width(self):
    obs = ...("pole-far"); self.assertGreaterEqual(o_width(obs), 0.25)
def test_ground_never_blocks_above_the_horizontal(self):
    self.assertTrue(all(a < 0.0 for az, a in open_azimuths(h)))  # e.g. az 300..330 has only ground
def test_background_landmark_hidden_by_the_roof_is_not_observable(self):
    # a ring-3 landmark (alt 55) at an azimuth behind the roof must report observable False,
    # and a ring-0 landmark at an open azimuth True.
def test_ideal_panorama_shows_each_observable_landmark_at_its_direction(self):
    pano = ideal_panorama(scene, C_REF)
    for lm in landmark_directions(scene, C_REF):
        if not lm["observable"]: continue
        x = int(lm["az"] / 360 * 1080); y = int(round((90 - lm["alt"]) / 100 * 299))
        self.assertEqual(tuple(pano[y, x, :3]), PALETTE[lm_palette(lm["id"])])
def test_background_texture_is_deterministic_and_in_range(self):
    a = background_texture(scene, 512, 256); b = background_texture(scene, 512, 256)
    self.assertTrue((a == b).all()); self.assertGreaterEqual(a.min(), 70); self.assertLessEqual(a.max(), 255)
```
Fill in the helper functions (`alt_max_at`, `o_width` with wrap handling, `open_azimuths`) in the test file.

- [ ] **Step 2: Run to verify they fail** (module missing).

- [ ] **Step 3: Write `palette.py`, `scene.py`, `truth.py`, `chartyard.json`; append the schema to CONTRACT.md**

Vectorise the ray casting with numpy (all rays of a call at once); `horizon` at 3600 bins x 2001 altitudes is 7.2 M rays and must finish under 60 s on this machine.

- [ ] **Step 4: Run the tests to verify they pass**

- [ ] **Step 5: Report** (include the measured time of `horizon(scene, c_ref)` at 3600 bins). Do not commit.

---

### Task 3: Trajectories, change-driven sensors, and the case builder

**Files:**
- Create: `tools/photosphere_sim/routes/still.json`, `tools/photosphere_sim/routes/arc075.json`
- Create: `tools/photosphere_sim/sim/trajectory.py`, `tools/photosphere_sim/sim/sensors.py`, `tools/photosphere_sim/sim/cases.py`, `tools/photosphere_sim/sim/__main__.py`
- Create: `tools/photosphere_sim/cases/chartyard-still-60.json`, `tools/photosphere_sim/cases/chartyard-arc075-60.json`, `tools/photosphere_sim/cases/chartyard-arc075-70.json`
- Create: `tools/photosphere_sim/tests/test_trajectory.py`, `tools/photosphere_sim/tests/test_sensors.py`, `tools/photosphere_sim/tests/test_cases.py`
- Modify: `tools/photosphere_sim/CONTRACT.md` (append the "Route schema" section)

**Interfaces:**
- Consumes: `sim.geometry` (`look_basis`, `device_orientation`, `sky_angles`, `wrap_deg`), `sim.scene`, `sim.truth`.
- Produces:
  - `trajectory.build(route: dict, fps: int) -> Trajectory` with `frames: list[FrameTruth]` (`frame_id, t_capture_ms, position, basis, az, alt, angular_rate_deg_s`), `holds: list[Hold]`, `c_ref`, and `pose_at(t_ms) -> (position, basis)` (continuous, used by the sensor generator at 100 Hz)
  - `sensors.orientation_events(traj, threshold_deg=0.1, sample_hz=100, latency_ms=20) -> list[dict]` (change-driven)
  - `sensors.frame_records(traj, present_latency_ms=60) -> list[dict]`
  - `cases.build_case(case_def: dict, out_root: pathlib.Path, renderer) -> pathlib.Path` where `renderer(scene, camera, frames) -> Iterator[ndarray(H,W,3) uint8]` yields one image per frame in order; `build_case` writes everything in the contract's case layout, computes the hashes and the manifest, and copies `scene.json`.
  - `python -m sim make-case <case_id> [--out cache/cases] [--renderer three|flat]` (`flat` writes mid-grey frames and exists for tests and for Task 6 before Task 4 lands; the default `three` is wired in Task 4 and until then errors with "renderer not available").

**Route schema (append to CONTRACT.md):**
```markdown
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
```

Both stage-A routes: `pivot [0,0,0]`, `radius_m 0.75`, `height_m 1.4`, `move_s 0.8`, `hold_s 1.2`; `aims`: band 0 (alt 0) az `0, 24, ..., 336` (15 aims, ascending), band 1 (alt 35) az `348, 324, ..., 12` (15, descending, offset 12), band 2 (alt 70) az `6, 30, ..., 342` (15), then `[0, 89.5]`; `sweeps`: `[{"az":180,"alt_from":0,"alt_to":85,"duration_s":4.0,"hold_s":1.2}]`. `still.json` has `kind` `still` and `lift_m` 0; `arc075.json` has `kind` `arc` and `lift_m` 0.25. These aims are NOT derived from the production dome cells (spec section 6) and must never be.

**Sensor rules:** sample `pose_at` every 10 ms; convert with `device_orientation`; emit the first sample and then every sample whose `alpha`, `beta` or `gamma` differs from the LAST EMITTED by at least `threshold_deg` (alpha compared with wrap); `t_event_ms` is the sample time, `t_receive_ms = t_event_ms + latency_ms`; `absolute` true. During a hold nothing is emitted (the test checks this). Frame records: `t_capture_ms = round(k * 1000 / fps)`, `t_present_ms = t_capture_ms + present_latency_ms`, `frame_id = f"f{k:06d}"`, `file = f"frames/{frame_id}.png"`.

**Case definition schema** (`cases/<case_id>.json`): `{"case_id","scene","route","camera":{"width":480,"height":640,"fov_short_deg":60|70},"fps":10,"seed":1,"expected":"positive","profile":"chart-noise-free"}`. The three files differ only in `route` and `fov_short_deg` as their names say.

- [ ] **Step 1: Write the failing tests**

`test_trajectory.py`:
- the still route never translates: every frame `position == c_ref`.
- the arc route: `c_ref == [0, 0.75, 1.4]`; a 24 degree turn moves the camera by `2 * 0.75 * sin(12 deg) = 0.3119 m` between the first two holds (compare the positions at the two hold midpoints, tolerance 1 mm); at the aim `[0, 89.5]` the lift is `0.25 * 89.5 / 90` above `height_m` within 1 mm.
- holds: `len(holds) == 46 + 2` (46 aims plus the sweep's two holds), each `to_ms - from_ms == 1200`, and `angular_rate_deg_s` is 0 for every frame strictly inside a hold and above 5 deg/s at the midpoint of the first move.
- smoothstep continuity: the azimuth difference between consecutive 100 Hz samples never exceeds 1.0 degree.
- the sweep: frames with `az == 180` include `alt` rising monotonically from 0 to 85 over 4.0 s.

`test_sensors.py`:
- during every hold no orientation event is emitted (there is a gap of at least `hold_s` seconds without events).
- during the first move at least 20 events are emitted and consecutive emitted samples differ by at least 0.1 degree in some angle.
- `t_receive_ms - t_event_ms == 20` for all; events sorted; `absolute` true.
- frame records for 10 fps: `t_capture_ms` of frame 7 is 700, `t_present_ms` 760.

`test_cases.py` (uses the `flat` renderer):
- `build_case` on `chartyard-still-60` into a temp dir produces every file the contract lists; `manifest.hashes.frames` is stable across two builds; `observations.jsonl` is sorted by delivery time; `actions.jsonl` has `begin` at 0 and `finish` at the last frame's `t_present_ms + 1000`; `truth/landmarks.json` has 52 entries (46 background, 6 surface); `truth/reference-horizon.json` has 3600 `alt_max`; frames count equals `len(traj.frames)` and every PNG is 480 x 640 RGB.

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement**

`__main__.py` uses `argparse` with subcommands `make-case`, `score`, `corrupt`, `ideal`, `report`; only `make-case` is implemented here, the others print "not implemented in this task" and exit 2 (Tasks 7 and 8 fill them in).

- [ ] **Step 4: Run the tests to verify they pass; run `python -m sim make-case chartyard-still-60 --renderer flat` from `tools/photosphere_sim` and confirm the case directory under `cache/cases/`**

- [ ] **Step 5: Report** (frame count and total case duration). Do not commit.

---

### Task 4: The Three.js renderer under headless Chromium, verified against hand-specified views

**Files:**
- Create: `tools/photosphere_sim/renderer/index.html`, `tools/photosphere_sim/renderer/render.js`
- Create: `tools/photosphere_sim/sim/render.py`
- Modify: `tools/photosphere_sim/sim/cases.py` and `sim/__main__.py` (wire `--renderer three` as the default)
- Create: `tools/photosphere_sim/tests/test_render_cardinal.py`

**Interfaces:**
- Consumes: `sim.scene`, `sim.truth.background_texture` recipe (the JS implements the SAME recipe from CONTRACT.md; it may not call Python), `sim.geometry.Camera` for the test's predictions, `sim.cases.build_case(..., renderer)`.
- Produces: `render.ThreeRenderer(scene, camera)` context manager with `.render(position, basis) -> ndarray(H,W,3) uint8` (top row first) and `.version -> {"three":..,"chromium":..}`; `render.three_renderer(scene, camera, frames)` generator matching `build_case`'s `renderer` argument.

**Renderer rules:**
- `index.html` loads `../node_modules/three/build/three.module.js` through an import map and `render.js`; Python serves `tools/photosphere_sim/` over `http.server` on an ephemeral localhost port in a background thread and opens `http://127.0.0.1:<port>/renderer/index.html` in Playwright Chromium (`headless=True`, args `--use-angle=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist`).
- Three.js axes: `x` east, `y` up, `z` south. `toThree([e,n,u]) = [e, u, -n]`. Every mesh uses `MeshBasicMaterial` (unlit) so colours are exact; set `THREE.ColorManagement.enabled = false`, `renderer.outputColorSpace = THREE.LinearSRGBColorSpace`, textures `colorSpace = THREE.NoColorSpace`, no antialiasing on the render target (the truth has hard edges; the scorer tolerates 1.5 px).
- Background: a sphere of radius `distance_m`, `side: BackSide`, textured with a 4096 x 2048 canvas painted by the CONTRACT recipe (noise, stripes, then landmark discs; a disc is filled as the polygon of 64 points at angular distance `radius_deg` around the centre, converted to equirectangular coordinates, split at the azimuth seam). Objects: `PlaneGeometry` 4000 x 4000 for `plane`, `BoxGeometry`, `CylinderGeometry` (`openEnded: false`), `SphereGeometry(radius, 48, 32)`. Surface landmarks: `CircleGeometry(radius_m, 48)` placed 2 mm along the normal from `centre`, oriented with `lookAt(centre + normal)`.
- Camera: `PerspectiveCamera(vfov, W/H, 0.05, 20000)` with `vfov = 2 atan((H/2) / fx)` and `fx` from the contract; pose from a `Matrix4` whose columns are `toThree(right)`, `toThree(up)`, `toThree(-forward)` (three's camera looks down its own `-z`); position `toThree(C)`.
- Readback: render into a `WebGLRenderTarget(W, H)`, `readRenderTargetPixels` into a `Uint8Array(W*H*4)`, FLIP rows (WebGL returns the bottom row first), drop alpha, return base64 of RGB. `window.simRender = { load(sceneJson, cameraJson), render(poseJson) -> base64 }`.
- `render.py` decodes base64 to numpy; writes PNG with Pillow. Report `three` version from `package.json` and Chromium's version from Playwright.

- [ ] **Step 1: Write the failing test**

`tests/test_render_cardinal.py` (skips with reason if Chromium cannot launch):
```python
C_REF = np.array([0, 0.75, 1.4]); CAM = Camera(480, 640, 60.0)
def blob_centroid(img, colour):   # pixels within max-channel distance 40 of colour
    ...
class Cardinal(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.r = ThreeRenderer(scene, CAM).__enter__()
    def test_landmarks_land_where_the_pinhole_says(self):
        # For each of the six views, every observable background landmark whose
        # predicted centre is at least 12 px inside the frame must have a blob
        # whose centroid is within 1.5 px of the prediction.
        for az, alt in [(0,0),(90,0),(180,0),(270,0),(0,89),(200,30)]:
            b = look_basis(az, alt); img = self.r.render(C_REF, b); checked = 0
            for lm in landmark_directions(scene, C_REF):
                if not lm["observable"] or lm["kind"] != "background": continue
                d = CAM.to_camera(b, sky_vector(lm["az"], lm["alt"]))
                uv = CAM.project(d[None,:])[0]
                if np.isnan(uv).any() or not (12 <= uv[0] <= 468 and 12 <= uv[1] <= 628): continue
                c = blob_centroid(img, PALETTE[palette_of(lm)]); self.assertIsNotNone(c, lm["id"])
                self.assertLess(np.hypot(*(c - uv)), 1.5, f"{lm['id']} at view {(az,alt)}: {c} vs {uv}"); checked += 1
            self.assertGreaterEqual(checked, 3, f"view {(az,alt)} checked too few landmarks")
    def test_top_and_bottom_and_left_and_right_are_not_swapped(self):
        img = self.r.render(C_REF, look_basis(0, 0))
        # ground (dark, spread < 60) fills the bottom rows; sky texture (grey 70..170) the top rows
        self.assertLess(img[600:].mean(), img[:40].mean())
        # a ring-1 landmark east of north sits right of centre
        lm = [l for l in landmark_directions(scene, C_REF) if l["id"].startswith("R1") and 5 < wrap_deg(l["az"]) < 25][0]
        c = blob_centroid(img, PALETTE[palette_of(lm)]); self.assertGreater(c[0], 240)
    def test_colours_are_exact(self):
        img = self.r.render(C_REF, look_basis(0, 0))
        lm = ...a ring-0 landmark inside the frame...; c = blob_centroid(...)
        self.assertLessEqual(np.abs(img[int(c[1]), int(c[0])].astype(int) - PALETTE[...]).max(), 3)
    def test_rendering_is_deterministic(self):
        a = self.r.render(C_REF, look_basis(45, 10)); b = self.r.render(C_REF, look_basis(45, 10))
        self.assertTrue((a == b).all())
```

- [ ] **Step 2: Run to verify it fails** (module missing or skip; note which).

- [ ] **Step 3: Implement the renderer, `render.py`, wire `make-case`**

- [ ] **Step 4: Run the tests; then build the three real cases**

From `tools/photosphere_sim`: `python -m sim make-case chartyard-still-60`, `... chartyard-arc075-60`, `... chartyard-arc075-70`. Record wall time and the size of each `input/frames/` directory. Each case is about 960 frames; if a case takes longer than 20 minutes, report it rather than lowering the fps.

- [ ] **Step 5: Look at the pictures**

Open `cache/cases/chartyard-arc075-60/input/frames/f000000.png` and `f000420.png` with the Read tool and confirm they show a textured sky, coloured discs, the dark ground at the bottom of a level view, and the roof underside in the south-facing frames. A frame that is black, white, or upside down fails this task regardless of the tests.

- [ ] **Step 6: Report** (versions, timings, sizes, what the two frames showed). Do not commit.

---

### Task 5: Two honest observables on the production scanner

**Files:**
- Modify: `ui/src/next/hubs/sky/sheets/photosphere.ts` (class fields; `grabFrame`; a new `captureLog` getter and a new `panoramaPixels` getter; `stop()` does not clear the log)
- Modify: `ui/src/next/hubs/sky/sheets/__tests__/photosphereCaptureDom.test.tsx` (append two cases)

**Interfaces:**
- Produces:
  - `export type CaptureOutcome = 'accepted'|'not-recording'|'not-ready'|'unhealthy'|'no-image'|'alignment-wait'|'overlap-wait'|'no-target'|'already-captured'|'too-soon'|'below-horizon'|'read-failed'`
  - `export interface CaptureRecord { at: number; outcome: CaptureOutcome; cell?: number; basis?: CameraBasis; sensorBasis?: CameraBasis; adjusted?: boolean }`
  - `PhotosphereSweep.captureLog: readonly CaptureRecord[]` (bounded to the newest 4096 records; every `grabFrame` call appends exactly one record naming the branch it returned from; `manualOverhead` captures record `accepted` with no `cell`)
  - `PhotosphereSweep.panoramaPixels: { width: number; height: number; pixels: Uint8ClampedArray } | null` (a copy of the mosaic, RGBA, or null before any capture)

This is the minimal diagnostic recorder of doc 14 section 4.3 ("record rejection reasons, so a session with zero accepted frames is still diagnosable"). It records; it decides nothing. Map each early `return false` in `grabFrame` to its outcome in order of appearance; do not reorder the gates.

- [ ] **Step 1: Write the failing tests** (append to `photosphereCaptureDom.test.tsx`, following that file's harness): (a) a sweep that captures once has a log whose last record is `accepted` with a `cell` and a `basis`, and whose earlier records include at least one `alignment-wait`; (b) a sweep with no accepted pose after 20 frames has a log of 20 records, none `accepted`, and `panoramaPixels` is null; after a capture `panoramaPixels.width === 1080` and some alpha byte is 255.

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run the eight photosphere test files and `npx tsc -b`**

- [ ] **Step 5: Report**. Do not commit.

---

### Task 6: Replaying a case through the real scanner

**Files:**
- Create: `ui/src/next/hubs/sky/sheets/__sim__/png.ts` (a minimal PNG codec: decode 8-bit RGB/RGBA non-interlaced with all five filter types; encode RGBA with filter 0; uses `node:zlib`)
- Create: `ui/src/next/hubs/sky/sheets/__sim__/harness.ts` (the jsdom environment with an injectable frame source, a virtual clock, and canvas stubs that resample the current frame)
- Create: `ui/src/next/hubs/sky/sheets/__sim__/replay.ts` (the driver; `export async function replayCase(caseDir: string): Promise<Summary>` and a CLI `main` when run directly)
- Create: `ui/src/next/hubs/sky/sheets/__tests__/photosphereReplay.test.ts`

**Interfaces:**
- Consumes: `PhotosphereSweep` (public API only: `start`, `begin`, `cells`, `aimTarget`, `cameraBasis`, `compassReady`, `tiltReady`, `frameCount`, `captureCue`, `columns()`, `captureLog`, `panoramaPixels`), `traceSkyCoverage` from `../photosphere`; the case layout from `tools/photosphere_sim/CONTRACT.md` (read that file; the driver reads `manifest.json`, `input/observations.jsonl`, `input/actions.jsonl`, `input/frames/*.png` and NOTHING else).
- Produces: the `result/` files exactly as CONTRACT.md's "Result directory" lists them.

**Harness rules** (model the DOM test harness in `__tests__/photosphereStillnessDom.test.tsx`; do not import that file):
- jsdom with `pretendToBeVisual`, `isSecureContext` true, `DeviceOrientationEvent` defined, `mediaDevices` returning one rear camera and a live unmuted track, `visibilityState` visible, `localStorage` empty (so the scanner assumes its default lens).
- `HTMLVideoElement.prototype`: `videoWidth`/`videoHeight` from the manifest; `currentTime` = current frame's `t_capture_ms / 1000`; `paused` false; `readyState` 2; `play()` resolves; `requestVideoFrameCallback` stores the callback; `cancelVideoFrameCallback` clears it.
- Canvas stub: each canvas's 2d context records the last `drawImage(video, 0, 0, w, h)` target size; `getImageData(0, 0, w, h)` returns the CURRENT frame box-resampled to `w x h` (exact area averaging from 480 x 640; write `resample(rgba, W, H, w, h)`); `createImageData`, `putImageData` and `toDataURL` are inert.
- `performance.now` and `Date.now` both read the virtual clock.
- Timeline: merge observations by delivery time; before the first item call `sweep.start(video, canvas)`; at `begin`, `sweep.begin()`; for an orientation item set the clock to `t_receive_ms`, dispatch `deviceorientationabsolute` with `timeStamp = t_event_ms`, `alpha/beta/gamma`, `absolute: true`; for a frame item set the clock to `t_present_ms`, load the PNG (cache decoded frames in an LRU of 8), make it the current frame, call the stored rVFC callback with `(t_present_ms, {captureTime: t_capture_ms, mediaTime: t_capture_ms/1000, presentationTime: t_present_ms, expectedDisplayTime: t_present_ms, width, height, presentedFrames: k+1})`, then append the events.jsonl line for that frame. At `finish` write the result files: `panorama.png` from `panoramaPixels` (or a fully transparent 1080 x 300 image when null), `horizon.json` from `traceSkyCoverage(sweep.columns())` with `bins: 30`, `columns.json`, `captures.jsonl` from `captureLog`, `summary.json` (`app_commit` from `git rev-parse HEAD` via `child_process.execFileSync`, `elapsed_ms` = the last delivery time).

- [ ] **Step 1: Write the failing tests**

`photosphereReplay.test.ts`:
- `png.ts` round trip: encode a 5 x 3 RGBA gradient, decode, equal bytes; decode a PIL-written 4 x 4 RGB PNG embedded as base64 (generate it once with `python -c "from PIL import Image; ..."` and paste the base64; document the generating command in a comment) and check the four corner pixels.
- `resample`: a 4 x 4 checkerboard to 2 x 2 gives the exact averages.
- determinism and shape: build a tiny synthetic case directory under `os.tmpdir()` in the test itself (frames: 30 PNGs of the DOM harness's gradient-plus-block scene at 480 x 640 with the block moving for the first 8 frames then still; orientation events: 8 events approaching a direction at alt 30 then silence; `begin` at 0, `finish` at 4000), run `replayCase` twice, assert `summary.json` identical, `panorama.png` bytes identical, `events.jsonl` has 30 lines, `captures.jsonl` has at least one line and every line's `outcome` is one of the `CaptureOutcome` strings; assert the driver source contains no `truth` (belt and braces with the Python test).

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement `png.ts`, `harness.ts`, `replay.ts`**

- [ ] **Step 4: Run the test; then replay a real case**

From `ui`: `node --import tsx src/next/hubs/sky/sheets/__sim__/replay.ts C:/Users/bear/astro/tools/photosphere_sim/cache/cases/chartyard-still-60`. Record `summary.json` (frames accepted, cells covered) and the outcome histogram from `captures.jsonl`. Then the two arc cases. Then `npx tsc -b` from `ui` and `npm.cmd test` (only the three #36 failures allowed). Also re-run the Python independence test.

- [ ] **Step 5: Look at the panorama**

Open `cache/cases/chartyard-still-60/result/panorama.png` with the Read tool. Report what it shows in two sentences.

- [ ] **Step 6: Report**. Do not commit.

---

### Task 7: The scorer, and an ideal result it must pass

**Files:**
- Create: `tools/photosphere_sim/sim/score.py`, `tools/photosphere_sim/sim/ideal.py`, `tools/photosphere_sim/sim/blobs.py`
- Modify: `tools/photosphere_sim/sim/__main__.py` (`score`, `ideal` subcommands)
- Create: `tools/photosphere_sim/tests/test_blobs.py`, `tools/photosphere_sim/tests/test_score.py`
- Modify: `tools/photosphere_sim/CONTRACT.md` (append the "scores.json" section)

**Interfaces:**
- Consumes: the case layout; `sim.truth`, `sim.palette`, `sim.geometry`.
- Produces:
  - `blobs.find(rgb: ndarray(H,W,3), alpha: ndarray(H,W), colour, tolerance=40, min_pixels=6) -> list[Blob(cx, cy, pixels)]` (4-connected components of pixels within max-channel distance `tolerance` of `colour` with alpha 255; components are found with an explicit stack, no scipy)
  - `score.score_case(case_dir, result_dir=None) -> dict` (writes `result/scores.json`; `result_dir` lets corruption tests score another directory)
  - `ideal.make_ideal_result(case_dir, out_dir, c_ref_offset=(0,0,0), horizon_bins=3600) -> pathlib.Path`
  - `python -m sim score <case_id> [--result <dir>]`, `python -m sim ideal <case_id>`

**scores.json (append to CONTRACT.md):**
```markdown
## scores.json

{"schema":1,"case_id","input_hash","app_commit","profile",
 "landmarks":{"expected","found","omitted":[ids],"duplicated":[ids],"spurious","errors_deg":{"median","p95","p99","max"},
              "per_landmark":[{"id","truth":{"az","alt"},"measured":{"az","alt"}|null,"error_deg"|null,"status":"found"|"omitted"|"duplicate"}]},
 "horizon":{"truth_bins":3600,"measured_bins","measured_resolution_deg","signed_error_deg":{"median","p95","max"},
            "false_open_sr","false_blocked_sr","unresolved_sr","missed_obstructions":[{"id","truth_alt","measured_alt"}],"north_offset_deg"},
 "overlay":{"samples","missing_fraction","moving":{"median_deg","p95_deg"},"settled":{"median_deg","p95_deg"}},
 "capture":{"holds","holds_with_capture","latency_ms":{"p95","max"},"accepted_frames"},
 "coverage":{"panorama_alpha_fraction","observable_fraction_covered","cells_covered_fraction"},
 "gates":{"landmarks_p95_lt_0_5","landmarks_p99_lt_1","no_omissions","no_duplicates","horizon_p95_lt_1","no_missed_obstructions","no_unresolved_boundary",
          "overlay_settled_p95_lt_0_5","overlay_moving_p95_lt_1","capture_p95_le_1500","every_hold_captured","coverage_ge_0_95","pass"}}
```

**Scoring rules:**
- Landmarks: decode `panorama.png`; for each palette colour find blobs; convert each blob centroid to `(az, alt)` by the contract's raster mapping; match each blob to the nearest landmark of that colour (angular distance) within 8 degrees; a landmark with one match is `found` with `error_deg` = spherical angular distance between the measured direction and the truth direction; two or more matches is `duplicate`; an observable landmark with no match is `omitted`; a blob matching nothing is `spurious`. Statistics are over `found` landmarks; `expected` counts observable landmarks only, and omissions are reported as such, never dropped. Blobs smaller than 40 percent of the expected disc area at that altitude (`pi r^2 / (cell_az * cell_alt)` raster cells, where one raster cell spans `cell_az = 0.3333 * cos(alt)` degrees of true angle in azimuth and `cell_alt = 0.3344` degrees in altitude) are ignored.
- Horizon: measured profile as a step function of azimuth from `horizon.json` (bin `i` covers `[i*360/N, (i+1)*360/N)`); truth `alt_max` clipped to `[0, 90]` (the scanner's floor is 0; record the clipping in a `note`); bins listed in `uncertain_bins` are UNRESOLVED: they are excluded from `signed_error`, `false_open_sr` and `false_blocked_sr`, their area is `unresolved_sr`, and the gate `no_unresolved_boundary` (`unresolved_sr == 0`) fails a positive case on them; `signed_error` per resolved 0.1 degree bin = measured - truth; `false_open_sr` = sum over 0.1 x 0.1 degree cells where truth is blocked (alt below truth `alt_max`) and measured says open (alt above measured), weighting each cell by `cos(alt) * (0.1 deg)^2` in steradians; `false_blocked_sr` the converse; `unresolved_sr` the area of bins listed in `uncertain_bins`; `missed_obstructions`: for each truth obstacle, `measured_alt` = the minimum measured altitude over the obstacle's azimuth span; missed when `measured_alt < truth_alt - 1.0`; `north_offset_deg` = the shift in `[-10, 10]` (0.1 steps) minimising the mean absolute signed error.
- Overlay: for each `events.jsonl` line with a `basis`, truth basis for that `frame_id` from `trajectory.jsonl`; error = angle between forward vectors; a frame is `moving` when its `angular_rate_deg_s > 2`, else `settled`; `missing_fraction` = lines with null basis over all lines.
- Capture: for each hold in `holds.json`, the first `captures.jsonl` record with `outcome == "accepted"` and `from_ms <= at <= to_ms + 1500`; latency = `at - from_ms`.
- Coverage: `panorama_alpha_fraction` = cos-weighted fraction of raster cells with alpha 255; `observable_fraction_covered` = among raster cells whose direction from `c_ref` projects inside at least one delivered frame (using `trajectory.jsonl` poses and `camera.json`, ignoring parallax) AND is not below alt -10, the cos-weighted fraction with alpha 255; `cells_covered_fraction` from `summary.json`.
- Gates as named in the schema, thresholds from spec section 9's "Provisional gates" (landmarks p95 < 0.5, p99 < 1.0; horizon p95 of |signed| < 1.0; overlay settled p95 < 0.5, moving p95 < 1.0; capture p95 <= 1500 ms and every hold captured; coverage >= 0.95); `pass` is the AND of all.
- The ideal result (`ideal.py`): `panorama.png` = `truth.ideal_panorama(scene, c_ref + offset)`; `horizon.json` with `horizon_bins` bins where each bin's `alt` is the max of the truth `alt_max` (clipped to 0..90) inside it; `events.jsonl` with the truth basis per frame, `compass_ready` true, `frame_count` counting holds passed; `captures.jsonl` with one `accepted` record per hold at `from_ms + 700`; `summary.json` with `cells_covered == cells_total == 1`.

- [ ] **Step 1: Write the failing tests**

`test_blobs.py`: a 20 x 10 image with two discs of one colour and one of another finds three blobs with the right centroids and pixel counts; a disc of alpha 0 is not found; two touching discs are one blob.

`test_score.py` (build `chartyard-still-60` with the flat renderer into a temp dir, then `make_ideal_result`):
- scoring the ideal result gives `landmarks.omitted == []`, `duplicated == []`, `errors_deg.p95 < 0.4` (raster quantisation only), `horizon.signed_error_deg.p95 < 0.5`, `missed_obstructions == []`, `overlay.settled.p95_deg < 0.01`, `capture.holds_with_capture == holds`, `coverage.observable_fraction_covered > 0.99`, and `gates.pass` true.
- scoring an ideal result whose `horizon.json` is re-binned to 30 bins reports `measured_resolution_deg == 12.0` and `gates.horizon_p95_lt_1` false (the format cannot carry the pole), while every landmark gate stays true.
- the per-landmark list has 52 entries and `expected` equals the number of observable landmarks.

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run the tests; score the three real replays**

From `tools/photosphere_sim`: `python -m sim score chartyard-still-60` and the two arc cases (their `result/` dirs were written in Task 6). Paste the three `gates` objects and the landmark and horizon summaries into the report.

- [ ] **Step 5: Report**. Do not commit.

---

### Task 8: Prove the evaluator can fail, and render the report

**Files:**
- Create: `tools/photosphere_sim/sim/corrupt.py`, `tools/photosphere_sim/sim/report.py`
- Modify: `tools/photosphere_sim/sim/__main__.py` (`corrupt`, `report` subcommands; `score` also writes `report.html`)
- Create: `tools/photosphere_sim/tests/test_corrupt.py`

**Interfaces:**
- Consumes: `sim.score.score_case`, `sim.ideal.make_ideal_result`, `sim.truth`.
- Produces: `corrupt.apply(case_dir, result_dir, name, out_dir, **params) -> pathlib.Path` for the names below (`case_dir` gives `wrong-reference` the scene and `c_ref`; the others ignore it); `report.render(case_dir, scores, out_path)` writing a self-contained `report.html` (inline base64 PNGs, inline SVG, no external assets, no script).

**Corruptions** (each takes a result directory and writes a new one; every file not touched is copied):
- `yaw` (`deg`): roll panorama columns by `round(deg / 360 * 1080)` px, add `deg` to every horizon point's `az` (re-binned), rotate every overlay `basis` about `[0,0,1]` by `deg`.
- `north-wrap`: `yaw` with `deg = 350` (a scorer that does not wrap angles would report 350 degrees; the truth is a 10 degree error).
- `focal` (`scale`): remap altitude `alt' = atan(scale * tan(alt))` for the panorama rows (nearest-neighbour resample), the horizon alts and the overlay forward vectors.
- `flip-vertical`, `mirror` (columns reversed, `az -> 360 - az` for horizon and overlay).
- `duplicate-section` (`az0`, `width`): copy columns `[az0, az0 + width)` over `[az0 + width, az0 + 2 width)`.
- `remove-section` (`az0`, `width`): alpha 0 over the range and the horizon bins inside it set to `alt 90` and listed in `uncertain_bins`.
- `wrong-reference` (`offset_m`): replaces `panorama.png` with `truth.ideal_panorama` from `c_ref + offset` (this one needs the case dir; it is the reference-position corruption of spec section 10).
- `blur` (`radius_px`): box blur of the RGB channels.
- `erase-landmarks`: paint every blob of every palette colour with `[110,110,110]`.
- `empty`: alpha 0 everywhere; `captures.jsonl` emptied.
- `erase-horizon-strip`: alpha 0 for altitudes in `[-10, 15]`; all horizon bins uncertain.
- `brightness` (`gain`): RGB scaled and clipped; nothing else.

**report.html**: a title with case id, input hash, app commit; the gates table (PASS/FAIL words, no symbols); the landmark table sorted by error; the horizon plot as an inline SVG (truth in one colour, measured in another, x azimuth 0..360, y altitude -10..90); the overlay error timeline as an inline SVG; three inline PNGs: `result/panorama.png`, the ideal panorama, and the landmark error map (the result panorama with a small ring drawn at each truth direction, ring colour by error bucket). Plain HTML, one `<style>` block, system font.

- [ ] **Step 1: Write the failing tests**

`test_corrupt.py` (a `setUpClass` that builds the flat still case in a temp dir and its ideal result once; each test corrupts and scores):
- `yaw 1`: landmark `median` in `[0.8, 1.2]`, `north_offset_deg` in `[0.8, 1.2]`, `gates.pass` false. `yaw 5`: median in `[4.6, 5.4]`.
- `north-wrap`: median in `[9.5, 10.5]` (never 350).
- `focal 1.05`: for found landmarks with `35 <= |truth alt| <= 65`, mean error in `[0.9, 1.6]`; landmarks at `|alt| < 6` error below 0.4; `horizon_p95_lt_1` false.
- `flip-vertical`: omitted count at least half of expected; `mirror`: median error above 20 or omitted at least half.
- `duplicate-section 60 40`: `duplicated` non-empty; `remove-section 100 40`: `omitted` non-empty and `observable_fraction_covered < 0.95` and `unresolved_sr > 0`.
- `wrong-reference (1,0,0)`: surface landmarks `W1`,`W2` error above 5, background landmark errors median below 0.4, `gates.pass` false.
- `blur 5`: omitted at least 80 percent of expected. `erase-landmarks`: omitted == expected. `empty`: `found == 0`, `holds_with_capture == 0`, `panorama_alpha_fraction == 0`.
- `erase-horizon-strip`: `panorama_alpha_fraction > 0.7` AND `unresolved_sr` above half of the total sky area AND `no_unresolved_boundary` false AND `gates.pass` false, while the landmark gates for landmarks above altitude 15 stay true. This is the case that proves total area cannot hide a missing boundary.
- `brightness 1.2`: every geometry number (landmark median/p95, horizon p95, overlay p95) within 0.05 of the uncorrupted ideal's, `gates.pass` unchanged (true).

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement `corrupt.py`, `report.py`; wire the CLI; make `score` write `report.html`**

- [ ] **Step 4: Run all Python tests; regenerate the three real reports (`python -m sim score <case>` for each); open one `report.html` in the browser through the Read tool on its PNG parts or by describing its tables from `scores.json`**

- [ ] **Step 5: Report**. Do not commit.

---

### Task 9: The honest baseline

**Files:**
- Create: `docs/ui-rebuild/17-photosphere-simulator-baseline.md`
- Modify: `tools/photosphere_sim/README.md` (the reproduce section: the exact commands and expected durations)

**Interfaces:** consumes everything above; produces the document.

- [ ] **Step 1: Reproduce end to end from a clean cache**

Delete `tools/photosphere_sim/cache/cases/chartyard-*` (only those), then for each of the three cases: `make-case`, `replay`, `score`. Record wall times. Confirm the `manifest.json` hashes equal the ones recorded in Task 4's report (determinism across runs); if they differ, that is a finding to report, not to hide.

- [ ] **Step 2: Write the document**

Sections, in this order: purpose (one paragraph: stage A of doc 16, what it measures and what it does not); reproduction (case ids, input hashes, app commit, the commands, durations); results per case: the gates table and the landmark, horizon, overlay, capture and coverage summaries copied from `scores.json`; what the numbers say (which gates the current scanner fails and the mechanism the evidence supports for each: for example the 12 degree horizon bins, the 60 degree assumed lens on the 70 degree case, translation on the arc cases, dropped holds); known limits of the instrument itself (noise-free chart, no distortion, 10 fps, the raster quantisation floor measured on the ideal result, the fallback path not exercised); and the stage B entry point (spec section 11.B). Numbers appear in tables, never in prose. No claims beyond what `scores.json` shows.

- [ ] **Step 3: Needle and style scan**

Run `grep -l -F -f C:/Users/bear/.astrodeck/privacy-needles.txt docs/ui-rebuild/17-photosphere-simulator-baseline.md tools/photosphere_sim/README.md` and report only whether any file matched (there must be none). Confirm no emoji and no BOM.

- [ ] **Step 4: Report** (the three gates tables inline). Do not commit.
