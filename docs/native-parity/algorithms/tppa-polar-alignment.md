# TPPA — Three Point Polar Alignment: Algorithm Dossier

Status: extracted 2026-07-03 from the reference clone at `references/tppa` (plugin
version 2.2.3.2, commit `9a026fc5c4e6740eee5a5e058ac1f3c6b6eac2c1`, MPL-2.0) and the
NINA astrometry primitives it links against (`references/nina`, MPL-2.0).
This document is the single source of truth for the Rust reimplementation; the
implementer must not read the original C#.

Everything below is stated in the *exact* form the source computes it: formulas,
constants, defaults, units, retry counts, sign conventions, and all failure paths.
Pseudocode is Rust-flavored. A source map with file/line provenance is at the end.

---

## 0. Contents

1. Coordinate frames, units, and primitives
2. Refraction model and parameter acquisition
3. The measurement procedure (3 captures): automated / manual / blind
4. Plate-solve wrapper (capture + retry policy)
5. `Position` — plate solve → topocentric unit vector
6. `PolarErrorDetermination` — 3 vectors → mount axis → alt/az error
7. Continuous-update phase (knob-turning loop): exact update math
8. Reference-star tracking and display geometry
9. Avalon UPA automated adjustments (optional hardware)
10. Configuration knobs (defaults, ranges, units)
11. Validation, edge cases, and failure paths (complete list)
12. Golden test vectors (from the upstream test suite)
13. Message-bus/remote-control surface
14. Source map (MPL-2.0 provenance)
15. Recommended defaults and variants for AstroDeck

---

## 1. Coordinate frames, units, and primitives

### 1.1 Angle conventions

- Azimuth: **N = 0°, E = 90°** (SOFA/ERFA convention), range handled mod 360.
- Altitude: degrees above horizon; zenith = +90°.
- RA/Dec: solves are always normalized to **J2000** (`PlateSolveResult.Coordinates`
  setter transforms any epoch to J2000).
- Position angle (PA) of a solve: degrees, stored mod 360.
  `orientation = mod(360 − position_angle, 360)`
  (`orientation` is the **clockwise** rotation angle passed to all pixel↔sky
  projection functions below).
- `Angle` arithmetic in the source operates on **radians** internally
  (`a * b` = product of radian values, `a.Sin()` = angle whose radian slot holds
  `sin(a_rad)`, etc.). All pseudocode here is therefore written in plain `f64`
  radians unless a `_deg` suffix is used.
- `arcminutes = degrees × 60`, `arcseconds = degrees × 3600`.
- Euclidean modulus (always non-negative for positive divisor):
  `emod(x, y) = ((x % y) + y) % y`.

### 1.2 Image scale

```
ARCSEC_PER_PIX_FACTOR = (180/π) * 3600 / 1000      // = 206.264806...
arcsec_per_pixel(pixel_size_um, focal_length_mm) =
    (pixel_size_um / focal_length_mm) * ARCSEC_PER_PIX_FACTOR
```

TPPA computes the working image scale for the adjustment phase as:

```
arcsec_per_pix = arcsec_per_pixel(profile.pixel_size_um * binning_x, focal_length_mm)
```

(Source quirk: the C# expression is `(PixelSize * Binning?.X) ?? 1` — if binning is
null the whole *product* collapses to 1 µm, not the pixel size. Treat as a bug; in
Rust use `pixel_size * binning.unwrap_or(1)`.)

### 1.3 Topocentric unit-vector frame

Right-handed frame fixed to the observer:

- `+x` → horizon point at azimuth 0° (north)
- `+y` → horizon point at azimuth 270° (west)
- `+z` → zenith

```rust
fn altaz_to_unit_vector(az: f64, alt: f64) -> Vec3 {      // radians
    let theta = -az;
    let phi = FRAC_PI_2 - alt;                            // polar angle from zenith
    Vec3 {
        x: theta.cos() * phi.sin(),
        y: theta.sin() * phi.sin(),
        z: phi.cos(),
    }
}

fn unit_vector_to_altaz(v: Vec3) -> (f64 /*az*/, f64 /*alt*/) {
    if v.x == 0.0 && v.y == 0.0 {
        return (0.0, FRAC_PI_2);                          // exactly at zenith
    }
    // NOTE: source returns az = 0 whenever y == 0, even if x < 0 (which is
    // geometrically az = 180°). Faithful port keeps this quirk; it only matters
    // for the exact meridian.
    let az = if v.y == 0.0 { 0.0 } else { -v.y.atan2(v.x) };
    let alt = FRAC_PI_2 - v.z.acos();                     // requires |v| == 1
    (az, alt)          // az may be negative; consumers treat it mod 360
}
```

`alt` from `acos(z)` requires unit-normalized input; the plane-fit output is
normalized before this call.

### 1.4 Vector operations

```rust
cross(a, b) = (a.y*b.z − a.z*b.y, a.z*b.x − a.x*b.z, a.x*b.y − a.y*b.x)
dot(a, b)   = a.x*b.x + a.y*b.y + a.z*b.z
normalize(v)= if |v| == 0 { (0,0,0) } else { v / |v| }     // zero-safe, returns zero vector
```

Rodrigues rotation of `v` about unit axis `k` by angle `θ` (radians):

```rust
fn rotate_rodrigues(v: Vec3, k: Vec3, theta: f64) -> Vec3 {
    v * theta.cos() + cross(k, v) * theta.sin() + k * dot(k, v) * (1.0 - theta.cos())
}
```

### 1.5 Equatorial ⇄ topocentric transforms (SOFA/ERFA)

TPPA relies on two IAU SOFA routines (ERFA equivalents in Rust: the `erfa` /
`erfa-sys` crates):

- **J2000 RA/Dec → observed Az/Alt**: `iauAtco13(rc, dc, 0,0,0,0, jd_utc, 0,
  dut1, lon_rad, lat_rad, elevation_m, 0, 0, pressure_hpa, temp_c, rh, wavelength_um,
  → aob, zob, hob, dob, rob, eo)`; then `az = aob`, `alt = π/2 − zob`.
  With `pressure = 0` the routine applies **no refraction** (all four atmosphere
  parameters 0 ⇒ pure geometric transform). The observation time used is
  "now at call time" (see §5 note on timing).
- **Observed Az/Alt → J2000 RA/Dec**: `iauAtoc13("A", az_rad, zenith_dist_rad,
  jd_utc, 0, dut1, lon_rad, lat_rad, elevation_m, 0, 0, p, t, rh, wl, → ra, dec)`.
- `dut1` (UT1−UTC): NINA looks it up from an Earth-orientation database, defaulting
  to 0 when unavailable. **DUT1 = 0 is acceptable for this algorithm**: a DUT1
  error rotates all three measurement points rigidly about the true celestial
  pole, which leaves the fitted axis direction unchanged to first order.
- Julian date from UTC civil time in the usual way.

Epoch conversions J2000⇄JNOW (needed for test vectors) use
`iauAtci13`/`iauAtic13` with the equation-of-origins correction
(`ra_apparent = anp(ri − eo)`), i.e. CIRS-based apparent place.

### 1.6 Stereographic sky⇄pixel projection

All TPPA projection calls use the **stereographic** type (the source default).
`rotation` below is the solve `orientation` (= 360 − PA), interpreted **clockwise**.

Sky → pixel (`xy_projection`): project `target` (RA/Dec, J2000) into an image whose
center pixel `c_px` solves to `center` (RA/Dec, J2000):

```rust
fn xy_projection(target: RaDec, center: RaDec, c_px: Point,
                 scale_x_arcsec: f64, scale_y_arcsec: f64, rotation_deg: f64) -> Point {
    // unwrap RA to within ±180° of the center
    let mut ra_t = target.ra_deg;
    let d = ra_t - center.ra_deg;
    if d > 180.0  { ra_t -= 360.0; }
    if d < -180.0 { ra_t += 360.0; }

    let (sdt, cdt) = target.dec_rad.sin_cos();
    let (sdc, cdc) = center.dec_rad.sin_cos();
    let dra = to_rad(ra_t) - center.ra_rad;
    let (sr, cr) = to_rad(rotation_deg).sin_cos();

    let dd = 2.0 / (1.0 + sdt * sdc + cdt * cdc * dra.cos());
    let ra_mod  = dd * dra.sin() * cdt;                       // dimensionless (≈ radians on sky)
    let dec_mod = dd * (sdt * cdc - cdt * sdc * dra.cos());

    let (dx, dy) = if rotation_deg != 0.0 {
        (ra_mod * cr + dec_mod * sr, dec_mod * cr - ra_mod * sr)
    } else { (ra_mod, dec_mod) };

    Point {
        x: c_px.x - rad_to_arcsec(dx) / scale_x_arcsec,       // rad_to_arcsec = rad·(180/π)·3600
        y: c_px.y - rad_to_arcsec(dy) / scale_y_arcsec,
    }
}
```

