// The REAL capture driver, driven the way a still phone drives it.
//
// photospherePose and photosphereStability are each well tested alone, but the
// glue between them lives in photosphere.ts - the `sourceHealthy` getter,
// `observeStillness`, and the composed evidence object handed to every
// forFrame call site - and that glue is what the S25 deadlock was about. A unit
// test that calls forFrame directly cannot fail when the wiring breaks, so this
// file drives PhotosphereSweep itself: orientation events for an approach, then
// NOTHING at all, while the camera keeps delivering frames.
//
// Both clocks are stubbed together on purpose. performance.now alone would let
// a wall-clock freshness gate (Date.now, as in `compassReady`) stay green
// during a silence the test claims lasts seconds - a fixture hiding the very
// thing under test, which is how the original suite passed while the phone
// deadlocked.
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const dom=new JSDOM('<html><body></body></html>',{url:'https://localhost/',pretendToBeVisual:true});
const w=dom.window as any,g=globalThis as any;
for(const k of ['window','document','navigator','HTMLElement','HTMLVideoElement','HTMLCanvasElement','Element','Node','Event','localStorage','requestAnimationFrame','cancelAnimationFrame'])
  Object.defineProperty(g,k,{value:k==='window'?w:w[k],writable:true,configurable:true});
w.DeviceOrientationEvent=class {};
Object.defineProperty(w,'isSecureContext',{value:true});
Object.defineProperty(w.HTMLVideoElement.prototype,'videoWidth',{get:()=>640});
Object.defineProperty(w.HTMLVideoElement.prototype,'videoHeight',{get:()=>480});
w.HTMLVideoElement.prototype.play=async function(){};

// A textured scene, so a one-pixel shift is measurable rather than invisible:
// a 32-step luminance gradient with a bright block, generated at whatever size
// the caller asks for. `shift` slides it; `hidden` drives the page lifecycle.
let shift=0,hidden=false;
const scenePixel=(x:number,y:number,width:number,height:number)=>{
  const col=Math.round(((x+shift)%width)*31/Math.max(1,width-1));
  const block=col>=10&&col<=17&&y>=Math.floor(height/3)&&y<=Math.floor(2*height/3);
  return block?180:40+Math.round(col*80/31);
};
const ctxStub={ drawImage(){},
  getImageData:(_x:number,_y:number,width:number,height:number)=>{
    const data=new Uint8ClampedArray(width*height*4);
    for(let y=0;y<height;y++)for(let x=0;x<width;x++){
      const v=scenePixel(x,y,width,height),i=(y*width+x)*4;
      data[i]=data[i+1]=data[i+2]=v;data[i+3]=255;
    }
    return {data};
  }, createImageData:(width:number,height:number)=>({data:new Uint8ClampedArray(width*height*4)}), putImageData(){},
};
// `blind` fails ONLY the 32x24 stillness canvas. The capture canvas losing its
// context is a different defect with a cue of its own, and a stub that failed
// both could not tell the two apart.
let blind=false;
w.HTMLCanvasElement.prototype.getContext=function(this:{width:number;height:number}){
  return blind&&this.width===32&&this.height===24?null:ctxStub;
};
w.HTMLCanvasElement.prototype.toDataURL=()=>'data:image/png;base64,';
Object.defineProperty(w.document,'visibilityState',{get:()=>hidden?'hidden':'visible',configurable:true});
// The interval fallback is a path under test, not noise to be swallowed: keep
// the callback so a test without requestVideoFrameCallback can drive it.
let intervalFn:(()=>void)|null=null;
g.setInterval=(fn:()=>void)=>{intervalFn=fn;return 1;};
g.clearInterval=()=>{intervalFn=null;};
const track={stop(){},getSettings:()=>({deviceId:'main'}),addEventListener(){}};
Object.defineProperty(w.navigator,'mediaDevices',{value:{
  enumerateDevices:async()=>[{kind:'videoinput',deviceId:'main',label:'Back main wide camera'}],
  getUserMedia:async()=>({getTracks:()=>[track],getVideoTracks:()=>[track]}),
},configurable:true});
let clock=10000;
Object.defineProperty(performance,'now',{value:()=>clock,configurable:true});
Object.defineProperty(Date,'now',{value:()=>clock,configurable:true});

const { PhotosphereSweep } = await import('../photosphere');

let passed=0,failed=0;
async function test(name:string,fn:()=>Promise<void>){
  try{await fn();passed++;console.log(`PASS ${name}`);}
  catch(e){failed++;console.log(`FAIL ${name}\n     ${(e as Error).message.split('\n')[0]}`);}
}

