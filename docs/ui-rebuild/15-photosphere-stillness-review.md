# Photosphere stillness implementation review

> Resolution (2026-09-18): the three findings below are closed on `feat/photosphere-production` by the continuity plan (`docs/superpowers/plans/2026-09-18-photosphere-continuity.md`), commits `b28026ff`, `2903c71d`, `034f0a30`, `a865d4d4` and `f0ddf567`. Open follow-ups from its whole-branch review: issues #46, #48 and #49.

Date: 2026-09-18  
Reviewed commits: `b72c66b9..730a59b9`  
Result: Changes requested. Application code was not modified during review.

## Scope

The new commits implement the stillness correction and fixture changes described in `docs/superpowers/plans/2026-09-18-photosphere-stillness.md`. They cover part of Phases 0–1 of `14-photosphere-production-handoff.md`.

The full production plan is still outstanding: retained original photographs and replay packages, provider qualification, continuous visual tracking, global stitching and loop closure, revised horizon detection, and physical-device acceptance. This review assesses the implemented stillness work without treating those later phases as completed.

## Findings

### P1 — A new steady view can certify an old direction after the phone moved

Locations: `ui/src/next/hubs/sky/sheets/photospherePose.ts:87–94`, `photosphere.ts:313–316`, and `photosphereStability.ts:122–129`.

`VisualStability` establishes a new anchor after motion or an observation gap. Its boolean says that the current view has settled against that new anchor. It does not establish that the view stayed unchanged since the last orientation reading. Both `forFrame()` and `vouched()` nevertheless use that boolean to trust an arbitrarily old direction.

Reproduction using the actual `PhotosphereSweep` and the existing DOM harness:

1. Run `approachAndHold()`; no further orientation events arrive.
2. For 30 video ticks, increment the fixture's image shift each tick. The app correctly reports `compassReady: false`, `frameCount: 0`.
3. Keep the new image stationary for 12 more ticks, still with no orientation events.
4. The app now reports `compassReady: true`, `frameCount: 1`, and `Captured. Move to another blue dot.`

The photograph has been accepted at the direction from before the movement. The same loss of continuity can arise across backgrounding or a camera gap followed by a different steady view. This can corrupt the panorama even if motion detection itself is perfect.

Required change: tie visual continuity to the specific pose being vouched for. Once observed motion or an unobserved interval invalidates that correspondence, settling alone must not restore it. Require a fresh coherent pose or successful visual relocalization. Add an end-to-end move-with-silent-sensor, then stop case; the existing continuously-moving case does not cover this transition.

### P1 — The timer fallback treats repeated reads of a frozen frame as new video evidence

Location: `ui/src/next/hubs/sky/sheets/photosphere.ts:548–557`; the read is stamped fresh at `:536`.

Without `requestVideoFrameCallback`, each timer tick redraws the video and calls `VisualStability.observe()` with the current wall-of-execution time. It never establishes that a new media frame was delivered. A stalled or paused video can retain nonzero dimensions and a textured last image, while the camera track has not ended and the page remains visible.

Repeated reads then refresh the evidence indefinitely and earn a successful hold. The stopped-video test covers absence of frame callbacks, which cannot model this fallback because its timer keeps running.

Reproduction: run the real driver's existing `approachAndHold(false)` fixture for ten interval ticks without advancing the video media clock. It reports `compassReady: true`, `frameCount: 1`. The mock element remains paused with an unchanged `currentTime`; the production code never consults those facts. A previously playing element stalled on its last decoded image follows the same read path.

Required change: update stability only when the fallback can establish a newly delivered media frame. Include appropriate playback, readiness and track-muted/lifecycle checks. Timer activity must not substitute for video activity. Add separate paused/stalled fallback tests and verify recovery after real frame delivery resumes.

### P2 — The last-sample jitter guard can permanently reject a valid quick movement

Location: `ui/src/next/hubs/sky/sheets/photospherePose.ts:88–90`.

The new guard classifies a change above 8 degrees within 150 ms as an outlier, regardless of visual motion or actual sensor quality. Such movement is physically plausible. Because rejection runs before the silent-settle return, the same final pair remains disqualifying for the entire hold. History is pruned only when another orientation sample arrives, so waiting cannot resolve it.

Reproduction with the actual class: provide readings at 0, 100, ..., 500 ms facing 0 degrees, followed by a valid 10 degree reading at 600 ms. Supply steady-video and healthy-source evidence afterward. `forFrame()` returns null at 1,100, 2,000, 10,000, and 30,000 ms. The final movement was 100 degrees/second, followed by a steady phone.

Required change: use corroborating motion/pose evidence and an explicit recovery path for suspect readings. Do not permanently reject the final direction solely from this rate threshold. Add a valid rapid approach followed by event silence alongside the magnetometer-outlier regression.

## Verification performed

- 77 existing targeted checks passed across `photospherePose`, `photosphereStillness`, `photosphereStability`, `photosphereGeometry`, `photosphereRegistration`, `photosphereVertical`, `photosphereStillnessDom`, and `photosphereCaptureDom`.
- `node node_modules/typescript/bin/tsc -b --pretty false` completed successfully from `ui`.
- The additional reproductions above ran in memory against production modules and the existing DOM harness. No application or test source files were edited.
- No live telescope actions, deployment, or physical S25 validation were performed.

The first two findings are independent of the already documented limitation where a mostly smooth scene can hide motion from a small luminance grid. They remain failures even with sufficiently textured images and correct detection of the intervening movement.