Pixel → sky (`shift`): given the coordinates of the image center, compute the
RA/Dec of a point offset by `(dx_px, dy_px)` pixels:

```rust
fn shift(center: RaDec, dx_px: f64, dy_px: f64, rotation_deg: f64,
         scale_x_arcsec: f64, scale_y_arcsec: f64) -> RaDec {
    let dx_deg = dx_px * scale_x_arcsec / 3600.0;
    let dy_deg = dy_px * scale_y_arcsec / 3600.0;
    shift_stereographic(center, dx_deg, dy_deg, rotation_deg)
}

fn shift_stereographic(o: RaDec, dx_deg: f64, dy_deg: f64, rot_deg: f64) -> RaDec {
    let mut dx = -to_rad(dx_deg);
    let mut dy = -to_rad(dy_deg);
    let rot = to_rad(rot_deg);
    if rot != 0.0 {
        let (sr, cr) = rot.sin_cos();
        let dx0 = dx;
        dx = dx * cr - dy * sr;
        dy = dy * cr + dx0 * sr;
    }
    let (sd, cd) = o.dec_rad.sin_cos();
    let sins = dx * dx + dy * dy;
    let dz = (4.0 - sins) / (4.0 + sins);
    let dec = (dz * sd + dy * cd * (1.0 + dz) / 2.0).asin();
    let mut dra = (dx * (1.0 + dz) / (2.0 * dec.cos())).asin();
    // quadrant disambiguation:
    let mg = 2.0 * (dec.sin() * cd - dec.cos() * sd * dra.cos())
           / (1.0 + dec.sin() * sd + dec.cos() * cd * dra.cos());
    if (mg - dy).abs() > 1.0e-5 { dra = PI - dra; }
    let ra = emod(o.ra_deg + to_deg(dra), 360.0);
    RaDec { ra_deg: ra, dec_deg: to_deg(dec) }
}
```

Angular separation between two RA/Dec (used once, see §7 dead code note):

```
distance = acos( sin d1·sin d2 + cos d1·cos d2·cos(ra1 − ra2) )
```

---

## 2. Refraction model and parameter acquisition

### 2.1 Parameter set

`RefractionParameters { pressure_hpa, temperature_c, relative_humidity, wavelength_um }`

Acquisition, with a weather device optionally connected:

```rust
const STANDARD_PRESSURE_HPA: f64 = 1013.25;
const STANDARD_TEMPERATURE_C: f64 = 15.0;
const STANDARD_HUMIDITY: f64 = 0.0;
const DEFAULT_WAVELENGTH_UM: f64 = 0.55;

fn get_refraction_parameters(weather: Option<&WeatherInfo>, wavelength: f64 /*=0.55*/)
    -> RefractionParameters {
    match weather {
        Some(w) if w.connected => {
            let p = if w.pressure.is_nan() || w.pressure < 500.0 { STANDARD_PRESSURE_HPA } else { w.pressure };
            let t = if w.temperature.is_nan() || w.temperature < -100.0 || w.temperature > 100.0
                    { STANDARD_TEMPERATURE_C } else { w.temperature };
            let h = if w.humidity.is_nan() { STANDARD_HUMIDITY } else { w.humidity };
            RefractionParameters { pressure_hpa: p, temperature_c: t, relative_humidity: h, wavelength_um: wavelength }
        }
        _ => RefractionParameters { pressure_hpa: STANDARD_PRESSURE_HPA,
                                    temperature_c: STANDARD_TEMPERATURE_C,
                                    relative_humidity: STANDARD_HUMIDITY,
                                    wavelength_um: wavelength }
    }
}
```

The `pressure < 500` guard exists because real devices were seen reporting 0 hPa
(changelog 2.2.2.0).

**Unit caveat (upstream quirk):** NINA weather sources (ASCOM
ObservingConditions) report humidity in percent (0–100), but SOFA
`iauRefco`/`iauAtco13` expect a fraction 0–1 and clamp inputs to [0, 1]. So any
device humidity > 1% is effectively treated as 100% RH. Notably, TPPA's own test
suite passes humidity as a **fraction** (0.8) and only one test uses a
percent-style value (20, which the clamp turns into 1.0) — evidence the intended
unit is the fraction. The refraction difference is small (< a few arcsec), but
the Rust port should pass **fraction 0–1** and divide device percent by 100.

Refraction parameters are captured **once**, immediately after the first solve,
and reused for all three measurement `Position`s; the continuous phase re-reads
them on each display recomputation (they only affect the disabled-refraction pole
and display geometry there — see §7).

### 2.2 Refracted altitude of the pole (`calculate_refracted_altitude`)

Inverts the SOFA refraction model `dZ = A·tan(Z) + B·tan³(Z)` (A, B from
`iauRefco(p, t, rh, wl)`) to find the observed (refracted) altitude for a given
vacuum altitude, by scanning in 1-arcsec steps:

```rust
fn calculate_refracted_altitude(alt_deg: f64, p: f64, t: f64, rh: f64, wl: f64,
                                step_arcsec: f64 /*=1.0*/, max_iter: u32 /*=1000*/) -> f64 {
    assert!(alt_deg >= 0.0, "altitude must be >= 0");        // source throws
    let (refa, refb) = erfa_refco(p, t, rh, wl);
    let z = to_rad(90.0 - alt_deg);                          // true (vacuum) zenith distance
    let inc = to_rad(step_arcsec / 3600.0);
    let mut roller = inc;
    for _ in 0..max_iter {
        let z_refr = z - roller;                             // candidate observed ZD
        let dz = refa * z_refr.tan() + refb * z_refr.tan().powi(3);
        if dz.is_nan() { return f64::NAN; }
        if ((z_refr + dz) - z).abs() < inc {
            return 90.0 - to_deg(z_refr);                    // refracted altitude, degrees
        }
        roller += inc;
    }
    f64::NAN                                                 // > max_iter arcsec of refraction
}
```

- Works down to ~5° altitude; unreliable lower (documented upstream).
- Returns NaN if the model diverges or 1000 iterations (≈16.7′ of refraction) are
  exceeded; the caller falls back to the unrefracted pole and logs an error.

---

## 3. The measurement procedure

Three plate-solved fields separated by pure RA-axis rotation. High-level flow
(`PolarAlignment::Execute`):

```rust
fn execute(ctx) -> Result<()> {
    // 0. window/UI setup; a fresh session state (TPAPAVM) is created per run.
    //    Closing the window cancels the whole procedure (treated as normal end).
    log_all_settings();
    state.activate_step(1);                 // also resets UPA `last_movement = None`

    if !manual_mode {
        if !start_from_current_position {
            set_tracking_sidereal(true);                       // best effort, errors swallowed
            mount.slew_to(initial_alt_az_target)?;             // topocentric target, see §10
        }
        // start_from_current_position: no slew, tracking NOT forced on here
    } else if mount.connected {
        set_tracking_sidereal(true);                           // semi-manual mode
    }                                                          // blind manual: nothing

    if mount.connected && dome.connected { dome.wait_for_synchronization()?; }

    let solve1 = solve(search_radius_increment_on_failure = 5.0)?;   // §4
    let refr = get_refraction_parameters(weather.info());            // captured ONCE
    let pos1 = Position::from_solve(&solve1, lat, lon, elevation, &refr);   // §5

    state.activate_step(2);
    let solve2 = if !manual_mode { automated_next_point()? } else { manual_next_point(&solve1)? };
    let pos2 = Position::from_solve(&solve2, ...);

    state.activate_step(3);
    let mut solve3 = if !manual_mode { automated_next_point()? } else {
        let s = manual_next_point(&solve2)?;
        wait(10 s, "settle; keep tracking; don't move");       // manual mode only
        solve(5.0)?                                            // fresh, settled 3rd solve
    };
    let pos3 = Position::from_solve(&solve3, ...);

    // 4. Error determination — reference frame is the THIRD solve.
    let ped = PolarErrorDetermination::new(solve3, pos1, pos2, pos3,
                                           lat, lon, elevation, refr,
                                           settings.refraction_adjustment);   // §6
    state.activate_step(4);
    run_continuous_phase(ped)?;                                // §7
}
```

Notes:

- Tracking must be **sidereal and enabled** while measurements are captured; the
  code re-asserts it after every axis move. `set_tracking_sidereal` swallows all
  driver errors (mounts without settable tracking still work).
