# Photosphere Issue Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every open photosphere issue on `epim/astrodeck` (#38, #41, #42, #46, #48, #49, #50, #51, #52, #53, #57, #58, #59) on `feat/photosphere-production`, one issue per task, in issue-number order, and refresh the simulator baseline once at the end.

**Architecture:** The scanner is `ui/src/next/hubs/sky/sheets/photosphere.ts` (driver: gates, cues, capture log, horizon tracer) over `photosphereStability.ts` (the video witness), `photospherePose.ts` (readings and vouching) and `photosphereGeometry.ts` (dome cells, lens, panorama). The simulator under `tools/photosphere_sim/` grades the real scanner through the jsdom replay (`ui/src/next/hubs/sky/sheets/__sim__/replay.ts`) against ray-cast truth. Each task changes one seam and pins it with a test that a named mutation reddens.

**Tech Stack:** TypeScript (ui, plain `node --import tsx` test files, jsdom for DOM cases, `npm test` = `run-tests.mjs`), Python 3 stdlib unittest for the simulator (`python -m unittest discover -s tools/photosphere_sim/tests -t tools/photosphere_sim`), Playwright Chromium for renders (already cached cases replay without it).

**Spec:** the issue bodies, saved verbatim under `.superpowers/sdd/2026-09-18-photosphere-issue-pass/issues/<n>.md` (copied from the GitHub issues by the controller). `docs/ui-rebuild/16-photosphere-calibration-simulator.md` (section 9) and `docs/ui-rebuild/14-photosphere-production-handoff.md` are the standing specs the issues argue from. `tools/photosphere_sim/CONTRACT.md` binds every simulator change.

## Global Constraints

