# Automatic camera calibration and a photosphere test simulator

Date: 2026-09-18  
Status: Design and implementation specification. The simulator and automatic calibration described here are not implemented yet.  
Related: [production handoff](14-photosphere-production-handoff.md), [stillness review](15-photosphere-stillness-review.md).

## 1. Decisions

1. Measure the geometry of the actual video stream automatically. A phone model name or a saved guess is not calibration.
2. Treat normal handheld translation as an input to support. A person turning with an extended phone moves the camera around their body; looking upward can move it again. A stationary optical centre is a control test, not the required scanning technique.
3. Test against an independently rendered 3D world. A textured sphere is useful for orientation tests but cannot represent a nearby roof and distant trees at different depths.
4. Define the observing position. The final horizon must describe the view from the telescope's intended position, not an unspecified mixture of camera positions.
5. Feed images and realistic sensor observations into the real scanner. Keep exact camera and scene information outside its reach.
6. Measure errors automatically, including the pixels of the finished image. Make deliberately corrupted results fail before using the simulator to certify improvements.

This extends handoff 14. Its earlier recommendation to pivot around the lens is superseded. Rotation-only stitching can remain a supported special case; it cannot be the sole geometry for the requested handheld experience.

## 2. What automatic FoV measurement means

Field of view (FoV) is derived from the camera's pixel-to-ray mapping. Estimate that mapping for the selected lens, video resolution, crop, zoom, and image orientation. Include lens distortion when evidence supports it. The video stream may differ from the camera's advertised or still-photo FoV.

For a centred, undistorted image, horizontal FoV is `2 atan(width / (2 fx))`. For an off-centre or distorted image, calculate angles between actual edge rays instead. Report horizontal and vertical FoV, plus angular uncertainty across the image. One short-axis number is useful for diagnostics but is not a complete camera model.

### Proposed estimator

