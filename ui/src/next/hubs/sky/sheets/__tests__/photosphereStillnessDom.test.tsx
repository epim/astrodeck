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
// The interval fallback consults the element's media state: a paused or
// frozen element is not delivering frames however often the timer fires.
let mediaTime=0,paused=false;
Object.defineProperty(w.HTMLVideoElement.prototype,'currentTime',{get:()=>mediaTime,configurable:true});
Object.defineProperty(w.HTMLVideoElement.prototype,'paused',{get:()=>paused,configurable:true});
Object.defineProperty(w.HTMLVideoElement.prototype,'ended',{get:()=>false,configurable:true});
Object.defineProperty(w.HTMLVideoElement.prototype,'readyState',{get:()=>2,configurable:true});

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
const track={stop(){},getSettings:()=>({deviceId:'main'}),addEventListener(){},readyState:'live',muted:false};
Object.defineProperty(w.navigator,'mediaDevices',{value:{
  enumerateDevices:async()=>[{kind:'videoinput',deviceId:'main',label:'Back main wide camera'}],
  getUserMedia:async()=>({getTracks:()=>[track],getVideoTracks:()=>[track]}),
},configurable:true});
let clock=10000;
Object.defineProperty(performance,'now',{value:()=>clock,configurable:true});
Object.defineProperty(Date,'now',{value:()=>clock,configurable:true});

const { PhotosphereSweep } = await import('../photosphere');
// The capture's own azimuth convention, so the pose an outlier case reads back
// out of the alignment report is measured the way the driver measured it.
const { skyAngles } = await import('../photosphereGeometry');

let passed=0,failed=0;
async function test(name:string,fn:()=>Promise<void>){
  try{await fn();passed++;console.log(`PASS ${name}`);}
  catch(e){failed++;console.log(`FAIL ${name}\n     ${(e as Error).message.split('\n')[0]}`);}
}

/** A recording sweep aimed at one dome cell by a 10 degree approach, with the
 *  camera watching the whole time: one video frame after each orientation
 *  event, the scene shifting a pixel each time so the video sees the motion
 *  the sensor reports.
 *  The schedule, because the assertions below depend on it: six readings, each
 *  one `aim` advance (100 ms) plus one frame tick after the one before it - so
 *  200 ms apart on the rVFC path and 450 ms apart on the interval fallback -
 *  except the LAST, delivered with no advance of its own so that it lands on
 *  the frame tick before it, `step` degrees in exactly one frame interval
 *  (100 ms with rVFC, 350 ms on the fallback). The approach therefore spans
 *  900 ms (rVFC) or 2150 ms (fallback) from the first reading to the last, and
 *  that last reading is where silence begins: it is the returned `silentFrom`.
 *  With `rvfc:false` the element has no requestVideoFrameCallback at all -
 *  Firefox Android - and the returned tick drives the 350 ms setInterval
 *  fallback instead, with the media clock advancing unless a test freezes it.
 *  `step` is the size of the LAST orientation step in degrees (2 by default);
 *  at 10 on the rVFC path that final pair is 100 ms and 10 degrees, inside
 *  JITTER_GAP_MS and past JITTER_SEPARATION_DEG, which is the only way this
 *  harness reaches the jitter branch at all.
 *  `lag` models a camera PIPELINE DELAY, and it is the default for the returned
 *  tick's own optional argument: the frame is presented at `clock`, as before,
 *  but carries `captureTime = clock - lag` - the instant the camera saw the
 *  scene in it. The scene content stays tied to the tick, as it was, so `shift`
 *  still describes what the camera saw at the moment the frame was captured.
 *  At the default 0 every frame is stamped when it is presented and nothing
 *  above this line changes. */
