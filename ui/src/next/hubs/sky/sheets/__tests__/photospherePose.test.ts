import assert from 'node:assert/strict';
import { CameraPoseHistory } from '../photospherePose';
import { lookBasis, orientationBasis } from '../photosphereGeometry';
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
console.log(`photospherePose.test: ${passed}/${passed} passed`);
export const result={passed,failed:0,total:passed};
