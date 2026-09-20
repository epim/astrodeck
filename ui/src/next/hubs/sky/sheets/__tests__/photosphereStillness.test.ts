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

test('A continuity with no break at all vouches for nothing',()=>{
  // It would vouch for a reading of ANY age: there is no instant in it before
  // which the witness stops answering. No producer emits one - the first frame
  // a witness ever sees is itself a break - so this shape can only arrive from
  // a hand-made object, and it must not be the one shape that certifies
  // everything.
  const h=new CameraPoseHistory();
  const finalBasis=approachThenStop(h);
  assert.equal(h.forFrame(1500,undefined,{view:{stillSince:-1000,lastBreak:null},sourceHealthy:true}),null,
    'a witness with no break behind it certified a reading it cannot have watched');
  // The same watching, with the break every real witness carries, still works.
  assert.equal(h.forFrame(1500,undefined,{view:{stillSince:-1000,lastBreak:{from:-1000,to:-1000}},sourceHealthy:true}),finalBasis);
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
  // The break is real and old: every continuity carries one, because the first
  // frame a witness ever sees is itself a break (photosphereStability), so a
  // fixture with none describes a witness that cannot exist.
  const stillAllAlong:PoseEvidence={view:{stillSince:-1000,lastBreak:{from:-1000,to:-1000}},sourceHealthy:true};
  assert.equal(h.forFrame(1500,undefined,stillAllAlong),steady[5].basis,'the outlier was not refuted, or refusal replaced recovery');
  assert.equal(h.forFrame(30000,undefined,stillAllAlong),steady[5].basis);
});

test('P2: when the spike comes FIRST and the sensor corrects itself, the correction is worn',()=>{
  // The case that showed the video cannot arbitrate (issue #47). The phone is
  // still; the magnetometer throws one reading 20 degrees off and then reports
  // the truth again 40 ms later. The video says only that the phone did not
  // move across the pair - which is true, and which does not name the bad
  // reading. Wearing the older reading because it is older wears the spike.
  const h=new CameraPoseHistory();
  const steady=Array.from({length:6},(_,i)=>({at:i*100,basis:lookBasis(0,20),screenAngle:0}));
  steady.forEach(p=>h.add(p));
  h.add({at:520,basis:lookBasis(20,20),screenAngle:0});          // the spike
  const correction={at:560,basis:lookBasis(0,20),screenAngle:0}; // the sensor's own correction
  h.add(correction);
  const held:PoseEvidence={view:{stillSince:400,lastBreak:{from:300,to:400}},sourceHealthy:true};
  for(const now of [1100,2000,10000,60000]){
    assert.equal(h.forFrame(now,undefined,held),correction.basis,
      `the spike was worn instead of the correction at ${now} ms`);
  }
});

test('P2: a refuted pair with nothing to arbitrate it gets no pose at all',()=>{
  // Both members of the pair disagree with the reading before it, so the
  // history cannot name the bad one either. Refusing here is not the old rate
  // rejection: the video and the history have both spoken, and the next
  // reading of any kind clears it by moving the pair along.
  const h=new CameraPoseHistory();
  h.add({at:0,basis:lookBasis(0,20),screenAngle:0});
  h.add({at:100,basis:lookBasis(20,20),screenAngle:0});
  h.add({at:150,basis:lookBasis(40,20),screenAngle:0});
  const held:PoseEvidence={view:{stillSince:0,lastBreak:{from:-100,to:0}},sourceHealthy:true};
  assert.equal(h.forFrame(1500,undefined,held),null,'a pair no reading agrees with produced a pose anyway');
  assert.equal(h.forFrame(30000,undefined,held),null);
  // And a fresh reading clears it: the pair is now (40, 40), no jump at all.
  h.add({at:1600,basis:lookBasis(40,20),screenAngle:0});
  assert.notEqual(h.forFrame(3000,undefined,{view:{stillSince:1500,lastBreak:{from:1400,to:1500}},sourceHealthy:true}),null,
    'a fresh reading did not clear the refusal');
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
