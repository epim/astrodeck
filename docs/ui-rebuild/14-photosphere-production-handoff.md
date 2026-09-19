# Photosphere production implementation handoff

Date: 2026-09-18  
Audience: Claude implementing AstroDeck's phone photosphere scanner  
Status: Implementation plan; application code was not changed when this document was written.

Update, 2026-09-18: [Automatic calibration and simulator specification](16-photosphere-calibration-simulator.md) extends this plan. Normal handheld camera translation must be supported, automatic FoV needs measured confidence, and independent 3D simulation must provide quantitative acceptance evidence. Its requirements supersede the earlier fixed-lens-pivot assumption.

## 1. Assignment

Replace the unreliable photosphere capture core while preserving the Classic UI, Guided setup wizard, and the scanning experience described below. Deliver a scanner whose captures can be replayed, corrected, stitched, and reviewed. Establish accuracy with real camera evidence before describing it as production ready.

The user has repeatedly tested on a Samsung S25 Ultra, primarily in Brave. Symptoms include no capture when aiming at a dot, excessive alignment rejection, a grid that slides against the video, a warped or scrambled panorama, and an unusable automatic horizon. An earlier screenshot appeared upside down; the saved two-frame diagnostic does not reproduce that inversion. Treat it as unresolved.

The intended experience:

1. Open the rear camera at the telescope's intended observing position.
2. See a geodesic dome that stays registered to the scene as the phone rotates.
3. Blue wireframe cells and centre dots show what remains to scan.
4. A successful capture turns the relevant cells green; its dot animates toward the screen centre.
5. Scan sideways and upward, including high nearby obstructions and the zenith.
6. Finish into a panorama with a proposed horizon drawn over it.
7. Correct the proposal directly on the image, save, and continue Guided setup.
8. Use the same saved horizon in the Classic SkyDome and Sky Atlas.

AstroDeck runs on Windows, Linux, Raspberry Pi, Orange Pi, and potentially older Android hardware. Keep the core application vendor agnostic. Phone-specific capture providers may share a portable scan format and the same editor.

### Scope and working rules

- Work in `C:\Users\bear\astro`. The checkout has many unrelated modified and untracked files. Inspect its current state and preserve unrelated work. Never broadly stage, reset, clean, or revert it.
- Implement and test against the isolated simulator and physical phone preview. This task does not authorize telescope motion, deployment to the live scope, or changes to live rig authentication or safety configuration.
- Preserve the Classic layout, Guided navigation, existing horizon editing, and explicit Save behavior. A finished scan is a draft until its horizon is reviewed and saved.
- Keep manual horizon editing available throughout development and on unsupported devices.
- Treat the old scanner's image output as unqualified. A small hold-still fix alone is not completion of this plan.
- Make implementation decisions within this scope independently. Record material architecture choices and measured limitations. Ask the user only for physical-device actions or decisions that cannot be inferred.

## 2. Evidence and confirmed defects

Read `13-photosphere-alignment-audit.md` for history. Its older passing test counts do not establish real-device correctness. The findings below take precedence over optimistic interpretations of those checks.

### 2.1 Change-driven orientation events cause a capture deadlock

`CameraPoseHistory.forFrame()` requires both a 500 ms stable interval and an orientation event no older than 250 ms, with no large gaps in that interval. Chromium emits orientation events on significant changes; its current implementation uses a 0.1 degree threshold. A stationary phone need not emit a heartbeat.

The actual production class was exercised in memory with a 10 degree approach over 0–500 ms, followed by no events while perfectly stationary. Every capture attempt from 500 through 2,000 ms failed. Initially the history still contains movement; later the final reading is classified as stale.

The latest relative-stream implementation can compound this by ignoring absolute updates after relative anchoring. A source that goes quiet while still can consequently appear unavailable.

Existing pose tests, capture DOM tests, and the browser fixture repeatedly emit unchanged readings. They simulate a heartbeat the browser does not guarantee.

Required correction: distinguish unchanged orientation, actual sensor unavailability, stale video, and lost visual tracking. Do not extend a stale-pose timeout indefinitely or fabricate sensor events. Establish visual stability and explicit lifecycle/source-health rules.

### 2.2 The panorama becomes an irreversible reference