/** A recording sweep aimed at one dome cell by a 10 degree approach over
 *  0-500 ms, with the video frame callback under the test's control. Returns
 *  the moment the approach ended: silence starts here. With `rvfc:false` the
 *  element has no requestVideoFrameCallback at all - Firefox Android - and the
 *  returned tick drives the 350 ms setInterval fallback instead. */
async function approachAndHold(rvfc=true){
  shift=0;hidden=false;blind=false;intervalFn=null;
  const video=w.document.createElement('video');
  let frame:((now:number,metadata:unknown)=>void)|undefined;
  if(rvfc){
    video.requestVideoFrameCallback=(fn:typeof frame)=>{frame=fn;return 1;};
    video.cancelVideoFrameCallback=()=>{};
  }
  const sweep=new PhotosphereSweep();
  await sweep.start(video,w.document.createElement('canvas'));
  const cell=sweep.cells.find((c)=>c.alt>20&&c.alt<60)!;
  for(let i=10;i>=0;i-=2){
    const ev=new w.Event('deviceorientationabsolute');
    clock+=100;Object.defineProperty(ev,'timeStamp',{value:clock});
    Object.assign(ev,{alpha:(360-(cell.az+i))%360,beta:90+cell.alt,gamma:0,absolute:true});
    w.dispatchEvent(ev);
  }
  sweep.begin();
  // From here the browser sends no orientation event ever again.
  const tick=rvfc
    ? ()=>{clock+=100;frame!(clock,{captureTime:clock,mediaTime:clock,presentationTime:clock,
        expectedDisplayTime:clock,width:640,height:480,presentedFrames:1});}
    : ()=>{clock+=350;intervalFn!();};
  return {sweep,cell,tick,silentFrom:clock};
}

await test('A still phone captures within 1.5 s of the hold, with no sensor event at all',async()=>{
  const {sweep,cell,tick,silentFrom}=await approachAndHold();
  let capturedAfter:number|null=null;
  for(let i=0;i<20&&capturedAfter===null;i++){
    tick();
    if(sweep.cells.find((c)=>c.id===cell.id)?.captured)capturedAfter=clock-silentFrom;
  }
  assert.notEqual(capturedAfter,null,'DEADLOCK: a still phone never captured');
  assert.ok(capturedAfter!<=1500,`took ${capturedAfter} ms of stillness, the acceptance row is 1500`);
  assert.ok(capturedAfter!>=500,`captured after only ${capturedAfter} ms, before the view could settle`);
  // The user must not be told to wiggle the phone while it is capturing.
  assert.ok(sweep.aimTarget,'the aim dot vanished during a successful capture');
  assert.doesNotMatch(sweep.captureCue,/compass/i);
  sweep.stop();
});

await test('A view that is still moving captures nothing, however quiet the sensor is',async()=>{
  const {sweep,tick}=await approachAndHold();
  for(let i=0;i<20;i++){shift++;tick();}   // one pixel per frame, over 2 s
  assert.equal(sweep.frameCount,0,'a moving view was treated as a steady hold');
  sweep.stop();
});

await test('A hidden page captures nothing, because lost is not still',async()=>{
  // The page lifecycle is the signal driven here: visibilityState reports
  // "hidden" from the moment the hold begins. Two independent guards refuse
  // it - the evidence object's sourceHealthy and grabFrame's own visibility
  // check - and this pins the OUTCOME rather than which one fired, because no
  // public observable separates them (cameraBasis falls back to the last
  // sensor basis, so it cannot be used to isolate frameBasis).
  const {sweep,tick}=await approachAndHold();
  hidden=true;
  for(let i=0;i<20;i++)tick();
  assert.equal(sweep.frameCount,0,'a hidden page captured from a stale pose');
  hidden=false;
  sweep.stop();
});

await test('Stopped video captures nothing, and a frame after the gap cannot vouch for it',async()=>{
  const {sweep,tick}=await approachAndHold();
  clock+=2000;                 // the stream stops: no frame callback for 2 s
  assert.equal(sweep.frameCount,0,'a stopped stream captured on its own');
  tick();                      // one frame returns, with nothing watched in between
  assert.equal(sweep.frameCount,0,'a resumed frame vouched for a silence it never saw');
  sweep.stop();
});

await test('A still phone reads the compass as ready at 3 s of stillness, with no sensor event at all',async()=>{
  // The same deadlock one layer up (issue #37): the driver already captures by
  // this point (case 1), but the OLD compassReady read the same silence as
  // staleness and told a still phone to move, which destroys the hold.
  const {sweep,tick}=await approachAndHold();
  for(let i=0;i<30;i++)tick();   // 3 s of vouched-for stillness, no orientation event at all
  assert.equal(sweep.compassReady,true,'a still phone read the compass as stale');
  assert.notEqual(sweep.aimTarget,null,'no aim target after 3 s of stillness');
  assert.doesNotMatch(sweep.captureCue,/Waiting for the compass/,`cue was: "${sweep.captureCue}"`);
  sweep.stop();
});

