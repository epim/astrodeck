# Photosphere continuity and simulator review

> Resolution (2026-09-18, later the same day): both findings are closed on `feat/photosphere-production`. P1 by `0d372612`, `9512c190` and `2a7b543e` (a capture requires a delivered image on every path; new outcomes `stale-image` and `frame-already-captured`; the identity clause pinned by a test). P2 by `52df7eb3` and `9e3097fb` (moves are a swing-twist of the full attitude, timed by the attitude angle; the camera position follows a continuous azimuth; issues #55 and #56 found and fixed on the way). The scope observations are addressed by the rest of the stage A plan: the Three.js renderer, the replay through the production scanner, scoring, corruptions and the baseline (`18-photosphere-simulator-baseline.md`) all landed; automatic FoV calibration and the reconstruction work remain stage B and later, as the review says.

Date: 2026-09-18  
Reviewed range: `730a59b9..9fde718c`, plus the existing working-tree UI used by the phone preview.  
Result: Changes requested. Two reproduced findings below. Application code was not changed during this review.

## Findings

### P1 — Frame freshness gates the stillness witness, but does not gate capture

Location: `ui/src/next/hubs/sky/sheets/photosphere.ts:603` and the capture flow immediately below it.

`newMediaFrame(video)` correctly refuses paused/stalled media when deciding whether to update `VisualStability`. However, a false result does not stop `grabFrame`. It continues to obtain a pose and read the video's retained pixels. Fresh, nearly steady sensor events satisfy the strict pose path even when visual continuity is unavailable. The manual overhead path additionally bypasses the pose/stability requirements and also continues when the video is paused.

Reproduced against the actual `PhotosphereSweep`, using a separate copy of the existing DOM fixture:

1. Start recording and approach a target using the interval fallback.
2. Freeze the video media clock, leaving the element playing with data and the track live.
3. Continue orientation events every 100 ms, alternating 0 and 0.2 degrees around the target to model small sensor variation.
4. Drive timer callbacks. The frame count increases from 0 to 1 despite the media clock remaining fixed at 1.75 seconds.

Separate reproduction: pause the video after the approach, advance the clock by three seconds, and call `captureOverhead()`. It returns `true` and the frame count again increases from 0 to 1.

Impact: retained pixels can be assigned the phone's current direction, or treated as a newly captured overhead view, even though the camera has not supplied that view. The new frozen-video regressions all use a silent sensor and therefore do not exercise the first path.

Required correction: make freshness/availability of an actual image a capture requirement on every path, including manual overhead, independently of sensor validity. Associate a permitted capture with an identifiable delivered frame. Avoid a fix that makes a manual click consume or reject a fresh frame merely because the stillness observer already saw it. Add positive recovery tests alongside the two failing cases.

Local reproducer, from `ui`:

```text
node .probe/photosphere-review-repros.mjs
node --import tsx .probe/photosphere-review-repros.tsx
```

The generator reads the existing fixture and writes an ignored diagnostic copy. Both review assertions fail on the reviewed implementation; the production test files remain unchanged.

### P2 — The simulator times one path but moves the camera along another

Location: `tools/photosphere_sim/sim/trajectory.py:109` through the duration calculation at line 111; interpolation is in `_eval_segment`.

`_move_duration_ms` calculates duration from the shortest great-circle distance between the endpoints. `_eval_segment` then interpolates azimuth and altitude separately, which need not follow that shortest path. The resulting movement exceeds the stated average limit of 30 degrees/second and peak limit of 45 degrees/second.

This occurs in the shipped `arc075` route, not only in an invented input. The transition from `(az=0, alt=89.5)` to `(az=180, alt=0)` lasts 3.017 seconds. Integrating the implementation's own continuous angular-rate formula across 1,001 samples gives:

| Quantity | Measured |
|---|---:|
| Average look-direction speed | 50.052 degrees/second |
| Peak look-direction speed | 84.908 degrees/second |
| Endpoint full-attitude change | 180 degrees |

Impact: a supposedly controlled human-motion fixture can create unexpectedly fast motion, affecting tracking, blur and capture results. The current test permits up to 1 degree per 10 ms sample, so this route still passes it.

Required correction: time the actual interpolation path, including its peak, or use a path consistent with the duration calculation. Measure full camera attitude as well as forward direction so near-zenith roll cannot disappear from the movement budget. Add checks against the declared limits over all shipped route segments.

Local numerical reproducer: `.probe/photosphere-review-sim.py`. It only reads the route and simulator modules and prints the violating segment.

## What this implementation covers

The continuity changes address the original silent-sensor move/settle failure and the valid quick-approach deadlock in the scenarios exercised by the tests. The simulator now includes coordinate conventions, a chart yard, analytic scene truth, prescribed camera trajectories, change-driven sensor generation, and a case builder.

The following are still absent from the reviewed checkout:

- The Three.js image renderer. The implemented renderer only produces flat grey images for case-builder tests; the default renderer command explicitly refuses to run.
- Replay through the production scanner and the numeric scoring/corruption/report commands. The replay-independence test is skipped because the replay driver does not exist yet.
- Automatic FoV calibration. Production still starts at 60 degrees or loads a manually stored estimate.
- The broader production reconstruction work described in handoffs 14 and 16, including translation-aware reference-position rendering.

These are scope/completion observations, separate from the two implementation defects above. Passing the new tests does not yet establish panorama alignment accuracy.

## Validation

- UI production build: passed, including TypeScript compilation. Existing bundler warnings remain.
- Eight relevant photosphere suites: **98 passed, 0 failed**.
- Independent simulator suite: **90 tests run, 89 passed, 1 skipped**, about 41 seconds. The skipped test requires the unfinished replay driver.
- Additional review reproductions: **2 capture assertions fail**, sharing the P1 cause above; the numerical trajectory check confirms P2.
- The whole application test suite was not rerun; unrelated working-tree changes were preserved.
- No physical Android camera test was performed by this review.

The simulator tests used the existing Python 3.12 installation. No packages were installed.

## Phone preview

The existing phone server was already listening. The UI was rebuilt, and the running server was verified against the new output rather than restarted unnecessarily.

URL: `https://192.168.216.72:8843/#/classic/tonight?experience=guided`

Verified: trusted TLS, saved local login, closed first-run bootstrap, authenticated status, camera/geolocation permissions policy, origin checks, isolated simulator connection, and live secure WebSocket status. The served entry and horizon bundles match the rebuilt files byte for byte:

- `index-BG7qUME6.js`: SHA-256 `1281d598246b9e7ec300131d1f8f378cf796f12411d54ea1d125cf78359e3e38`
- `horizon-aS3ikjqC.js`: SHA-256 `ca6cf0d144cd4294ffbf15a47cb948ff06a86e881e888a66b5980115371c11e6`

The existing test login and phone certificate were preserved. The preview uses the isolated simulator, not the physical telescope. Reload an already open page to receive the rebuilt UI.