Portrait frames are reduced to roughly 180 × 320 and painted into a 1080 × 300 panorama. The capture history retains luminance strips; the diagnostic retains at most 16 small images. Persistent storage retains a baked PNG, not a complete original photo set.

Incoming images are compared with that composite. Registration searches only small rotations: approximately ±6 degrees yaw, ±4 pitch, and ±2 roll, with a separate 10 degree correction limit. Earlier poses and lens estimates cannot be revised. An early mistake can therefore make later valid photos impossible to match.

### 2.3 The trial FOV is not a calibrated lens

The supplied diagnostic has two upright 180 × 320 images with upright sensor bases. Forty-four matched landmarks lie mostly in narrow overlapping edge strips.

- Original 60 degree short-axis assumption: approximately 36.6 pixels RMS landmark error with sensor rotation fixed.
- Fitted focal length with sensor rotation fixed: 41.14 degrees, approximately 2.82 pixels RMS.
- Fitted focal length and relative rotation: 36.52 degrees, approximately 0.545 pixels RMS, with about 2.81 degrees of rotation correction.

Several other focal estimates also fit this single pair well when rotation changes. Intrinsics, rotation, and possibly translation are confounded. Preserve 41.1 degrees only as a legacy experimental setting. Do not turn it into an S25 default or label it calibrated.

### 2.4 Capture, tracking, and confidence are coupled incorrectly

- Matching runs after a steady hold, no more often than every 600 ms. The moving overlay remains primarily sensor-driven.
- A frame can be rejected before image matching unless the estimated view is within 8 degrees of a target dot, or 5 degrees near zenith. Relocalization therefore depends on already having an approximately correct direction.
- Returning to a green cell can currently update the visual anchor after passing that gate, but cannot replace its photograph.
- Insufficient overlap or texture returns `unknown`; that can still be inserted into the panorama and turn coverage green. Pixel presence is not proof of correct placement.
- Camera frame capture time, orientation event time, and callback time are not interchangeable. Sharing a clock origin does not prove equal acquisition times.

### 2.5 Horizon extraction fails independently of stitching

`traceSkyCoverage()` estimates upper-sky brightness, then searches for darker pixels. If the upper-sky percentile is below 40, or a column has missing coverage, that direction becomes a 90 degree obstruction. Dark sky beside an illuminated building violates the model.

Only one image column per azimuth bin is sampled, with 30 bins by default. Narrow obstacles can fall between those rays, approximately 12 degrees apart.

Required correction: retain separate sky, obstruction, and unknown classifications with confidence. A planner may conservatively exclude uncertain terrain, but the editor must not present uncertainty as a measured obstruction height.

### 2.6 Physical limitations remain

A rotation-only panorama assumes approximately one camera position. A 5 cm sideways translation beside an object 1 metre away changes its bearing by about 2.9 degrees. One rotation cannot simultaneously fix nearby and distant scenery. Blending may conceal a join while leaving the horizon geometrically wrong.

Normal scanning moves the camera around the person, including when looking upward. Model camera translation and finite scene depth where necessary, and define a reference observing position for the final horizon. Fixed-lens rotation is a control case, not the required user technique. Low light, featureless sky, and unobservable geometry must retain uncertainty. See specification 16 for automatic calibration, reference-position rendering, and positive tests with 0.5–1 metre camera radii.

## 3. Source map and local evidence

Paths below are relative to the repository root unless absolute.

| Area | Current files |
|---|---|
| Capture, device selection, diagnostics, brightness trace | `ui/src/next/hubs/sky/sheets/photosphere.ts` |
| Camera basis, lens model, cells, projection, mosaic | `ui/src/next/hubs/sky/sheets/photosphereGeometry.ts` |
| Sensor source selection and pose history | `ui/src/next/hubs/sky/sheets/photospherePose.ts` |
| Bounded local registration | `ui/src/next/hubs/sky/sheets/photosphereRegistration.ts` |
| Live overlay | `ui/src/next/hubs/sky/sheets/PhotosphereDome.tsx` |
| Capture UI, review, Save integration | `ui/src/next/hubs/sky/sheets/horizon.tsx`, `photosphere.css` |
| Existing browser photo persistence | `ui/src/next/hubs/sky/sheets/photosphereStorage.ts` |
| Editor coordinates and point model | `horizonStrip.ts`, `ui/src/next/lib/horizonModel.ts` |
| Site API and horizon persistence | `ui/src/api/site.ts`, location routes in `server/astrodeck/api/app.py` |
| Existing tests | `ui/src/next/hubs/sky/sheets/__tests__/photosphere*.test.*`, `horizonDom.test.tsx` |
| Classic integration tests | `ui/src/components/sky/__tests__/classicSkyToolsDom.test.tsx` |
| Guided integration | `ui/src/guided/`, `docs/ui-rebuild/12-guided-production-contract.md` |
| Phone HTTPS simulator | `tools/ui_probe/android_https.py` |