async function approachAndHold(rvfc=true,step=2,lag=0){
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
  // The interval fallback is handed no frame metadata at all, so a pipeline
  // delay is not observable there and its tick ignores the argument.
  const tick=rvfc
    ? (lagMs=lag)=>{clock+=100;mediaTime+=0.1;frame!(clock,{captureTime:clock-lagMs,mediaTime,presentationTime:clock,
        expectedDisplayTime:clock,width:640,height:480,presentedFrames:1});}
    : (_lagMs=lag)=>{clock+=350;if(!paused)mediaTime+=0.35;intervalFn!();};
  const aim=(offset:number,advanceMs=100)=>{
    const ev=new w.Event('deviceorientationabsolute');
    clock+=advanceMs;Object.defineProperty(ev,'timeStamp',{value:clock});
    Object.assign(ev,{alpha:(360-(cell.az+offset))%360,beta:90+cell.alt,gamma:0,absolute:true});
    w.dispatchEvent(ev);
  };
  // begin() gates on compassReady, so the scan can only start once the first
  // reading has arrived - a sweep begun before it silently never records, and
  // every case that only asserts "nothing was captured" then passes for the
  // wrong reason. From there the camera watches a RECORDING sweep, as on a
  // phone; grabFrame refuses without an accepted pose, so nothing is captured
  // during the approach.
  aim(10);sweep.begin();shift++;tick();
  for(let i=8;i>=step;i-=2){aim(i);shift++;tick();}
  // No advance of its own, so the last step spans exactly the frame tick above:
  // `step` degrees in one frame interval, which is the only pair this harness
  // produces that is close enough together to reach the jitter branch.
  aim(0,0);shift++;
  // From here the browser sends no orientation event ever again.
  return {sweep,cell,tick,aim,silentFrom:clock};
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
  // The refutation is only available where the video can speak for the pair of
  // readings it judges: `view.stillSince <= previous.at`. The approach above is
  // still moving right up to its last reading, so a jump there is taken as the
  // movement it looks like (the documented limit in photospherePose) - the
  // outlier this case is about is the one that arrives after the phone has come
  // to rest. So: hold still until the view has settled, let the sensor confirm
  // the direction once, and only then deliver the bad reading.
  const {sweep,cell,tick,aim}=await approachAndHold();
  for(let i=0;i<6;i++)tick();                   // 600 ms: the view has settled, and it settled BEFORE the reading below
  assert.equal(sweep.frameCount,0,'the cell was captured before the outlier arrived, so nothing here is about the outlier');
  aim(0);                                       // the phone confirms the direction it is already holding
  const good=clock;
  clock+=40;aim(20);                            // one reading 20 degrees off, 140 ms after it, phone unmoved
  let capturedAfter:number|null=null;
  for(let i=0;i<20&&capturedAfter===null;i++){
    tick();
    if(sweep.cells.find((c)=>c.id===cell.id)?.captured)capturedAfter=clock-good;
  }
  assert.notEqual(capturedAfter,null,'the outlier blocked the hold');
  assert.ok(capturedAfter!<=1500,`took ${capturedAfter} ms after the good reading`);
  // "Captured" alone cannot tell the two outcomes apart: a frame worn 20 degrees
  // off still covers this cell, because the image is wider than the cell. The
  // pose the frame was worn at is the only thing that separates a refuted
  // outlier from an accepted one, and the alignment report carries it.
  const wornAz=skyAngles(JSON.parse(sweep.alignmentReport()).samples.at(-1).sensorBasis.forward).az;
  const off=Math.abs(((wornAz-cell.az+540)%360)-180);   // wrap-safe, for a cell near due north
  assert.ok(off<1,
    `the photograph was worn at ${wornAz.toFixed(1)} degrees, ${off.toFixed(1)} off the pre-outlier ${cell.az}`);
  sweep.stop();
});

await test('A camera that reports its frames 250 ms late still captures a still phone, because the witness is timed by capture, not by callback',async()=>{
  // A 250 ms pipeline delay: every frame is PRESENTED a quarter of a second
  // after the camera saw the scene in it. The phone comes to rest as its last
  // reading arrives, so the frames that watched it stop are presented 200 and
  // 300 ms AFTER that reading. Timed by the callback, the break straddling the
  // reading is stamped 200 ms after it - past CONTINUITY_SLOP_MS, so it reads
  // as a movement the phone made after the last thing the sensor said, and
  // nothing can ever vouch for that reading again: the sensor is silent and no
  // later stillness may reach back across a movement (the P1 case above). The
  // phone deadlocks with the same symptom as issue #37 - a still phone that can
  // never capture - from a different cause, and one it has no control over:
  // how long its own camera takes to hand a frame over. Timed by capture, and
  // this is the whole of change 1, the same break lands 50 ms BEFORE the
  // reading, which is where it happened - it is the tail of the approach.
  const {sweep,cell,tick,silentFrom}=await approachAndHold(true,2,250);
  // The turn ends with the reading; three of its frames are still in flight,
  // and the last pair of them straddles the reading in capture time (-50 ms,
  // +50 ms) while trailing it by 200 ms in callback time.
  for(let i=0;i<3;i++){shift++;tick();}
  let capturedAfter:number|null=null;
  for(let i=0;i<20&&capturedAfter===null;i++){
    tick();
    if(sweep.cells.find((c)=>c.id===cell.id)?.captured)capturedAfter=clock-silentFrom;
  }
  assert.notEqual(capturedAfter,null,'DEADLOCK: a still phone never captured behind a 250 ms camera pipeline');
  assert.ok(capturedAfter!<=1500,`took ${capturedAfter} ms of stillness, the acceptance row is 1500`);
  assert.ok(capturedAfter!>=500,`captured after only ${capturedAfter} ms, before the view could settle`);
  sweep.stop();
});

console.log(`photosphereStillnessDom.test: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};
if(failed)process.exitCode=1;