- On any non-cancellation exception: notify
  `"Three Point Polar Alignment failed - {msg}"`, close the window, rethrow (the
  sequence item fails). Cancellation (window closed, tolerance reached, user stop)
  is swallowed = normal termination.
- `finally`: dispose session, clear status, and if `stop_tracking_when_done`
  (default **true**) disable tracking.
- A pause/resume token is honored at three places: before each automated move,
  inside each manual-move wait iteration, and before each continuous-phase
  iteration.

### 3.1 RA distance metric

All "how far did the RA axis travel" checks use the wrap-safe degree distance:

```rust
fn ra_distance_deg(ra1_deg: f64, ra2_deg: f64) -> f64 {
    180.0 - ((ra1_deg - ra2_deg).abs() - 180.0).abs()          // result in [0, 180]
}
```

Distances are measured on the **mount-reported** RA (not the solved RA) whenever a
mount is connected — near the pole with several degrees of PA error, solved RA
differs wildly from axis rotation (upstream changelog 1.5.0.0).

### 3.2 Automated next point (`AutomatedNextPoint`)

```rust
fn automated_next_point() -> Result<PlateSolveResult> {
    let target = target_distance_deg as f64;                   // default 10
    let ra_before = mount.current_position().ra_degrees;

    wait_if_paused();
    move_to_next_point(target, move_rate)?;                    // §3.3
    if dome.connected { dome.wait_for_synchronization()?; }

    let solve = solve(search_radius_increment_on_failure = 5.0)?;

    let traveled = ra_distance_deg(ra_before, mount.current_position().ra_degrees);
    if traveled - target < -1.0 {                              // >1° shortfall
        warn!("mount did not move far enough ({traveled:.2}°/{target:.2}°) — \
               driver rate not in °/s? increase rate / adjust timeout factor");
        // warning only; the procedure continues with the shorter baseline
    }
    Ok(solve)
}
```

### 3.3 Axis move (`MoveToNextPoint`)

Uses the mount's *MoveAxis* facility on the primary (RA) axis. `EastDirection`
(default **true**) selects the sign: east ⇒ `+rate`, west ⇒ `−rate`.

```rust
fn move_to_next_point(distance_deg: f64, rate_deg_per_s: f64) -> Result<()> {
    let start = mount.current_position();
    // rate selection: from the mount's advertised primary-axis rate ranges
    // (list of (min,max)), sorted ascending by max, pick the LAST range where
    //   (min <= rate && rate <= max) || max < rate
    // If the chosen range's max < rate, the rate is unsupported: clamp to that max
    // and log a warning. (Source quirk: the warning prints the un-clamped rate.)
    let adjusted = clamp_to_supported(rate_deg_per_s);

    mount.move_axis(Primary, if east_direction { adjusted } else { -adjusted });

    // Failsafe timeout: rate is *expected* to be °/s
    let timeout = distance_deg / adjusted * move_timeout_factor;   // seconds; factor default 2
    let deadline = now() + timeout;
    loop {                                                     // poll every 100 ms
        if ra_distance_deg(mount.current_position().ra_degrees, start.ra_degrees) >= distance_deg { break; }
        if now() > deadline { break; }                         // timeout is NOT an error
        report_progress(traveled / distance);
        sleep(100 ms)?;                                        // user cancel propagates
    }
    // Cleanup happens even on error/cancel:
    mount.move_axis(Primary, 0.0);                             // stop
    while mount.info().slewing { sleep(500 ms); }              // wait for full stop
    wait(profile.telescope_settle_time_s);                     // profile setting
    set_tracking_sidereal(true);
    Ok(())
}
```

Edge cases:

- Unsupported rate ⇒ clamped down to the largest supported max below the request.
- **Rate list empty, or requested rate below every supported range's minimum**:
  `LastOrDefault` yields the default `(0, 0)` tuple, so `foundRate.max (=0) <
  rate` and the rate clamps to **0**. `move_axis(Primary, 0)` is then a no-op
  and the timeout computes `distance / 0 * factor = +∞`;
  `TimeSpan.FromSeconds(+∞)` **throws** (Overflow/ArgumentException), so the
  procedure deterministically aborts through the generic failure path
  (axis reset to 0 by the catch block, "Three Point Polar Alignment failed -
  ..." toast). Rust port: validate `adjusted > 0` and fail with a clear error
  instead.
- Timeout expiring mid-move is silently accepted; the subsequent shortfall check
  in §3.2 produces the user-facing warning.
- On any exception the axis rate is reset to 0 before rethrowing.

### 3.4 Manual next point (`ManualNextPoint`)

Two sub-modes:

**(a) Mount connected ("semi-manual", user presses N/E/S/W or hand controller):**

```rust
let ra_before = mount.current_position().ra_degrees;
loop {
    wait_if_paused();
    let d = ra_distance_deg(ra_before, mount.current_position().ra_degrees);
    if d - target >= -1.0 { break; }                      // within 1° of target is enough
    status!("Move mount along RA axis! {d:.2}°/{target:.2}°");
    sleep(1 s);
}
while mount.info().slewing {
    status!("Moved far enough. Stop axis rotation now!");
    sleep(500 ms)?;
}
wait(profile.telescope_settle_time_s);
set_tracking_sidereal(true);
if dome.connected { status!("Waiting for dome to synchronize"); dome.wait_for_synchronization()?; }
solve(5.0)                                                // fresh solve after settling
```

**(b) No mount connection ("blind" mode):** distance must be measured by solving.
Each iteration takes a full capture+**blind** solve; the RA distance is computed
between the *previous point's solve* and the current solve:

```rust
let mut solve = None;
loop {
    wait_if_paused();
    let s = solve_blind(5.0)?;                            // blind solver (§4)
    let d = ra_distance_deg(previous_solve.ra_degrees, s.coordinates.ra_degrees);
    solve = Some(s);
    if d - target >= -1.0 { break; }
    status!("Move mount along RA axis! ...");
    sleep(1 s);
}
return solve.unwrap();                                    // last solve IS the measurement
```

For the **third** point in either manual sub-mode, the caller additionally waits
10 s ("make sure the scope is tracking and don't move any further!") and takes one
more solve, which replaces the loop's result (§3 flow). The second point gets no
such re-solve — in blind mode it may have been captured while still creeping.

---

## 4. Plate-solve wrapper (capture + retry)

One routine serves both the three measurements and the continuous phase; only the
search-radius escalation differs.

```rust
fn solve(search_radius_increment_on_failure: f64) -> Result<PlateSolveResult> {
    let mut used_radius = self.search_radius;                  // instruction setting, °
    loop {
        check_cancelled()?;
        // Blind solver only when manual mode AND no mount connection.
        let capture = CaptureSpec {
            binning, gain, offset, exposure_time, filter,
            image_type: Snapshot,                              // no dark/flat handling
        };
        let image = match camera.capture_and_prepare(capture /* autoStretch=true, detectStars=false */) {
            Ok(img) => img, Err(e) => { log(e); wait(1 s, "Image capture failed. Retrying..."); continue; }
        };
        session.image = image;                                 // displayed; flags "waiting for update"

        let params = PlateSolveParams {
            binning: binning.x.unwrap_or(1),
            hint_coordinates: mount.current_position(),        // even in blind mode (ignored there)
            downsample: profile.plate_solve.downsample,
            focal_length: profile.telescope.focal_length,
            max_objects: profile.plate_solve.max_objects,
            pixel_size: profile.camera.pixel_size,             // unbinned µm
            regions: profile.plate_solve.regions,
            search_radius: used_radius,
            disable_notifications: true,
        };
        match solver.solve(image, params) {
            Ok(r) if r.success => return Ok(r),
            _ => {
                used_radius += search_radius_increment_on_failure;   // +5° per failure (measurements)
                wait(1 s, "Plate solve failed. Retrying...");        // +0° in continuous phase
            }
        }
    }
}
```

Failure policy — **there is no maximum failure count**. The loop retries forever
until success or user cancellation, growing the radius by 5° per failure during
the three measurement points (unbounded — no 180° cap in the loop; solvers clamp
internally) and keeping it constant during the continuous phase. Capture failures
also retry forever at 1 s intervals. The escalated radius is a **local** of each
`solve()` invocation: every new measurement point / continuous-phase frame starts
again from the configured `SearchRadius`; escalation never persists across calls.

---

## 5. `Position` — solve → topocentric unit vector

```rust
struct Position {
    topocentric: AltAz,       // observed az/alt at the observer, refraction applied
    vector: Vec3,             // unit vector, frame of §1.3
    position_angle: f64,      // camera PA of the solve, degrees
}

fn position_from_solve(solve: &PlateSolveResult, lat, lon, elevation_m,
                       refr: &RefractionParameters) -> Position {
    // J2000 RA/Dec → observed alt/az via iauAtco13 with the FULL refraction
    // parameter set, evaluated at the current system time:
    let topo = atco13_transform(solve.coordinates_j2000, lat, lon, elevation_m,
                                refr.pressure_hpa, refr.temperature_c,
                                refr.relative_humidity, refr.wavelength_um,
                                now_utc());
    Position { topocentric: topo, vector: altaz_to_unit_vector(topo.az, topo.alt),
               position_angle: solve.position_angle }
}
```

Timing note: the transform time is "now" at the moment the `Position` is built
(shortly after the solve completes), not the exposure midpoint. This is correct by
design: with the mount tracking sidereally, the solved J2000 field center is
time-invariant, and what the algorithm needs is the *current* alt/az of the mount
pointing. It does mean the mount must genuinely be tracking between capture and
computation; tracking interruptions inject error (documented in the upstream FAQ).

**Refraction is always applied here** (with standard-atmosphere fallback values if
no weather source). The refraction *setting* only changes which pole the axis is
compared against (§6.2).

---

## 6. `PolarErrorDetermination` — 3 vectors → mount axis → alt/az error

### 6.1 Axis from the plane through the three points

The three unit vectors lie on a small circle about the mount's RA axis; the circle
plane's normal *is* the axis direction:

```rust
fn determine_plane_vector(a: Vec3, b: Vec3, c: Vec3) -> Vec3 {
    normalize(cross(b - a, c - b))
}

let mut axis = determine_plane_vector(pos1.vector, pos2.vector, pos3.vector);
// Hemisphere disambiguation: in the north the axis must point northward (+x),
// in the south southward (−x). northern := latitude_deg > 0 (exactly 0 ⇒ south branch).
if (northern && axis.x < 0.0) || (!northern && axis.x > 0.0) { axis = -axis; }
let axis_topo = unit_vector_to_altaz(axis);          // az may be negative (treated mod 360)
```

Properties / edge cases:

- Point order doesn't matter (verified by upstream test: reversing the order
  changes the raw normal's sign, which the hemisphere flip re-normalizes; errors
  agree to < 1″).