Private evidence already present locally:

- `.probe/alignment-real/report.json`: the two-frame user diagnostic.
- `.probe/alignment-real/frame-0.jpg`, `frame-1.jpg` and corresponding RGBA buffers.
- `.probe/alignment-real/matches.json`, `fit-free.json`, `fit-sensor.json`: previous numerical analysis.
- `.probe/alignment-real/register-replay.ts`: replay of the current registration against the real pair.
- `C:\Users\bear\Downloads\astrodeck-scan-alignment.json`: original downloaded diagnostic, if still present.
- `.codex-remote-attachments/01a0a2f6-f5e2-7e50-a9d0-3f47b3554e67/0ffc3948-8712-49b8-acc0-9a3b76023afa/1-Photo-1.jpg`: screenshot of the failed night panorama.
- `ui/.probe/photosphere.html` and `.tsx`: synthetic browser fixture. Its repeated unchanged orientation events must be corrected before using it as capture evidence.

Private photos and reports should remain outside committed fixtures. Use anonymous landmark coordinates or purpose-made scenes for distributable tests. Existing analysis scripts may write probe outputs; inspect before invoking. OpenCV installed under the ignored diagnostic directory is not a production dependency.

## 4. Architecture and contracts

Separate these responsibilities behind explicit interfaces:

```text
Camera provider + motion observations
                 |
       Frame timing + live tracking ----> dome and recovery guidance
                 |
       Durable selected photographs
                 |
       Feature matches + global fit
                 |
       Regenerable panorama + confidence
                 |
       Terrain classification + review
                 |
       Explicitly saved site horizon
```

### 4.1 Capture provider

Use one provider interface for ordinary browser video and, where supported, tracked WebXR camera access. It should expose frame pixels, dimensions, image orientation, timing fields, camera model, pose, and confidence. Unknown fields must remain explicitly unknown.

Run a bounded WebXR capability trial on the actual S25 in Brave and Chrome before choosing the preferred provider. Test an actual immersive AR session with camera access, pose/projection consistency, overhead movement, tracking loss, and recovery. The presence of `navigator.xr`, Chromium ancestry, or ARCore device support alone is insufficient.

If the trial succeeds, use its synchronized pose/image/projection information. Keep the ordinary browser provider for other devices. If it fails, implement browser feature tracking with inertial support and establish its performance before promising the same experience. A native Android capture companion is a later option if those browser proofs fail; creating a companion app is a separate scope decision.

Select one physical rear lens and stable crop/zoom for a session. Inspect available settings and capabilities; request supported controls without assuming every browser can lock them. Camera switching, changed dimensions, zoom changes, and screen rotation must invalidate or re-establish the appropriate calibration and timing state. Do not infer lens identity from device order.

### 4.2 Frame and timing contract

Retain these meanings separately:

- Camera capture timestamp, when provided, and its documented clock origin.
- Video presentation/media timestamp and callback receipt timestamp.
- Orientation event timestamp and receipt timestamp.
- Underlying sensor acquisition timestamp only if the provider actually supplies it.
- Absolute or relative reference, source identity, display orientation, and camera configuration version.

Associate an immutable image copy with its metadata before asynchronous work can select a different video frame. Record uncertainty in timing. Test missing capture timestamps, delayed events, long low-light exposures, and screen rotation where image dimensions and display angle update at different times.

Keep a coherent pose: do not combine heading and tilt from unrelated readings. Absolute-only, relative-only, both-stream, and interrupted-stream cases need explicit behavior. A relative-only scan may use a local level reference, but cannot publish geographic azimuth until a north reference is established.