- No site coordinates or site label anywhere (code, tests, docs, commit messages, issue text). Scan every file before it is committed: `grep -l -F -f C:/Users/bear/.astrodeck/privacy-needles.txt <files>` must print nothing.
- No emojis in code, docs, tests or messages. Files are UTF-8 without BOM, LF line endings.
- Implementers never commit and never push. The controller commits with explicit pathspecs after review. The working tree carries other sessions' uncommitted edits (`horizon.tsx`, `horizonStrip.ts`, `horizonDom.test.tsx`, `main.tsx`, `horizonModel.ts` and about twenty more): never touch, stage, reset, stash or clean them.
- No telescope motion, no rig deploy, no rig configuration from this plan.
- Every test added must be shown to fail under a named mutation of the code it pins (record the mutation in the task report and in a comment on the case). A test that cannot fail is a defect.
- Numbers in this plan marked "measured" were computed by the controller on 2026-09-18 from the committed code; numbers marked "to measure" must be measured by the implementer and recorded in the report and in the test's comments before being asserted.
- ui checks per task: the touched test files directly (`node --import tsx <file>` from `ui/`), then `node node_modules/typescript/bin/tsc -b --pretty false` from `ui/`. Simulator tasks: the unittest command above with `-v`, plus `python -m tools.photosphere_sim score <case>` on the cached cases named in the task.
- The five DOM test files that crash in this checkout on `ERR_UNKNOWN_FILE_EXTENSION` (#50) crash because of another session's uncommitted `horizon.tsx`; until Task 7 lands, run only the files a task names.
- Cue strings are UI copy: they say what the software knows and name the action that clears the state (never "move the phone" to a phone that is holding still; see #37).

---

### Task 1: #38 The stillness bounds mean the same thing on every scene

**Files:**
- Modify: `ui/src/next/hubs/sky/sheets/photosphereStability.ts`
- Test: `ui/src/next/hubs/sky/sheets/__tests__/photosphereStability.test.ts`

**Interfaces:**
- Consumes: `VisualStability.observe/stableAt/continuity` as they are.
- Produces: exports `gradient(grid): number` (mean absolute difference between horizontally and vertically adjacent cells of the normalised grid, one number, dimensionless), constants `GRADIENT_FLOOR`, `STILL_CELLS`, `ANCHOR_CELLS` replacing `TEXTURE_FLOOR`, `STILL_DIFF_LIMIT`, `ANCHOR_DIFF_LIMIT` (keep the old names exported as deprecated aliases only if a test outside this file imports them; `grep -rn "STILL_DIFF_LIMIT\|ANCHOR_DIFF_LIMIT\|TEXTURE_FLOOR" ui/src` first and say in the report what imports them). `stableAt` keeps its tri-state contract.

The rule (issue #38, "Suggested shape of a fix"): a frame's own spatial gradient `G` is the size of one cell of apparent motion. The per-frame and anchor bounds become `STILL_CELLS * G` and `ANCHOR_CELLS * G`. A frame whose `G` is below `GRADIENT_FLOOR` cannot witness motion at all and reads `null` (this subsumes `TEXTURE_FLOOR`: variance says how much the frame varies, gradient says whether a shift would be visible; the thin-treeline scene has large variance and small gradient, which is exactly the case variance gets wrong).

- [ ] **Step 1: Measure before changing anything.** Add a temporary measurement block (not committed) that prints, for the three scenes below at 320x240, the normalised grid's variance, `G`, the per-frame diff for a 1 px shift, and the anchor diff after 5, 10, 30, 120 and 180 frames of 1 px/frame pan:
  - `treeline` (exists in the test file),
  - `overcast` (exists),
  - `thinTreeline`: NEW scene, smooth graded sky (base 150, spread 20 vertically) over the top seven eighths, a jagged treeline (the `treeline` canopy function scaled to the bottom eighth) below. Issue #38 measured its variance at 0.076 and its anchor diff saturating near 0.010; confirm or record your own numbers.
  Record the table in the report. Choose `GRADIENT_FLOOR` above the sensor-noise gradient: noise of 3 luma levels per pixel averaged over a 10x10 block leaves about 0.3 levels per cell; on a mean of 120 that is 0.0025 per cell, and adjacent-cell differences of independent noise are sqrt(2) of that, 0.0035. Set `GRADIENT_FLOOR` at least three times that (0.011 or higher) and say in the constant's comment what it is three times of. Choose `STILL_CELLS` and `ANCHOR_CELLS` so that on `treeline` the new bounds reproduce the old behaviour within the acceptance table below (the old 0.02 and 0.04 were 1.4 and 2.8 times the treeline's 1 px diff of 0.0142; measured).
- [ ] **Step 2: Write the failing tests** (add to the existing file; keep every existing case, updating only constants it imports):

```ts
test('A thin treeline under a smooth sky cannot vouch for a hold while panning (issue #38)',()=>{
  const s=new VisualStability();
  let vouched=false;
  for(let i=0;i*FRAME_MS<=2000;i++){s.observe(i*FRAME_MS,thinTreeline(i),PW,PH);if(s.stableAt(i*FRAME_MS)===true)vouched=true;}
  assert.equal(vouched,false,'a 1 px/frame pan of a low-gradient view read as still at some point');
  // Mutation that reddens this: ANCHOR_CELLS = 1e9 together with GRADIENT_FLOOR = 0.
});
test('A frame with no gradient to see a shift by is unknown, never still, whatever its variance',()=>{
  // A high-contrast but gradient-free frame: two flat halves. Variance is large,
  // gradient is one edge. Mutation: GRADIENT_FLOOR = 0 makes this true.
  ...assert stableAt === null after 700 ms of identical frames...
});
test('A held treeline with per-pixel sensor noise still reads still',()=>{
  // deterministic noise: px[i] += ((i*7919+frame*104729)%7)-3, clamped
  // Mutation: STILL_CELLS = 0 makes this false.
});
```

Keep 'One pixel of pan is under the frame-to-frame limit' and 'A textured view panning one pixel a frame is motion' green with the new constants, and add to each the comment naming the mutation that reddens it (issue #42 item 4 asks for exactly these two: tightening the per-frame bound reddens the first, `ANCHOR_CELLS = 1e9` reddens the second).

- [ ] **Step 3: Run the file; the new cases fail.** `node --import tsx src/next/hubs/sky/sheets/__tests__/photosphereStability.test.ts`
- [ ] **Step 4: Implement** `gradient()`, the three constants with measured comments (replace the constant comments' numbers with the ones you measured; issue #42 item 1 was already applied, keep those figures if they still hold), `observe()` using `gradient(grid) < GRADIENT_FLOOR` for the untextured branch and the two `_CELLS * G` bounds where `G` is the CURRENT frame's gradient (state in a comment why the current frame's, not the anchor's: the current frame is the one whose shift is being judged, and the two agree on a still scene).
- [ ] **Step 5: Run the file and every mutation named in the comments; each reddens exactly its case. Then** `node node_modules/typescript/bin/tsc -b --pretty false` **and** `node --import tsx src/next/hubs/sky/sheets/__tests__/photosphereStillnessDom.test.tsx` **and** `photosphereReplay.test.ts` (the replay's 21 cases replay recorded frames through this module: if a recorded case's stillness verdicts change, say which and by how much in the report; the sim cases' textures are hash lattices with high gradient, so the expectation is no change).
- [ ] **Step 6: Report** with the measurement table, the constants chosen and why, the mutation table, and the replay before/after summary.

### Task 2: #41 A featureless view during a short silence holds the last ready state and says why

**Files:**
- Modify: `ui/src/next/hubs/sky/sheets/photosphere.ts` (`compassReady`, `tiltReady`, `vouched`, `captureCue`)
- Modify: `ui/src/next/hubs/sky/sheets/photosphereStability.ts` (one new getter, see Interfaces)
- Test: `ui/src/next/hubs/sky/sheets/__tests__/photosphereStillnessDom.test.tsx`

**Interfaces:**
- Consumes: Task 1's `GRADIENT_FLOOR` semantics: `stableAt()` returns `null` for a frame that cannot witness.
- Produces: `VisualStability.witness(now): 'still' | 'moving' | 'featureless' | 'stale'` (a named reason beside the tri-state; `stableAt` unchanged), and in the driver a new private `viewFeatureless` fact read by the cue.

Rule (issue #41): at the compass gate, `unknown-because-featureless` is not `unknown-because-stale`. While the source is healthy and the newest frame is FRESH (within `STALE_FRAME_MS`) but featureless, a reading that was ready when the view last could be judged stays ready; stopped video (stale frame) or a moving view still read as lost within 2 s. Capture itself is unchanged (a featureless view still cannot satisfy the relaxed capture rule; the strict rule needs a sample within 250 ms), so this changes what the dome and the cue say, not what is captured. The cue for a still phone on a featureless view says the sky here has nothing to track and invites the user to bring some terrain or a building edge into the view; it never asks the user to move the phone to register.

- [ ] **Step 1: Write the failing DOM cases** in `photosphereStillnessDom.test.tsx`, using the harness's `blind`/scene controls (read the harness header first; it can present a flat scene by setting the drawn luminance to a constant):
  - 'A silent hold on featureless sky keeps the compass ready and says the view has nothing to track (issue #41)': approach and hold on the treeline scene until ready; switch the scene to flat (variance 0, gradient 0) with frames still flowing; advance 3 s with no orientation event; assert `compassReady === true`, `aimTarget !== null`, and `captureCue` contains 'nothing to track' and does not contain 'move the phone'. Mutation: reverting the gate to `vouched(at)` alone makes `compassReady` false at 3 s.
  - 'Stopped video during silence still reads the compass as lost': same start, then `stalled=true` (no frames) for 3 s: `compassReady === false`. This pins that the featureless branch does not swallow the stale branch.
  - 'A moving featureless view does not hold the compass': flat scene, then a genuine sensor-silent pan is not observable by the video, so this case is about the SENSOR: deliver a reading that moves 10 degrees, then silence on a flat scene: `compassReady` false after 2 s because the last reading's continuity was broken by the reading itself. (If the harness cannot express this, say so in the report and drop the case rather than write one that cannot fail.)
- [ ] **Step 2: Run the file; the new cases fail.**
- [ ] **Step 3: Implement.** In `photosphere.ts`: keep `vouched(at)` as is for capture; add `readingStands(at)` = `vouched(at) || (this.stability.witness(now)==='featureless' && this.lastVouchedAt !== null && this.lastVouchedAt >= at)`, where `lastVouchedAt` is updated to `now` whenever `vouched(at)` is true for the reading in question (one field per reading kind: heading and tilt). `compassReady`/`tiltReady` use `readingStands`. Cue: before the 'Waiting for the compass' line, if `this.cameraBasis` is non-null and `this.stability.witness(now)==='featureless'` and no sample within 250 ms, return the featureless sentence (exact copy in the report; it must name the action: include terrain or a building edge in the view). Explain in comments why capture stays strict.
- [ ] **Step 4: Run** the file, the mutation, tsc, and `photosphereCaptureDom.test.tsx`.
- [ ] **Step 5: Report** including the exact cue sentence.

### Task 3: #42 items 3 and 4: the listening gate on browsers without DeviceOrientationEvent, and the mutation record

**Files:**
- Modify: `ui/src/next/hubs/sky/sheets/photosphere.ts` (`sourceHealthy`, `start()`)
- Test: `ui/src/next/hubs/sky/sheets/__tests__/photosphereCaptureDom.test.tsx`

Ruling (controller): a browser with no `DeviceOrientationEvent` constructor has no pose stream to be healthy or unhealthy; the manual overhead press must work there as it did before 730a59b9. `sourceHealthy` becomes `(this.listening || !this.orientationSupported) && !this.trackEnded && visible`, where `orientationSupported` is recorded in `start()` from `"DeviceOrientationEvent" in window`. Automatic capture still needs a reading (`hasOrientation`), so nothing but the manual press changes. Item 4 is done in Task 1 (the two mutation comments); this task verifies they are present and adds the same comment to the case in `photosphereStillnessDom.test.tsx` that the #42 comment describes (the refuted-outlier case), naming its mutation ("remove the refutation branch").

- [ ] **Step 1: Failing test:** 'A browser with no DeviceOrientationEvent can still capture the overhead by hand (issue #42 item 3)': delete `w.DeviceOrientationEvent` before `sweep.start`, drive frames, call `captureOverhead()` with the video showing an image, assert `true` and `overheadCaptured`. Mutation: `sourceHealthy` without the `orientationSupported` term.
- [ ] **Step 2: Run; fails. Step 3: Implement. Step 4: Run file, mutation, tsc. Step 5: Report.**

### Task 4: #46 Media-gate refusals are counted, reported and, after a run of them, said in the cue

**Files:**
- Modify: `ui/src/next/hubs/sky/sheets/photosphere.ts` (`newMediaFrame`, `alignmentReport`, `captureCue`, `stop`)
- Test: `ui/src/next/hubs/sky/sheets/__tests__/photosphereStillnessDom.test.tsx`

**Interfaces:**
- Produces: `alignmentReport()` envelope gains `mediaGateRefusals: number` beside `stillnessReadFailures`; a new `CaptureOutcome` is NOT added (the gate refuses before `grabFrame` records; the timer path's `stale-image` record already names the capture consequence). Constant `MEDIA_GATE_BLIND_AFTER = 6` consecutive refusals (2.1 s of 350 ms ticks: one interval past the 1.5 s acceptance budget, so a healthy camera that merely stuttered never reaches it; say this in the comment).

- [ ] **Step 1: Failing cases:** 'A frozen fallback preview is counted in the alignment report (issue #46)': paused element, 3.5 s of ticks: `JSON.parse(sweep.alignmentReport()).mediaGateRefusals >= 6` and `stillnessReadFailures === 0`. 'After six refused ticks the cue names the camera image, not the compass': `captureCue` contains 'camera image' and 'open the camera again', and does not contain 'compass'. Recovery: after frames flow again for one tick the counter resets and the cue is the normal one. Mutations: counter never incremented; threshold 1e9.
- [ ] **Step 2..5 as in Task 3.**

### Task 5: #48 The interval fallback's vouching margin is the resolution of its witness

**Files:**
- Modify: `ui/src/next/hubs/sky/sheets/photospherePose.ts` (`viewVouchesFor` gains a `slopMs` parameter, default `CONTINUITY_SLOP_MS`)
- Modify: `ui/src/next/hubs/sky/sheets/photosphere.ts` (the fallback path passes `Math.max(CONTINUITY_SLOP_MS, GRAB_INTERVAL_MS)` where `GRAB_INTERVAL_MS = 350` replaces the two literal 350s; the rVFC path passes the default; the comment above the fallback observation states the limit and the trade)
- Test: `ui/src/next/hubs/sky/sheets/__tests__/photosphereStillnessDom.test.tsx` (harness: the interval tick models a pipeline delay by presenting the scene as it was `lag` ms before the tick, which the harness CAN do because `shift` is the harness's own state; replace the `(_lagMs=lag)=>` stub and its comment)

Arithmetic to pin (issue #48): with the read-instant stamp, the last observation showing motion lands in `(M + L - I, M + L]`; vouching needs `lastBreak.from <= R + slop`. With `slop = I = 350` every `L <= 350` is vouched on the fallback; `L` between 350 and 700 is intermittent; above 700 never. State those three bands in the comment (they replace 150/500).

- [ ] **Step 1: Failing cases:** 'The interval fallback still captures behind a 300 ms camera pipeline delay (issue #48)': `approachAndHold(false, 2, 300)`, hold 3 s: `frameCount === 1`. Mutation: the fallback passing the default slop makes it 0. And the converse pin: 'The fallback does not vouch across a delay longer than one interval': `lag = 800`: `frameCount === 0` (this pins the widened margin's size; mutation: slop 1e9 captures).
- [ ] **Step 2..5 as in Task 3;** also run `photosphereCaptureDom.test.tsx` and `photosphereReplay.test.ts` (the replay uses the rVFC path; expect no change and say so).

### Task 6: #49 Four residual minors

**Files:**
- Modify: `ui/src/next/hubs/sky/sheets/photosphereStability.ts` (item 1), `photosphere.ts` (item 2 comment only)
- Test: `photosphereStability.test.ts` (item 1), `photosphereStillnessDom.test.tsx` (items 3, 4)

- Item 1: after an untextured frame, the next textured pair must NOT open the run at the untextured frame's time. In `observe`, treat a previous frame that was untextured like a gap: `broken(at, at)` and return, so the run opens on the next textured pair. Test: untextured frame at 0, textured at 100 and 200 and 600: `stableAt(600)` is false under the fix (run opened at 100, settle needs 500 from 100 = 600, so use 550 to be strictly inside) and true without it. Compute the exact instants in the report before asserting.
- Item 2: `captureOverhead()` calls `grabFrame(true)`, and the `delivered` term excludes `manualOverhead`, so a manual press never observes stillness on any path since review 17 P1. Verify by reading, state it in a comment where item 2's concern lived, and in the report; no code.
- Item 3: four `newMediaFrame` conditions with no case: `ended`, `readyState < 2`, `track.readyState !== 'live'`, `track.muted`. One DOM case each on the fallback path: set the flag, 3 s of ticks with the media clock advancing, `frameCount === 0`; clear it, frames flow, capture happens. Each case's mutation is deleting its condition.
- Item 4: DOM case 8's first assertion holds by freshness alone; move its instant past `SENSOR_SILENCE_MS` (2 s) so only the video can satisfy it, and confirm it still passes and that reverting the continuity rule (make `viewVouchesFor` return false) reddens it.

### Task 7: #50 The test runner stubs `.css` imports for every file

**Files:**
- Create: `ui/test-css-stub.mjs` (a `node:module` `registerHooks` load hook answering any `.css` specifier with an empty module)
- Modify: `ui/run-tests.mjs` (the child's `execFile` args gain `"--import", <file URL of the stub>` before `"--import", "tsx"`)
- Test: `ui/src/__tests__/runTestsCss.test.ts` (new): spawn `run-tests.mjs`'s `runOne` (it is exported) on a temporary test file that imports a temporary `.css` file and asserts a value; expect green. Mutation: removing the `--import` reddens it with `ERR_UNKNOWN_FILE_EXTENSION`.

Do not remove the per-file stubs 53 files already carry; they are harmless and the convention stays for module stubs. The R7 rule tests (`r7Css.test.ts`) are unaffected: they read source text. After this task `npm test` (from `ui/`) must report the five formerly-crashing files as scored; report their tallies. One of them, `r7Disabled.test.ts`, fails on the other session's uncommitted `horizon.tsx:512` `disabled` attribute; that is not ours and stays red (say so).

### Task 8: #51 The grant test restores `getUserMedia`

**Files:**
- Test: `ui/src/next/hubs/sky/sheets/__tests__/photosphereCaptureDom.test.tsx`

Wrap the body of 'A grant arriving after cancellation is released' so the original mock is restored in a `finally`, and remove the re-installation block that follows it (the comment there explains the leak; keep one sentence of it on the `finally`). Pin: add a trivial case after it, 'The mocks are the ordinary ones again', that calls `sweep.start` and resolves within the harness's usual wait; mutation: delete the `finally`, the case hangs (record that the hang is the failure mode, exit 13, and keep the case last so a future leak is caught by it).

### Task 9: #52 After repeated overlap refusals with no lens calibration, the cue names the lens

**Files:**
- Modify: `ui/src/next/hubs/sky/sheets/photosphere.ts` (`captureCue`, a consecutive-`overlap-wait` counter reset by any other outcome, `LENS_DOUBT_AFTER = 8`)
- Test: `ui/src/next/hubs/sky/sheets/__tests__/photosphereCaptureDom.test.tsx`, and a replay assertion in `photosphereReplay.test.ts` on `chartyard-arc075-70`

Cue when `consecutiveOverlapWaits >= LENS_DOUBT_AFTER && !this.hasLensCalibration`: it says the camera's view angle may be set wrong for this lens and names the camera view angle control; it does not repeat the aim instruction. Once `hasLensCalibration` is true the ordinary overlap cue returns. Eight refusals at the 350 ms cadence is 2.8 s; the arc075-70 replay logged 29 consecutive ones, arc075-60 at most 2 (measured from the baseline's captures.jsonl; confirm in the report).

- [ ] Failing cases: unit-level in the capture DOM file by forcing `overlap-wait` outcomes (read how the file drives registration; if it cannot force a conflict, drive it through the replay harness instead: replay arc075-70 and assert the final `captureCue` names the view angle; replay arc075-60 and assert it does not). Mutation: threshold 1e9.
- [ ] Also record the larger follow-up (a scale term in `registerFrame`) in the report; it is out of scope.

### Task 10: #53 Obstacle widths the product can resolve; the sphere disc deferred

**Files:**
- Modify: `tools/photosphere_sim/sim/score.py` (`_score_obstacles`), `tools/photosphere_sim/CONTRACT.md`, `tools/photosphere_sim/scenes/chartyard.json` (comments only if the format allows; otherwise leave)
- Test: `tools/photosphere_sim/tests/test_score.py` (or the file that tests `_score_obstacles`)

Ruling (controller, after reading the product): the scanner reports the horizon in `bins` azimuth columns (30, so 12 degrees each; `PhotosphereSweep` constructor default) and the planner consumes a wrap-interpolated profile (`server/astrodeck/sequence/schedule.py`, `interp_wrap`/`effective_floor`). An obstacle narrower than one product bin cannot be represented by the product at all, whatever the scanner does, so a miss rule keyed on a declared width below a bin grades the fixture. The rule becomes: an obstacle is missed when a stretch at least `max(declared min_width_deg, 360 / product_bins)` wide is under-reported by more than `MISSED_OBSTRUCTION_DEG`; `product_bins` is read from the case's `result/horizon.json` (the number of points) and recorded in `scores.json` beside each obstacle row as `resolvable_width_deg`. Rows for obstacles narrower than that carry `resolvable: false` and their deficits stay informational. The contract says so. Part 2 of the issue (a sphere-hosted disc) is stage B scene work: leave a comment on the issue in the report text for the controller to post, and do not change the chart yard scene (its manifest hash is the baseline's identity).

- [ ] Failing unit test: a synthetic obstacle 2 degrees wide under-reported by 5 degrees with 30 product bins is NOT a miss and its row says `resolvable: false`; the same obstacle with declared width 15 IS a miss. Mutation: drop the `max()`.
- [ ] Run the suite and `score` on the three cached cases; report the `no_missed_obstructions` change on `chartyard-still-60`.

### Task 11: #57 Every direction above the horizon has a target, and the driver and the aim dot choose the same one

**Files:**
- Modify: `ui/src/next/hubs/sky/sheets/photosphereGeometry.ts` (export `AIM_CONE_DEG = 11` and `targetCell(forward: V3): DomeCell | null`, the NEAREST cell whose centre is within the cone, the pole included at the same cone)
- Modify: `ui/src/next/hubs/sky/sheets/photosphere.ts` (`aimTarget` and both `DOME_CELLS.find` sites in `grabFrame` use `targetCell`; the cue's 'Bring a blue dot into the centre ring' branch stays for below-horizon directions)
- Test: `ui/src/next/hubs/sky/sheets/__tests__/photosphereGeometry.test.ts` (exists; add), `photosphereCaptureDom.test.tsx`

Measured (controller, 2026-09-18, from the committed `makeDome`): 91 cells; ring altitudes 90, 74.1, 63.4, 58.3, 46.4, 42.4 and lower; the first ring is five cells at azimuths 0, 72, 144, 216, 288 and altitude 74.1. A hold at azimuth 180, altitude 85 is exactly 5.00 degrees from the pole (the pole's 5 degree cone, at equality) and about 12 degrees from the nearest ring-1 cell, so it has no target. On a 0.5 degree azimuth-altitude grid (not area weighted, so high altitudes count more), the fraction of directions with no target is 23.5 percent with the current cones (8, pole 5), 20.1 percent with 8 everywhere, 5.8 percent with 9, 0.4 percent with 10; the largest distance from any direction to its nearest cell centre is 10.75 degrees (the pole pentagon's corners), so a cone of 11 degrees leaves no direction without a target. Image fit: a cell's far corner is at most 10.75 degrees from its centre, so at 11 degrees off-centre it lies within 21.75 degrees of the image centre; a 60 degree short axis has 30 degrees of half-width, and the tilt-only overhead path is unchanged (`cameraBasis` still needs altitude 85 for a tilt-only basis). The 35 degree lens minimum (17.5 degrees half-width) already spilled cells with the 8 degree cone; state that in the comment.

- [ ] Failing tests: geometry: 'Every direction above the horizon has a target within the aim cone' (a 1 degree grid, all altitudes 0..90, `targetCell` non-null; mutation: cone 8 reddens it at about a quarter of the grid); 'The target is the nearest cell, not the first in the list' (a direction between two cells, both inside the cone: the nearer wins; mutation: `find`-style first match). Capture DOM: 'A hold at altitude 85 captures the zenith cell' (drive a basis at az 180 alt 85; `frameCount === 1` and the accepted record's `cell` is the pole; mutation: pole cone 5).
- [ ] Run the two files, tsc, `photosphereReplay.test.ts`, and the replay of `chartyard-still-60` (from `ui`: `node --import tsx src/next/hubs/sky/sheets/__sim__/replay.ts ../tools/photosphere_sim/cache/cases/chartyard-still-60`, then `python -m tools.photosphere_sim score ...` from the repo root); report `every_hold_captured`, `holds_satisfied` and the outcome histogram before and after. Do not edit the baseline doc; Task 14 does.

### Task 12: #58 The horizon tracer finds the sky boundary by its transition, not by a luminance threshold

**Files:**
- Modify: `ui/src/next/hubs/sky/sheets/photosphere.ts` (`traceSkyCoverage`; `columnFromImageData` may gain a second output if colour is used, see below)
- Test: `ui/src/next/hubs/sky/sheets/__tests__/photosphereHorizon.test.ts` (find the existing tracer tests with `grep -rln traceSkyCoverage ui/src`; add there)
- Scoring: `tools/photosphere_sim` cached cases `chartyard-still-60`, `chartyard-arc075-60`, `chartyard-arc075-70`

The defect (issue #58): the tracer takes the first row below 0.7 of the sky level; a wall brighter than that (chart yard wall luminance 104 against a sky level 122, threshold 85) is reported as open sky over 12 degrees of a 25 degree wall. Real buildings are routinely brighter than overcast sky; this is the spec's safety-relevant false-open.

Design constraints, not the algorithm (the implementer designs, the simulator grades):
- The column is per-row luminance from the top of the frame down (`columnFromImageData`, 24 rows per frame, folded into 101-row columns at 1 degree per row by `SkyPanorama.columns`; read that code first and record in the report what one column row is).
- A boundary is a TRANSITION from the sky's statistics to something else, in either direction, that persists downward (a passing dark disc in the sky must not become the horizon; the chart yard has coloured discs in the sky at altitudes 5 to 85, and the baseline records a dark-disc artefact near the zenith as the chart's problem, not the tracer's). Use the sign-agnostic contrast against the sky level AND the local row-to-row structure; if colour is needed (a wall the same luminance as the sky), add a per-row blueness sample beside luminance and say what it costs.
- The `uncertainBins` rule (fewer than 101 rows, any non-finite value in the top 91, a sky level under 40) is unchanged.
- Acceptance is the simulator: on `chartyard-still-60`, `no_missed_obstructions` passes and the wall bins (azimuth 72 to 84) report within 1 degree of the truth profile (24.5 to 25.5), with `horizon_p95` not worse than the baseline's number for that case; on both arc075 cases no gate that passes today fails. Record the three cases' horizon scores before and after in the report.
- Unit tests: a synthetic column with a bright wall (above 0.7 of sky) reports the wall's altitude (mutation: the old threshold rule); a synthetic column with a dark disc 10 rows tall in the sky and open sky below it reports 0 (mutation: taking the first transition without the persistence test); the existing tracer cases stay green.

### Task 13: #59 The overlay metric grades the whole attitude, and a roll corruption proves it

**Files:**
- Modify: `tools/photosphere_sim/sim/score.py` (`_score_overlay`: error = max of the angles between measured and true `forward`, `right`, `up`; the three per-axis maxima reported beside it), `tools/photosphere_sim/sim/corrupt.py` (new `roll` corruption: rotate every event's `right` and `up` about its own `forward` by `deg`, default 3.0, forward untouched), `tools/photosphere_sim/CONTRACT.md` (the overlay bullet says what is measured; the corruption table gains `roll`)
- Test: `tools/photosphere_sim/tests/test_score.py` and `test_corrupt.py`: the roll corruption on a cached case moves `overlay.settled.max_deg` to at least the roll angle (mutation: the forward-only metric reports 0 change), and `frames_over_gate` counts it; the `yaw` corruption's existing expectations are unchanged (record the numbers).

`events.jsonl` lines carry `basis.right/up/forward` already (`score.py:651` reads all three for another purpose; confirm). Gate names and thresholds are unchanged; say in the report whether any cached case's overlay gate changes (the replay never fits roll, so a change here would mean the recorded route rolls, which `truth` says it does not on the still route and may on arc075 through the swing-twist; report the number either way).

### Task 14: Baseline refresh and the record of the pass

**Files:**
- Modify: `docs/ui-rebuild/18-photosphere-simulator-baseline.md` (re-run all three cases from the clean cache at the pass's HEAD; replace the tables; section 4.6 and the coarse-binning paragraph say what changed and which task changed it; keep the previous numbers in a "before this pass" column)
- Modify: `docs/ui-rebuild/14-photosphere-production-handoff.md` (the acceptance rows this pass touched: a sentence each, no rewrite)

- [ ] Run, from the repo root: `python -m tools.photosphere_sim make-case` is NOT needed (cases are cached and hashed); run `score` on each case after replaying each with the current app (`app_commit` in `summary.json` must be the pass's HEAD, `-dirty` is not acceptable for the baseline: the controller commits before this task runs).
- [ ] Every gate that changed from FAIL to PASS or the reverse is listed with the task that changed it. The document's determinism claim is re-verified (two replays byte-identical).
- [ ] Report: the final gate table for the three cases and the ideal.
