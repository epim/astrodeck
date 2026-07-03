# NINA Plate-Solving Orchestration — Algorithm Dossier

**Purpose**: single source of truth for reimplementing NINA's plate-solving pipeline in Rust
without reading the original C#. Extracted 2026-07-03 from the NINA reference clone at
`references/nina/` (MPL-2.0, © 2016–2024 Stefan Berg and the N.I.N.A. contributors).
Every algorithm below carries a source-map entry (file + line ranges) for MPL-2.0 provenance.

Scope: capture-for-solve, solver selection (near vs blind, failover), ASTAP and
astrometry.net (local cygwin + online nova API) invocation and result parsing, the
centering loop (solve → sync/offset → reslew), rotation/PA math and the rotator loop,
epoch handling (J2000 ↔ JNOW), all error paths, and every configuration knob with defaults.

---

## 1. Data model

### 1.1 PlateSolveParameter (solver input)

```rust
struct PlateSolveParameter {
    focal_length_mm: f64,          // REQUIRED; NaN or <= 0 aborts before solving
    pixel_size_um: f64,            // stored unbinned; getter returns max(binning,1) * pixel_size
    binning: i32,                  // camera binning used for the solve exposure
    search_radius_deg: f64,        // near-solve search radius (ASTAP -r, solve-field --radius)
    regions: f64,                  // Platesolve2 only: number of search regions
    downsample_factor: i32,        // 0 = auto (ASTAP only); range 1..=4 for astrometry.net local
    max_objects: i32,              // star-count cap (ASTAP -s, solve-field --objs)
    disable_notifications: bool,   // default false; suppress user-facing error toasts
    blind_failover_enabled: bool,  // default true
    coordinates: Option<Coordinates>, // hint; setter ALWAYS transforms to J2000.
                                      // None => blind solve.
}
```

Critical detail — **effective pixel size**: the `PixelSize` getter returns
`max(binning, 1) * pixel_size_um`. Callers store the *unbinned* camera pixel size and the
binning separately; the solver math always sees the binned pixel size. The image passed to
the solver is the binned image, so width/height are binned pixels and everything is
consistent.

Critical detail — **hint epoch**: the `coordinates` setter runs
`value.transform(Epoch::J2000)`. All solver hints are J2000 regardless of what the caller
passed (e.g. a JNOW mount position).

`clone()` is a shallow copy plus a defensive re-instantiation of `coordinates`
(same epoch — no re-transform).

### 1.2 CaptureSolverParameter / CenterSolveParameter

```rust
struct CaptureSolverParameter {   // extends PlateSolveParameter
    attempts: i32,                // capture+solve retries (profile default 10)
    reattempt_delay: Duration,    // wait between failed attempts (profile default 2 min)
}
struct CenterSolveParameter {     // extends CaptureSolverParameter
    threshold_arcmin: f64,        // centering tolerance in ARCMINUTES (profile default 1.0; must be > 0)
    no_sync: bool,                // if true never issue mount sync; use offset-slew instead
}
```

### 1.3 PlateSolveResult

```rust
struct PlateSolveResult {
    success: bool,                // NOTE: default constructor sets success = TRUE
    solve_time: DateTime,         // set at construction (used as identity in history lists)
    coordinates: Option<Coordinates>, // setter ALWAYS transforms to J2000
    position_angle_deg: f64,      // setter applies euclidian_mod(value, 360)
    pixscale_arcsec_px: f64,      // arcsec/pixel of the solved (binned) image; C# default 0.0 (NOT NaN)
    radius_deg: f64,              // half of the image diagonal FOV, degrees; C# default 0.0
    flipped: bool,                // image parity (mirrored on one axis)
    separation: Option<Separation>, // filled by centering / manual sync flows
}
// legacy accessor: orientation = euclidian_mod(360 - position_angle, 360)
// display helpers:
//   ra_pix_error  = round(separation.ra.arcseconds  / pixscale, 2)
//   dec_pix_error = round(separation.dec.arcseconds / pixscale, 2)
```

### 1.4 Separation (difference of two coordinates)