Define coordinate conversions at module boundaries. Existing world axes are east, north, up; the existing image basis names camera right, image up, and rear-camera forward. OpenCV convention commonly uses camera right, image down, forward. The corresponding camera-to-world matrix is therefore formed from `[right, -up, forward]`, with a proper rotation determinant of +1. Do not pass the existing three named vectors directly as an OpenCV rotation. Specify matrix direction, pixel-centre convention, image scaling, and radians versus degrees in the shared contract. Cover this conversion with independently specified landmark tests.

### 4.3 Scan storage

Introduce a versioned scan package; proposed shape:

| Record | Required content |
|---|---|
| Manifest | Schema version, scan ID, site association, creation time, provider/build version, camera model references, lifecycle state |
| Keyframe | Image blob, dimensions, immutable frame ID, timing fields, camera configuration reference, sensor observations, estimated pose, quality/confidence |
| Match | Frame IDs, correspondences or reproducible feature references, inlier statistics, residuals, validity and reason |
| Solution | Camera parameters, refined poses, level/north transform, uncertainty, coverage, algorithm version |
| Review | Proposed terrain mask/boundary, uncertain sectors, user corrections, accepted boundary, site revision |

Persist selected compressed photographs locally as they are acquired, with transactional writes and recovery after reload. Keep a bounded decoded-image pool and separate low-resolution tracking images from retained stitch sources. Decide and document storage, frame-count, and decoded-memory budgets after the first phone measurement. Never silently evict originals required to rebuild an accepted scan; offer partial review or export when a budget is reached.

Export/import must include enough information to replay a scan. Preserve the existing PNG-only site images as legacy review backgrounds; do not imply they can be restitched.

Normal scan retention and optional diagnostics are distinct. A bounded diagnostic recorder should retain frame samples before capture gates and record rejection reasons, so a session with zero accepted frames is still diagnosable. Record sensor cadence rather than only the most recent reading. Reports must omit credentials and avoid unnecessary identifiers.

### 4.4 Live tracking and capture eligibility

Use image features across successive frames to maintain a local camera reference, with inertial estimates as supporting information. Run tracking in a worker where practical; render the overlay against the corresponding displayed image/pose. Handle unavoidable latency explicitly and measure residual sliding.

Separate tracking/relocalization from photograph selection. Search recognizable overlap even when the user is between target dots. A suggested dot is a capture guide, not a prerequisite for recovering tracking.

Capture quality considers blur, exposure, overlap, usable feature distribution, and pose uncertainty. A geometrically unverified image may be retained provisionally, but it cannot establish confirmed coverage merely because pixels were painted.

Internally distinguish `unseen`, `photographed`, `aligned`, and `needs-review`. Reobserving a patch can replace an inferior image or improve its constraints. Preserve useful keyframes and their provenance when the global solution changes.

### 4.5 Camera calibration and global stitching

Use a tested computer-vision library, with a reproducible dependency and build path. An OpenCV-based host processor is the preferred initial implementation for final stitching; inspect current packaging before adding it. Ensure the selected components can be packaged on Windows and Linux/ARM, or feature-detect an unavailable processor and retain manual/import paths. Do not require public cloud processing or an internet connection at the observing site.

Implement feature extraction, robust correspondence filtering, connected overlap selection, model selection between distant rotation and finite-depth motion, and joint camera/pose/scene refinement. Use several views with useful yaw and pitch diversity. Validate on held-out views and test whether the FoV is actually identifiable. Treat sensor orientation as a prior whose reliability is measured, not an unquestioned fixed rotation. A standard rotational stitcher alone does not satisfy the handheld translation requirement in specification 16.

Constrain the camera model to what the data supports. Fit additional distortion or principal-point parameters only with adequate calibration evidence. Avoid an unconstrained fit that absorbs parallax into fictitious lens distortion.

Version calibration against provider, lens, crop, image dimensions, and relevant settings. Existing `astrodeck.photosphere.lens.*` local-storage values are user-entered estimates. Migrate them as optional unverified priors, not trusted calibration records. A calibrated flag requires measured evidence and a compatible camera configuration.

Close loops when the scan returns to a known view and distribute correction across retained frames. A disconnected image set remains provisional until connected or explicitly reviewed. Expose large residuals or excessive parallax instead of forcing an apparently plausible fit.

