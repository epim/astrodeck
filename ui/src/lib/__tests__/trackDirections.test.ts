import assert from 'node:assert/strict';
import {trackDirections} from '../trackDirections';
import {hourlyTrackSamples} from '../../next/hubs/sky/finder/track';

let passed=0,failed=0;
function test(name:string,fn:()=>void) {
  try {fn();passed++;} catch(e) {failed++;console.error(`${name}: ${String(e)}`);}
}

test('arrows follow increasing time even when a track moves left',()=>{
  const arrows=trackDirections([{x:160,y:30},{x:80,y:30},{x:0,y:30}],40);
  assert.equal(arrows.length,4);
  assert(arrows.every(a=>Math.abs(a.angle)===180));
  assert.deepEqual(arrows.map(a=>a.x),[140,100,60,20]);
});
test('projection jumps never get an arrow bridging them',()=>{
  assert.equal(trackDirections([{x:0,y:0},{x:900,y:0}]).length,0);
});
test('hourly ticks land on clock hours and interpolate across north',()=>{
  const start=new Date(2026,8,15,22,30).getTime();
  const hours=hourlyTrackSamples([{t:0,alt:30,az:350,st:'ok'},{t:1,alt:40,az:10,st:'ok'},{t:2,alt:50,az:30,st:'ok'}],start);
  assert.deepEqual(hours.map(h=>h.t),[.5,1.5]);
  assert.equal(hours[0].az,0);
  assert.equal(new Date(start+hours[0].t*3600000).getMinutes(),0);
});
test('hour markers never bridge a below-horizon interval',()=>{
  const start=new Date(2026,8,15,22,30).getTime();
  assert.equal(hourlyTrackSamples([{t:0,alt:-5,az:0,st:'below'},{t:1,alt:5,az:10,st:'floor'}],start).length,0);
});

console.log(`trackDirections.test: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};
if(failed)process.exitCode=1;