Subtraction `a - b` (coordinates; `b` is transformed to `a`'s epoch first):

```text
ra_diff  = a.ra  - b.ra                       (raw angle difference, NOT wrapped)
dec_diff = a.dec - b.dec
distance = acos( sin(a.dec)sin(b.dec) + cos(a.dec)cos(b.dec)cos(ra_diff) )   // great circle
bearing  = atan2( sin(ra_diff)cos(b.dec),
                  cos(a.dec)sin(b.dec) - sin(a.dec)cos(b.dec)cos(ra_diff) )
```

`coords + sep` / `coords - sep` apply **per-axis** RA/Dec offsets (not great-circle):

```text
(a + s).ra  = euclidian_mod(a.ra_deg + s.ra_deg, 360)
(a + s).dec = a.dec_deg + s.dec_deg           // NOT clamped to ±90
```

### 1.5 Unit helpers and constants

```text
euclidian_mod(x, y):  r = x % y; if r < 0 { r + y } else { r }   (y > 0)
deg_to_arcsec(d)   = d * 3600
arcsec_to_deg(a)   = a / 3600
arcsec_to_arcmin(a)= a / 60
ARCSEC_PER_PIX_FACTOR = (180/π) * 3600 / 1000 = 206.264806247...   // µm & mm → arcsec
arcsec_per_pixel   = (pixel_size_um_binned / focal_length_mm) * ARCSEC_PER_PIX_FACTOR
fov_arcmin(width_px)  = arcsec_per_pixel * width_px / 60
fov_deg(width_px)     = arcsec_per_pixel * width_px / 3600
ANGLE_EQUALS_EPSILON  = 1e-13     // tolerance comparisons (rotator loop)
```

### 1.6 PlateSolveImageProperties (derived per solve)

```text
focal_length = parameter.focal_length_mm
pixel_size   = parameter.pixel_size (binned, µm)
image_width  = source.width_px      // binned pixels
image_height = source.height_px
arcsec_per_pixel = as above
fov_h_deg = fov_deg(image_height)   // ASTAP -fov uses HEIGHT
fov_w_deg = fov_deg(image_width)
```

---

## 2. Capture-for-solve parameters

Every orchestration entry point (Center sequencer item, SolveAndSync, SolveAndRotate,
CenterAndRotate, meridian-flip recenter, manual solve pane) builds the same capture:

```rust
CaptureSequence {
    exposure_time_s: profile.platesolve.exposure_time,   // default 2.0 s
    image_type: SNAPSHOT,
    filter: profile.platesolve.filter,                   // default None (= keep current filter)
    binning: (profile.platesolve.binning, profile.platesolve.binning), // default 1x1, symmetric
    exposure_count: 1,
    gain: profile.platesolve.gain,                       // default -1 => camera default gain
    offset: -1,                                          // camera default offset
}
```

- Gain resolution at the camera layer: `if gain > -1 { cam.gain = gain } else { cam.gain = default_gain }`.
- **Gain wiring caveat** (verified in source): only `Center`/`CenterAndRotate`'s *centering* phase
  (`Center.DoCenter`, `seq.Gain = profile.platesolve.gain`) and the meridian-flip recenter set the
  profile gain on the capture sequence. `SolveAndRotate`, `SolveAndSync`, and the *rotate loop* of
  `CenterAndRotate` never assign `seq.Gain`, so those solves always run at gain −1 (camera default).
  For AstroDeck: wire the profile gain uniformly (treat NINA's inconsistency as an oversight).
- The image is prepared with `detect_stars = false` (no star analysis pass; just debayer/stretch prep).
- Manual solve pane exception: `binning` falls back to the camera's current `BinX` when no snap
  binning is selected, and `attempts` is forced to 1; before capturing it also awaits any pending
  dome synchronization (`WaitForDomeSynchronization`).

**Filter handling** (two layers, both must be replicated):

1. `CenteringSolver.center`: if the capture sequence specifies a filter, remember the currently
   selected filter, switch to the solve filter *before the loop*, and in a `finally` block switch
   back afterwards using a *fresh* 5-minute-timeout cancellation source (the caller's token may
   already be cancelled).
2. `CaptureSolver.solve` (each attempt): snapshot the currently selected filter before the
   exposure; after the exposure completes, start switching back to that filter *concurrently*
   with the solver run, and await the filter change after the solve finishes. (When invoked
   from `CenteringSolver` this is a no-op because the solve filter is already selected.)

---

## 3. Solver selection: near vs blind, and failover

### 3.1 Which solver runs

```rust
fn get_solver(param) -> &Solver {
    if param.coordinates.is_none() { blind_solver } else { near_solver }
}
```

That is the entire near/blind decision: **a solve is "blind" iff no coordinate hint exists.**
Orchestration always supplies a hint (target coordinates or current mount position), so the
primary path is always a near solve; blind only happens via failover or an explicitly null hint.

### 3.2 Solver factory

```text
near solver  (profile.platesolver_type):
  ASTAP (default) | ASTROMETRY_NET (online) | LOCAL (cygwin astrometry.net) |
  PLATESOLVE2 | PLATESOLVE3 | ASPS (AllSkyPlateSolver) | TSX_IMAGELINK | PINPOINT
blind solver (profile.blind_solver_type):
  ASTAP (default) | ASTROMETRY_NET | LOCAL | PLATESOLVE3 | ASPS | PINPOINT
  (PLATESOLVE2 and TSX are NOT offered as blind solvers; PS2 requires a hint —
   its argument builder dereferences coordinates unconditionally)
```

### 3.3 ImageSolver orchestration (with blind failover)

```rust
async fn image_solve(image, param) -> PlateSolveResult {
    // Prerequisite: focal length must be usable
    if param.focal_length_mm.is_nan() || param.focal_length_mm <= 0.0 {
        return Err("No focal length configured");           // hard error, no solve attempted
    }
    let solver = get_solver(&param);
    let mut result = solver.solve(image, &param).await;
    if param.blind_failover_enabled && !result.success
       && param.coordinates.is_some() && blind_solver_exists {
        let mut blind_param = param.clone();
        blind_param.coordinates = None;                      // forces blind solver
        result = image_solve(image, blind_param).await;      // recursion; cannot failover again
    }
    result
}
```

Notes: failover happens once (the recursive call has no coordinates so the guard fails).
The failed near-solve already showed its error notification unless `disable_notifications`.

---

## 4. CaptureSolver retry loop

```rust
async fn capture_and_solve(seq, param: CaptureSolverParameter) -> PlateSolveResult {
    let mut remaining = param.attempts;                       // default 10
    loop {
        remaining -= 1;
        let old_filter = filter_wheel.selected_filter();
        let image = camera.capture_and_prepare(seq, detect_stars=false).await;
        if image.is_none() {
            result = PlateSolveResult { success: false, ..default() };
            // NOTE: no reattempt delay on capture failure — retries immediately
        } else {
            let revert_filter = spawn(filter_wheel.change_filter(old_filter)); // concurrent
            emit_progress(thumbnail(image));
            check_cancelled()?;
            result = image_solver.solve(image.raw_data, &param).await;
            emit_progress(result);
            revert_filter.await;
            if !result.success && remaining > 0 {
                cancellable_sleep(param.reattempt_delay).await;   // default 2 min
            }
        }
        if result.success || remaining <= 0 { return result; }
    }
}
```

---

## 5. CenteringSolver — the centering loop

This is NINA's "slew-center" core. Threshold is in **arcminutes** on the great-circle
separation. Max 10 iterations. Handles mounts that reject syncs (offset mode), mounts that
silently ignore syncs, and `no_sync` configurations.

```rust
async fn center(seq, param: CenterSolveParameter) -> PlateSolveResult {
    assert!(param.coordinates.is_some());        // ArgumentException otherwise
    assert!(param.threshold_arcmin > 0.0);       // ArgumentException otherwise

    // filter pre-switch (see §2), restore in finally with 5-min timeout
    let mut offset = Separation::zero();
    let mut attempts_left = 10;                  // hardcoded maxSlewAttempts
    let mut centered = false;
    let mut result;
    loop {
        attempts_left -= 1;
        result = capture_and_solve(seq, &param).await;
        if !result.success { break; }            // solving failed after all retries: give up

        // ---- epoch normalisation: EVERYTHING in the mount's epoch ----
        let position       = mount.current_position();               // mount epoch (usually JNOW)
        let solved         = result.coordinates.transform(position.epoch);
        let target         = param.coordinates.transform(position.epoch);
        result.separation  = target - solved;                        // Separation (see §1.4)

        if result.separation.distance.abs_arcminutes() > param.threshold_arcmin {
            // ---------- sync or offset ----------
            if param.no_sync || !mount.sync(solved).await {
                // sync disabled or rejected by mount:
                // model the pointing error as an offset instead
                offset = position - solved;
            } else {
                let position_after_sync = mount.current_position();
                let sync_effect = position_after_sync - position;
                if sync_effect.distance.abs_arcseconds() < 1.0 {
                    // "silent" sync failure: mount accepted the call but didn't move
                    // its reported position; fall back to offset mode
                    offset = position_after_sync - solved;
                } else {
                    offset = Separation::zero();        // sync worked; clear any offset
                }
            }
            // ---------- reslew ----------
            mount.slew_to(target + offset).await;       // per-axis offset addition (§1.4)
            // ---------- manual dome follow ----------
            if dome.connected && dome.can_set_azimuth && !dome_follower.is_following {
                if !dome_follower.trigger_telescope_sync().await {
                    warn_user("dome sync failed during centering");  // continue anyway
                }
            }
        } else {
            centered = true;
        }
        if centered || attempts_left <= 0 { break; }
    }
    if !centered && attempts_left <= 0 {
        result.success = false;    // "Cancelling centering after 10 unsuccessful slew attempts"
    }
    result   // (NINA wraps this in CenteringSolveResult with per-attempt timing measurements)
}
```

Key subtleties:

- The **offset survives across iterations**: if the mount never accepts syncs, each iteration
  recomputes `offset = reported_position - solved` and slews to `target + offset`. The unit
  tests assert the reslew target equals `target + (target - solved)` when the mount keeps
  reporting it is on target but the solve says otherwise.
- The **silent-sync check** threshold is 1.0 arcsecond of great-circle movement of the
  *reported* mount position across the sync call.
- Solve failure inside the loop breaks out immediately (no reslew, `success=false`).
- Each iteration re-captures and re-solves with the full `attempts × reattempt_delay` retry
  budget of §4 (so worst case is 10 centering iterations × 10 capture retries).
- Mount `sync()` returns false when: profile `NoSync` set, mount not connected, ASCOM
  `CanSync` false, mount not tracking, or driver throws. After a successful driver sync NINA
  waits `max(2 s, telescope.settle_time)` plus one telemetry-poll cycle before reading the
  position again — replicate this wait or the silent-sync check will misfire.
- Mount `slew_to()` (TelescopeVM): transforms target to the mount epoch, waits for any
  in-progress slew, refuses when parked (returns false), optionally kicks a parallel dome-sync
  task, then waits `settle_time` (default 5 s) after the driver slew completes; total slew
  wrapped in a 10-minute timeout.

---

## 6. Coordinate epoch handling (J2000 ↔ JNOW)

Canonical rule set:

1. **All solver inputs and outputs are J2000.** `PlateSolveParameter.coordinates` and
   `PlateSolveResult.coordinates` setters force-transform to J2000.
2. **All mount interaction happens in the mount's epoch.** The ASCOM driver reports
   `EquatorialSystem`: interface v1 ⇒ assume JNOW; else map
   `{B1950→B1950, J2000→J2000, J2050→J2050, Other→J2000 + unknown-epoch flag, Topocentric/Local→JNOW}`.
   `Sync` and `SlewToCoordinates` transform their argument to that epoch first; the reported
   `Coordinates` property carries that epoch.
3. **The centering comparison is done in the mount epoch** (§5): both the solved coordinates
   and the target are transformed to `mount.position.epoch` before computing the separation.
   This makes the sync argument, the offsets, and the reslew target all mount-epoch.
4. Sync guard: if the RA (hours) of the transformed sync target is negative, apply
   `euclidian_mod(ra_hours, 24)` before calling the driver.

Transform math (NINA delegates to SOFA; in Rust use the `erfa` crate — same algorithms):

```text
J2000 → JNOW:   jd_tt from now (UTC→TAI→TT);
                (ri, di, eo) = eraAtci13-equivalent: CelestialToIntermediate(ra, dec, 0,0,0,0, jd_tt)
                ra_apparent = anp(ri - eo)     // CIRS → apparent via equation of origins
                dec_apparent = di
JNOW → J2000:   jd_tt, jd_utc from the coordinate's creation timestamp;
                (rc, dc) = IntermediateToCelestial(anp(ra + Eo06a(jd_utc)), dec, jd_tt)
```

JNOW coordinates carry a `creation_date`; the reverse transform uses it (not "now").

---

## 7. ASTAP solver (primary + default blind)

### 7.1 Validation before solving

- Executable path empty → error `"ASTAP executable location missing!"`.
- Executable missing on disk → error `"ASTAP executable not found at <path>"`.
- Legacy version detection: if the exe has no file-version resource (pre-0.9.1.0) and
  `downsample_factor == 0` → error (auto-downsample unsupported).

### 7.2 Input file

The image is saved as **FITS** (force file type) with a random filename into
`%LOCALAPPDATA%/NINA/PlateSolver/`. Before saving, if the image metadata lacks target
coordinates, they are filled from the telescope's coordinates (FITS header hint only; ASTAP
gets its hint via CLI args).

Exact save pipeline (traced through `BaseImageData.SaveToDisk` → `SaveFits`, verified):

- The data written is `RawImageData` — the **raw sensor array** of the (binned) solve exposure:
  monochrome or **undebayered Bayer** data, never the stretched/debayered render. `forceFileType`
  bypasses the DSLR-RAW passthrough branch.
- `FileSaveInfo` is built without a profile, so its defaults apply: `FITSUseLegacyWriter = true`,
  no compression. The **legacy writer** is what runs:
  - header: `SIMPLE=T, BITPIX=16, NAXIS=2, NAXIS1=w, NAXIS2=h, BZERO=32768, EXTEND=T`
    plus metadata cards (incl. RA/DEC of the target hint);
  - data: each `u16` pixel written as big-endian **signed** `i16` = `pixel − 32768`
    (standard FITS unsigned-short convention). Always 16-bit — there is no 32-bit path in the
    legacy writer.
- So for a Rust port: write a plain 16-bit FITS of the raw binned frame with BZERO=32768.
  ASTAP debayers/handles Bayer patterns itself if the relevant header cards (BAYERPAT etc.)
  are present in the metadata cards.

### 7.3 Exact CLI invocation

```text
astap.exe -f "<image.fits>" -fov <fov_h> -z <downsample> -s <max_objects> [hint args]

fov_h       = round(fov_h_deg, 6)          // image HEIGHT fov in degrees, 6 decimals,
                                           // invariant-culture decimal point
downsample  = parameter.downsample_factor  // 0 = ASTAP auto
max_objects = parameter.max_objects        // default 500

hint args, when search_radius > 0 AND coordinates present (near solve):
  -r   <search_radius_deg>                 // as configured, default 30
  -ra  <round(ra_HOURS, 6)>                // J2000 RA in HOURS (not degrees!)
                                           // (the NINA source comment says "degrees" but
                                           //  Coordinates.RA is the HOURS accessor — verified)
  -spd <round(dec_deg + 90.0, 6)>          // J2000 south-pole distance in degrees

otherwise (blind):
  -r 180
```

Locale quirk (do not replicate): `-fov`, `-ra`, `-spd` are formatted invariant-culture, but
`-r` uses the default-culture `ToString()` of the double — always format invariant in Rust.

No output-path argument: ASTAP writes `<image>.ini` (and a `<image>.wcs` sidecar) next to
the input. NINA polls nothing — it waits for process exit, then reads the `.ini`.

Process handling: NINA *intends* to stream stdout/stderr lines to progress + log, but the
handlers never fire (verified: `RedirectStandardError` is never set and
`BeginOutputReadLine()`/`BeginErrorReadLine()` are never called — only `RedirectStandardOutput
= true`, and the redirected pipe is never drained; ASTAP writes little enough that the pipe
buffer never fills). Rust port: drain stdout+stderr to the debug log — that is the intent —
but do drain them (an undrained redirected pipe can deadlock a chatty child). Intended
timeout: 10 minutes (see §12 note — the reference code constructs a 10-min linked token but
mistakenly passes the outer token to the process wait; implement the *intended* 10-min
timeout). On user cancel the process wait's token registration kills the child process.

### 7.4 Result file parsing (`<image>.ini`)

Key=value lines, split on the **first** `=` only, blank lines skipped, values parsed with
invariant culture:

```text
PLTSOLVD  "T" on success; anything else (or key missing) = failure
WARNING   optional; logged + shown as warning toast even on success
ERROR     optional; appended to the failure notification
CRVAL1    solved center RA, degrees, J2000
CRVAL2    solved center Dec, degrees, J2000
CRPIX1/2  reference pixel
CD1_1 CD1_2 CD2_1 CD2_2   CD matrix, degrees/pixel
```

Failure paths: file missing → error "No output file found", `success=false`.
`PLTSOLVD != T` → log/notify WARNING+ERROR, `success=false`.

On success:

```text
coordinates    = (CRVAL1, CRVAL2) deg, J2000
pixscale       = deg_to_arcsec( sqrt(CD1_2² + CD2_2²) )        // arcsec/px (y-column norm)
                 // Source guards this with `if dict has CD1_2 && CD2_2`, but the WCS
                 // constructor above already dereferenced dict["CD1_2"]/["CD2_2"]
                 // (unconditional double.Parse — throws KeyNotFoundException if absent),
                 // so for a PLTSOLVD=T .ini those keys are ALWAYS present and pixscale is
                 // ALWAYS set. The ContainsKey guard is dead. `PlateSolveResult.Pixscale`
                 // is a plain auto-property: its C# default is **0.0, not NaN** (§1.3).
radius_deg     = arcsec_to_deg( sqrt((W·pixscale)² + (H·pixscale)²) / 2 )
                 // Source guards with `if !isNaN(pixscale)`; since the default is 0.0 and
                 // pixscale is always set for a valid solve, this branch always runs.
                 // (The isNaN check is defensive dead code — sqrt of sums of squares is
                 //  never NaN. In Rust just compute radius whenever the solve succeeded.)
position_angle = euclidian_mod(360 - (wcs.rotation - 180), 360)   // see §7.5
                 // (PositionAngle setter re-wraps via euclidian_mod(value,360), §1.3)
flipped        = !wcs.flipped                                      // note the negation
```

(If any of CRVAL1/2, CRPIX1/2 or a CD key is missing while `PLTSOLVD=T`, the WCS-constructor
`double.Parse(dict["…"])` throws `KeyNotFoundException`. NINA does not guard those. The
CLISolver try-block only catches `OperationCanceledException`, so this exception escapes
CLISolver entirely and surfaces to the CaptureSolver / orchestration layer as an unhandled
exception (NOT a clean `success=false`). In Rust: parse the .ini defensively and return
`success=false` on any missing/unparseable key.)

### 7.5 WCS rotation/parity math (CD-matrix form)

```rust
fn wcs_from_cd(cd11, cd12, cd21, cd22) -> Wcs {
    let det  = cd11 * cd22 - cd12 * cd21;
    let sign = if det < 0.0 { -1.0 } else { 1.0 };
    let cdelt1 = sign * (cd11*cd11 + cd21*cd21).sqrt();
    let cdelt2 =        (cd12*cd12 + cd22*cd22).sqrt();
    let flipped = cdelt1 >= 0.0 || cdelt2 < 0.0;      // parity indicator
    let rot2_cd = f64::atan2(sign * cd11, cd21) - PI / 2.0;
    let rotation_deg = to_deg(if flipped { -rot2_cd } else { rot2_cd });
    Wcs {
        rotation: euclidian_mod(rotation_deg, 360.0),
        flipped,
        pixel_scale_x: deg_to_arcsec(cdelt1.abs()),
        pixel_scale_y: deg_to_arcsec(cdelt2.abs()),
        // generic PA accessor (not used by ASTAP path): euclidian_mod(360 - rotation, 360)
    }
}
```

(There is also a CROTA2 form: `flipped = cdelt1 >= 0 || cdelt2 < 0`;
`rotation = euclidian_mod(flipped ? -crota2 : crota2, 360)`.)

### 7.6 Cleanup

After every solve (success or fail): delete or archive `<image>.fits`, `<image>.ini` and
sidecar `<image>.wcs` (see §12 file lifecycle).

---

## 8. Astrometry.net — local (cygwin `solve-field`)

Runs `solve-field` under cygwin bash via
`cmd.exe /C ""<cygwin>/bin/bash.exe" --login -c '/usr/bin/solve-field <options> "<image>"'"`
(image path with `/` separators).

### 8.1 Exact options

```text
--overwrite --index-xyls none --corr none --rdls none --match none --new-fits none
-center --objs <max_objects> --no-plots --resort --downsample <downsample_factor>
--scale-units arcsecperpix
-L <arcsec_per_pixel - 0.2>          // "0.00" 2-decimal invariant format
-H <arcsec_per_pixel + 0.2>
[ near solve, when search_radius > 0 && coordinates present:
  --ra <ra_degrees "0.00"> --dec <dec_deg "0.00"> --radius <search_radius "0.00"> ]
```

Scale window is a **fixed ±0.2 arcsec/px** around the computed scale. Downsample valid
range here is 1–4 (0 not allowed by the UI for this solver).

### 8.2 Result parsing

Output is `<image>.wcs`. NINA shells out again to cygwin `wcsinfo <file>.wcs` and parses
stdout lines of the form `key value` (single space split, exactly 2 tokens):

```text
crval0 crval1 crpix0 crpix1 cd11 cd12 cd21 cd22   -> build WCS (§7.5) -> flipped = !wcs.flipped
ra_center dec_center                              -> J2000 degrees (default 0 if key missing!)
orientation_center -> position_angle = euclidian_mod(360 - (180 - orientation + 360), 360)
                                      = euclidian_mod(orientation - 180, 360)
pixscale           -> arcsec/px; radius_deg = arcsec_to_deg(sqrt((W·ps)² + (H·ps)²)/2)
```

Edge cases: `.wcs` file missing → `success=false`. If the file exists, `success=true`
unconditionally — even if keys are missing (RA/Dec default to 0). A robust Rust port should
treat missing `ra_center`/`dec_center` as failure; documented here because parity tests
against NINA would show `success=true, (0,0)`.

Recommended Rust deviation: parse the `.wcs` FITS header directly (CRVAL/CD keys) rather
than shelling out to `wcsinfo`.

---

## 9. Astrometry.net — online (nova API)

Config: `astrometry_url` (default `http://nova.astrometry.net`) + `api_key` (required,
must contain no whitespace — validated with an explicit error each solve).

Endpoints (relative to the configured URL):

```text
POST /api/login/                body: request-json=<urlencoded {"apikey":"KEY"}>
                                (x-www-form-urlencoded)  -> {status:"success", session}
POST /api/upload                multipart: file=<FITS bytes> +
                                request-json={"publicly_visible":"n","allow_modifications":"d",
                                              "session":"<session>","allow_commercial_use":"d"}
                                -> {status:"success", subid}
GET  /api/submissions/{subid}   poll every 1000 ms until jobs[] has a non-empty first id
GET  /api/jobs/{jobid}          poll every 1000 ms until status == "success" | "failure"
GET  /api/jobs/{jobid}/calibration/
     -> { parity, orientation, pixscale, radius, ra, dec }
```

Whole operation wrapped in a 10-minute timeout (same intended-vs-actual caveat, §12).
The image is uploaded as the same random-named FITS, deleted right after upload.

Result mapping:

```text
flipped        = parity < 0
position_angle = euclidian_mod(360 - (180 - orientation + 360), 360)
               = euclidian_mod(orientation - 180, 360)
pixscale       = pixscale                  (arcsec/px)
coordinates    = (ra, dec) degrees, J2000
radius_deg     = radius                    (server-provided, degrees)
```

Quirk (verified): the online path never sets `success = true` explicitly — it relies on the
`PlateSolveResult` constructor default (`success = true`, §1.3) and only sets `false` on
exception/timeout. In Rust, set success explicitly after a parsed calibration.

Failure paths: login failure / upload failure / job status `"failure"` all raise
`AstrometryNetFailedException`; a job `"failure"` is logged at info level ("failures are
normal"), but the exception is then caught by the generic handler, so it still produces
`success=false` + an error toast (unless notifications disabled). Status `"error"` → server
`errormessage` surfaced the same way. Timeout → `success=false` +
"Platesolver timed out after 10 minutes" log. No hint is ever sent — the online solver is
always effectively blind (NINA passes no scale/position hints to nova).

---

## 10. Other bundled solvers (for completeness)

### AllSkyPlateSolver (ASPS) — CLI
Args: `/solvefile "<img>" "<out.txt>" <focal_len "0.00"> <pixel_size "0.00"> <ra_deg "0.00"> <dec_deg "0.00"> <search_radius "0.00">`
(0/0 when no hint). Output `<img>.txt`: line0 `OK` (needs ≥ 8 lines), 1=ra_deg, 2=dec_deg,
3/4=fovW/H (unused), 5=pixscale, 6=orientation → `PA = mod(orientation - 180, 360)`,
7=focal length (unused). Radius from pixscale half-diagonal as usual.

### Platesolve2 — CLI (comma-joined single argument)
`<ra_rad>,<dec_rad>,<fovW_rad>,<fovH_rad>,<regions>,<image_path>,0` — hint **required**
(coordinates dereferenced unconditionally); `regions` default 5000. Output `<img>.apm`:
line0 `ra_rad,dec_rad,status` (status must be 1), line1 `pixscale,orientation,flip[,…]`:
`PA = 360 - orientation`, `flipped = (flip >= 0)`, and **if flipped, PA += 180**.
Contains a decimal-comma workaround (splitting on comma may cut numbers; NINA re-joins
pairs) — do not replicate; always run the child process in an invariant-culture locale.

### Platesolve3 — CLI
`"<img>" <ra_rad|0> <dec_rad|0> <fovW_rad> <fovH_rad>`. Output `<img>_PS3.txt`:
line0 must be `true`; line1 `ra_rad,dec_rad` (J2000); line2 `imscale_px_per_rad,rot_deg` →
`pixscale = 206264.8 / imscale`, `PA = rot` (no transform); flip not parsed.

### TheSkyX ImageLink — TCP script API
Sends image path + `arcsec_per_pixel` (`isUnknownScale=true`); reads
`ImageCenterRAJ2000` (hours!), `ImageCenterDecJ2000`, `ImagePositionAngle` (used as PA
directly), `ImageScale` (pixscale), `IsImageMirrored` (flipped, no negation).

### PinPoint — COM
Sets `ArcsecPerPixelHoriz/Vert = arcsec_per_pixel`, catalog config; hinted:
`RightAscension = ra_hours, Declination = dec_deg` + `Solve()`; blind: `SolveAllSky()`.
Reads back RA (hours)/Dec, `PositionAngle` (used directly), `pixscale = |ArcsecPerPixelHoriz|`.

---

## 11. Rotation / position-angle orchestration

### 11.1 Where PA comes from
See per-solver mappings above. Universal post-condition: `position_angle` is stored
mod 360. `flipped` indicates mirror parity; NINA reports it in the UI and rotator logic
implicitly handles 180° ambiguity via the mod-180 comparisons below.

### 11.2 Rotator sync + target mapping

- `rotator.sync(sky_angle)`: tells the (possibly virtual/manual) rotator that its current
  mechanical position corresponds to the solved sky PA. Offset = mechanical − sky.
- `rotator.get_target_position(sky_target)` — **throws if the rotator has not been synced**
  ("Rotator not synced!"); maps a desired sky PA to the reachable equivalent given the
  configured mechanical range (fully verified, incl. the HALF and QUARTER branches):
  ```text
  position   = euclidian_mod(sky_target, 360)
  offset     = mechanical_position - synced_sky_position     // both read from the driver
  mech       = euclidian_mod(position + offset, 360)
  mech_tgt   = get_target_mechanical_position(mech)
  return euclidian_mod(mech_tgt - offset + 360, 360)

  get_target_mechanical_position(p):                // p already in [0, 360)
    d = euclidian_mod(p - range_start + 360, 360)   // distance past range start
    match range_type {
      FULL    => t = p,
      HALF    => t = if d < 180 { p } else { p + 180 },
      QUARTER => t = if d < 90  { p }
                     else if d < 180 { p + 270 }
                     else if d < 270 { p + 180 }
                     else            { p + 90 },
    }
    return euclidian_mod(t, 360)
  ```
  Profile `RotatorSettings` defaults: `range_type = FULL`, `range_start_mechanical_position = 0.0`.
- `rotator.move(target)` (VM layer): after computing the range-adjusted target (same mapping,
  user notified when adjusted by > 0.1°), issues `MoveAbsolute` and then polls until
  `!is_moving && |position − target| ≤ 1.0°` (angle-equality, mod 360), halting the rotator
  if cancelled.

### 11.3 The rotate loop (SolveAndRotate; CenterAndRotate runs it before centering)

```rust
let mut target: f32 = desired_position_angle;         // mod 360 at assignment
let mut distance: f32 = f32::MAX;
loop {
    let solve = capture_and_solve(seq, param).await;   // hint = target coords (CenterAndRotate)
    if !solve.success { fail("plate solve failed"); }  //        or mount position (SolveAndRotate)
    let orientation = solve.position_angle as f32;
    rotator.sync(orientation);

    let prev = target;
    target = rotator.get_target_position(prev);
    if (target - prev).abs() > 0.1 { notify("target adjusted to mechanical range"); }

    distance = target - orientation;
    if rotator_range == FULL {
        // consider the 180°-rotated frame if closer (camera frames are PA-ambiguous mod 180)
        let m  = euclidian_mod(distance, 180.0);
        let m2 = m - 180.0;
        if m < m2.abs() { distance = m; }
        else { target = euclidian_mod(target + 180.0, 360.0); distance = m2; }
    }
    // tolerance check: |distance| ≈ 0 (mod 180), tolerance = profile rotation_tolerance (default 1.0°)
    let within = angle_equals_mod180(distance, 0.0, rotation_tolerance); // §11.4
    if !within {
        rotator.move_relative(distance).await;
        continue;
    }
    break;
}
```

### 11.4 Angle equality with tolerance

```text
angle_equals(a, b, tol):        d = |mod(a,360) - mod(b,360)|
                                (d - mod(tol,360)) <= 1e-13 || ((360-d) - mod(tol,360)) <= 1e-13
angle_equals_mod180(a, b, tol): same with 180 substituted for 360   // "oneEightyIsEqual"
```

The rotate loop uses the mod-180 variant: a frame rotated 180° is considered equal.

---

## 12. File lifecycle, timeout, process management (CLI solvers)

- Working dir: `%LOCALAPPDATA%/NINA/PlateSolver/`; failed dir: `.../PlateSolver/Failed/`.
  Both created at startup; existing files older than **7 days** deleted at startup.
- Input image: random 8.3 filename, FITS, force-written.
- On **success or user cancel**: input, output and sidecar files are deleted.
- On **failure (not user-cancelled)**: files are *moved* to the failed dir as
  `<yyyyMMdd-HHmmss>.<pid>[.<sanitized_target_name>][.blind].<ext>` (session timestamp and
  pid fixed at process start; `.blind` appended when no hint was used). Existing destination
  is overwritten. (Reference-code quirk: the rename produces a double dot before the
  extension, e.g. `...blind..fits` — cosmetic; don't replicate.)
- Timeout: **intended 10 minutes** for the child process / online job.
  *Reference-code bug*: a linked cancellation source with `cancel_after(10 min)` is created,
  but the original token is passed to the process wait / HTTP calls, so the timeout never
  actually fires. Implement the intended behavior: kill the child / abort the job after 10
  minutes, log "Platesolver timed out after 10 minutes", and return `success=false`.
- Child process: no shell, stdout+stderr captured line-by-line (progress + debug log);
  no exit-code check — result determination is purely from the output file.
- CLI solver missing executable at spawn time → hard `FileNotFoundException`
  ("Platesolver executable not found…").

---

## 13. Orchestration entry points (contract summary)

| Entry point | Hint coordinates | Loop | On solve fail |
|---|---|---|---|
| `Center` (sequencer) | target coords (falls back to mount position) | full centering loop §5 | sequence item fails ("Platesolve failed") |
| `CenterAndRotate` | target coords | slew → rotate loop §11.3 → centering loop | sequence item fails |
| `SolveAndRotate` | current mount position | rotate loop only | sequence item fails |
| `SolveAndSync` | current mount position | single capture-solve, then mount sync + rotator sync | fails; also fails if the mount sync returns false |
| Meridian-flip recenter | flip target coords | full centering loop | best-effort: error toast, flip continues |
| Manual solve pane | current mount position | attempts=1; optional sync; optional slew-to-target (runs centering with threshold from the pane) | error toast |

Common pre-steps for Center/CenterAndRotate: refuse if mount parked (error); slew to target
first; if dome connected + can-set-azimuth + not following, trigger dome sync (warn on
failure, continue). Guiding is stopped before and resumed after (Center: only if it was
stopped; resume failures logged, not fatal).
`SolveAndSync` mount sync uses the solved J2000 coordinates transformed to mount epoch by
the mount layer; the manual pane computes `separation = mount_pos − solved` in the mount
epoch before syncing.

---

## 14. Error paths — exhaustive checklist

| Condition | Behavior |
|---|---|
| focal length NaN/≤0 | exception before any capture ("no focal length") |
| centering param: no coordinates | ArgumentException |
| centering param: threshold ≤ 0 | ArgumentException |
| mount parked at Center start | error notification + sequence failure |
| capture returns null | attempt counts as failed, immediate retry (no delay) |
| solver exe path empty/missing (ASTAP) | ASTAP validation exception (before capture-solve) |
| ASTAP pre-0.9.1 + downsample 0 | validation exception |
| CLI exe vanishes at spawn | FileNotFoundException |
| solver timeout (10 min intended) | `success=false`, log timeout, files archived |
| user cancel | temp files deleted (not archived). NOTE (verified): CLI + online solvers *swallow* the cancellation exception internally and return `success=false`; the cancellation surfaces at the CaptureSolver layer (`ThrowIfCancellationRequested` before each solve, cancellable reattempt wait) and above |
| ASTAP: no `.ini` | `success=false` + "No output file found" toast |
| ASTAP: `PLTSOLVD!=T` | `success=false` + toast incl. WARNING/ERROR strings |
| ASTAP: WARNING on success | warning toast, solve still succeeds |
| local astrometry.net: `.wcs` missing | `success=false`, silent |
| online: API key blank/whitespace | ArgumentException at solve start |
| online: job status "failure" | AstrometryNetFailedException → `success=false` (info-level; failures are normal) |
| online: status "error" | server errormessage surfaced |
| near solve fails + failover on + hint present | one blind re-solve |
| blind solve fails | final `success=false` |
| all capture-solve attempts fail | centering aborts, `success=false` |
| sync rejected / NoSync | offset mode (no failure) |
| sync silently ignored (<1″ movement) | offset mode + warning log |
| 10 centering iterations without convergence | `success=false`, error log |
| dome sync fails during centering | warning toast, centering continues |
| filter restore after centering | separate 5-min timeout token (survives cancelled caller token) |
| guider resume fails after rotate/center | error log only |
| notifications disabled flag | suppresses all error/warning toasts (logs remain) |

---

## 15. Configuration knobs (defaults + valid ranges)

Profile section `PlateSolveSettings` unless noted:

| Knob | Default | Range / notes |
|---|---|---|
| `plate_solver_type` | `ASTAP` | enum §3.2 |
| `blind_solver_type` | `ASTAP` | enum §3.2 (no PS2/TSX) |
| `blind_failover_enabled` | `true` | bool |
| `exposure_time` | `2.0` s | > 0 |
| `binning` | `1` | ≥ 1, symmetric N×N |
| `gain` | `-1` | −1 = camera default; else camera gain range |
| `filter` | `None` | keep current filter when None |
| `search_radius` | `30`° | > 0 (UI rule); ≤ 0 forces `-r 180` for ASTAP |
| `downsample_factor` | `0` | ASTAP: 0–4 (0=auto); astrometry.net local: 1–4 |
| `max_objects` | `500` | > 0 |
| `regions` | `5000` | Platesolve2 only |
| `threshold` | `1.0` arcmin | > 0; centering tolerance |
| `rotation_tolerance` | `1.0`° | rotator loop tolerance (mod-180) |
| `number_of_attempts` | `10` | ≥ 1; capture-solve retries per centering iteration |
| `reattempt_delay` | `2` min | between failed capture-solve attempts |
| `sync` | `false` | manual pane: sync after solve |
| `slew_to_target` | `false` | manual pane: run centering (setting it also sets `sync`) |
| `astap_location` | `%programfiles%\astap\astap.exe` if present else "" | file path |
| `cygwin_location` | `""` | astrometry.net local root |
| `astrometry_url` | `http://nova.astrometry.net` | whitespace stripped |
| `astrometry_api_key` | `""` | whitespace stripped; required |
| `asps_location` | *intended*: `%programfiles(x86)%\PlateSolver\PlateSolver.exe` if present; *actual*: a chained-assignment bug in `SetDefaultValues` immediately overwrites it with the ASTAP location (astap.exe path or ""). Use the intended default in Rust | |
| `ps2_location` / `ps3_location` | `""` | |
| `theskyx_host` / `port` | `localhost` / `3040` | |
| PinPoint: catalog `ppGSCACT`, root `%SYSTEMDRIVE%\GSC11\`, max mag `20`, expansion `40`(%), allsky host `nova.astrometry.net` | | |
| RotatorSettings.`range_type` | `FULL` | FULL / HALF / QUARTER (§11.2) |
| RotatorSettings.`range_start_mechanical_position` | `0.0`° | mechanical range start |
| TelescopeSettings.`no_sync` | `false` | true = always offset mode |
| TelescopeSettings.`settle_time` | `5` s | post-slew wait; sync wait = max(2, settle) |
| TelescopeSettings.`focal_length` | `NaN` | must be configured (mm) |
| CameraSettings.`pixel_size` | `3.8` µm | unbinned |
| Centering max iterations | `10` | hardcoded |
| Silent-sync threshold | `1.0` arcsec | hardcoded |
| Solver timeout | `10` min | hardcoded (intended) |
| Online poll interval | `1000` ms | hardcoded |
| solve-field scale window | ±`0.2` arcsec/px | hardcoded |
| Temp-file retention (failed dir) | `7` days | hardcoded |

---

## 16. Source map (MPL-2.0 provenance; paths relative to `references/nina/`)

| Algorithm / fact | Source |
|---|---|
| Centering loop, sync/offset/silent-sync, 10 attempts, dome sync, filter restore | `NINA.Platesolving/CenteringSolver.cs` L58–173 (validation 59–60; epoch normalisation 92–96; threshold 103; sync/offset 107–129; silent-sync <1″ 117–126; reslew 131–138; dome 140–150; exhaustion 158–161; filter restore 163–172) |
| CenteringSolveResult / CenteringMeasurement wrappers | `NINA.Platesolving/CenteringSolver.cs` L176–209 |
| Capture-solve retry loop, concurrent filter revert, reattempt delay | `NINA.Platesolving/CaptureSolver.cs` L47–93 |
| Near/blind selection + blind failover + focal-length validation | `NINA.Platesolving/ImageSolver.cs` L37–90 |
| Parameter model, binned pixel size, J2000 hint coercion | `NINA.Platesolving/PlateSolveParameter.cs` L20–65; `CaptureSolverParameter.cs` L19–22; `CenterSolveParameter.cs` L17–20 |
| Result model, PA mod-360, J2000 result coercion, pixel-error display | `NINA.Platesolving/PlateSolveResult.cs` L22–77 |
| Image properties, FoV derivation | `NINA.Platesolving/PlateSolveImageProperties.cs` L20–40 |
| Working/failed dirs, 7-day cleanup, `EnsureSolverValid` + image-properties entry point | `NINA.Platesolving/Solvers/BaseSolver.cs` L26–69 |
| FITS save pipeline for solver input (legacy writer, BITPIX 16, BZERO 32768, big-endian i16, raw data) | `NINA.Image/ImageData/BaseImageData.cs` L322–518 (dispatch 381–408; SaveFits 476–518); `NINA.Image/FileFormat/FITS/FITSHeader.cs` L29–37; `NINA.Image/FileFormat/FITS/FITSData.cs` L18–33 |
| FileSaveInfo defaults (legacy writer true, no compression) | `NINA.Image/FileFormat/FileSaveInfo.cs` L20–46 |
| CLI lifecycle, 10-min timeout (incl. token bug), failed-file archive, FITS save, target-coords fill, process spawn | `NINA.Platesolving/Solvers/CLISolver.cs` L50–175 (timeout 69–79; archive 83–122; save 124–132; spawn 146–175) |
| ASTAP args (-f/-fov/-z/-s/-r/-ra/-spd), ini parsing, pixscale/radius/PA/flip, validation, output paths | `NINA.Platesolving/Solvers/ASTAPSolver.cs` L40–189 (ReadResult 40–114; args 125–162; validation 164–179; paths 181–189) |
| solve-field args, ±0.2 scale window, wcsinfo parsing, PA formula | `NINA.Platesolving/Solvers/LocalPlateSolver.cs` L27–151 |
| nova API endpoints, auth/upload/poll, calibration mapping, parity/PA, key validation | `NINA.Platesolving/Solvers/AstrometryPlateSolver.cs` L34–286 |
| ASPS args + output format | `NINA.Platesolving/Solvers/AllSkyPlateSolver.cs` L25–107 |
| Platesolve2 args/.apm parsing, flip+180 rule, regions | `NINA.Platesolving/Solvers/Platesolve2Solver.cs` L23–139 |
| Platesolve3 args/_PS3.txt parsing, 206264.8 pixscale | `NINA.Platesolving/Solvers/Platesolve3Solver.cs` L24–133 |
| TheSkyX ImageLink flow | `NINA.Platesolving/Solvers/TheSkyXImageLinkSolver.cs` L40–120 |
| PinPoint COM flow | `NINA.Platesolving/Solvers/Dc3PinPointSolver.cs` L32–160 |
| Solver factory near/blind matrices | `NINA.Platesolving/PlateSolverFactory.cs` L47–84 |
| WCS CD-matrix rotation/flip/pixscale math; CROTA2 form; PA accessor | `NINA.Astrometry/WorldCoordinateSystem.cs` L45–143 |
| Epoch transforms (SOFA), creation-date semantics | `NINA.Astrometry/Coordinates.cs` L100–212 |
| Separation math, coords ± separation | `NINA.Astrometry/Coordinates.cs` L540–596 |
| Angle tolerance equality (mod 360 / mod 180), epsilon | `NINA.Astrometry/Angle.cs` L87–106 |
| Unit conversions, 206.2648 factor, euclidian modulus, FieldOfView | `NINA.Astrometry/AstroUtil.cs` L39, L49–121, L727–739 |
| Profile defaults (all §15 platesolve rows) | `NINA.Profile/PlateSolveSettings.cs` L36–79 (+ property bodies) |
| Telescope defaults (focal length NaN, settle 5, no_sync false) | `NINA.Profile/TelescopeSettings.cs` L31–43 |
| Camera pixel-size default 3.8 | `NINA.Profile/CameraSettings.cs` L32–36 |
| Center sequencer item (park check, slew, dome, parameter build, guider stop/start) | `NINA.Sequencer/SequenceItem/Platesolving/Center.cs` L127–213 |
| CenterAndRotate (rotate loop + center) | `NINA.Sequencer/SequenceItem/Platesolving/CenterAndRotate.cs` L111–231 |
| SolveAndRotate (rotate loop, FULL-range 180 logic) | `NINA.Sequencer/SequenceItem/Platesolving/SolveAndRotate.cs` L129–226 |
| SolveAndSync | `NINA.Sequencer/SequenceItem/Platesolving/SolveAndSync.cs` L95–143 |
| Meridian-flip recenter (best-effort) | `NINA.WPF.Base/ViewModel/MeridianFlipVM.cs` L274–315 |
| Manual solve pane (attempts=1, sync/slew options, separation calc) | `NINA/ViewModel/Imaging/AnchorablePlateSolverVM.cs` L103–347 |
| Solve progress/history VM | `NINA.WPF.Base/ViewModel/PlateSolvingStatusVM.cs` L29–98 |
| Mount sync (epoch transform, RA mod 24, settle wait, NoSync gate) | `NINA.WPF.Base/ViewModel/Equipment/Telescope/TelescopeVM.cs` L787–811 |
| Mount slew (epoch transform, park refusal, dome parallel sync, settle, 10-min timeout) | `NINA.WPF.Base/ViewModel/Equipment/Telescope/TelescopeVM.cs` L891–966 |
| Mount epoch determination (ASCOM EquatorialSystem) | `NINA.Equipment/Equipment/MyTelescope/AscomTelescope.cs` L106, L560–615, L764–791 |
| Rotator sync / mechanical-range target mapping (FULL/HALF/QUARTER) / move completion poll | `NINA.WPF.Base/ViewModel/Equipment/Rotator/RotatorVM.cs` L110–125 (sync), L127–160 (move), L474–518 (GetTargetPosition + GetTargetMechanicalPosition) |
| Rotator range defaults (FULL, start 0.0) | `NINA.Profile/RotatorSettings.cs` L31–36, L75–99 |
| Gain −1 → default gain | `NINA.WPF.Base/ViewModel/Equipment/Camera/CameraVM.cs` L828–840 |
| CaptureSequence defaults (gain/offset −1) | `NINA.Equipment/Model/CaptureSequence.cs` L35–59 |
| Temp path + directory cleanup | `NINA.Core/Utility/CoreUtil.cs` L32, L184–199 |
| UI validation ranges (radius>0, downsample 0–4 ASTAP / 1–4 local, max objects>0) | `NINA/View/Options/PlateSolverView.xaml` L459–527, L959–974 |
| Behavioral contract tests for centering offsets | `NINA.Test/PlateSolving/CenterSolverTest.cs` L56–291 |

---

## 17. Recommended for AstroDeck

**Solver**: ASTAP as both near and blind solver (NINA's default and the only one that is
cross-platform, fast, and cleanly CLI-driven). Keep `blind_failover_enabled = true`.
Implement astrometry.net *online* as a zero-install fallback for first-run UX; skip the
cygwin local astrometry.net path entirely (Windows-legacy; on Linux, spawning native
`solve-field` with the §8.1 argument set is trivial if ever wanted). Skip PS2/PS3/ASPS/
TSX/PinPoint — Windows-only, closed, or obsolete.

**Defaults to adopt verbatim** (proven values):
exposure 2 s, binning 1 (recommend exposing 2×2 for slow rigs), gain −1 (camera default),
search radius 30°, downsample 0 (ASTAP auto), max objects 500, threshold 1.0 arcmin,
attempts 10 with 2-min reattempt delay wired but consider a 30 s default (2 min is tuned for
clouds; AstroDeck is interactive), centering cap 10 iterations, rotation tolerance 1.0°.

**Centering**: implement §5 exactly, including offset-sync. This is the part that makes
centering work on mounts that reject or silently ignore syncs (common over Alpaca; some
strain-wave mounts refuse syncs near the pole) — do not simplify it away. Keep the 1″
silent-sync detection and the `max(2 s, settle)` post-sync wait, otherwise the detection
misfires on slow-polling drivers.

**Epochs**: keep NINA's rule — J2000 everywhere internally (hints, results, targets);
transform to the mount's reported epoch only at the mount boundary and inside the centering
comparison. Use the `erfa` crate (`eraAtci13`/`eraAtic13`/`eraEo06a`/`anp`) as the SOFA
equivalent; carry a creation timestamp on JNOW values for the reverse transform.

**Fixes over the reference** (intentional deviations, all noted inline above):
1. actually enforce the 10-minute solver timeout (§12 token bug);
2. treat a missing `ra_center` in local-astrometry output as failure (§8.2);
3. parse `.wcs`/`.ini` with a real FITS/ini reader and invariant number parsing — never
   locale-dependent (§10 PS2 decimal-comma workaround exists because NINA didn't; NINA also
   formats ASTAP `-r` and PS2 `regions` with the current culture, §7.3);
4. use clean archive filenames for failed solves (single dot);
5. wire the profile solve-gain into *every* entry point (NINA forgets it for SolveAndRotate,
   SolveAndSync, and CenterAndRotate's rotate loop — §2 caveat);
6. set `success` explicitly on every result path instead of relying on a default-true
   constructor (§1.3/§9);
7. use the intended ASPS default path, not NINA's clobbered one (§15).

**Result contract for the Rust engine**: return
`{ra_j2000_deg, dec_j2000_deg, position_angle_deg (mod 360), pixscale_arcsec_px, flipped,
radius_deg, separation {ra_deg, dec_deg, distance_deg, bearing_deg}}` so the UI can render
exactly what NINA renders (including pixel-error = separation/pixscale).