Render the final sphere from original retained images after refinement, at the declared observing position. Near-object reprojection needs supported depth and occlusion handling; sparse feature points alone are insufficient for a complete silhouette. Add exposure compensation, seam selection, and suitable blending. Produce coverage and uncertainty alongside the color image. Preserve the calibrated angular map; local cosmetic warps must not silently alter a roof edge used for planning.

Maintain local photographic coordinates separately from earth azimuth/elevation. Use gravity for level and a separately verified north transform. Account for the actual heading reference, including magnetic versus true north where applicable. Record how north was established and the remaining uncertainty.

### 4.6 Host processing and site integration

Implement final processing as a cancellable asynchronous job with persisted progress and resumable source uploads. Proposed operations are create scan, upload idempotent frame, submit processing, inspect progress, retrieve result, cancel, and delete. Exact route names should follow existing server conventions; these endpoints do not exist merely because this plan names them.

Use current authentication, authorization, ownership, input limits, and site permissions. Validate image dimensions and decoded size as well as upload byte size. Keep processing outside the server event loop and telescope-control execution path; bound worker concurrency and resource use on Pi-class hosts. Cancelling a job must not modify the active site horizon.

The existing experience keeps photos in the browser. If host processing is introduced, tell the user plainly that photos will be sent to their AstroDeck computer when they select processing. Keep external cloud transmission out of this flow. Store scans separately from capture frames and provide deletion/retention controls.

Reuse the existing location update flow for the accepted horizon: `PUT /api/locations/{loc_id}` with its current revision, permissions, and active-site behavior. Inspect that implementation before changing it. Omitted `horizon_points`, `null`, and an empty array have distinct existing meanings. Preserve them.

Uncertainty requires a sidecar mask/review record or an explicit model extension. Do not encode unknown as open sky or falsely measured altitude 90. Before a partial result influences automatic field selection, uncertain sectors must remain excluded or be resolved through review. A user can save a scan draft without replacing the existing active horizon.

## 5. Horizon detection and editor behavior

Replace the global brightness threshold with a sky/terrain classifier or another validated segmentation method, with confidence and boundary refinement. Document model license, weights, runtime, offline packaging, and supported conditions if a learned model is used. Classify before cosmetic blending where seam treatment could alter boundaries, then project evidence into the common angular map.

Use dense angular evidence. Reduce each interval conservatively so a narrow obstruction cannot disappear between centre samples. Retain the underlying mask; a single-valued horizon line is a conservative summary and cannot express open gaps beneath an overhanging branch.

Treat daylight and usable twilight as the first qualified automatic-detection conditions. Preserve night capture and manual review paths where useful, but require separate evidence before describing night detection as reliable. Dark sky, clouds, illuminated walls, moving leaves, and missing images must exercise explicit uncertainty handling.

Review requirements:

- Panorama and editable horizon use the same calibrated projection and north transform.
- Preserve the existing angular aspect correction and touch-friendly panning/zooming.
- Show unknown areas distinctly and offer targeted rescan or manual correction.
- Keep Save prominent; Clear uses a trash icon and remains secondary.
- Saving a reviewed horizon updates the intended site and the Guided step only after the actual save succeeds.
- Late processing results must not overwrite newer user edits, another site's draft, or a newly saved horizon. Use scan/job IDs and revision checks.
- Leaving the scan, switching sites, closing the sheet, losing permission, or backgrounding must release camera resources and preserve recoverable work as appropriate.

## 6. User experience and state machine

Suggested lifecycle:

```text
checking support -> opening camera -> establishing tracking -> scanning
scanning <-> recovering tracking
scanning <-> paused
scanning/paused -> processing -> reviewing -> saving -> saved
processing -> processing failed -> retry or review available data
```

Preserve the blue/green dome language. Show a subtle pending state for retained photographs awaiting reliable placement. Explain one immediate action at a time. Examples:

- "Hold here for a moment."
- "Captured. Follow the next blue dot."
- "Point back at this view so I can find your place." Show the matching thumbnail.
- "It's too dark to identify this edge. Draw it here, or scan this section before dark."
- "This section is missing. You can scan it again or mark the boundary yourself."

Guide a connected route with overlap through the lower scene, upward obstructions, and zenith. Adapt targets to the calibrated field of view; a narrower lens needs more overlap. Do not make every empty sky region require a textured image match. Uncertainty may grow through blank sky and should trigger reobservation of visible terrain or explicit review.