- If any two points coincide (mount didn't actually move) the cross product is 0;
  `normalize` yields the zero vector, which `unit_vector_to_altaz` maps to
  az 0°, alt 90° — the result is garbage but no exception is raised. The Rust port
  should detect `|cross| < ε` and fail with "mount did not move between points".
- No least-squares: exactly three points, exact plane. Measurement noise is
  mitigated procedurally (bigger `target_distance`, warnings below), not
  numerically.

### 6.2 Axis → altitude/azimuth polar error

```rust
fn calculate_mount_axis_error(axis_topo: AltAz, lat_deg: f64, refr: &RefractionParameters,
                              correct_for_refraction: bool) -> (f64 /*alt_err_deg*/, f64 /*az_err_deg*/) {
    let mut pole = lat_deg.abs();                        // true-pole altitude
    if !correct_for_refraction {
        // Compare against the REFRACTED pole instead of the true pole:
        let refracted = calculate_refracted_altitude(pole, refr.pressure_hpa,
                          refr.temperature_c, refr.relative_humidity, refr.wavelength_um,
                          1.0, 1000);
        if refracted.is_nan() {
            log_error!("refracted pole could not be calculated ... falling back");
        } else { pole = refracted; }
    }

    let (alt_err, mut az_err) = if northern {
        (axis_topo.alt_deg - pole,           axis_topo.az_deg)
    } else {
        (pole - axis_topo.alt_deg,           axis_topo.az_deg + 180.0)
    };
    // wrap azimuth error into (−180, 180]
    if az_err >  180.0 { az_err -= 360.0; }
    if az_err < -180.0 { az_err += 360.0; }
    (alt_err, az_err)
}

total_err_deg = hypot(alt_err_deg, az_err_deg);
```

Semantics of `correct_for_refraction` (`RefractionAdjustment` setting,
default **false**):

- The measurement vectors are *always* refraction-corrected observed alt/az (§5).
  The mount axis derived from them is therefore the axis of the *refracted* sky's
  apparent rotation, which sits slightly **above** the true pole.
- `true` ("Adjust for refraction" ON): compare against the **true pole**
  (altitude = |latitude|). Result: the reported error, when zeroed, aligns the
  axis with the true celestial pole.
- `false` (default): compare against the **refracted pole** (apparent pole
  altitude). Result: zeroing the error aligns with the apparent rotation axis of
  the refracted sky — the usual practical choice for tracking.
- Refracted pole ≈ true pole + refraction(|lat|); e.g. at lat 40°,
  P=1005 hPa, T=7 °C, RH=0.8 (fraction, within [0,1] — not clamped),
  λ=0.574 µm ⇒ ≈ +69.3″ (test-verified).

### 6.3 Sign conventions and knob directions

| Quantity | Positive means (Northern) | Positive means (Southern) |
|---|---|---|
| `alt_err` | axis **above** the pole → "Move down 🠗" | axis **below** the pole (pole−alt>0) → "Move up 🠕" |
| `az_err` | axis **east of north** → "🠔 Move left/west" | axis west of south (az>180) → "🠔 Move left/east" |
| negative `alt_err` | "Move up 🠕" | "🠗 Move down" |
| negative `az_err` | "Move right/east 🠖" | "Move right/west 🠖" |

("left/right" as seen facing the pole.)

### 6.4 Quality flags computed alongside

```rust
position_angle_spread = max over pairs (1,2),(2,3),(3,1) of
    min(|pa_i − pa_j|, 360 − |pa_i − pa_j|)          // wrap-aware, degrees
position_angle_spread_large = spread > 5.0            // UI warning: camera rotated
                                                      // between frames ⇒ not a pure RA move
initial_error_large = total > 2.0° && total <= 10.0°  // warn: adjustment phase error-prone,
                                                      //   re-run after reducing error
initial_error_huge  = total > 10.0°                   // warn: mount way off / wrong site
                                                      //   location / RA axis not moved exclusively
```

None of these aborts the procedure; they are warnings only.

The initial errors are frozen as `initial_{alt,az,total}_error`; the *current*
errors start equal to them and are re-estimated continuously (§7). The
`initial_reference_frame` = `current_reference_frame` = **solve 3**.

---

## 7. Continuous-update phase (adjustment loop)

After the initial error is known, the mount keeps tracking and TPPA loops:
capture → solve → update displayed error, while the user turns the alt/az knobs.
**The axis is not re-fit** — each new solve re-scales the initial error via a
pixel-space geometric construction.

### 7.1 Loop skeleton

```rust
fn run_continuous_phase(ped: &mut PolarErrorDetermination) -> Result<()> {
    if upa.enabled { upa.connect(); if upa.do_automated_adjustments && !upa.connected {
        bail!("Unable to connect to Universal Polar Alignment system...") } }

    arcsec_per_pix = arcsec_per_pixel(pixel_size * bin_x, focal_length);
    center = (image_width/2, image_height/2);
    select_new_reference_star(center)?;                       // §8; anchor graphics on a star

    let started = Instant::now();
    let correlation_id = new_uuid();                          // constant for this session
    loop {
        wait_if_paused();
        let solve = solve(search_radius_increment_on_failure = 0.0)?;   // §4, blocks until success
        update_details(&solve)?;                                        // §7.2
        publish_error_message(correlation_id, ped.current_errors());    // §13

        if ped.current_total_error.arcminutes().abs() <= alignment_tolerance_arcmin {
            // NOTE: default tolerance = 0 ⇒ never triggers (error is never exactly 0)
            notify_info!("Total Error is below alignment tolerance ... finishing");
            cancel();                                          // normal completion
        }
        if started.elapsed() > 5 min {                         // one-shot notice
            notify_info!("correction phase running for multiple minutes; \
                          consider restarting to improve precision");
            stop_measuring_elapsed();
        }
        upa_move_closer()?;                                    // §9; no-op unless UPA automation on
        if settings.auto_pause { pause(); }                    // wait for user resume each frame
    }
}
```

### 7.2 Per-solve update (`UpdateDetails`)

```rust
fn update_details(psr: &PlateSolveResult) -> Result<()> {
    ped.current_reference_frame = psr.clone();
    // Upstream contains an unreachable "re-acquire star" branch here. The code
    // is (C# shape): assign CurrentReferenceFrame = psr FIRST, then test
    //   angular_separation(psr.coordinates, current_reference_frame.coordinates)
    //       .arcseconds() > arcsec_per_pix
    // — i.e. "did the frame center move by more than 1 pixel since the previous
    // solve?". Because of the assignment-before-compare bug the separation is
    // always 0 and the branch never runs. Its body would have been:
    //   let p = xy_projection(reference_star_coords, psr.coordinates, center,
    //                         s, s, orientation(psr));
    //   select_new_reference_star(p)?;   // §8: star detection near projected pos
    // Effective (reachable) behavior:
    reference_star_px = xy_projection(reference_star_coords /*J2000*/,
                                      psr.coordinates, center,
                                      arcsec_per_pix, arcsec_per_pix,
                                      orientation(psr));       // = mod(360−PA,360)
    calculate_error_details();                                 // §7.3
    waiting_for_update = false;                                // clears UI spinner
}
```

### 7.3 The exact update math (`CalculateErrorDetails`)

Everything happens in **pixel space of the current image**, using stereographic
projections around the current solve's center. Let:

- `init` = initial reference frame (solve 3: J2000 center + PA),
- `cur` = current reference frame (latest solve),
- `rot` = `orientation(cur)`, `s` = `arcsec_per_pix`, `C` = image center pixel,
- `azErr0`, `altErr0` = the *initial* errors in degrees (§6.2).

```rust
fn calculate_error_details() {
    let refr = get_refraction_parameters(weather.info());     // re-read each update

    // (1) Where does the ORIGINAL frame center now appear?
    let mut origin_px = xy_projection(init.coordinates, cur.coordinates, C, s, s, rot);
    let shift2 = (C - origin_px) * 2.0;                       // reflection through center
    origin_px += shift2;                                      // = 2C − origin_px_raw

    // (2) Destination = original center as it would appear if the axis were moved
    //     by (−azErr0, −altErr0): rotate on the celestial sphere, then project.
    let dest_eq   = topo_to_j2000_norefr(get_destination_coordinates(-azErr0, -altErr0));
    let dest_px   = xy_projection(dest_eq, cur.coordinates, C, s, s, rot) + shift2;

    // (3) Azimuth-only and altitude-only corner points of the correction "parallelogram".
    let azonly_eq  = topo_to_j2000_norefr(get_destination_coordinates(-azErr0, 0.0));
    let azonly_px  = xy_projection(azonly_eq, ...) + shift2;
    let altonly_eq = topo_to_j2000_norefr(get_destination_coordinates(0.0, -altErr0));
    let altonly_px = xy_projection(altonly_eq, ...) + shift2;

    // (4) Azimuth leg: translate the origin→azonly line so it passes through C
    //     (same slope), and intersect with the azonly→dest line.
    let m_az = slope(origin_px, azonly_px);                    // (y2−y1)/(x2−x1)
    let line_az_c   = Line { slope: m_az, intercept: C.y - m_az * C.x };
    let line_az_dst = Line::from_points(azonly_px, dest_px);
    let corr_az_px  = intersect(line_az_dst, line_az_c);       // None if parallel ⇒ upstream panics
    let corr_az_dist = dist(corr_az_px, C);
    let orig_az_dist = dist(azonly_px, origin_px);

    // (5) Altitude leg, symmetric.
    let m_alt = slope(origin_px, altonly_px);
    let line_alt_c   = Line { slope: m_alt, intercept: C.y - m_alt * C.x };
    let line_alt_dst = Line::from_points(altonly_px, dest_px);
    let corr_alt_px  = intersect(line_alt_dst, line_alt_c);
    let corr_alt_dist = dist(corr_alt_px, C);
    let orig_alt_dist = dist(altonly_px, origin_px);

    // (6) Signs: has the user overshot past the destination on either axis?
    //     dest − altonly is parallel to the AZIMUTH leg (parallelogram side);
    //     compare with dest − corrected-altitude point.
    let az_sign  = if dot2(dest_px - altonly_px, dest_px - corr_alt_px) > 0.0 { 1.0 } else { -1.0 };
    let alt_sign = if dot2(dest_px - azonly_px,  dest_px - corr_az_px ) > 0.0 { 1.0 } else { -1.0 };

    // (7) Current error = initial error rescaled by remaining/original leg length.
    ped.current_az_err  = azErr0  * az_sign  * corr_az_dist  / orig_az_dist;   // degrees
    ped.current_alt_err = altErr0 * alt_sign * corr_alt_dist / orig_alt_dist;
    ped.current_total   = hypot(ped.current_alt_err, ped.current_az_err);

    // (8) Display quads (anchored on the reference star; see §8):
    //     ErrorDetail  = [C, corr_alt_px, corr_az_px, dest_px]   (corrected overlay)
    //     ErrorDetail2 = [origin_px, altonly_px, azonly_px, dest_px] (initial overlay)
    //     both shifted by (reference_star_px − C).
    // (9) Optional JSON-lines log of {lat, lon, alt_err, az_err, total_err} when LogError.
}
```

Interpretation: `origin_px` (after the reflection) is where the mount's *pointing*
sits relative to the initial frame; the destination is where it must go for a
perfect axis. As knobs move the sky, the current image center `C` slides along the
correction parallelogram; each leg's remaining length, divided by its original
length, scales the corresponding initial error component. The dot-product test
flips the sign when the user overshoots past the destination.

### 7.4 Destination coordinates (`GetDestinationCoordinates`)

Rotates the initial frame center on the celestial sphere by an azimuth change then
an altitude change, in the topocentric frame of §1.3:

```rust
fn get_destination_coordinates(az_angle_deg: f64, alt_angle_deg: f64) -> AltAz {
    // NOTE: the source accepts refraction parameters here but NEVER uses them;
    // the transform below is refraction-free by construction.
    let ref_topo = atco13_transform(init.coordinates, lat, lon, elevation,
                                    /*p,t,rh,wl =*/ 0.0, 0.0, 0.0, 0.0, now_utc());
    let v = altaz_to_unit_vector(ref_topo.az, ref_topo.alt);
    let az_rot  = to_rad(az_angle_deg);
    let alt_rot = to_rad(alt_angle_deg);

    let z_axis = Vec3::new(0.0, 0.0, 1.0);                    // zenith
    let az_dest = rotate_rodrigues(v, z_axis, az_rot);        // rotate about zenith
    // altitude axis = the east-west axis, itself rotated by the azimuth change:
    let alt_axis = rotate_rodrigues(Vec3::new(0.0, 1.0, 0.0), z_axis, az_rot);
    let final_dest = rotate_rodrigues(az_dest, alt_axis, alt_rot);
    unit_vector_to_altaz(final_dest)                          // then atoc13 (no refraction) → J2000
}
```

The conversion of these destination alt/az back to J2000 for projection uses the
refraction-free `iauAtoc13` (all atmosphere parameters zero) at "now".

### 7.5 Degenerate geometry (why the FAQ warns about certain sky areas)

The line-intersection construction fails or degenerates when:

- pointing at **azimuth exactly 90°/270°**: the altitude leg's pixel projection
  collapses (altitude correction unobservable);
- pointing at the **zenith**: the azimuth leg collapses;
- either leg is vertical in pixel space: `slope` is ±∞ and
  `Line::from_slope_intercept` misbehaves;
- the two lines are parallel: `intersect` returns none and the upstream code
  dereferences it (NullReference ⇒ procedure aborts with the generic failure
  notification).

The Rust port should detect near-parallel/vertical configurations explicitly and
surface a "move to a better sky area" error instead of crashing. Also avoid
crossing the meridian during measurements/adjustments (FAQ guidance; the math
doesn't forbid it, but the mount may flip or stop tracking).

---

## 8. Reference-star tracking and display geometry

The overlay is anchored to a real star so the user can watch it walk into the
target circle. Values of the error are **independent** of the chosen star; only
the graphics shift.

```rust
fn select_new_reference_star(p: Point) -> Result<()> {        // called with image center
    // Serialized by a lock; user can also left-click any star at any time.
    let stars = detect_stars(current_image,                    // NINA star detection with
                             profile.star_sensitivity,         //   profile sensitivity +
                             profile.noise_reduction)?;        //   noise reduction settings
    // nearest star by squared pixel distance; cache detection per image id
    reference_star_px = stars.min_by(d2(star, p)).map(star.position)
        .unwrap_or_else(|| { warn!("No star could be found. Using previous reference point"); p });

    // The star's J2000 coords via stereographic un-projection from the current frame:
    reference_star_coords = shift(cur.coordinates,
                                  reference_star_px.x - C.x,
                                  reference_star_px.y - C.y,
                                  orientation(cur), arcsec_per_pix, arcsec_per_pix);
    calculate_error_details();
}
// Any error here ⇒ warning toast "Failed to determine new reference star on
// current image"; the previous reference remains in use. Never fatal.
```

On every subsequent solve the star's pixel position is re-projected (§7.2), so the
overlay follows the star even as the user moves the mount. Dragging the overlay
(`DragMove`) simply translates the displayed quad — cosmetic only.

Target circles are drawn at **30″, 1′ and 5′** radii based on `arcsec_per_pix`.

---

## 9. Avalon UPA automated adjustments (optional)

Only active when `use_avalon_polar_alignment_system && do_automated_adjustments`.
A GRBL-based two-axis motorized adjuster is nudged toward zero error after each
solve (`MoveCloser`):

```rust
fn upa_move_closer() -> Result<()> {
    if !(upa.enabled && upa.do_automated_adjustments) { return Ok(()); }
    let az = ped.current_az_err;      // degrees
    let alt = ped.current_alt_err;

    // Adaptive direction learning: if the previous nudge was single-axis and the
    // corresponding error grew by >15%, flip that axis's sign permanently.
    let mut az_sign  = last_movement.map(|m| m.az_sign ).unwrap_or(1.0);
    let mut alt_sign = last_movement.map(|m| m.alt_sign).unwrap_or(1.0);
    if let Some(m) = &last_movement {
        if m.alt == 0.0 && m.az != 0.0 && az.abs() > (m.az_err_before * 1.15).abs() {
            az_sign = -m.az_sign;                             // azimuth axis is reversed
        } else if m.az == 0.0 && m.alt != 0.0 && alt.abs() > (m.alt_err_before * 1.15).abs() {
            alt_sign = -m.alt_sign;
        }
    }

    // Correct the LARGER error component only, with a 0.75 proportional gain.
    // UPA position units are calibrated ≈ 1 unit per arcminute via gear ratios.
    if az.abs() > alt.abs() {
        let nudge = az.arcminutes() as f32 * az_sign * 0.75;
        upa.nudge_x(nudge)?;                                  // applies ReverseAzimuth flag, backlash comp
        last_movement = Some(Movement { az: nudge, alt: 0.0, az_sign, alt_sign, az_err_before: az, alt_err_before: alt });
    } else {
        let nudge = alt.arcminutes() as f32 * alt_sign * 0.75;
        upa.nudge_y(nudge)?;
        last_movement = Some(Movement { az: 0.0, alt: nudge, az_sign, alt_sign, az_err_before: az, alt_err_before: alt });
    }
    wait(upa.automated_adjustment_settle_time_s);             // default 2 s
}
```

`last_movement` is reset to `None` at the start of every alignment run.

Error handling in the nudge layer: `NudgeX`/`NudgeY` catch **all** exceptions
internally and only log them — a failed or timed-out nudge never aborts the
alignment loop; the next solve simply sees an unchanged (or partially changed)
error and the 1.15× reversal heuristic may fire. The settle wait always runs.

Hardware layer (for completeness; serial GRBL dialect):

- Auto-detected by probing every serial port at **115200 8N1**, sending `"?"` and
  matching `<(?<status>\w+)\|MPos:(?<x>...),(?<y>...),(?<z>...)\|`. No match on
  any port ⇒ "Unable to find Avalon Polar Alignment System".
- Relative move: `$J=G91G21{X|Y|Z}{delta_units×gear_ratio}F{speed}`; absolute:
  `$J=G53{axis}{pos×gear_ratio}F{speed}`. After sending, poll `"?"` every 300 ms
  until `|position − target| ≤ 0.01` steps.
- Backlash clearing (X axis only): when the move direction changed vs. the
  previous move and `x_backlash_compensation ≠ 0`, jog `−comp` then `+comp`.
- `ReverseAzimuth`/`ReverseAltitude` flags negate the commanded delta.
  (Upstream bug: the manual **absolute** Y move (`MoveY`) negates its target by
  `ReverseAzimuth` instead of `ReverseAltitude`; the automated path only uses
  the relative nudges, which apply the correct flags.)
- Gear ratios: X default 2, Y default 22, clamped ≥ 1; speeds default 700.
- Positions are displayed as `steps / gear_ratio` (≈ arcminutes).

Validation: automated adjustments with `alignment_tolerance == 0` is rejected
up-front ("Please set an alignment tolerance!") because the loop would never
terminate on its own.

---

## 10. Configuration knobs

### 10.1 Per-instruction settings (serialized with the sequence)

| Setting | Default | Range / validation | Unit | Notes |
|---|---|---|---|---|
| `ManualMode` | false | bool | — | no-mount / no-MoveAxis fallback |
| `StartFromCurrentPosition` | false | bool | — | skips initial slew (auto mode) |
| `Coordinates` (start alt/az) | N: az = 0° + `DefaultAzimuthOffset`, alt = lat + `DefaultAltitudeOffset`; S: az = 180° + azOffset, alt = |lat| + altOffset | topocentric | ° | i.e. defaults: N (az 1°, alt lat+2°); S (az 181°, alt |lat|+2°) |
| `TargetDistance` | 10 | degrees-rule validated (int) | ° | RA rotation per step |
| `MoveRate` | 3 | clamped to mount's supported primary-axis rates | °/s | auto mode only |
| `EastDirection` | true | bool | — | east ⇒ `+rate` on primary axis |
| `SearchRadius` | 10 stored → **clamped to [30, 180]** ⇒ effective 30 | [30, 180] | ° | plate-solve hint radius; +5°/failure during measurements |
| `AlignmentTolerance` | 0 (= disabled) | ≥ 0 | arcmin | auto-finish threshold on \|total error\| |
| `ExposureTime` | profile plate-solve exposure | > 0 | s | |
| `Filter` | none (sequencer) / plate-solve filter (imaging-tab panel) | installed filters | — | FW must be connected if set |
| `Binning` | profile plate-solve binning | camera modes | — | |
| `Gain` | profile plate-solve gain | −1 (camera default) or [camera min, max] | — | |
| `Offset` | −1 (camera default) | −1 or [camera min, max] | — | |

### 10.2 Plugin-level settings (global)

| Setting | Default | Range | Unit | Purpose |
|---|---|---|---|---|
| `DefaultMoveRate` | 3 | — | °/s | seed for new instructions |
| `DefaultEastDirection` | true | bool | — | |
| `DefaultTargetDistance` | 10 | — | ° | |
| `DefaultSearchRadius` | 10 stored, setter clamps [30,180] | [30,180] | ° | |
| `DefaultAltitudeOffset` | 2 | — | ° | start-position offset above pole |
| `DefaultAzimuthOffset` | 1 | — | ° | start-position offset from pole azimuth |
| `MoveTimeoutFactor` | 2 | > 0 | × | move failsafe = distance/rate × factor |
| `RefractionAdjustment` | **false** | bool | — | true ⇒ aim at true pole; false ⇒ refracted pole (§6.2) |
| `AlignmentTolerance` (default seed) | 0 | ≥ 0 (setter clamps) | arcmin | |
| `StopTrackingWhenDone` | true | bool | — | tracking off in `finally` |
| `AutoPause` | false | bool | — | pause after every continuous solve |
| `LogError` | false | bool | — | JSON-lines error log to Documents/N.I.N.A/PolarAlignment |
| `UseAvalonPolarAlignmentSystem` | false | bool | — | |
| `DoAutomatedAdjustments` | false | bool | — | requires UPA + tolerance > 0 |
| `AutomatedAdjustmentSettleTime` | 2 | — | s | |
| `AvalonXGearRatio` / `AvalonYGearRatio` | 2 / 22 | ≥ 1 | steps per unit | 1 unit ≈ 1 arcmin |
| `AvalonXSpeed` / `AvalonYSpeed` | 700 / 700 | — | GRBL feed | |
| `AvalonXBacklashCompensation` | 0 | — | steps | 0 = off |
| `AvalonReverseAzimuth` / `AvalonReverseAltitude` | false / false | bool | — | |
| UI colors | alt #FFFF00, az #00FFFF, total #FF0000, target circle #FF0099, success #32CD32 | — | — | display only |

### 10.3 Profile settings consumed (not TPPA-owned)

Telescope settle time (s), focal length (mm), camera pixel size (µm), plate-solver
choice + downsample/max-objects/regions, star-detection sensitivity/noise
reduction, site latitude/longitude/elevation.

---

## 11. Validation, edge cases, and failure paths (complete)

Pre-run validation (`Validate()`) — any item blocks start:

1. Latitude == 0 **and** longitude == 0 ⇒ "No location has been set..." (0,0 is
   mid-Atlantic; almost certainly unset).
2. Camera not connected.
3. Gain set (> −1) but outside camera [min, max]; same for offset.
4. Filter selected but filter wheel not connected ("either connect the filter
   wheel or clear the filter selection").
5. Auto mode: mount not connected ("switch to manual mode..."), or mount lacks
   `CanMovePrimaryAxis` (MoveAxis unsupported ⇒ manual mode required).
6. Mount connected but parked ⇒ "unpark first".
7. Primary solver is Astrometry.NET (too slow) — rejected; in full-blind manual
   mode the *blind* solver being Astrometry.NET is rejected instead.
8. UPA automation enabled with `AlignmentTolerance == 0` ⇒ rejected.

Runtime paths:

| Situation | Behavior |
|---|---|
| Capture throws / returns null | log, wait 1 s, retry forever |
| Plate solve fails | wait 1 s, radius += 5° (measurements) or +0 (continuous), retry forever |
| Mount move timeout (rate not °/s) | move stops at timeout; if traveled > 1° short ⇒ warning toast with remediation tips; procedure continues with shorter baseline |
| Requested move rate unsupported | clamp to largest supported max below request (warning) |
| Rate list empty or rate below all supported ranges | rate clamps to 0 ⇒ `TimeSpan.FromSeconds(∞)` throws ⇒ deterministic abort via generic failure toast (§3.3) |
| Dome connected | wait for dome synchronization after initial slew and after every axis move (auto + semi-manual) |
| Refracted-pole computation NaN | log error, fall back to unrefracted pole |
| Three points degenerate (no motion) | zero normal → axis reported as zenith (garbage); **not detected upstream** — detect in port |
| Position-angle spread > 5° | UI warning flag only |
| Initial total error > 2°..10° / > 10° | UI warning flags only |
| No star near reference point | keep previous reference point, warning log |
| Reference-star selection error | warning toast, previous star kept |
| Parallel/vertical lines in §7.3 | upstream crashes (procedure aborts with generic failure); port must pre-detect |
| Continuous phase > 5 min | one-shot "consider restarting" notification |
| \|total error\| ≤ tolerance (tolerance > 0) | success notification + auto-finish (cancel) |
| Window closed / user stop | cancellation ⇒ silent, normal termination |
| UPA connect failure with automation on | hard failure of the sequence item |
| Any other exception | toast "Three Point Polar Alignment failed - {msg}", rethrow |
| Always (`finally`) | stop status, dispose, stop tracking if `StopTrackingWhenDone` |

Known upstream quirks the port should FIX (behavioral notes, not parity targets):

- **Dead sibling error method (do NOT port this one).** `PolarAlignment.cs`
  (`Instructions/PolarAlignment.cs` L620–630) defines a private
  `CalculateError(TopocentricCoordinates axis)` that is **never called** (grep:
  only `TPAPAVM.CalculateErrorDetails` is used in the live path). It computes a
  *different, incorrect* formula from the production `CalculateMountAxisError`
  (§6.2): Northern `altErr = axis.alt − latitude`, `azErr = axis.az` (matches);
  but Southern `altErr = axis.alt + latitude` (latitude is negative in the south,
  so ≈ `alt − |lat|`, the **opposite sign** of §6.2's `pole − alt`) and it has
  **no refracted-pole logic at all**. It is a stale leftover. The authoritative
  error math is §6.2 (`CalculateMountAxisError`); ignore this method entirely.
- `UpdateDetails` re-acquire branch is dead code: `CurrentReferenceFrame` is
  assigned the new solve *before* the comparison, so the separation is always
  0. The written trigger is explicit in the code — "frame center moved more
  than `arcsec_per_pix` arcsec (= 1 px) since the previous solve ⇒ re-run star
  detection at the star's projected position" (§7.2). Implement that intent
  with the comparison against the *previous* frame.
- Manual absolute `MoveY` (UPA UI) negates by `ReverseAzimuth` instead of
  `ReverseAltitude` (§9); the automated nudge path is unaffected.
- `Coordinates.Transform(lat, lon, elev, p, t, rh, wl)` (7-arg, no time) in NINA
  silently drops the refraction parameters; TPPA's production path uses the 8-arg
  overload and is unaffected.
- Humidity percent-vs-fraction mismatch (§2.1).
- `ArcsecPerPix` binning null-coalescing bug (§1.2).
- Dockable start-message handler reads the *filter name* from the
  `AlignmentTolerance` key (§13).
- Warning for unsupported move rate logs the unclamped value.

---

## 12. Golden test vectors (upstream test suite)

All tolerances 1″ = 1/3600° unless noted. `CustomTime` pins both wall clock and
UTC to **2000-01-01T00:00:00Z** for every transform.

1. **Symmetry / non-null error** — lat 49°, lon 7°E, elev 250 m. Weather:
   connected, pressure 0 (⇒ std 1013.25), T = 0.0001 °C, RH = 20 (clamped to 1.0
   inside SOFA), λ = 0.55 µm. Points given in **JNOW** then converted to J2000:
   (RA 20°, Dec 40°), (60°, 41°), (90°, 42°). With `correct_for_refraction=true`:
   `error(1,2,3)` equals `error(3,2,1)` in alt/az/total within 1″, and
   |total| > 1° (the upstream assertion is `NotBeApproximately(0, 0001)` — the
   precision literal `0001` parses as **1.0 degree**, so the guaranteed bound is
   "more than 1° from zero", not merely non-zero).
2. **Pure-RA circle ⇒ zero error** — same site; refraction params
   (0, 0.0001 °C, 0, 0) ⇒ no refraction. JNOW points (20°,40°), (60°,40°),
   (90°,40°) (constant dec = circle about the true pole).
   alt/az/total errors ≈ 0 within 1″.
3. **Synthetic misaligned axis, low altitude** — lat 40°, lon 0°, elev 250 m;
   weather P=1005 hPa, T=7 °C, RH=0.8 (a 0–1 **fraction**; passes through SOFA
   unclamped), λ=0.574 µm. Axis at (az 1°, alt 41°) topocentric; first point
   (az 70°, alt 20°); rotate twice by −30° (Rodrigues, about the axis) for
   points 2, 3; convert each to J2000 **with refraction**. The three positions
   are passed to the fit in reversed order (3, 2, 1) with solve 1 as the
   reference frame — order does not affect the error (§6.1).
   - `correct_for_refraction = true` ⇒ altErr = **1.0°**, azErr = **1.0°** (±1″).
   - `correct_for_refraction = false` ⇒ altErr = **1.0° − 69.3″**, azErr = 1.0°.
4. **Astropy cross-check (same scenario, precomputed J2000 inputs)** —
   s1 = (186.4193401°, +27.75369312°), s2 = (156.6798968°, +27.40124463°),
   s3 = (127.00972423°, +27.34989335°), J2000@2000-01-01. Same expectations as 3
   (true pole: 1.0/1.0; refracted pole: altErr = 1.0 − 69.3/3600).
5. **Astropy near-pole points** — s1 = (120.40437197°, +87.88183512°),
   s2 = (133.73661393°, +87.76679023°), s3 = (147.13178399°, +87.8022287°),
   J2000@2000-01-01, same site/atmosphere. True pole ⇒ altErr = azErr = 1.0°;
   refracted pole ⇒ altErr = 1.0 − **69**/3600, azErr = 1.0.
6. Sanity property (tests 3/4/5 setup): transforming the refraction-affected
   J2000 solves back to alt/az *without* refraction yields altitudes strictly
   lower than the original alt/az — confirms refraction handling direction.

Test-data generator: `references/tppa/plate-solved-field-calculator.py` builds
these vectors with Astropy AltAz (+pressure/temperature/humidity/obswl) and
quaternion rotation about the misaligned axis — useful as an independent oracle
for the Rust test suite.

---

## 13. Message-bus / remote-control surface

Topics (string constants):

- `PolarAlignmentPlugin_DockablePolarAlignmentVM_StartAlignment` — starts the
  panel-hosted alignment; message content may carry overrides (read reflectively
  by property name): `ManualMode: bool`, `TargetDistance: int`, `MoveRate: int`,
  `EastDirection: bool`, `StartFromCurrentPosition: bool`, `AltDegrees/AltMinutes:
  int`, `AltSeconds: double`, `AzDegrees/AzMinutes: int`, `AzSeconds: double`,
  `AlignmentTolerance: double`, `ExposureTime: double`, `Binning: short`,
  `Gain: int`, `Offset: int`, `SearchRadius: double`. (Upstream bug: the filter
  override is read from the `AlignmentTolerance` key; intended `Filter: string`.)
- `..._DockablePolarAlignmentVM_StopAlignment` — cancels.
- `PolarAlignmentPlugin_PolarAlignment_PauseAlignment` / `..._ResumeAlignment`.
- Broadcast: `PolarAlignmentPlugin_PolarAlignment_AlignmentError`, published after
  every successful continuous-phase solve with content
  `{ AzimuthError, AltitudeError, TotalError }` in **degrees** and a correlation
  GUID constant for the session.

---

## 14. Source map (MPL-2.0 provenance)

All paths relative to `C:/Users/bear/astro/references/`.

| Algorithm / section | Source |
|---|---|
| Procedure orchestration, tracking, slews, pause (§3) | `tppa/PolarAlignment/Instructions/PolarAlignment.cs` L396–598 (`Execute`), L282–304 (`AutomatedNextPoint`), L306–354 (`ManualNextPoint`), L356–364 (`SetTrackingSidereal`), L600–608 (`WaitIfPaused`) |
| RA distance metric (§3.1) | same file, L751–753 (`Distance`) |
| Axis move + rate clamp + timeout (§3.3) | same file, L755–812 (`MoveToNextPoint`) |
| Solve wrapper + retry policy (§4) | same file, L697–749 (`Solve`) |
| Instruction defaults / clamps / start coords (§10.1) | same file, L105–199 (ctors + `Clone`), L213–220 (`SearchRadius` clamp), L158–163 (start position) |
| Validation (§11) | same file, L840–903 (`Validate`) |
| Dead/unused `CalculateError` (§11 quirks) | same file, L620–630 (defined, never called — ignore; authoritative math is §6.2) |
| Pause/resume/error message topics (§13) | same file, L80–81, L905–919, L1005–1030; `tppa/PolarAlignment/Dockables/DockablePolarAlignmentVM.cs` L39–40, L178–268 |
| ArcsecPerPix + center + continuous loop (§7.1) | `Instructions/PolarAlignment.cs` L513–577 |
| `Position` (§5) | `tppa/PolarAlignment/TPAPAVM.cs` L703–731 |
| Plane fit, hemisphere flip, PA spread (§6.1, §6.4) | `TPAPAVM.cs` L501–536 (`PolarErrorDetermination` ctor), `tppa/PolarAlignment/Vector3.cs` L91–102 (`DeterminePlaneVector`) |
| Axis→error, refracted pole, wrap, signs (§6.2–6.3) | `TPAPAVM.cs` L645–682 (`CalculateMountAxisError`), L599–636 (direction strings), L587–597 (warning flags) |
| Destination rotation (§7.4) | `TPAPAVM.cs` L684–699 (`GetDestinationCoordinates`), `Vector3.cs` L141–143 (`RotateByRodrigues`) |
| Continuous update math (§7.2–7.3) | `TPAPAVM.cs` L108–123 (`UpdateDetails`), L184–286 (`CalculateErrorDetails`) |
| Reference star selection (§8) | `TPAPAVM.cs` L92–105 (`SelectNewReferenceStar`), L318–353 (star detection) |
| UPA controller (§9) | `TPAPAVM.cs` L125–182 (`Movement`, `MoveCloser`); `tppa/PolarAlignment/Avalon/UniversalPolarAlignmentVM.cs` L196–295 (`NudgeX`/`NudgeY`/`MoveX`/`MoveY`/`ClearBacklash`/`StartPoll`); `tppa/PolarAlignment/Avalon/UniversalPolarAlignment.cs` L18–182 |
| Refraction parameters (§2.1) | `tppa/PolarAlignment/RefractionParameters.cs` L1–44 |
| Settings defaults (§10.2) | `tppa/PolarAlignment/Properties/Settings.Designer.cs` L26–361; option clamps `tppa/PolarAlignment/PolarAlignmentPlugin.cs` L99–108, L197–207 |
| Unit-vector frame, alt/az↔vector (§1.3) | `Vector3.cs` L36–43 (`ToTopocentric`), L74–82 (`CoordinatesToUnitVector`) |
| Refracted altitude iteration (§2.2) | `nina/NINA.Astrometry/AstroUtil.cs` L861–890 (`CalculateRefractedAltitude`) |
| Image scale (§1.2) | `nina/NINA.Astrometry/AstroUtil.cs` L38–39, L727–731 |
| RA/Dec→AltAz with refraction (§1.5) | `nina/NINA.Astrometry/Coordinates.cs` L230–243; `nina/NINA.Astrometry/SOFA.cs` L633–635 (`iauAtco13`) |
| AltAz→RA/Dec with refraction (§1.5) | `nina/NINA.Astrometry/TopocentricCoordinates.cs` L74–89; `SOFA.cs` L787–789 (`iauAtoc13`) |
| Refraction constants (§2.2) | `SOFA.cs` L939–941 (`iauRefco`) |
| Stereographic shift (§1.6) | `nina/NINA.Astrometry/Coordinates.cs` L297–342 (`ShiftStenographic`), L360–393 (`Shift`) |
| Stereographic projection (§1.6) | `Coordinates.cs` L495–538 (`StenographicProjection`); gnomonic variant L441–486 (unused by TPPA) |
| Angular separation (§1.6) | `Coordinates.cs` L540–559 (`operator-`) |
| Angle arithmetic semantics (§1.1) | `nina/NINA.Astrometry/Angle.cs` L27–181 |
| Orientation = 360 − PA (§1.1) | `nina/NINA.Platesolving/PlateSolveResult.cs` L35–46 |
| Golden tests (§12) | `tppa/NINA.Plugins.PolarAlignment.Test/PolarErrorDeterminationTest.cs` L1–237; `tppa/plate-solved-field-calculator.py` L1–93 |
| Behavioral documentation | `tppa/PolarAlignment/FAQ.md`, `tppa/PolarAlignment/Changelog.md` |

PHD2 is not involved in this algorithm; NINA sources above are MPL-2.0, same as
TPPA.

---

## 15. Recommended for AstroDeck

**Adopt the TPPA algorithm essentially verbatim** — it is simple (exact 3-point
plane fit, no solver), validated against Astropy, hemisphere-complete, and the
continuous phase needs only one solve per update.

Defaults to ship:

- Automated mode with `start_from_current_position = false`; start position
  N: (az 1°, alt = lat + 2°), S: (az 181°, alt = |lat| + 2°) — matches TPPA and
  keeps the geometry well-conditioned near the pole.
- `target_distance = 10°`, `east_direction = true`, `move_rate = 3 °/s`,
  `move_timeout_factor = 2`. Expose target distance up to ~30° for users chasing
  sub-arcminute results (larger baseline ⇒ better conditioning; TPPA's own FAQ
  notes error sensitivity near the celestial equator is higher).
- `search_radius = 30°` (adopt the clamp [30, 180]; drop the vestigial stored 10).
- **Retry policy**: keep infinite retry + 5°/failure escalation during
  measurements, but add a configurable cap (e.g. 20 attempts) for headless
  sequences so an unattended rig can't spin all night; TPPA has no cap.
- `refraction_adjustment = false` (align to the refracted pole) as default, same
  as TPPA; always apply full refraction in the measurement transforms with the
  standard atmosphere (1013.25 hPa / 15 °C / 0% RH / 0.55 µm) fallback. **Pass
  humidity as a 0–1 fraction** (fix NINA's percent bug).
- `alignment_tolerance`: default **0.5′** with auto-finish, instead of TPPA's 0
  (disabled) — AstroDeck flows are headless-first; keep 0 = "run until stopped".
- `stop_tracking_when_done = true`, `auto_pause = false`.
- Snapshot-type captures, plate-solve profile's exposure/binning/gain; offset −1.

Implementation guidance:

- Use the `erfa` crate for `atco13` / `atoc13` / `refco` (byte-exact vs. SOFA);
  DUT1 = 0 (§1.5 justifies it). Reuse the §12 vectors as integration tests with
  1″ tolerances.
- Fix the enumerated upstream quirks (§11 list): live star re-acquisition instead
  of the dead branch, degenerate-geometry detection (collinear/identical points,
  parallel/vertical lines near az 90°/270° and zenith) with actionable error
  messages, `adjusted_rate > 0` guard, correct binning in `arcsec_per_pix`.
- Keep the pixel-space distance-ratio update exactly as specified (§7.3) for
  parity — it is what users see in TPPA and it is cheap. A future enhancement
  (not for parity) could re-fit the axis continuously from
  (solve3-replacement, solve2, solve1) but note that mount-axis motion during
  adjustment invalidates the original two points, which is precisely why TPPA
  rescales instead of re-fitting.
- Skip the Avalon UPA serial driver initially, but keep §9's controller behind a
  `PolarAdjuster` trait (nudge_x/nudge_y in arcmin, settle time), since the
  0.75-gain + 1.15-reversal logic is hardware-agnostic and will work for any
  motorized alt/az adjuster.
- Surface the §6.4 warning flags (PA spread > 5°, error > 2°, > 10°) and the
  direction strings of §6.3 in the UI/API verbatim — they encode the sign
  conventions users rely on.