await test('A page hidden mid-hold reads the compass as lost, not ready',async()=>{
  // hasOrientation stays true forever once a reading has arrived; only a lost
  // source - not the passage of time - may turn compassReady back to false.
  const {sweep,tick}=await approachAndHold();
  for(let i=0;i<30;i++)tick();   // 3 s of vouched-for stillness while visible
  hidden=true;
  assert.equal(sweep.compassReady,false,'a lost source still read as ready');
  hidden=false;
  sweep.stop();
});

await test('A moving view reads the compass as LOST after 3 s of sensor silence',async()=>{
  // The other half of case 5, and the reason compassReady cannot be lifecycle
  // alone. A magnetometer that simply stops - wedged, permission revoked with
  // no lifecycle event, a stuck Chromium pump - fires no event and ends no
  // track, so every lifecycle flag stays true. Only the video can tell that
  // the world kept moving while the readings stopped, and if nothing does,
  // the dome and the aim dot ride a cached direction indefinitely.
  const {sweep,tick}=await approachAndHold();
  for(let i=0;i<30;i++){shift++;tick();}   // one pixel per frame, 3 s, no orientation event
  assert.equal(sweep.compassReady,false,'a dead sensor over a moving view still read as ready');
  assert.equal(sweep.aimTarget,null,'the aim dot survived a lost heading');
  assert.match(sweep.captureCue,/Waiting for the compass/,`cue was: "${sweep.captureCue}"`);
  sweep.stop();
});

await test('A stopped video cannot vouch for the compass either: unknown is not ready',async()=>{
  // Same silence, but now nothing is watching at all - the frame callback has
  // stopped. Stability is UNKNOWN here, not false, and unknown must not read
  // as a vouch any more than a lost source does.
  const {sweep,tick}=await approachAndHold();
  for(let i=0;i<5;i++)tick();              // half a second of vouched stillness
  assert.equal(sweep.compassReady,true,'the hold should be believed while the video is watching');
  clock+=3000;                             // the stream stops; no frame callback at all
  assert.equal(sweep.compassReady,false,'a stopped video vouched for a 3 s silence it never saw');
  assert.match(sweep.captureCue,/Waiting for the compass/,`cue was: "${sweep.captureCue}"`);
  sweep.stop();
});

await test('A camera image that cannot be read for stillness says so, and is counted',async()=>{
  // Unknown stability means the strict rule means a still phone can never
  // capture. Swallowing the read failure leaves the user holding the phone
  // still against a rule that holding still cannot satisfy, with nothing
  // anywhere recording why (spec 4.3).
  const {sweep,tick}=await approachAndHold();
  blind=true;
  for(let i=0;i<10;i++)tick();
  assert.equal(sweep.frameCount,0,'a blind driver captured anyway');
  assert.doesNotMatch(sweep.captureCue,/Hold the phone still/,'the user was told to do the one thing that cannot work');
  assert.match(sweep.captureCue,/can’t read the camera image/,`cue was: "${sweep.captureCue}"`);
  assert.match(sweep.captureCue,/Move the phone/,`cue was: "${sweep.captureCue}"`);
  const diagnostic=JSON.parse(sweep.alignmentReport());
  assert.ok(diagnostic.stillnessReadFailures>=5,
    `the alignment report recorded ${diagnostic.stillnessReadFailures} read failures`);
  blind=false;
  sweep.stop();
});

await test('A still phone captures on the interval fallback, with no requestVideoFrameCallback',async()=>{
  // Firefox Android has no requestVideoFrameCallback, so the whole rVFC path
  // above never runs there. The fallback used to pass NO stability at all,
  // which left the strict rule in force and the original deadlock intact on
  // every browser without that API.
  const {sweep,cell,tick,silentFrom}=await approachAndHold(false);
  assert.ok(intervalFn,'the interval fallback was never started');
  let capturedAfter:number|null=null;
  for(let i=0;i<5&&capturedAfter===null;i++){
    tick();
    if(sweep.cells.find((c)=>c.id===cell.id)?.captured)capturedAfter=clock-silentFrom;
  }
  assert.notEqual(capturedAfter,null,'DEADLOCK: a still phone never captured on the fallback path');
  assert.ok(capturedAfter!<=1500,`took ${capturedAfter} ms of stillness, the acceptance row is 1500`);
  assert.ok(capturedAfter!>=500,`captured after only ${capturedAfter} ms, before the view could settle`);
  sweep.stop();
});

console.log(`photosphereStillnessDom.test: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};
if(failed)process.exitCode=1;
