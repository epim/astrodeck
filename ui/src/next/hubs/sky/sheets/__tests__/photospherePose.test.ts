import assert from 'node:assert/strict';
import { CameraPoseHistory, MotionStability, viewVouchesFor,
  MOTION_STALE_MS, QUIET_DRIFT_DEG, QUIET_RATE_DEG_S } from '../photospherePose';
import { lookBasis, orientationBasis } from '../photosphereGeometry';
// The fixture's dispatch predicate lives in this tracked helper (the fixture
// itself imports it too, from the same directory) so this test fails if the
// fixture and the production contract it models ever drift apart.
import { orientationChanged } from '../__fixtures__/orientationChange';
let passed=0;
function test(name:string,fn:()=>void){fn();passed++;console.log(`PASS ${name}`);}
test('A delayed image uses its capture-time direction, not the newest phone direction',()=>{
  const h=new CameraPoseHistory();
  const poses=Array.from({length:11},(_,i)=>lookBasis(i*5,20));
  poses.forEach((basis,i)=>h.add({at:i*100,basis,screenAngle:0}));
  assert.equal(h.forFrame(1000,600),poses[6]);
  assert.notEqual(h.forFrame(1000,600),poses[10]);
  assert.equal(h.forFrame(1000),null,'Moving frames without capture times must wait');
});
test('Untimed frames require a continuous half-second of stable pitch, yaw and roll',()=>{
  const h=new CameraPoseHistory(),basis=lookBasis(0,35);
  for(let at=0;at<=500;at+=100)h.add({at,basis,screenAngle:0});
  assert.equal(h.forFrame(500),basis);
  h.add({at:600,basis:orientationBasis(270,50,90),screenAngle:0});
  assert.equal(h.forFrame(600),null);
});
test('Small heading wrap jitter is tolerated without inventing a 360-degree movement',()=>{
  const h=new CameraPoseHistory();
  for(let at=0;at<=600;at+=100)h.add({at,basis:lookBasis(at%200?359.7:.3,30),screenAngle:0});
  assert.ok(h.forFrame(600));
});
test('Dropped orientation data, future timestamps and old frames cannot register',()=>{
  const h=new CameraPoseHistory(),basis=lookBasis(0,0);
  for(let at=0;at<=600;at+=100)h.add({at,basis,screenAngle:0});
  assert.equal(h.forFrame(600,601),null);assert.equal(h.forFrame(600,-1000),null);
  assert.equal(h.forFrame(1000,600),null);
  h.add({at:1500,basis,screenAngle:0});assert.equal(h.forFrame(1500),null);
});
test('Rotating the screen discards poses from the previous video orientation',()=>{
  const h=new CameraPoseHistory(),basis=lookBasis(0,0);
  for(let at=0;at<=600;at+=100)h.add({at,basis,screenAngle:0});
  h.add({at:700,basis,screenAngle:90});assert.equal(h.forFrame(700,600),null);
  for(let at=800;at<=1300;at+=100)h.add({at,basis,screenAngle:90});
  assert.equal(h.forFrame(1300),basis);assert.equal(h.forFrame(1300,600),null);
});
test('The browser sends nothing while still: with evidence the settled pose is returned',()=>{
  const h=new CameraPoseHistory();
  // Mirrors photosphereStillness.test.ts case 1: a 10 degree approach over
  // 0-500 ms (one sample per 100 ms), then real silence - no heartbeat.
  const poses=Array.from({length:6},(_,i)=>lookBasis(10-i*2,20));
  poses.forEach((basis,i)=>h.add({at:i*100,basis,screenAngle:0}));
  const finalBasis=poses[5];
  assert.equal(h.forFrame(1000,undefined,{view:{stillSince:550,lastBreak:{from:450,to:550}},sourceHealthy:true}),finalBasis);
  assert.equal(h.forFrame(2000,undefined,{view:{stillSince:550,lastBreak:{from:450,to:550}},sourceHealthy:true}),finalBasis);
  // Smoke test of the fixture's own dispatch predicate: this is the change
  // threshold that makes the fixture stop sending once the pose above
  // settles, exactly like Chromium's real 0.1 degree deviceorientation gate.
  assert.equal(orientationChanged(null,{alpha:10,beta:20,gamma:0}),true,'the first reading always fires');
  assert.equal(orientationChanged({alpha:10,beta:20,gamma:0},{alpha:10.05,beta:20,gamma:0}),false,'a 0.05 degree wobble must not fire');
  assert.equal(orientationChanged({alpha:10,beta:20,gamma:0},{alpha:10.15,beta:20,gamma:0}),true,'a change past the 0.1 degree threshold must fire');
});
test('A delivered-late event does not move a frame captured before it',()=>{
  const h=new CameraPoseHistory();
  const poses=[10,8,6].map(yaw=>lookBasis(yaw,20));
  poses.forEach((basis,i)=>h.add({at:i*100,basis,screenAngle:0}));
  // captureTime 150 sits exactly 50 ms from both the 100 ms and 200 ms
  // samples - outside the existing 40 ms nearest-sample rule - and this
  // three-sample, 200 ms-wide stream is also too short for forFrame's own
  // 500 ms/250 ms freshness gates to ever admit a captureTime lookup here,
  // so today's code returns null. There are exactly two acceptable answers:
  // null (the warm-up gate refusing a stream it has not watched for long
  // enough) or poses[1], the OLDER of the two neighbours, which is the one
  // that cannot be newer than the frame. The newest sample - yaw 6 at 200 ms,
  // after the shutter - is never one of them, and "not the newest" alone is a
  // near-vacuous assertion that any refactor returning some third thing passes.
  // The evidence has to be a continuity a witness could actually hold at this
  // `now`: frames the video has already seen, not ones from its future. A run
  // beginning 350 ms after the moment being asked about would be nonsense the
  // production code has no reason to survive, and a fixture like that grades
  // nothing.
  const result=h.forFrame(200,150,{view:{stillSince:-300,lastBreak:{from:-400,to:-300}},sourceHealthy:true});
  assert.ok(result===null||result===poses[1],
    'a late callback must resolve to the older neighbour or refuse, never to the newest phone direction');
});
// THE SECOND WITNESS (MotionStability, issues #63, #76, #106). Every case below
// samples at 16 ms, which is the 60 Hz Chromium is reported to deliver
// `devicemotion` at and comfortably inside MOTION_STALE_MS.
//
// `still` is a phone holding still AS A REAL GYRO REPORTS ONE: a noise floor,
// not an exact zero. 0.0374 deg/s of magnitude, a thirteenth of
// QUIET_RATE_DEG_S, and 0.0359 degrees integrated over the longest run below -
// so neither the floor nor the total is anywhere near it and every break in
// these cases is the one the case is about.
// It is not `{0,0,0}` for a load-bearing reason (issue #106): an exact zero
// triple is no measurement at all, because a stuck driver reports one and so
// does a synthesised stream, and `observe` refuses it. A fixture written on
// exact zeros would describe a device that does not exist and would now vouch
// for nothing - which the case at the end of this file pins directly.
const still={alpha:0.02,beta:-0.03,gamma:0.01};
/** Feed `n` samples at `step` ms from `from`, all at one rate, and return the
 *  instant of the last one. Written as a helper because every case below needs
 *  a RUN and a run of hand-written lines is where an off-by-one hides. */
