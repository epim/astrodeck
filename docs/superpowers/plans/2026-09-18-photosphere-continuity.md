# Photosphere continuity (review 15 fixes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three findings of `docs/ui-rebuild/15-photosphere-stillness-review.md`: a steady view can no longer certify a direction the phone held BEFORE it moved or before an unobserved interval; the interval fallback observes only frames the camera actually delivered; and a suspect final reading is refuted or recovered from with video evidence instead of being rejected for the rest of the hold.

**Architecture:** `VisualStability` stops answering only "is the view still now" and starts answering "since when has this view been continuous": it records the start of the current still run and the last interval in which the view moved, could not be judged, or was not observed. `CameraPoseHistory.forFrame()` vouches for a reading only when that continuity reaches back to the reading, refutes a last-event jump when the still run already covered it, and otherwise treats the jump as a movement. `PhotosphereSweep` feeds capture-time frame times to the witness, gates the timer fallback on a newly delivered media frame, and its compass/tilt readiness uses the same continuity rule.

**Tech Stack:** React/TypeScript/Vite UI. Tests are hand-rolled: `node --import tsx <file>` from `ui`, each file exports `result`. No test framework to add. `npx tsc -b` from `ui` must stay green (`noUnusedLocals`).

**Spec:** `docs/ui-rebuild/15-photosphere-stillness-review.md` (the three findings, verbatim required changes); background `docs/ui-rebuild/14-photosphere-production-handoff.md` sections 2.1, 4.2 and 4.4. The previous plan for this code is `docs/superpowers/plans/2026-09-18-photosphere-stillness.md`; its ledger with 24 rulings is `.superpowers/sdd/2026-09-18-photosphere-stillness/progress.md`.

## Global Constraints

