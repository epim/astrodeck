# Photosphere Residuals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Close the photosphere residuals left open by the 2026-09-18 issue pass: #39, #61, #62, #63, #64, #65, #68, #70, #71, #74, #76, and the half of #78 that does not wait on another session. Record the true status of #48, #53 part 2, #66, #69 and #78's second half on their issues instead of pretending to close them.

**Architecture:** Same seams as the first pass. `ui/src/next/hubs/sky/sheets/photosphere.ts` is the driver (readiness, cues, capture log, registration anchor, horizon tracer), `photosphereStability.ts` the video witness, `photosphereGeometry.ts` the dome and panorama, `tools/photosphere_sim/` the grader, `ui/run-tests.mjs` plus `ui/src/next/__tests__/r7*.test.ts` the test infrastructure. Tasks are grouped so that no two running at once touch the same file.

**Tech Stack:** TypeScript under plain `node --import tsx`, jsdom for DOM cases, Python 3 stdlib unittest for the simulator, GitHub Actions for CI.

**Spec:** the issue bodies, saved verbatim under `.superpowers/sdd/2026-09-19-photosphere-residuals/issues/<n>.md`. `docs/ui-rebuild/16-photosphere-calibration-simulator.md` section 9 and `tools/photosphere_sim/CONTRACT.md` bind the simulator. `docs/ui-rebuild/18-photosphere-simulator-baseline.md` carries the numbers a change must move honestly.

## Global Constraints

- No site coordinates, no site label, anywhere. Scan before finishing: `grep -l -F -f C:/Users/bear/.astrodeck/privacy-needles.txt <files>` must print nothing. Never print that file.
- No emojis. UTF-8 without BOM, LF line endings.
- Implementers never commit and never push; the controller commits with explicit pathspecs after review.
- **Another session owns `ui/src/next/hubs/sky/sheets/horizon.tsx`, `horizonStrip.ts`, `__tests__/horizonDom.test.tsx`, `ui/src/main.tsx`, `ui/src/next/lib/horizonModel.ts` and about twenty more files, uncommitted in this tree.** Never touch, stage, stash, reset or clean them. Never run a git write operation of any kind.
- No telescope motion, no rig deploy, no rig configuration. The rig is running tonight's chain.
- Every test added must be shown to fail under a named mutation, recorded in a comment on the case and in the task report. A test that cannot fail is a defect.
- Numbers asserted must be measured, and the measurement recorded. A ruling's number is a claim like any other: compute it.
- The capture DOM file's last case stays last (#51).
- Per task: run the touched test files directly from `ui/` (`node --import ./test-css-stub.mjs --import tsx <file>`), then `node node_modules/typescript/bin/tsc -b --pretty false`; simulator tasks run `python -m unittest discover -s tools/photosphere_sim/tests -t tools/photosphere_sim` from the repo root and `python -m sim score <case>` from `tools/photosphere_sim`.

---

### Task 1: #61 and #39, the test infrastructure's own blind spots

**Files:** `.github/workflows/ci.yml`, `ui/src/next/__tests__/r7Css.test.ts`, `ui/src/next/__tests__/r7Parity.test.ts`, and a new case file if one is needed. Nothing under `hubs/sky/sheets/`.

- **#61:** CI's `ui` job pins Node 20, which has no `node:module` `registerHooks`, so the 53 per-file `.css` stubs already crash there and the runner-level stub from #50 would extend that to every file. `ui/test-css-stub.mjs` already feature-detects and falls back to `module.register`. Decide between raising CI's `node-version` to a release that has `registerHooks` (22.15 or later) and making the per-file stubs feature-detect the same way, and say why. Whichever is chosen, the fix must be demonstrated against the Node the workflow actually pins: read `.github/workflows/ci.yml`, state the version, and if Node 20 is not installed locally say so rather than claiming a run.
- **#39:** `specsOf` in both guards matches double-quoted specifiers only, so a single-quoted module contributes no import edges and the graph-dependent rules under-report in both directions. Accept either quote character and a substitution-free template literal. Pin it: a fixture module written with single quotes that imports its area stylesheet must be seen by the "every area stylesheet is imported by a module inside its own area" rule (mutation: the double-quote-only scanner). Add the corpus assertion the issue asks for: the scan's edge count for a known file, so a scanner that silently finds nothing fails.

### Task 2: #64 and #68, the simulator's width term and its absence from CI

**Files:** `tools/photosphere_sim/sim/score.py`, `tools/photosphere_sim/CONTRACT.md`, `tools/photosphere_sim/tests/test_score.py`, and for #68 whatever committed fixture the implementer chooses plus `ui/src/next/hubs/sky/sheets/__tests__/photosphereReplay.test.ts`. Nothing else under `ui/`.

- **#64:** `width_missed_deg` counts every under-reported bin while the contract says "a stretch at least ... wide". Report both: the longest contiguous under-reported run and the total. Key the miss rule on the run, which is what the planner's interpolated profile would cross. Re-score the three cached cases and report every row that changes (the review measured `roof-south` on `chartyard-still-60` as carried entirely by the total: 16.6 degrees over five runs, longest 4.9). Also pin the `product_bins` fallback when `result/horizon.json` is missing or empty, which is currently untested and undocumented.
- **#68:** the recordings are git-ignored, so every replay-backed case skips in CI. Commit a small fixture case: the issue's own first option. Size it deliberately (a short route, a small frame size, few frames), say what it costs in repository bytes, and make it exercise the assertions that matter rather than being a token file. The three `(#52)` cases must run on a clean checkout with no local cache. Keep the loud skip for the large recordings.