Explain how to establish the intended observing position, then guide a natural handheld sweep with connected overlap. Do not require the user to rotate precisely around the lens. If the available motion or scene does not constrain calibration or depth, request one specific additional view and preserve existing work.

Controls must remain reachable on portrait phones, landscape phones, and tablets. Reserve layout space for status text; never draw a toast or changing message over Capture, Finish, Save, Next, or Continue. Support text scaling, reduced motion, readable contrast, and touch targets of at least 44 CSS pixels. Permit partial review and pause without discarding progress.

## 7. Implementation sequence

Use separable changes that can be reviewed independently. Complete the evidence gates in order; continue independent implementation while waiting for physical-device input.

### Phase 0 — Baseline and reproduce

- Inspect the current checkout and relevant repository instructions.
- Record the existing tests/build state and classify unrelated failures separately.
- Add a failing regression for change-driven orientation: move toward a target, stop, suppress unchanged events, and verify the current deadlock.
- Add distinct cases for actual sensor interruption and stopped video. A correction must not make those appear valid.
- Correct synthetic fixtures so they can model real event suppression, delivery delays, and camera latency.
- Confirm the two saved images' axes and existing replay without claiming a full scan has been reproduced.

Exit: the confirmed failure is reproducible, and the tests distinguish stillness from actual loss.

### Phase 1 — Record evidence and select the capture provider

- Add versioned recording and export/import independent of successful capture.
- Keep a complete selected-photo set and a bounded diagnostic time window with rejection reasons.
- Implement the provider abstraction and explicit sensor/video/tracking states.
- Correct static-event starvation using those states and visual stability evidence.
- Run the bounded S25 WebXR capability trial and record the decision. If physical access is unavailable, leave the capability result unqualified and proceed with provider-independent work.
- Collect one short controlled recording before requesting another full scan: recognizable verticals, slow yaw/pitch, stops, and return to start.

Exit: a failed session is replayable; a steady real phone can capture; timing and camera parameters are inspectable.

### Phase 2 — Tracking and connected acquisition

- Implement visual tracking, coherent pose association, and recovery independent of dot gates.
- Add target selection that preserves overlap during normal handheld motion and requests useful calibration views when necessary.
- Track separate photographed/aligned/review states and permit better replacement frames.
- Persist pause/resume state and handle permission, visibility, source, orientation, and crop transitions.
- Measure actual phone frame times and memory; move expensive work off the UI thread.

Exit: the live dome meets the tracking tests; lost tracking has an actionable recovery path; source images survive reload.

### Phase 3 — Rebuildable global panorama

- Implement the host processor, job lifecycle, feature matches, camera estimation, joint refinement, loop closure, and rendering.
- Keep all transforms and quality measurements tied to the same immutable frame IDs.
- Produce a low-resolution preview early and a refined result afterward without overwriting edits.
- Test wrong initial pose, wrong FOV, disconnected views, blank sky, repeated textures, parallax, cancellation, and retry.

Exit: complete recorded multi-elevation scans can be rebuilt consistently, and bad input remains identifiable rather than corrupting the only panorama.

### Phase 4 — Horizon proposal and Guided integration

- Add validated terrain detection, dense sampling, unknown sectors, and manual correction.
- Establish level/north review and preserve angular uncertainty.
- Integrate explicit Save with site revision handling, Classic Atlas/SkyDome, and Guided completion.
- Retain old PNG backgrounds and manual horizons; migrate storage without data loss.
- Verify phone/tablet layouts and status/control separation.

Exit: the edited line matches the calibrated photo, saves to the correct site, and is used consistently by planning and both sky views.

### Phase 5 — Qualification and cleanup

- Run the full acceptance matrix below on recorded data, then on the physical S25.
- Check packaging and bounded processing on representative desktop and Linux/ARM hardware. Mark hardware that was not tested explicitly.
- Remove obsolete capture paths only after their callers and regression coverage are accounted for.
- Update the audit with measurements, evidence locations, known limitations, and the tested build ID.
- Verify the phone server serves the final assets before asking the user to refresh.

Exit: report what is qualified, what remains experimental, and any physical test still required. Do not substitute a count of passing unit tests for these results.

## 8. Acceptance matrix