function samples(m:MotionStability,from:number,n:number,rate:{alpha:number;beta:number;gamma:number}|null,step=16):number{
  let at=from;
  for(let i=0;i<n;i++){m.observe(at,rate);if(i<n-1)at+=step;}
  return at;
}
test('A quiet gyro run vouches for a reading taken inside it, and for nothing before the run began',()=>{
  // The shape the whole witness exists for: the phone is holding still over a
  // view that can say nothing, and the gyro says it has not turned since before
  // the reading arrived. The first sample records a break at its own instant -
  // nothing watched the phone before it - so the run reaches back exactly that
  // far and no further, which is what makes the continuity one
  // `viewVouchesFor` can use at all (a continuity with no break vouches for a
  // reading of any age whatsoever).
  // Instants: samples at 0, 16, ... 960 (61 of them). The break is {0, 0} and
  // the run opens at 0 (the earlier sample of the first quiet pair).
  // Mutation: make the first sample return without recording a break
  // (`if(!Number.isFinite(previousAt))return;` ahead of the gap test), leaving
  // `lastBreak` null. Observed red: 'a reading taken inside the quiet run was
  // not vouched for' - because a continuity with no break vouches for NOTHING,
  // which is `viewVouchesFor`'s own rule and the reason the first sample has to
  // record one.
  const m=new MotionStability();
  const last=samples(m,0,61,still);
  assert.equal(last,960);
  assert.equal(m.witness(960),'quiet');
  const view=m.continuity(960);
  assert.equal(viewVouchesFor(500,view),true,'a reading taken inside the quiet run was not vouched for');
  assert.equal(viewVouchesFor(-1000,view),false,
    'the first sample of a run vouched for a reading taken before the phone was watched');
  assert.deepEqual(view,{stillSince:0,lastBreak:{from:0,to:0}},
    'the run must open at the earlier sample of the first quiet pair, and carry the first sample as its break');
});
test('Rotation above the floor breaks the run at the earlier sample of the pair',()=>{
  // The floor is the per-pair test, and the break has to start at the EARLIER
  // sample because the turn may have begun anywhere inside the pair - the same
  // rule, for the same reason, as the video witness's motion test.
  // Instants: 16 quiet samples from 0 to 240, one sample at 256 turning at
  // 2 deg/s (over QUIET_RATE_DEG_S = 0.5), then 16 quiet samples from 272 to
  // 512. The break is {240, 256} and the new run opens at 256, so a reading at
  // 0 - 240 ms before the break and so well past CONTINUITY_SLOP_MS's 150 ms
  // margin - is no longer vouched for, while a reading at 256 is.
  // Two mutations, because this case is the only one that grades the floor at
  // all. Delete the floor branch from `observe` outright: the turn here is
  // 2 deg/s over a 16 ms pair, which adds 0.032 degrees, so the TOTAL cannot
  // catch it and only the floor can. Observed red: 'a pair above the floor did
  // not break the run'. (The DOM case for a turning gyro reports 10 deg/s over 100 ms
  // pairs, where the total catches it on the first pair, so that case does not
  // grade this branch and says so.)
  // And: record the break as `this.broken(at, at)` instead of
  // `this.broken(previousAt, at)`. Observed red: 'the break must span the pair
  // the turn could have begun in, not the instant it was noticed'. It reddens
  // the shape assertion rather than the vouching one, and that is the point of
  // keeping both: at this fixture's spacing the narrowed break still refuses
  // the reading at 0, so a case that only asked the yes/no question would grade
  // nothing here.
  const m=new MotionStability();
  assert.equal(samples(m,0,16,still),240);
  assert.equal(viewVouchesFor(0,m.continuity(240)),true,'the quiet run before the turn is the premise of this case');
  m.observe(256,{alpha:0,beta:0,gamma:2});
  assert.equal(m.witness(256),'turning','a pair above the floor did not break the run');
  assert.equal(m.continuity(256),null,'a turning phone produced a continuity');
  assert.equal(samples(m,272,16,still),512);
  const view=m.continuity(512);
  assert.deepEqual(view,{stillSince:256,lastBreak:{from:240,to:256}},
    'the break must span the pair the turn could have begun in, not the instant it was noticed');
  assert.equal(viewVouchesFor(0,view),false,
    'a turn was credited to the instant it was noticed rather than to the interval it could have begun in');
  assert.equal(viewVouchesFor(256,view),true,'the run after the turn vouches for a reading taken at the turn');
});
test('A creep under the floor is caught by the total, well inside the alignment gate',()=>{
  // Without a bound on the TOTAL this is only a speed limit, and an
  // arbitrarily slow turn walks arbitrarily far while every pair stays under
  // the floor - the failure `VisualStability`'s anchor frame exists to stop,
  // reached here through the rate instead of through the pixels.
  // The arithmetic: 0.4 deg/s is under QUIET_RATE_DEG_S = 0.5, so no pair ever
  // breaks. Each 16 ms pair adds 0.4 * 0.016 = 0.0064 degrees, so the total
  // passes QUIET_DRIFT_DEG = 0.5 on the 79th pair:
  //   78 pairs -> 0.4992 degrees, still quiet;
  //   79 pairs -> 0.5056 degrees, broken.
  // 79 pairs is 1264 ms, and 0.5056 degrees is a third of `grabFrame`'s 1.5
  // degree alignment gate - which is the whole claim the bound is chosen for:
  // a creep this witness vouches for never reaches the disagreement capture
  // already refuses.
  // Mutation: delete the total test (the `Math.hypot(...this.drift)` branch).
  // Observed red: 'a sub-floor creep walked past the total unchallenged'.
  const m=new MotionStability();
  const creep={alpha:0.4,beta:0,gamma:0};
  assert.ok(Math.hypot(creep.alpha,creep.beta,creep.gamma)<=QUIET_RATE_DEG_S,'the creep must be UNDER the floor or this case grades the floor');
  assert.equal(samples(m,0,79,creep),1248);
  assert.equal(m.witness(1248),'quiet','the total fired early: 78 pairs is 0.4992 degrees, under the bound');
  m.observe(1264,creep);
  assert.equal(m.witness(1264),'turning','a sub-floor creep walked past the total unchallenged');
  assert.ok(0.4*1.264>QUIET_DRIFT_DEG&&0.4*1.264<1.5,
    'the creep caught here must be past the bound and still inside the 1.5 degree alignment gate');
});
test('Samples that stop go stale, and the gap they leave is covered by nothing',()=>{
  // The stale bound, both ends of it. A witness that answered on its last
  // sample forever would vouch for a phone it stopped watching, which is the
  // recurring defect class in this module - a verdict riding a value-producing
  // path - reached through the gyro this time.
  // Instants: 61 samples from 0 to 960. MOTION_STALE_MS = 200, so the witness
  // still speaks at 1160 (exactly 200 ms on) and is stale at 1161. A sample at
  // 1400 is 440 ms after the last one, past the bound, so it opens a new run
  // that covers nothing earlier than itself.
  // Mutation: change the staleness test to `now - this.at >= MOTION_STALE_MS *
  // 100`. Observed red: 'a stopped gyro went on vouching'.
  const m=new MotionStability();
  assert.equal(samples(m,0,61,still),960);
  assert.equal(m.witness(960+MOTION_STALE_MS),'quiet','the bound is inclusive at its own edge');
  assert.equal(m.witness(961+MOTION_STALE_MS),'stale','a stopped gyro went on vouching');
  assert.equal(m.continuity(961+MOTION_STALE_MS),null);
  samples(m,1400,16,still);
  const view=m.continuity(1640);
  assert.deepEqual(view,{stillSince:1400,lastBreak:{from:1400,to:1400}});
  assert.equal(viewVouchesFor(900,view),false,'a run reopened after a gap vouched across the gap');
});
test('A browser with the event but no gyro vouches for nothing',()=>{
  // `devicemotion` fires on devices with no rate sensor at all, with
  // `rotationRate` null or its three axes null. That is not a phone reporting
  // stillness, it is no measurement, and reading it as zero would hand every
  // featureless hold a witness that can never break. It is not counted as a
  // sample either, which is what makes a device delivering only these read as
  // STALE - nothing is being watched - rather than sit at 'turning' forever
  // claiming to be watching something.
  // Mutation: delete the `axes.every(...)` guard from `observe` outright.
  // Observed red: 'a device with no gyro at all vouched for a reading' - with
  // the guard gone, `Math.hypot(null,null,null)` is 0 and `null * dt` is 0, so
  // the run opens and never breaks.
  // NOT `rate?.alpha ?? 0`, which was this case's mutant in fix round 0 and is
  // now EQUIVALENT: since issue #106 an exact zero triple is refused a few lines
  // below, so mapping nulls to zeros changes which branch refuses and changes no
  // behaviour at all. It is recorded here because it is the mutant a reader
  // would reach for, and it grades nothing.
  const m=new MotionStability();
  samples(m,0,61,null);
  assert.equal(m.witness(960),'stale','a device with no gyro at all vouched for a reading');
  assert.equal(m.continuity(960),null);
  const axesNull=new MotionStability();
  for(let at=0;at<=960;at+=16)axesNull.observe(at,{alpha:null,beta:null,gamma:null});
  assert.equal(axesNull.witness(960),'stale','a null axis was read as zero rotation');
  assert.equal(axesNull.continuity(960),null);
  // And a real stream that goes empty for longer than the stale bound is the
  // same fact reached from the other side: the empty events advance nothing, so
  // the witness goes stale on the last MEASURED sample and the run cannot be
  // resumed across the silence.
  const wentEmpty=new MotionStability();
  assert.equal(samples(wentEmpty,0,16,still),240);
  assert.equal(wentEmpty.witness(240),'quiet','the run before the stream went empty is the premise of this half');
  for(let at=256;at<=640;at+=16)wentEmpty.observe(at,null);
  assert.equal(wentEmpty.witness(640),'stale','empty events kept a run alive that nothing was measuring');
  wentEmpty.observe(656,still);
  const view=wentEmpty.continuity(656);
  assert.equal(view,null,'a run resumed across a silence longer than the stale bound');
});
test('A channel that reports exact zeros is not a witness, and one that dies into zeros stops being one',()=>{
  // Issue #106, and the one wrong guess in this witness that used to VOUCH
  // rather than refuse. The spec spelling of "no rate sensor" is `null`, and
  // that was refused; a device that spells its silence `0` was read as a phone
  // holding perfectly still. Nothing downstream catches it - the video is
  // `featureless` by construction wherever this witness is consulted - so it is
  // a false hold, which puts a frame into the mosaic at a pose the phone has
  // left, and that is the outcome this whole subsystem exists to make
  // impossible.
  // A real MEMS gyro has a noise floor and does not report an exact zero triple
  // twice running; a stream that does is synthetic. So an exact zero is treated
  // as NO SAMPLE, the same as a null, which is strictly stronger than a
  // "has it ever measured anything" flag in either scope - see `observe`.
  // The second half is what neither flag scope would have caught: a channel
  // that WORKS, opens a run, and then dies into zeros. A stream of zeros
  // produces no break, so a per-run flag is never cleared and a per-session flag
  // was set long ago; here `at` simply stops advancing and the witness goes
  // stale MOTION_STALE_MS after the last real sample.
  // Mutation: delete `if(a===0&&b===0&&c===0)return;` from `observe`. Observed
  // red: 'a stream of exact zeros opened a run'.
  const zeros={alpha:0,beta:0,gamma:0};
  const dead=new MotionStability();
  samples(dead,0,61,zeros);
  assert.equal(dead.witness(960),'stale','a stream of exact zeros opened a run');
  assert.equal(dead.continuity(960),null,'a stream of exact zeros vouched for a reading');
  // Two real samples are needed to open a run, so a single non-zero sample in a
  // sea of zeros is not enough either - that stream delivers about one
  // measurement a second and can witness nothing.
  const oneSample=new MotionStability();
  for(let at=0;at<=960;at+=16)oneSample.observe(at,at===480?still:zeros);
  assert.equal(oneSample.continuity(960),null,'one measurement in a second of zeros opened a run');
  // The working channel that dies.
  const died=new MotionStability();
  assert.equal(samples(died,0,61,still),960);
  assert.equal(died.witness(960),'quiet','the run before the channel died is the premise of this half');
  assert.equal(viewVouchesFor(500,died.continuity(960)),true,'the live channel must vouch, or the death below shows nothing');
  for(let at=976;at<=1600;at+=16)died.observe(at,zeros);
  assert.equal(died.witness(1600),'stale','a channel that died into zeros went on vouching from its last real sample');
  assert.equal(died.continuity(1600),null);
});
console.log(`photospherePose.test: ${passed}/${passed} passed`);
export const result={passed,failed:0,total:passed};