1. Retain overlapping photographs and track static landmarks across several views. Include features near the image edges, yaw and pitch variation, and return views. Keep useful photographs while calibration is pending.
2. Start with a restrained model: shared focal length, known pixel aspect, and a centred principal point. Treat any provider-supplied intrinsics as evidence to validate against the delivered pixels. A future native provider can obtain camera intrinsics; that does not imply ordinary browser video exposes them. [ARCore camera intrinsics](https://developers.google.com/ar/reference/java/com/google/ar/core/CameraIntrinsics)
3. Compare a distant-scene rotation model, a planar-scene explanation, and a general 3D model with camera translation. A good planar fit does not establish that the camera rotated in place.
4. Jointly refine camera parameters, camera positions/orientations, and tracked scene points. Use robust losses and uncertain motion priors. Reject moving leaves, people, and inconsistent correspondences. Do not let compass drift become fictitious lens distortion.
5. Add distortion parameters only when spatial coverage and independent validation justify them. Do not release every camera parameter at once to make the fitting error smaller.
6. Validate using withheld observations and disjoint sections of the scan. Vary initial focal estimates and sensor-prior weights. Sweep focal length while re-optimizing the other variables: a flat or multimodal error profile means the FoV is unresolved.
7. Set `verified` only when both predictive accuracy and parameter uncertainty meet the declared profile. Optimizer convergence and a small training residual are insufficient.

There is a real ambiguity here: an exact outward-facing circular camera path is a critical motion for focal autocalibration from fundamental matrices. The panorama-style SfM literature uses additional structure, including distant-feature rotation models, rather than assuming a circular-motion prior establishes focal length. Use this as an initialization reference, not a guarantee that every handheld scene is identifiable. [Sweeney et al., section 4](https://arxiv.org/html/1906.03539)

If natural scanning does not resolve the ambiguity, ask for one short, specific action that adds evidence: include a distant textured view, or change tilt while keeping recognizable details in view. If necessary, request a brief change in the relationship between arm motion and camera rotation. More frames along the same ambiguous path are not automatically more information. Do not ask the user to type a guessed angle.

### Calibration state and reuse

Use `collecting`, `verified`, `needs-more-evidence`, and `invalidated` states, with reasons and uncertainty. Cache by provider, physical camera identity where available, dimensions, crop/zoom, orientation transform, and model version. Revalidate at the next session. Invalidate or revise after lens switches, crop changes, stabilization changes, or other detected changes in pixel geometry. Focus breathing may also require updating the model.

Migrate the existing 60 degree default, experimental 41.1 degree setting, and manually saved FoV values as unverified priors. None is an S25 calibration. A trustworthy provider may supply the initial mapping, but silent changes still need detection.

## 3. Define the finished panorama's viewpoint

Choose a reference position `C_ref`. A practical first implementation records the initial camera centre with the phone held at the intended telescope position and height, before the user starts turning. It is unnecessary to recover absolute metric scale just to use that recorded centre as the reconstruction's reference. Choosing another point in metres requires a scale/reference measurement; do not assume the user's arm radius is known.

For a reconstructed scene point `X`, the direction to plot is:

```text
direction at the telescope = normalize(X - C_ref)
```

This differs from its direction in an image acquired at another position. For example, a perpendicular 1 metre viewpoint shift changes the bearing of an object initially 5 metres away by about 11.3 degrees. Correct FoV alone cannot remove that parallax.

Consequences for production:

- Estimate translation and scene depth where near objects matter. Use rotation-only geometry only where justified by distance and uncertainty.
- Sparse feature reconstruction helps recover camera geometry, but does not produce a complete roof or tree silhouette. Reprojection to `C_ref` also needs supported surface/depth estimates and visibility handling.
- Resolve occlusions at the reference position. A surface never seen from any useful capture must remain unknown. Blending cannot manufacture the missing evidence.
- Keep a confidence mask and angular uncertainty. Cosmetic seam warps must not move a planning boundary without updating its geometry.
- Level and true north are separate references. Good visual calibration does not establish geographic north.

This is a larger change than replacing a focal-length constant or calling a standard panorama stitcher. A normal, observable handheld scan near mixed-depth scenery must eventually pass the positive tests below; rejecting every such scan is not completion.

## 4. Simulator architecture

```text
Seeded scene + continuous physical trajectory + camera configuration
                |
       +--------+----------------------+-------------------+
       |                               |                   |
 GPU image renderer              Sensor/delivery       Independent truth
       |                         fault generator       and ray evaluator
       +--------------+----------------+                   |
                      |                                    |
            Input-only recording                           |
                      |                                    |
     Real capture / tracking / calibration / stitching     |
                      |                                    |
          Actual output + event log ----------------> Scorer
                                                           |
                                        JSON results, heatmaps, replay
```

Use Three.js/WebGL for the first renderer and interactive trajectory viewer. Offscreen render targets and pixel readback provide the image-generation boundary. Keep this tooling out of the production UI bundle. Use a separate Python numerical evaluator and independent ray intersections for truth. [Three.js renderer API](https://threejs.org/docs/pages/WebGLRenderer.html)

Add a small Blender-generated cross-check corpus later for a second rendering implementation and more realistic lighting. GPU rendering is acceptable on the development machine; it does not impose a GPU requirement on every AstroDeck installation. Cache rendered sequences so most algorithm regressions only replay existing images.

### Independence requirements

- Do not import production projection, orientation, registration, or horizon helpers into the renderer or truth evaluator. The existing fixture shares production geometry and therefore cannot serve as this independent benchmark.
- Share declarative scene/recording schemas, not implementation functions for the geometry being tested.
- Make `input/` and `truth/` separate artifacts. The process under test receives only input images and metadata a real provider could supply. It must not read exact camera parameters, world landmark positions, scene IDs that encode FoV, or uncorrupted poses.
- Keep a separate, explicitly named provider-assisted profile when testing supplied intrinsics. Do not count it as visual self-calibration.
- Check rendering against hand-specified cardinal views and independent CPU ray/primitive intersections before evaluating AstroDeck. Check GPU buffer row order explicitly; an inverted readback must fail a top/bottom marker test.

## 5. Define the world and test patterns

| Scene family | Construction | What it tests |
|---|---|---|
| Angular reference | Distant directional background with an asymmetric spherical chart | Axes, FoV, roll, portrait/landscape, poles, wraparound, inversion |
| Mixed-depth chart | Ground, walls, roofs, overhangs, poles, branches and canopy with known geometry | Translation, parallax, depth, occlusion and reference-position accuracy |
| Natural scene | Irregular textures, repeated windows, foliage, shadows, bright walls and dark sky | Realistic feature selection, segmentation, exposure and failure handling |

Place nearby structures at 0.75–4 metres, intermediate objects at 5–20 metres, and distant scenery beyond 50 metres. Include a roof or canopy that reaches near the zenith. Test sparse poles and branches at declared angular widths; they must not disappear through coarse horizon sampling.

The chart should combine uniquely coded, asymmetric landmarks; irregular seeded texture; differently sized features; and slanted edges. Put orientation markers across north and near both poles. A repeated checkerboard or hexagonal grid alone allows a wrong correspondence to look right. Include repeated-pattern and featureless variants as deliberate difficult cases.

Use a directional background for the effectively infinite scene. A finite sphere has a single surface radius and can be useful as a separate controlled case, but it is not a substitute for a 3D yard. Natural-scene variants must omit the unusually helpful chart markers so the algorithm cannot succeed only on test patterns.

## 6. Model a person holding the phone

Use independently specified camera position `C(t)` and orientation `R(t)`, not yaw/pitch applied to a camera fixed at the world's origin.

A simple horizontal control trajectory is:

```text
C(t) = bodyPivot(t) + [r(t) sin(yaw(t)), r(t) cos(yaw(t)), heightOffset(t)]
R(t) = independently specified outward view, pitch, roll, and wrist motion
```

World axes are east, north, up. Treat 0.50, 0.75 and 1.00 metres as configurable body-to-camera radii for the requested tests, not a claim about every user's anatomy. Record radius and travelled arc length separately. A 0.75 metre radius turn through 24 degrees moves the camera about 31 centimetres along its chord.

Add an articulated shoulder/elbow/wrist trajectory for upward scans. Arm lift changes position; wrist tilt changes orientation; body sway and variable reach affect both. Do not implicitly keep the lens centre fixed when looking upward. Keep an optical-centre offset relative to the simulated handset so rotating the handset can translate its camera.

Required trajectories:

- Zero translation and 5 cm radius controls.
- 0.50, 0.75 and 1.00 metre loops, including an exact outward circular path to test calibration ambiguity.
- Imperfect loops with independent wrist yaw/pitch and changing arm extension.
- Multiple elevation bands, vertical sweeps beside a roof, and a zenith cap.
- Smooth stop–start sweeps, 0.8–2 second holds, backtracking, and return-to-start closure.
- Small seeded tremor, body sway, different panning speeds, and a step sideways.
- Portrait/landscape changes and simultaneous roll/pitch near overhead.

Trajectories must not be derived from the production target-dot coordinates. Initially replay prescribed routes. Later add a closed-loop simulated user that follows visible guidance through the public interaction surface; keep that separate from the prescribed-route tests so changing guidance cannot silently make the test easier.

## 7. Model the camera and its timing

### Optics and images

Sweep several rectilinear FoVs, including short-axis values of 30, 40, 60, 80 and 100 degrees, and multiple aspect ratios/resolutions. Add off-centre principal points, radial/tangential distortion, crop, zoom, and orientation transforms. Test wider or fisheye models as a separate capability profile; an unsupported model must not receive a false calibration certificate.

Generate distortion by mapping each delivered pixel to its physical camera ray, with enough source resolution and overscan. Specify pixel centres and resampling. Arbitrary post-render warping with untracked cropping would make the truth ambiguous.

Add exposure changes, saturation, shot/read noise, compression, blur and focus changes in stages. Integrate motion over the exposure interval. For rolling shutter, sample row-specific exposure times. Model stabilization as an explicit changing crop/warp, not merely another camera rotation.

Truth passes include surface IDs, depth, visibility, and landmark positions. State whether depth is camera-axis distance or Euclidean range. Do not interpolate integer IDs or interpret antialiased boundary pixels as a single certain surface. Keep blur/transparency mixtures explicit in advanced scenes.

### Motion and delivery

Generate sensor readings from the physical trajectory through an independent device-coordinate conversion. Apply seeded bias, drift, quantization, noise and delay. Emit change-driven orientation events with genuine silence during holds. Independently vary image delivery latency and cadence. [Device orientation convention](https://www.w3.org/TR/orientation-event/#model)

Record exposure start/end, any physical capture time, media presentation time, callback arrival, sensor acquisition time where available, and sensor event/arrival time. Missing physical timestamps stay missing in the app input. Video callbacks distinguish timing concepts; browser integration must not treat callback time as guaranteed acquisition time. [Video frame callback specification](https://wicg.github.io/video-rvfc/)

Required faults include dropped and duplicated frames, frozen video, delayed/out-of-order sensor events, source switching, background/resume, permission loss, dimension/display-angle races, and fallback without video-frame callbacks.

## 8. Recording and replay contract

Proposed tooling directory: `tools/photosphere_sim/`. The files and execution layers below are implementation targets, not existing tools.

```text
case/
  manifest.json             # schema, seed, versions, hashes, expected outcome
  input/
    frames/                # immutable RGB images; distinct physical frame IDs
    observations.jsonl     # available metadata and sensor/delivery events
    actions.jsonl          # open, begin, pause, resume, finish, review, save
  truth/                   # inaccessible to the pipeline under test
    scene.json
    camera.json
    trajectory.jsonl
    landmarks.json
    reference-horizon.json
    visibility/            # depth / surface evidence as needed
  result/
    scan/                  # actual app's retained scan and estimated solution
    panorama.png
    horizon.json
    events.jsonl
    scores.json
    report.html            # visual explanation of numeric failures
```

Use three execution layers:

1. **Algorithm replay:** actual estimator/stitcher consumes recorded images, with no DOM or rendering delay.
2. **Capture replay:** inject a narrow frame, motion, lifecycle, and clock provider into the actual scanner. Preserve capture selection, registration, confidence, and storage behavior. Advance a virtual clock deterministically.
3. **Browser integration:** drive the real sheet using synthetic media and scheduled events. Check presentation, frame copying, pause/resume, processing and Save. Record actual browser timings; a canvas stream alone does not give precise control of physical capture timestamps.

All layers use the same immutable frame IDs and can trace an accepted photograph back to its exposure. Record renderer/runtime/GPU versions, app build, algorithm version and input hashes. Pin environments for pixel-level comparisons. Across GPUs, use documented numerical tolerances rather than expecting bit-identical rendering. Replaying a cached input removes renderer variance from algorithm comparisons.

## 9. Mechanical error measurement

The evaluator needs two truths: what each moving camera saw, and what is visible from `C_ref`. A world point can legitimately occupy different angular positions in successive moving-camera images. Do not count that physical parallax itself as a stitching defect.

### Geometry

- **Camera calibration:** focal-parameter error, horizontal/vertical FoV error, and angular ray error on a uniform image grid. Inspect edges separately.
- **Pose:** rotation error and camera-centre trajectory error. Monocular reconstruction may have one global similarity ambiguity; permit one declared global alignment for local reconstruction scoring, never a different alignment per frame. Report absolute level/north error separately without fitting it away.
- **Held-out prediction:** use independent world correspondences and visibility to measure predicted pixel location at the stated resolution. Retain residual distributions by depth, image edge, elevation and motion regime.
- **Finished panorama:** for each visible reference landmark, compare its actual output direction with `normalize(X - C_ref)`. Use spherical angular distance across north and near the poles. Measure median, p95, p99, maximum, duplicate landmarks and ghost edges.
- **Overlay:** compare rendered target/landmark positions with the corresponding displayed frame's truth through time. Measure moving and settled periods separately.

Score actual output pixels using independent chart decoding/feature localisation, as well as exported transforms or warp maps. Accurate metadata cannot excuse a broken raster. Count missing or undecodable expected landmarks as omissions; do not silently remove them from the denominator. Blur, a blank panorama, or refusal to capture everything must fail positive cases.

### Horizon and confidence

Ray-cast the world from `C_ref` to establish sky/obstruction truth. Use sufficiently fine or adaptive angular sampling to bound error below the smallest tested obstacle; cross-check simple roofs/poles analytically. Maintain the full angular mask and a conservative highest-obstruction envelope. The envelope cannot describe holes beneath branches.

Measure signed boundary error, missed-obstruction height and width, false-open and false-blocked solid angle, unresolved area, and north offset. Weight spherical area correctly rather than counting every equirectangular pixel equally. Treat moving objects as a separate profile with time-dependent truth or an explicitly defined conservative interval.

Record false confirmation: any region painted as reliably captured/aligned when its geometric error exceeds the profile limit. Report capture latency, recovery latency, rejection reasons, successful completion and resource use. Coverage thresholds refer to an independently defined observable region based on the scene and delivered sequence, not whichever subset the algorithm happened to accept.

Measure boundary completeness separately from panorama area. A narrow strip containing the entire difficult horizon can occupy less than 5% of the image. Masking that strip as unknown must not pass a positive horizon test. Require evidence for every independently observable boundary bin and every declared test obstacle; missing boundaries are failures, not samples omitted from the percentiles. Use 0.1 degree azimuth bins and obstacles at least 0.25 degree wide for the initial chart profile, with finer oracle sampling to verify visibility. These resolutions are benchmark settings, not sufficient clearance margins by themselves.

### Provisional gates

These are initial engineering targets, not measured performance or a final clearance guarantee. Reconcile them with the planner's horizon margin before release. Keep the thresholds fixed for comparisons and record any deliberate change.

| Profile | Initial gate |
|---|---|
| Noise-free, well-observed calibration chart | FoV error no more than 0.25 degrees; p95 ray error no more than 0.1 degree |
| Qualified noisy handheld scene | FoV error no more than 1 degree; p95 ray error no more than 0.5 degree; nominal 95% FoV intervals no wider than 2 degrees when verified |
| Calibration confidence qualification | At least 100 independently seeded held-out scans; empirical interval coverage at least 90%, with a binomial confidence interval reported; zero falsely verified cases in the qualification corpus. This is a corpus gate, not proof of zero real-world failure probability. |
| Finished panorama on positive cases | p95 landmark angular error below 0.5 degree; p99 below 1 degree; at least 95% of independently observable requested coverage |
| Live overlay | p95 below 1 degree during a slow pan, 0.5 degree while settled |
| Capture after a valid hold | Within 1.5 seconds; no fabricated orientation heartbeat |
| Loop closure and qualified horizon | Loop mismatch below 1 degree; p95 horizon boundary error below 1 degree; all independently observable boundary bins and minimum-width obstacles accounted for. A missing expected boundary or obstacle fails the positive case. |
| Safety-relevant confidence | No confidently false-open test obstacle of the profile's declared minimum width; unknown counts separately and cannot satisfy coverage |
| Ambiguous or unsupported inputs | Explicitly unresolved calibration/geometry, retained evidence, and actionable recovery; no confidently wrong result |

Require positive mixed-depth 0.5–1 metre arc cases that contain enough motion/texture diversity to succeed. For a deliberately ambiguous exact arc, provide only finite unknown structure, no qualifying distant-feature subset, and no sufficiently accurate independent rotation or intrinsics evidence. Pair it with an arc that has such additional evidence and should succeed: an exact arc is not invariably unobservable. A correctly uncertain result is appropriate for an unobservable case; it is not a blanket exemption for the normal scanning cases.

## 10. Prove the evaluator can fail

Before trusting a green report, run known corruptions against otherwise correct output:

- Change focal length by 5%; shift yaw by 1 degree and 5 degrees; introduce a north-wrap offset.
- Flip vertically, mirror horizontally, or mishandle portrait readback.
- Substitute an earlier camera pose, duplicate a frame, remove a connected part of the scan, and use the wrong reference position.
- Blur the entire output, erase landmarks, or return an empty panorama. Also erase only the horizon strip to prove that high total area coverage cannot hide missing boundary evidence.
- Change brightness only as a geometry-preserving control.

Each must produce the expected affected metrics and a failing result where applicable. Check quantitative response, not just a red flag. For example, a pure global yaw corruption should be visible in geographic/north scoring even if a separately reported relative-reconstruction score allows a global gauge alignment.

Make the three regressions from [review 15](15-photosphere-stillness-review.md) permanent cases:

1. Stop orientation events, move the image, then settle in a different view. New stillness must not certify the old direction without valid relocalization.
2. Freeze the physical video frame while fallback timer callbacks continue. It must not accumulate fresh visual evidence.
3. Approach by 10 degrees over 100 milliseconds and then hold with change-driven silence. A valid quick approach must recover rather than remain permanently rejected by an old jump.

## 11. Delivery sequence

### A. Build the measuring instrument

- Independent coordinate contract and hand-specified axis tests.
- One chart-covered mixed-depth scene, an outward 0.75 metre trajectory with an upward sweep, and a zero-translation control.
- Frame export, independent truth, replay through the current scanner, and a numeric report.
- Corrupted-output tests that prove the scorer detects wrong geometry and missing coverage.

Exit: the current implementation has an honest measured baseline. Do not adjust scenes until it passes.

### B. Add capture faults and automatic FoV

- Retain complete source frames and add provider/clock injection.
- Reproduce and fix the review 15 regressions.
- Implement multiview calibration with qualification and invalidation, including translation and sparse finite-depth reconstruction where mixed-depth handheld inputs require them. This geometry cannot be deferred to stage C.
- Expand camera models and motion cases; include deliberately ambiguous inputs.

Exit: supported unknown FoVs converge from different initial guesses; unsupported/ambiguous cases remain explicitly unverified.

### C. Support normal handheld geometry

- Extend the sparse reconstruction from stage B with sufficiently supported surface/depth geometry, occlusion handling, reference-position rendering and confidence.
- Add loop closure, connected multi-elevation coverage and recoverable processing jobs.
- Measure final pixels and horizon on mixed-depth arcs, including high nearby obstructions.

Exit: qualified positive cases meet accuracy and coverage together, rather than trading one away silently.

### D. Broaden qualification

- Natural textures, low light, rolling shutter, stabilization and dynamic-scene cases.
- Small second-renderer corpus; held-out seeds/scenes never used for tuning.
- Fast cached regressions per change; expanded GPU generation and replay in scheduled test runs.
- A small set of real phone recordings to check sensor/API assumptions, crop behavior, performance and calibration transfer. Replay these thereafter.

The simulator should eliminate repeated outdoor testing for every mathematical change. It cannot prove that a particular browser grants permissions, supplies the expected stream, or behaves like the simulated sensor. Ask for targeted physical checks only when they resolve one of those remaining uncertainties.

## 12. Completion evidence

Each implementation handoff should include a reproducible case ID and input hash, the tested build, pass/fail metrics, uncertainty and coverage, failure heatmaps, retained recordings, runtime/memory figures, and the exact supported camera/scene profile. State remaining limitations plainly.

The first useful milestone is one complete deterministic scan through the real scanner with a trustworthy score report. A beautiful rendered yard without that replay and evaluator is only a demo.