Numerical values below are proposed initial engineering targets, not claims about existing behavior. Confirm the required horizon clearance budget with the alignment-field planner before qualification. Changes to targets must be explained, not silently relaxed to obtain a pass.

| Test | Expected evidence |
|---|---|
| Move, then hold still with unchanged events suppressed | A sharp, tracked view becomes capturable within 1.5 seconds of a steady hold; no sensor wiggle is required. 2026-09-19: the stillness witness now grades apparent motion in raster cells rather than luminance (issue #38, commit `1df2b18f`); issue #62 and [the simulator baseline](18-photosphere-simulator-baseline.md) section 5 record what still limits it. |
| Genuine sensor/video loss | The app identifies loss without indefinitely accepting a cached direction or frozen image. |
| Absolute-only, relative-only, both streams, delayed anchor, dropped stream | Coherent poses, explicit geographic-reference status, and controlled recovery without rotating past captures silently. |
| Timestamp/latency replay | Missing or delayed timestamps remain distinguishable; an image is never assigned a newer pose merely because its callback ran later. 2026-09-19: the interval fallback's vouching margin is now pinned at one observation interval rather than left unbounded (issue #48, commit `512d1478`); the real device's own camera-pipeline and `video.currentTime` timing is still unmeasured. |
| Portrait, landscape, inverted display, pitch, roll, zenith | Known image landmarks project to the correct rays; transition frames cannot contaminate the scan. |
| Recovery between dots and on green cells | Recognizable overlap recovers tracking; existing coverage can improve without duplicate progress. |
| Camera calibration | Several diverse views constrain a stable camera model; held-out feature residuals are measured and reported at a stated image resolution. |
| Live overlay | Initial target: 95th-percentile angular displacement below 1 degree during a controlled slow pan, below 0.5 degree after settling. Measure against image landmarks. |
| Full loop | A real 360 degree multi-elevation scan returns to the initial landmark; initial target is less than 1 degree loop mismatch after refinement, with per-overlap residuals reported. |
| Wrong initial FOV/pose | The solver corrects recoverable estimates or rejects the solution clearly; it can rebuild from original images. |
| Near roof and distant trees | Observable 0.5–1 metre handheld arcs pass reference-position accuracy and coverage tests together. Unobservable geometry produces a specific recovery request or review state. |
| Thin pole/high canopy/clear zenith | Dense terrain evidence preserves small obstructions and handles high elevations. 2026-09-19: every direction above the horizon, including straight overhead, now has a dome cell within the aim cone, closing the case where a hold at the zenith could never capture (issue #57, commit `b1e629f1`). |
| Dark sky and lit house, clouds, moving foliage, blur | Confidence reflects limitations. Unknown never becomes a claimed measured boundary. 2026-09-19: a featureless hold (blank sky, no orientation events) now keeps a vouched reading and states why instead of reading the compass as lost (issue #41, commit `a720226d`); the device pipeline delay question of issue #48 remains unverified. |
| Surveyed horizon | Initial target: 95th-percentile boundary error below 1 degree on qualified static daylight scenes; also report missed-obstruction cases and north error separately. 2026-09-19: the tracer now finds the sky boundary by its transition rather than a luminance threshold, closing the case where a bright wall read as open sky (issue #58, commit `3fbd2039`); measured p95 on the simulator's noise-free chart yard fell from 75.9 to 20.6 degrees but is still far above the 1 degree target -- see [the simulator baseline](18-photosphere-simulator-baseline.md) section 3.3. |
| Pause, reload, interrupted upload, retry, cancel | Selected photographs survive; processing is idempotent and cancellable; the active horizon remains unchanged until Save. |
| Site switch and late job completion | No result can overwrite another site's draft or newer user edits. |
| Mobile layout | Portrait and landscape S25 plus tablet checks; text scaling and long errors cannot cover action buttons. |
| Resource use | Report phone frame-time distribution, decoded memory, storage, and processing duration; host jobs do not stall normal API responsiveness. |

Use several independently recorded scenes and complete scans. Synthetic tests should include distortion, translation, timing gaps, exposure changes, and repeated textures. Tests rendered with the same projection model as the implementation establish consistency, not physical calibration.

Implement the independent 3D renderer, hidden truth, production replay, mechanical scorer, and corrupted-output checks defined in [specification 16](16-photosphere-calibration-simulator.md). Include the three permanent regressions in [review 15](15-photosphere-stillness-review.md). Complete the first scoring milestone before relying on new synthetic pass counts.

For real test fixtures, preserve a small private corpus locally and use purpose-made or consented imagery for shared fixtures. Produce replay summaries with build/algorithm version, accepted/rejected counts, residual distributions, loop mismatch, coverage, and unresolved sectors.

## 9. Development and phone testing

Current environment at handoff:

- Windows PowerShell, repository `C:\Users\bear\astro`.
- UI is React/TypeScript/Vite. Build: run `npm.cmd run build` in `ui`.
- Full UI suite: run `npm.cmd test` in `ui`.
- Individual existing tests use `node --import tsx`, for example `node --import tsx src/next/hubs/sky/sheets/__tests__/photospherePose.test.ts` from `ui`.
- Relevant existing test groups: `photosphereGeometry`, `photospherePose`, `photosphereRegistration`, `photosphereVertical`, `photosphereCaptureDom`, `horizonDom`, `classicSkyToolsDom`, and the Guided tests affected by changes. Add server tests for new jobs, permissions, validation, cancellation, and site-save integration.
- Server Python exists at `server/.venv/Scripts/python.exe`; inspect the project's test/dependency configuration before adding packages.

Last known phone preview: `https://192.168.216.72:8843/#/classic/tonight?experience=guided`. The LAN address and certificate validity can change. Read `.probe/android-wifi/connection.json` selectively for current connection metadata; do not dump credentials. The helper's setup page identifies a local `PHONE-LOGIN.txt` for test credentials. Passwords and rig tokens do not belong in this document, commits, screenshots, or diagnostic reports.

The HTTPS helper serves `ui/dist` and runs an isolated simulator. Rebuild and confirm served asset identity before declaring a phone update available. Inspect the helper before invoking commands that recreate certificates, accounts, or simulator state. Preserve the working phone setup where possible.

The earlier desktop simulator used `http://127.0.0.1:8803`. Loopback addresses belong to the device opening them; the phone needs the PC's reachable LAN HTTPS address. Camera and motion access must be evaluated in the actual browser context, not assumed from the desktop fixture.

Physical acceptance requires the user or a connected test device. When that input is unavailable, finish replay tests, implementation, and documentation that can be completed locally, then state exactly which device gates remain open. Do not label the feature production ready in their absence.

## 10. Completion report for the user

When handing back the implementation, include:

1. What changed in capture, tracking, stitching, and horizon detection.
2. Which physical device/browser combinations and lighting conditions were tested.
3. Quantitative tracking, stitching, and horizon results, with evidence paths.
4. Whether the user's existing scan can be reprocessed or a new recorded scan is required.
5. The exact preview URL and build identity, with credentials handled separately.
6. Any remaining unsupported conditions, untested hardware, or incomplete acceptance gates.

Keep the explanation short; put measurements and detailed test records in the audit. The user has already spent significant time repeating scans. Ask for the smallest targeted physical test that resolves a specific uncertainty.

## 11. Primary technical references

- [Chromium orientation event pump](https://raw.githubusercontent.com/chromium/chromium/main/third_party/blink/renderer/modules/device_orientation/device_orientation_event_pump.cc): change threshold and event delivery behavior; implementation can change, so compare with the tested browser version.
- [W3C Device Orientation and Motion](https://www.w3.org/TR/orientation-event/): coordinate conventions, reference frames, and event semantics.
- [Video frame callbacks](https://wicg.github.io/video-rvfc/): distinct capture and presentation timing fields; capabilities must be measured.
- [WebXR Raw Camera Access](https://immersive-web.github.io/raw-camera-access/): camera imagery aligned with an XR view's pose and projection; specification availability is not proof of browser support.
- [OpenCV stitching pipeline](https://docs.opencv.org/4.x/d1/d46/group__stitching.html): feature matching, camera estimation, refinement, warping, exposure, seams, and blending.
- [ARCore camera intrinsics](https://developers.google.com/ar/reference/java/com/google/ar/core/CameraIntrinsics): camera calibration information available to a possible native provider.
- [ARCore tracking failure reasons](https://developers.google.com/ar/reference/java/com/google/ar/core/TrackingFailureReason): low-light and feature limitations still apply to native tracking.
