import assert from 'node:assert/strict';
import { CameraPoseHistory } from '../photospherePose';
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
  assert.equal(h.forFrame(1000,undefined,{visuallyStable:true,sourceHealthy:true}),finalBasis);
  assert.equal(h.forFrame(2000,undefined,{visuallyStable:true,sourceHealthy:true}),finalBasis);
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
  const result=h.forFrame(200,150,{visuallyStable:true,sourceHealthy:true});
  assert.ok(result===null||result===poses[1],
    'a late callback must resolve to the older neighbour or refuse, never to the newest phone direction');
});
console.log(`photospherePose.test: ${passed}/${passed} passed`);
export const result={passed,failed:0,total:passed};
