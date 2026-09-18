import assert from 'node:assert/strict';
import { CameraPoseHistory } from '../photospherePose';
import { lookBasis } from '../photosphereGeometry';

// Phase 0 regression for docs/ui-rebuild/14-photosphere-production-handoff.md
// section 2.1: change-driven orientation events cause a capture deadlock.
//
// Chromium emits deviceorientation only on a change (0.1 degree threshold).
// A phone held perfectly still emits NOTHING. The existing suite feeds an
// unchanged sample every 100 ms, which is a heartbeat the browser never
// sends, so the suite passes while a real phone cannot capture at all.
//
// This file models what the browser actually does: a 10 degree approach
// over 0-500 ms, then silence. It fails today on purpose. When it passes,
// forFrame() distinguishes unchanged orientation from a dead sensor.
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

test('A phone that stops moving becomes capturable, with no sensor wiggle required',()=>{
  const h=new CameraPoseHistory();
  const finalBasis=approachThenStop(h);
  // Every attempt from 500 ms through 2 s of perfect stillness must succeed
  // once the video confirms the view is steady and the source is alive.
  for(let now=1000;now<=2000;now+=250){
    assert.equal(h.forFrame(now,undefined,{visuallyStable:true,sourceHealthy:true}),finalBasis,
      `still and steady at ${now} ms was refused`);
  }
});

test('Silence without visual evidence keeps the strict rule: it is not trusted',()=>{
  const h=new CameraPoseHistory();
  approachThenStop(h);
  // No opts at all: the historical contract. A quiet stream with nothing
  // vouching for it must still not produce a pose. This is the guard that
  // stops the fix from simply extending a stale timeout indefinitely.
  assert.equal(h.forFrame(1500),null);
});

test('Video that still shows motion blocks capture even when the sensor is quiet',()=>{
  const h=new CameraPoseHistory();
  approachThenStop(h);
  assert.equal(h.forFrame(1500,undefined,{visuallyStable:false,sourceHealthy:true}),null);
});

test('An explicitly lost sensor refuses capture even with a fresh sample and steady video',()=>{
  const h=new CameraPoseHistory();
  approachThenStop(h);
  // The source itself has been declared gone (listener removed, page
  // hidden, permission revoked). That is loss, not stillness, and a
  // correction for stillness must not make it look valid.
  assert.equal(h.forFrame(600,undefined,{visuallyStable:true,sourceHealthy:false}),null);
});

test('A genuine gap DURING the approach is still movement, not a settle',()=>{
  const h=new CameraPoseHistory();
  // Moving, then a 900 ms hole, then a different direction: the phone was
  // not still across that hole, whatever the video later claims.
  h.add({at:0,basis:lookBasis(10,20),screenAngle:0});
  h.add({at:100,basis:lookBasis(8,20),screenAngle:0});
  h.add({at:1000,basis:lookBasis(0,20),screenAngle:0});
  assert.equal(h.forFrame(1100,undefined,{visuallyStable:true,sourceHealthy:true}),null);
});

console.log(`photosphereStillness.test: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};
if(failed)process.exitCode=1;