- Work only in `C:\Users\bear\astro` on branch `feat/photosphere-production` (HEAD `730a59b9` at plan time). The checkout holds much unrelated modified and untracked work, notably another agent's uncommitted edits to `horizon.tsx`, `horizonStrip.ts` and `horizonDom.test.tsx`: never touch those, never broadly stage, reset, clean or revert. Commit with explicit pathspecs only (`git commit -F msg -- <paths>`). Implementers do not commit; the controller commits.
- No telescope motion, no deployment, no change to rig authentication or safety configuration. Nothing here touches `astrotown`.
- Never fabricate sensor events and never merely extend a stale-pose timeout: a reading is trusted in silence only with explicit, per-reading evidence (spec 15 P1 and doc 14 section 2.1).
- Preserve `forFrame(now)` and `forFrame(now, captureTime)` behaviour exactly when no evidence argument is passed. The first five tests in `photospherePose.test.ts` stay unmodified; its cases 6 and 7 change only the evidence object shape.
- Baseline to keep green after every task: `photospherePose`, `photosphereGeometry` 14, `photosphereRegistration` 5, `photosphereVertical` 8, `photosphereCaptureDom` 16, `photosphereStillness`, `photosphereStability`, `photosphereStillnessDom`; plus `npx tsc -b` from `ui`. The full suite `npm.cmd test` from `ui` has three pre-existing failures (issue #36: `r7Css` x2 on `photosphere.css`, `r7Disabled` x1 on another agent's uncommitted `horizon.tsx`); anything beyond those three is a regression.
- No emojis in code, comments, docs or commit messages. The site's real latitude, longitude and label must never appear anywhere. Files are UTF-8 without BOM; write files with the Write/Edit tools, not PowerShell `Set-Content` (issue #40).
- Times are milliseconds on the performance clock throughout; the tests stub `performance.now` and `Date.now` together.
- Report file and brief file paths are given in the dispatch; write the full report to the report file.

---

### Task 1: Continuity in the witness and the pose history

**Files:**
- Modify: `ui/src/next/hubs/sky/sheets/photosphereStability.ts` (class `VisualStability`, lines 104-140; the `ANCHOR_DIFF_LIMIT` comment, lines 23-35; the `TEXTURE_FLOOR` comment, lines 36-48)
- Modify: `ui/src/next/hubs/sky/sheets/photospherePose.ts` (`PoseEvidence` line 40, the comments and constants at lines 42-63, the evidence branch of `forFrame` at lines 87-95)
- Modify: `ui/src/next/hubs/sky/sheets/__tests__/photosphereStability.test.ts` (append six cases)
- Rewrite: `ui/src/next/hubs/sky/sheets/__tests__/photosphereStillness.test.ts` (full content below)
- Modify: `ui/src/next/hubs/sky/sheets/__tests__/photospherePose.test.ts` (cases 6 and 7 only: the evidence object shape at lines 51, 52 and 74)

**Interfaces:**
- Consumes: `VisualStability.observe(at, luma, width, height)`, `stableAt(now)`, `SETTLE_MS`, `STALE_FRAME_MS`, `STILL_DIFF_LIMIT`, `ANCHOR_DIFF_LIMIT`, `TEXTURE_FLOOR`; `CameraPoseHistory`, `TimedPose`, `poseSeparation`, `lookBasis`.
- Produces (Task 2 relies on these exact names):
  - in `photosphereStability.ts`: `export interface ViewContinuity { stillSince: number; lastBreak: { from: number; to: number } | null }` and `VisualStability.continuity(now: number): ViewContinuity | null`.
  - in `photospherePose.ts`: `export interface PoseEvidence { view?: ViewContinuity | null; sourceHealthy?: boolean }` (the `visuallyStable` field is REMOVED), `export const CONTINUITY_SLOP_MS = 150`, and `export function viewVouchesFor(readingAt: number, view: ViewContinuity | null | undefined): boolean`.

**Semantics of `ViewContinuity`** (write these into the doc comments; they are the contract):
- `stillSince`: the frame time at which the current uninterrupted still run began. The view is KNOWN unchanged from `stillSince` to the newest frame. Only present (continuity non-null) when `stableAt(now) === true`, so the run has already lasted `SETTLE_MS`.
- `lastBreak`: the most recent interval `[from, to]`, in frame time, in which the view moved, could not be judged, or was not observed; `null` if no such interval has been seen since the object was cleared. Rules for what `observe(at, ...)` records, in this order:
  1. the frame has no texture (`variance < TEXTURE_FLOOR`): break `{from: at, to: at}`;
  2. no previous frame, or `at - previousAt > STALE_FRAME_MS`: break `{from: at, to: at}` (an unobserved interval; nothing before its end can be vouched for, including the interval before the first frame ever seen);
  3. the consecutive-frame difference exceeds `STILL_DIFF_LIMIT`: break `{from: previousAt, to: at}` (motion seen between these two frames; it may have begun anywhere inside the pair, which is why `from` is the earlier frame);
  4. otherwise the pair is still: if no run is open, open it with `stillSince = previousAt` and `anchor = previous`; if a run is open and the anchor difference exceeds `ANCHOR_DIFF_LIMIT`: break `{from: at, to: at}` (a drift caught against the anchor happened at an unknown moment of the run, so nothing before now is vouched for).
  Every break closes the run (`stillSince = null`, `anchor = null`) and stores `lastBreak`. `clear()` resets `lastBreak` to `null` too. `stableAt` is unchanged, except that rule 1 now also closes the run (before this change an untextured frame left `stillSince` in place).

**Semantics of the evidence path in `forFrame(now, captureTime?, evidence?)`** (replace the current branch at lines 87-95; the strict path below it is untouched):
```ts
if(latest && evidence?.sourceHealthy===true && evidence.view){
  const view=evidence.view;
  let pose=latest;
  const previous=this.samples.at(-2);
  // A jump between the last two readings, closer together than any real slew:
  // if the video's still run already covered that window, the phone did not
  // move and the newer reading is a bad one - recover with the reading before
  // it. If the run does not cover the window, the video cannot say the phone
  // held still across it, so the jump is treated as a movement.
  if(previous && latest.at-previous.at<JITTER_GAP_MS
    && poseSeparation(latest.basis,previous.basis)>JITTER_SEPARATION_DEG
    && view.stillSince<=previous.at)pose=previous;
  if(viewVouchesFor(pose.at,view)){
    const reference=captureTime===undefined?now:captureTime;
    const silence=reference-pose.at;
    if((captureTime===undefined||Number.isFinite(captureTime)) && reference<=now
      && silence>=SILENT_SETTLE_MS)return pose.basis;
  }
}
```
and
```ts
/** Frame times and sensor times are on the same clock but not aligned to the
 *  millisecond: a frame without a capture time is stamped when the callback
 *  ran, some tens of ms after the camera saw the scene, and an orientation
 *  event carries its own latency. A break that BEGAN within this margin after
 *  a reading is the tail of the approach that produced the reading, not a
 *  movement after it. The cost: a movement starting inside this margin, after
 *  the last reading, with a sensor that has already died, is not caught. */
export const CONTINUITY_SLOP_MS = 150;

/** Does the video vouch that the view has not changed since a reading taken
 *  at `readingAt`? True only with a settled continuity whose last break began
 *  no later than the reading (plus the alignment margin). A break recorded at
 *  a single instant - an unobserved gap, an unjudgeable frame, a drift caught
 *  late - has `from === to` at its END, so a reading older than that instant
 *  is never vouched for: nothing watched the view between the two. */
export function viewVouchesFor(readingAt:number,view:ViewContinuity|null|undefined):boolean {
  return !!view && (view.lastBreak===null || view.lastBreak.from<=readingAt+CONTINUITY_SLOP_MS);
}
```
Rewrite the comment block above `SILENT_SETTLE_MS` (lines 42-55) so it states this contract (continuity reaching back to the reading, not "the view has not drifted from the anchor"), and the `JITTER_*` comment (lines 58-62) so it states the refute-or-treat-as-movement rule and its limit: the video resolves motion no finer than its own frame interval, so a bad reading arriving inside the last moving pair of an approach is accepted as a movement; registration's overlap check and the next reading are the guards there. Import the `ViewContinuity` type from `./photosphereStability`.

Also correct the measured numbers in the `ANCHOR_DIFF_LIMIT` comment in `photosphereStability.ts` (issue #42 item 1): `0.0036 per frame` becomes `0.0142 per frame`, and change the phrase "a fifth of STILL_DIFF_LIMIT" to "under STILL_DIFF_LIMIT". In the `TEXTURE_FLOOR` comment, the treeline's `0.48` becomes `0.42`. Do not change any constant's value.

- [ ] **Step 1: Append the continuity cases to `photosphereStability.test.ts`**

Append before the final `console.log` line, using the file's existing `test`, `scene(shift, gain)` helper and `W`, `H`:

```ts
test('Continuity names the start of the still run and the last break the video saw',()=>{
  const s=new VisualStability();
  // Moving through 300 ms, then the same view held.
  for(let i=0;i<=3;i++)s.observe(i*100,scene(i),W,H);
  for(let t=400;t<=1200;t+=100)s.observe(t,scene(3),W,H);
  const c=s.continuity(1200);
  assert.ok(c,'a settled view has continuity');
  assert.equal(c!.stillSince,300,'the still run begins on the first frame of the first still pair');
  assert.deepEqual(c!.lastBreak,{from:200,to:300},'the last break is the last moving pair');
});

test('Continuity is null before the settle and null again the moment the view moves',()=>{
  const s=new VisualStability();
  for(let t=0;t<=300;t+=100)s.observe(t,scene(0),W,H);
  assert.equal(s.continuity(300),null,'not settled yet');
  for(let t=400;t<=600;t+=100)s.observe(t,scene(0),W,H);
  assert.ok(s.continuity(600),'settled');
  s.observe(700,scene(5),W,H);
  assert.equal(s.continuity(700),null,'a moved view has no continuity');
  for(let t=800;t<=1400;t+=100)s.observe(t,scene(5),W,H);
  assert.deepEqual(s.continuity(1400)!.lastBreak,{from:600,to:700});
});

test('An unobserved gap is a break at its end: nothing before the gap can be vouched for',()=>{
  const s=new VisualStability();
  for(let t=0;t<=500;t+=100)s.observe(t,scene(0),W,H);
  // Nothing for 1.5 s (over STALE_FRAME_MS), then the same view again.
  for(let t=2000;t<=2600;t+=100)s.observe(t,scene(0),W,H);
  const c=s.continuity(2600);
  assert.ok(c);
  assert.equal(c!.stillSince,2000);
  assert.deepEqual(c!.lastBreak,{from:2000,to:2000});
});

test('The first frame ever seen is a break at its own time',()=>{
  const s=new VisualStability();
  for(let t=1000;t<=1600;t+=100)s.observe(t,scene(0),W,H);
  assert.deepEqual(s.continuity(1600)!.lastBreak,{from:1000,to:1000});
});

test('A frame with no texture is a break, even between two identical textured frames',()=>{
  const s=new VisualStability();
  for(let t=0;t<=500;t+=100)s.observe(t,scene(0),W,H);
  s.observe(600,new Uint8Array(W*H).fill(120),W,H);
  for(let t=700;t<=1300;t+=100)s.observe(t,scene(0),W,H);
  const c=s.continuity(1300);
  assert.ok(c);
  assert.equal(c!.stillSince,700);
  assert.equal(c!.lastBreak!.to,700);
  assert.ok(c!.lastBreak!.from>=600,`the break began at ${c!.lastBreak!.from}, before the blank frame`);
});

test('clear() forgets the last break',()=>{
  const s=new VisualStability();
  for(let t=0;t<=300;t+=100)s.observe(t,scene(t/100),W,H);
  s.clear();
  for(let t=1000;t<=1600;t+=100)s.observe(t,scene(0),W,H);
  assert.deepEqual(s.continuity(1600)!.lastBreak,{from:1000,to:1000});
});
```

- [ ] **Step 2: Run it to verify it fails**

Run from `ui`: `node --import tsx src/next/hubs/sky/sheets/__tests__/photosphereStability.test.ts`
Expected: a TypeScript/runtime failure on `continuity` (not a function) for the six new cases; the existing cases pass.

- [ ] **Step 3: Implement `continuity` in `VisualStability`**

Add the interface, the `lastBreak` field, a private `break(from, to)` helper (name it `broken` if `break` collides with the keyword in your editor; it is a valid method name in TypeScript), apply the four rules in `observe` in the order given, reset in `clear()`, and add:
```ts
/** Since when has THIS view been continuous? Null until the run has settled
 *  (see stableAt), because an unsettled run vouches for nothing yet. */
continuity(now:number):ViewContinuity|null {
  if(this.stableAt(now)!==true||this.stillSince===null)return null;
  return {stillSince:this.stillSince,lastBreak:this.lastBreak};
}
```

- [ ] **Step 4: Run the stability tests**

Expected: every case passes, including the nine that were there before. If the existing "A view with no texture to judge by is unknown, never still" case changes outcome, read it: it must still pass because `stableAt` still returns `null` for an untextured frame.

- [ ] **Step 5: Rewrite `photosphereStillness.test.ts`**

Replace the file with this content:

```ts
import assert from 'node:assert/strict';
import { CameraPoseHistory, CONTINUITY_SLOP_MS, type PoseEvidence } from '../photospherePose';
import { lookBasis } from '../photosphereGeometry';

// Regression for docs/ui-rebuild/14-photosphere-production-handoff.md section
// 2.1 (a still phone deadlocks because Chromium sends no orientation event
// below 0.1 degree of change) and for the three findings of
// docs/ui-rebuild/15-photosphere-stillness-review.md:
//   P1  a new steady view must not certify a direction from before the phone
//       moved, or from before an interval nothing watched;
//   P2  a suspect last reading is refuted by the video or treated as a
//       movement, never rejected for the whole hold on a rate threshold alone.
//
// The evidence a caller hands forFrame is the video's CONTINUITY: since when
// the view has held still, and the last interval in which it moved, could
// not be judged, or was not observed. This file models what the browser
// does: a 10 degree approach over 0-500 ms with the camera watching, then
// silence.
let passed=0,failed=0;
function test(name:string,fn:()=>void){
  try{fn();passed++;console.log(`PASS ${name}`);}
  catch(e){failed++;console.log(`FAIL ${name}\n     ${(e as Error).message.split('\n')[0]}`);}
}

/** 10 degree approach, one event per 100 ms, then nothing at all. */
function approachThenStop(h:CameraPoseHistory){
  const poses=Array.from({length:6},(_,i)=>lookBasis(10-i*2,20));
  poses.forEach((basis,i)=>h.add({at:i*100,basis,screenAngle:0}));
  return poses[5];
}

/** What a camera at 10 frames per second saw of that approach: the pair
 *  straddling the last reading (450-550) still moving, everything after it
 *  still. This is the evidence a settled hold carries. */
const HELD:PoseEvidence={view:{stillSince:550,lastBreak:{from:450,to:550}},sourceHealthy:true};

test('A phone that stops moving becomes capturable, with no sensor wiggle required',()=>{
  const h=new CameraPoseHistory();
  const finalBasis=approachThenStop(h);
  for(let now=1100;now<=2000;now+=250){
    assert.equal(h.forFrame(now,undefined,HELD),finalBasis,`still and steady at ${now} ms was refused`);
  }
});

test('Silence without visual evidence keeps the strict rule: it is not trusted',()=>{
  const h=new CameraPoseHistory();
  approachThenStop(h);
  assert.equal(h.forFrame(1500),null);
});

test('Video that still shows motion blocks capture even when the sensor is quiet',()=>{
  const h=new CameraPoseHistory();
  approachThenStop(h);
  assert.equal(h.forFrame(1500,undefined,{view:null,sourceHealthy:true}),null);
});

test('An explicitly lost sensor refuses capture even with a fresh sample and steady video',()=>{
  const h=new CameraPoseHistory();
  approachThenStop(h);
  assert.equal(h.forFrame(1100,undefined,{view:HELD.view,sourceHealthy:false}),null);
});

test('A genuine gap DURING the approach is still movement, not a settle',()=>{
  const h=new CameraPoseHistory();
  h.add({at:0,basis:lookBasis(10,20),screenAngle:0});
  h.add({at:100,basis:lookBasis(8,20),screenAngle:0});
  h.add({at:1000,basis:lookBasis(0,20),screenAngle:0});
  assert.equal(h.forFrame(1100,undefined,{view:{stillSince:1050,lastBreak:{from:950,to:1050}},sourceHealthy:true}),null);
});

test('A phone on a tripod stays capturable for as long as the video vouches for it',()=>{
  const h=new CameraPoseHistory();
  const finalBasis=approachThenStop(h);
  assert.equal(h.forFrame(30000,undefined,HELD),finalBasis,'30 s of vouched-for stillness was refused');
  assert.equal(h.forFrame(300000,undefined,HELD),finalBasis,'5 min of vouched-for stillness was refused');
});

test('A capture time deep inside a long stillness resolves to the settled pose',()=>{
  const h=new CameraPoseHistory();
  const finalBasis=approachThenStop(h);
  assert.equal(h.forFrame(30000,29000,HELD),finalBasis);
});

test('P1: a phone that moves after its sensor went silent is never certified by the stillness that follows',()=>{
  const h=new CameraPoseHistory();
  approachThenStop(h);
  // The camera saw the view move 3 s into the hold, long after the last
  // reading, and then settle on something else. That settle vouches for the
  // NEW view; the old reading described a direction the phone has left.
  const moved:PoseEvidence={view:{stillSince:3600,lastBreak:{from:3500,to:3600}},sourceHealthy:true};
  assert.equal(h.forFrame(4500,undefined,moved),null,'a new steady view certified the old direction');
  assert.equal(h.forFrame(60000,undefined,moved),null,'and it never expires into acceptance');
});

test('P1: an interval nothing watched, after the reading, invalidates it',()=>{
  const h=new CameraPoseHistory();
  approachThenStop(h);
  // A gap in the video (backgrounded, stalled) ending at 2000: a break at a
  // single instant, its end. The reading at 500 predates it.
  const gap:PoseEvidence={view:{stillSince:2000,lastBreak:{from:2000,to:2000}},sourceHealthy:true};
  assert.equal(h.forFrame(3000,undefined,gap),null);
});

test('The tail of the approach straddling the last reading is not a movement after it',()=>{
  const h=new CameraPoseHistory();
  const finalBasis=approachThenStop(h);
  // The moving pair that contains the last reading began before it: allowed.
  assert.equal(h.forFrame(1500,undefined,{view:{stillSince:640,lastBreak:{from:480,to:640}},sourceHealthy:true}),finalBasis);
  // The margin is exact: a break beginning at reading + slop is the approach,
  // one millisecond later it is a movement the sensor did not report.
  const edge=500+CONTINUITY_SLOP_MS;
  assert.equal(h.forFrame(1500,undefined,{view:{stillSince:edge+100,lastBreak:{from:edge,to:edge+100}},sourceHealthy:true}),finalBasis);
  assert.equal(h.forFrame(1500,undefined,{view:{stillSince:edge+101,lastBreak:{from:edge+1,to:edge+101}},sourceHealthy:true}),null);
});

test('P2: a valid quick final movement the camera saw is the pose, for the whole hold',()=>{
  const h=new CameraPoseHistory();
  // Facing 0 degrees through 500 ms, then a real 10 degree turn inside 100 ms.
  for(let i=0;i<=5;i++)h.add({at:i*100,basis:lookBasis(0,20),screenAngle:0});
  const final=lookBasis(10,20);
  h.add({at:600,basis:final,screenAngle:0});
  // The video saw that turn: the pair 550-650 moved, and the run began at 650.
  const saw:PoseEvidence={view:{stillSince:650,lastBreak:{from:550,to:650}},sourceHealthy:true};
  for(const now of [1200,2000,10000,30000]){
    assert.equal(h.forFrame(now,undefined,saw),final,`a 100 deg/s final approach was rejected at ${now} ms`);
  }
});

test('P2: a magnetometer outlier as the last event is refuted by a still video, and the reading before it is used',()=>{
  const h=new CameraPoseHistory();
  const steady=Array.from({length:6},(_,i)=>({at:i*100,basis:lookBasis(0,20),screenAngle:0}));
  steady.forEach(p=>h.add(p));
  h.add({at:550,basis:lookBasis(20,20),screenAngle:0});
  // The camera has watched an unchanged view since long before any reading:
  // the phone did not turn 20 degrees at 550 ms. Recover, do not refuse.
  const stillAllAlong:PoseEvidence={view:{stillSince:-1000,lastBreak:null},sourceHealthy:true};
  assert.equal(h.forFrame(1500,undefined,stillAllAlong),steady[5].basis,'the outlier was not refuted, or refusal replaced recovery');
  assert.equal(h.forFrame(30000,undefined,stillAllAlong),steady[5].basis);
});

test('P2: a jump the video did not see held across is a movement, not an outlier',()=>{
  const h=new CameraPoseHistory();
  for(let i=0;i<=5;i++)h.add({at:i*100,basis:lookBasis(0,20),screenAngle:0});
  const final=lookBasis(20,20);
  h.add({at:550,basis:final,screenAngle:0});
  // The still run began at 600, after the jump: the video cannot say the phone
  // held still across 500-550, and the pair 500-600 moved.
  assert.equal(h.forFrame(1500,undefined,{view:{stillSince:600,lastBreak:{from:500,to:600}},sourceHealthy:true}),final);
});

test('The ordinary approach is untouched by the jump rule',()=>{
  const clean=new CameraPoseHistory();
  const final=approachThenStop(clean);
  assert.equal(clean.forFrame(1500,undefined,HELD),final);
});

console.log(`photosphereStillness.test: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};
if(failed)process.exitCode=1;
```

- [ ] **Step 6: Update cases 6 and 7 of `photospherePose.test.ts`**

Replace each `{visuallyStable:true,sourceHealthy:true}` at lines 51, 52 and 74 with `{view:{stillSince:550,lastBreak:{from:450,to:550}},sourceHealthy:true}`. Read case 7 (lines 60-79) first: its assertion `assert.ok(result===null||result===poses[1])` stays as it is. Do not touch cases 1-5.

- [ ] **Step 7: Run the two pose test files to verify they fail**

Run from `ui`: `node --import tsx src/next/hubs/sky/sheets/__tests__/photosphereStillness.test.ts` and `...photospherePose.test.ts`.
Expected: compile errors on `CONTINUITY_SLOP_MS` / `view` (the new shape does not exist yet).

- [ ] **Step 8: Implement the evidence path in `photospherePose.ts`**

Exactly the code and comments given under "Semantics of the evidence path" above. Remove `visuallyStable` from `PoseEvidence`; add the `ViewContinuity` import; keep `add()` and the strict path unchanged. `photosphere.ts` will not compile until Task 2 (it still builds `visuallyStable`); that is expected at this step and the reason `tsc -b` is run only in Task 2.

- [ ] **Step 9: Run the three unit test files**

Expected: `photosphereStillness` 14/14, `photospherePose` 7/7, `photosphereStability` 15/15 (9 existing + 6 new). Note `photosphere.ts` is not imported by any of these, so the pending Task 2 breakage does not reach them.

- [ ] **Step 10: Mutation check (record it in the report)**

Temporarily change `view.lastBreak.from<=readingAt+CONTINUITY_SLOP_MS` to `view.lastBreak.to<=readingAt+CONTINUITY_SLOP_MS` and confirm "The tail of the approach straddling the last reading" fails; revert. Temporarily change `view.stillSince<=previous.at` to `true` and confirm "a jump the video did not see held across" fails; revert. Temporarily drop the `pose=previous` recovery (leave `pose=latest`) and confirm the outlier case fails; revert. List the three results in the report with the exact failing case names.

- [ ] **Step 11: Report**

Write the report to the path in the dispatch. Do not commit.

---

### Task 2: The driver feeds capture times, gates the fallback on real frames, and the harness models a watching camera

**Files:**
- Modify: `ui/src/next/hubs/sky/sheets/photosphere.ts` (fields at lines 233-274; `compassReady`/`tiltReady`/`vouched` at 309-317; the rVFC callback at 491-505; `observeStillness` at 521-547; the fallback observation in `grabFrame` at 549-560 and the two `evidence` objects at 498 and 565; `start()` and `stop()` for the new field reset)
- Modify: `ui/src/next/hubs/sky/sheets/__tests__/photosphereStillnessDom.test.tsx` (harness lines 24-26, 61, 83-107; five new cases appended before the final `console.log`)

**Interfaces:**
- Consumes from Task 1: `VisualStability.continuity(now)`, `viewVouchesFor(readingAt, view)`, `PoseEvidence { view, sourceHealthy }`.
- Produces: no new public API. `PhotosphereSweep.compassReady`, `tiltReady`, `frameCount`, `cells`, `aimTarget`, `captureCue`, `alignmentReport()` keep their names and types.

**Driver changes, exactly:**

1. rVFC callback (lines 491-505): observe with the CAPTURE time when the browser supplies a plausible one, so frame content is placed on the sensor's timeline rather than tens of ms late:
```ts
const seen=Number.isFinite(metadata.captureTime)&&metadata.captureTime<=now&&now-metadata.captureTime<=STALE_FRAME_MS?metadata.captureTime:now;
this.observeStillness(video,seen);
const evidence:PoseEvidence={view:this.stability.continuity(now),sourceHealthy:this.sourceHealthy};
```
Import `STALE_FRAME_MS` from `./photosphereStability`. Add a comment: a capture time later than the callback or older than a stale frame is not believed.

2. `vouched(at)` (lines 312-317) becomes:
```ts
private vouched(at: number | null): boolean {
  if (at === null) return false;
  const now = performance.now();
  return now - at < SENSOR_SILENCE_MS || viewVouchesFor(at, this.stability.continuity(now));
}
```
Import `viewVouchesFor` from `./photospherePose`. Update the comment above `compassReady` so it says the video must vouch for THAT reading (continuity reaching back to it), not merely that the view is currently still.

3. The fallback observation in `grabFrame` (line 557): only a newly delivered media frame is evidence. Add a field `private lastMediaTime = -1;` (reset to `-1` in `start()` before the listeners attach and in `stop()`), and a method:
```ts
/** The interval fallback runs on a TIMER, and a timer proves nothing about
 *  the camera: a paused or stalled element keeps its last decoded image and
 *  its dimensions, and re-reading that image every 350 ms would earn a hold
 *  the camera never witnessed (review 15, P1). A frame counts only when the
 *  element is playing with data and the media clock has moved since the last
 *  one, and the track behind it is live and not muted. */
private newMediaFrame(video: HTMLVideoElement): boolean {
  const track = this.stream?.getVideoTracks?.()[0];
  if (video.paused || video.ended || video.readyState < 2) return false;           // 2 = HAVE_CURRENT_DATA
  if (track && (track.readyState !== "live" || track.muted)) return false;
  const t = video.currentTime;
  if (!(Number.isFinite(t) && t > this.lastMediaTime)) return false;
  this.lastMediaTime = t;
  return true;
}
```
and change line 557 to `if (!frame && video && this.sourceHealthy && this.newMediaFrame(video)) this.observeStillness(video, now);`. The evidence object at line 565 becomes `{view:this.stability.continuity(now),sourceHealthy:this.sourceHealthy}`.

4. Update the comment above the fallback (lines 551-556) to say the settle now lands one observed frame later than before only when the media clock is moving, and that a frozen element leaves stability unknown after `STALE_FRAME_MS`.

**Harness changes in `photosphereStillnessDom.test.tsx`:**

1. Model a playing element. After line 26 add:
```ts
// The interval fallback consults the element's media state: a paused or
// frozen element is not delivering frames however often the timer fires.
let mediaTime=0,paused=false;
Object.defineProperty(w.HTMLVideoElement.prototype,'currentTime',{get:()=>mediaTime,configurable:true});
Object.defineProperty(w.HTMLVideoElement.prototype,'paused',{get:()=>paused,configurable:true});
Object.defineProperty(w.HTMLVideoElement.prototype,'ended',{get:()=>false,configurable:true});
Object.defineProperty(w.HTMLVideoElement.prototype,'readyState',{get:()=>2,configurable:true});
```
and the track at line 61 becomes `const track={stop(){},getSettings:()=>({deviceId:'main'}),addEventListener(){},readyState:'live',muted:false};`.

2. `approachAndHold` becomes a camera that WATCHES the approach. Replace lines 83-107 with:
```ts
/** A recording sweep aimed at one dome cell by a 10 degree approach over
 *  0-600 ms, with the camera watching the whole time: one video frame after
 *  each orientation event, the scene shifting a pixel each time so the video
 *  sees the motion the sensor reports. Returns the moment the approach ended:
 *  silence starts here. With `rvfc:false` the element has no
 *  requestVideoFrameCallback at all - Firefox Android - and the returned tick
 *  drives the 350 ms setInterval fallback instead, with the media clock
 *  advancing unless a test freezes it. `step` is the size of the LAST
 *  orientation step in degrees (2 by default). */
async function approachAndHold(rvfc=true,step=2){
  shift=0;hidden=false;blind=false;intervalFn=null;mediaTime=0;paused=false;
  const video=w.document.createElement('video');
  let frame:((now:number,metadata:unknown)=>void)|undefined;
  if(rvfc){
    video.requestVideoFrameCallback=(fn:typeof frame)=>{frame=fn;return 1;};
    video.cancelVideoFrameCallback=()=>{};
  }
  const sweep=new PhotosphereSweep();
  await sweep.start(video,w.document.createElement('canvas'));
  const cell=sweep.cells.find((c)=>c.alt>20&&c.alt<60)!;
  const tick=rvfc
    ? ()=>{clock+=100;mediaTime+=0.1;frame!(clock,{captureTime:clock,mediaTime,presentationTime:clock,
        expectedDisplayTime:clock,width:640,height:480,presentedFrames:1});}
    : ()=>{clock+=350;if(!paused)mediaTime+=0.35;intervalFn!();};
  const aim=(offset:number)=>{
    const ev=new w.Event('deviceorientationabsolute');
    clock+=100;Object.defineProperty(ev,'timeStamp',{value:clock});
    Object.assign(ev,{alpha:(360-(cell.az+offset))%360,beta:90+cell.alt,gamma:0,absolute:true});
    w.dispatchEvent(ev);
  };
  sweep.begin();
  for(let i=10;i>=step;i-=2){aim(i);shift++;tick();}
  aim(0);shift++;                      // the last step: `step` degrees in 100 ms
  // From here the browser sends no orientation event ever again.
  return {sweep,cell,tick,aim,silentFrom:clock};
}
```
Note `begin()` now precedes the approach so the frames during it are frames of a recording sweep, as on a phone; `grabFrame` refuses without an accepted pose, so nothing is captured during the approach. If the existing first case ("captures within 1.5 s") changes its measured latency, that is the one observed frame the fallback gate adds; the acceptance row is 1500 ms and both paths must stay inside it.

3. Run the existing ten cases; all must pass with the new harness before any new case is added. If "A still phone captures on the interval fallback" fails on the media gate, the gate is reading a property the harness does not model: fix the harness only if the property is one a real element has.

4. Append these cases before the final `console.log`:
```ts
await test('P1: a phone that moves after the sensor goes silent is not certified by the stillness that follows',async()=>{
  const {sweep,tick}=await approachAndHold();
  for(let i=0;i<30;i++){shift++;tick();}      // 3 s of movement, no orientation event
  assert.equal(sweep.frameCount,0);
  for(let i=0;i<12;i++)tick();                 // then a new steady view for 1.2 s
  assert.equal(sweep.frameCount,0,'a new steady view certified the direction from before the movement');
  assert.equal(sweep.compassReady,false,'the compass read as ready for a direction the phone has left');
  assert.equal(sweep.aimTarget,null);
  assert.match(sweep.captureCue,/Waiting for the compass/,`cue was: "${sweep.captureCue}"`);
  for(let i=0;i<300;i++)tick();                // and it never expires into acceptance
  assert.equal(sweep.frameCount,0);
  sweep.stop();
});

await test('P1: the interval fallback learns nothing from a frozen frame',async()=>{
  const {sweep,tick}=await approachAndHold(false);
  paused=true;                                  // the element keeps its last image; the media clock stops
  for(let i=0;i<10;i++)tick();                  // 3.5 s of timer ticks
  assert.equal(sweep.frameCount,0,'repeated reads of one frozen frame earned a hold');
  assert.equal(sweep.compassReady,false,'a frozen video vouched for the compass');
  sweep.stop();
});

await test('P1: a frozen fallback video recovers once frames flow and a fresh reading arrives',async()=>{
  const {sweep,cell,tick,aim}=await approachAndHold(false);
  paused=true;
  for(let i=0;i<10;i++)tick();
  paused=false;
  for(let i=0;i<6;i++)tick();                   // frames flow again, 2.1 s, but nothing watched the freeze
  assert.equal(sweep.frameCount,0,'the old reading was certified across an interval nothing watched');
  aim(0);                                       // the sensor speaks once more
  const from=clock;
  let capturedAfter:number|null=null;
  for(let i=0;i<6&&capturedAfter===null;i++){
    tick();
    if(sweep.cells.find((c)=>c.id===cell.id)?.captured)capturedAfter=clock-from;
  }
  assert.notEqual(capturedAfter,null,'no capture after the video recovered and the sensor spoke');
  assert.ok(capturedAfter!<=1500,`took ${capturedAfter} ms after the fresh reading`);
  sweep.stop();
});

await test('P2: a valid 10 degree final step in 100 ms captures like any other approach',async()=>{
  const {sweep,cell,tick,silentFrom}=await approachAndHold(true,10);
  let capturedAfter:number|null=null;
  for(let i=0;i<20&&capturedAfter===null;i++){
    tick();
    if(sweep.cells.find((c)=>c.id===cell.id)?.captured)capturedAfter=clock-silentFrom;
  }
  assert.notEqual(capturedAfter,null,'a quick final movement was rejected for the whole hold');
  assert.ok(capturedAfter!<=1500,`took ${capturedAfter} ms of stillness`);
  sweep.stop();
});

await test('P2: a magnetometer outlier as the last event is refuted by the still video, and the cell is captured at the reading before it',async()=>{
  const {sweep,cell,tick,aim}=await approachAndHold();
  for(let i=0;i<4;i++)tick();                   // 400 ms into the hold: inside the settle, not yet captured
  clock+=40;aim(20);                            // one reading 20 degrees off, 140 ms after the last, phone unmoved
  let captured=false;
  for(let i=0;i<20&&!captured;i++){tick();captured=!!sweep.cells.find((c)=>c.id===cell.id)?.captured;}
  assert.ok(captured,'the outlier either became the pose or blocked the hold');
  sweep.stop();
});
```
Check the outlier case against the harness: `aim(20)` advances the clock 100 ms itself, so with `clock+=40` the two readings are 140 ms apart, inside `JITTER_GAP_MS` (150). If the cell is already captured before the outlier arrives (frameCount 1 after the first loop), reduce that loop to two ticks; the assertion is that the cell ends up captured at the pre-outlier direction and the hold is not blocked.

- [ ] **Step 1: Apply the harness changes (1 and 2), run the existing ten cases**

Run from `ui`: `node --import tsx src/next/hubs/sky/sheets/__tests__/photosphereStillnessDom.test.tsx`
Expected before the driver change: the file fails to compile (`visuallyStable` no longer exists after Task 1) or, if it compiles, the ten cases pass. Record which.

- [ ] **Step 2: Apply driver changes 1-4**

- [ ] **Step 3: Run the ten existing cases**

Expected: 10/10. Then `npx tsc -b` from `ui`: clean.

- [ ] **Step 4: Append the five new cases and run**

Expected: 15/15. If the outlier case fails, apply the adjustment described under it before touching the driver.

- [ ] **Step 5: Mutation check (record it in the report)**

Temporarily revert change 3 to the old unconditional observation and confirm "the interval fallback learns nothing from a frozen frame" fails; revert. Temporarily make `vouched` use `this.stability.stableAt(now)===true` again and confirm "a phone that moves after the sensor goes silent" fails on the compassReady assertion; revert.

- [ ] **Step 6: Run the whole photosphere baseline and the full suite**

From `ui`: the eight photosphere files listed in Global Constraints, then `npm.cmd test`. Expected: only the three #36 failures. Record the exact counts.

- [ ] **Step 7: Report**

Write the report to the path in the dispatch. Do not commit.