### Task 3: #62, the noise floor in the gradient

**Files:** `ui/src/next/hubs/sky/sheets/photosphereStability.ts`, `ui/src/next/hubs/sky/sheets/__tests__/photosphereStability.test.ts`. Nothing else.

Sensor noise lifts a frame's measured `min(Gh, Gv)` by up to about 0.0027, so a frame with 0.010 of true structure measures over the 0.0128 floor and its bound corresponds to fewer true cells than `STILL_CELLS` claims. Estimate the frame's own noise gradient and subtract it before both the floor test and the bounds. The issue suggests two estimators: the high-pass residual of the frame, and the difference between two consecutive frames of a still run, which is pure noise. Choose one, say why, and measure the result on every fixture (treeline, thin treeline, overcast, halves, fine texture, vertical ramp, the noisy variants) before and after. The hysteresis band from #75 interacts with this: re-derive it, since its width came from the same 0.0027. Every existing case keeps its meaning or is renamed with a stated reason.

### Task 4: #65 and #70, the driver's two bounds

**Files:** `ui/src/next/hubs/sky/sheets/photosphere.ts`, `ui/src/next/hubs/sky/sheets/__tests__/photosphereCaptureDom.test.tsx`. Sequential with Tasks 5 and 6.

- **#65:** `begin()` requires `compassReady`, which a browser with no `DeviceOrientationEvent` can never satisfy, so the manual overhead press that #42 item 3 restored is unreachable through the UI on exactly the browser it was restored for. Let a scan begin on such a browser when the tilt path can carry it, or say in the cue what the user must do instead; whichever is chosen, the state must be reachable in a test through the same route the UI takes.
- **#70:** `visualAnchor` is set latest-wins and `correctBasis` applies it to every later pose, bounded only per-adjustment at 10 degrees, which is `GATE_OVERLAY_MAX`'s own number. Two numbers that mean different things must not be the same constant by accident. Give the carried correction its own bound, derived from what a registration correction can legitimately be (the lens estimate's error times the field's half-width is the physical scale), and say the derivation. Replay `chartyard-arc075-70` and report `overlay_max_lt_10`, the overlay maxima and the accepted count before and after; a mis-set lens should refuse frames rather than wear a large correction.

### Task 5: #71 and #74, what one column cannot see

**Files:** `ui/src/next/hubs/sky/sheets/photosphere.ts`, `ui/src/next/hubs/sky/sheets/__tests__/photosphereVertical.test.ts`. Sequential after Task 4.

Both residuals need the same evidence and are one fix: a wall is local in azimuth and persists across neighbouring bins, while an exposure seam spans the whole mosaic row and a disc is a few bins wide and does not reach the ground. #71: a floating obstruction under the 12-row persistence floor with clear sky beneath reads as open. #74: an edge softer than about ten rows, and a grey wall 30 to 32 percent darker than its sky, read as open or low. Use the neighbouring bins' agreement, which `traceSkyCoverage` has because it receives every bin at once. Acceptance is the simulator: on `chartyard-still-60` `no_missed_obstructions` stays PASS and `false_open_sr` does not rise above 0.0261; on `chartyard-arc075-60` the roof at bins 20 and 21 is found and `false_open_sr` falls from 0.2487 toward the pre-#58 0.1544 or below; no gate that passes today fails anywhere. Unit cases: the 15-row soft edge, the 32 percent grey wall, a one-bin-wide floating disc that must stay open, and a seam across every bin that must stay open.

### Task 6: #76 and #63, the zenith hold and the reading that arrives on blank sky

**Files:** `ui/src/next/hubs/sky/sheets/photosphere.ts`, `photospherePose.ts`, their test files. Sequential after Task 5.

- **#76:** on both arc routes the zenith hold refuses with `alignment-wait` for its whole window while the still route captures it. Instrument first: the `alignment-wait` record should carry the separation and the anchor magnitude, both already computed, so the log says which term refused. Then fix what the instrument shows. The issue's hypothesis is the azimuth term of `poseSeparation` near the pole, where a small change in forward is a large change in azimuth, together with the carried anchor; Task 4 may have moved this already, so measure before changing anything.
- **#63:** a phone that comes to rest ON blank sky is held by nothing, because the featureless memory only keeps a reading the video already vouched for. The controller's note on the issue is that `devicemotion` is not change-driven: Chromium delivers it continuously and its `rotationRate` says directly whether the phone is turning, so a gyro reporting near-zero rotation since the reading arrived can vouch on the same terms the video does. Build it as a second witness with the same shape as `VisualStability` (a run of quiet samples, broken by rotation above a floor, stale when samples stop), choose the floor and the stale bound from measurement rather than assertion, and say what is not established without a device.

### Task 7: the issues that stay open, and why

**Files:** none. The controller posts these; an implementer does not.

- **#48** stays open for the device pass (the delay and the `currentTime` behaviour on real Firefox Android).
- **#53 part 2** stays open: a sphere-hosted disc changes the chart yard scene, which changes its manifest hash and invalidates every cached recording, so it belongs with stage B's scene work and its re-record.
- **#66** and **#69** stay open: the committed tree has no UI that reaches `PhotosphereSweep` at all, so neither the alignment report's route to the user nor a view-angle control can be built without colliding with the horizon sheet another session holds uncommitted. Both land with that file.
- **#78** first half is a one-line change to a file this session must not touch; the second half (a committed test that renders `HorizonSheet` with a `guided` prop only the uncommitted file has) resolves when that file lands. Record the patch on the issue and notify that session.
