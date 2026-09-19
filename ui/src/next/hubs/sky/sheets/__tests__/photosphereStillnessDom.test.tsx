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
// `paused` and `stalled` are the two different ways that happens, and they have
// to be separable: a paused element SAYS it is paused, while a stalled one
// still reports playing with data and only its clock has stopped - the case
// review 15 names ("a previously playing element stalled on its last decoded
// image"). Only `paused` reaches the element's own flags; `stalled` freezes
// nothing but the media clock.
// The gate has four more conditions, and each is a different way for a running
// media clock to be worth nothing: `ended` is the stream at its end (an element
// at its end keeps its last picture and its clock keeps reading the duration),
// `starved` is an element that has metadata but no decoded frame to show
// (readyState 1, HAVE_METADATA), and `trackLost` and `trackMuted` are the TRACK
// behind the element rather than the element - ended, or muted because the OS
// handed the camera to something else. They are separate flags from `paused`
// and `stalled` on purpose: each one is set with the media clock STILL RUNNING,
// so the condition under test is the only thing that can refuse the tick.
let mediaTime=0,paused=false,stalled=false,ended=false,starved=false,trackLost=false,trackMuted=false;
Object.defineProperty(w.HTMLVideoElement.prototype,'currentTime',{get:()=>mediaTime,configurable:true});
Object.defineProperty(w.HTMLVideoElement.prototype,'paused',{get:()=>paused,configurable:true});
Object.defineProperty(w.HTMLVideoElement.prototype,'ended',{get:()=>ended,configurable:true});
Object.defineProperty(w.HTMLVideoElement.prototype,'readyState',{get:()=>starved?1:2,configurable:true});

// A textured scene, so a one-pixel shift is measurable rather than invisible:
// a 32-step luminance gradient with a bright block, generated at whatever size
// the caller asks for. `shift` slides it; `hidden` drives the page lifecycle.
// `flat` is the same camera, working perfectly, pointed at a patch of smooth
// sky: one constant luminance, so the comparison grid's spatial gradient is 0,
// far under GRADIENT_FLOOR, and the witness can say nothing about a shift -
// `stableAt` is null and `witness` is 'featureless' (issue #41). It overrides
// `shift`, because a scene with no structure in it cannot show one.
// `faint` is the third camera: a one-cell checkerboard of plus or minus one
// luma level on the base it is set to, so the normalised gradient is exactly
// 2/base in both directions - which makes the base a dial on G. It is how a
// case puts the view ON GRADIENT_FLOOR rather than far under it the way `flat`
// does, and the point is what happens when it wanders across (issue #75). Like
// `flat` it overrides `shift`: a change of base is a change of exposure, not of
// aim.
// `thin` is the fourth: a flat sky over the top seven eighths with a
// three-column canopy pattern in the bottom eighth, the shape of a mostly-sky
// upper band. Its horizontal pairs are ZERO on 21 of the 24 rows, which is what
// makes it the scene sensor noise lifts (issue #62) - unlike `faint`, whose
// pairs are all the same size and which noise therefore does not lift at all.
// Measured through the module: G = 0.011688, under GRADIENT_FLOOR.
//
// EVERY MODE IS DEFINED ON THE CELL, not on the pixel. The driver draws the
// preview at CELL_SAMPLES samples across each grid cell (issue #62 round 1), so
// a scene written in pixels would mean a different scene at a different buffer
// size - `shift` would slide by a third of a cell and `faint` would average its
// own checkerboard away nine to one. Written on the cell, the STILLNESS grid
// this produces is bit-identical at 32x24 and at 96x72, which is what lets the
// driver's sampling change without moving a single case in this file.
// That identity is about the stillness grid and NOTHING ELSE. The same function
// also paints the 320x240 CAPTURE canvas, and there the new pixels are not the
// old ones: `col` went from `round(x*31/319)` to `floor(x/10)` and the bright
// block's lower edge moved from y 160 to y 169, so at shift 5 about 96 percent
// of the buffer differs, by 30 luma levels on average and 127 at worst. That
// buffer feeds `registerFrame`, the overlap gate and `panorama.add`. Measured
// through those functions, the overlap RESULT is the same on every frame tried
// and the search path differs on one of four (71 evaluations against 1), and
// the 24-row horizon column differs by up to 23 levels in one row. No assertion
// in this file or in photosphereCaptureDom depends on which of the two it is -
// checked by running both suites with the capture canvas put back on its old
// pixels: 41/41 and 26/26 either way. The evidence on the capture side is that
// experiment, not the identity argument above.
let shift=0,hidden=false,flat=false,faint:number|null=null,thin=false;
// The noise a real sensor leaves, in luma levels AT THE CELL - after the box
// average, which is the only scale at which "how much noise" is a property of
// the camera rather than of the buffer size the driver happened to pick. The
// per-sample amplitude is scaled up by the sampling factor to match, so a given
// `cellNoise` is the same camera whatever the driver asks for; that is what
// makes the one-sample-per-cell comparison at the bottom a fair one.
// Deterministic in (frame, x, y), so nothing here passes or fails by luck.
let cellNoise=0,noiseFrame=0;
const noiseAt=(x:number,y:number,width:number)=>{
  if(!cellNoise)return 0;
  let h=Math.imul((noiseFrame*73856093)^(x*19349663)^(y*83492791),2654435761);
  let sum=0;
  for(let k=0;k<12;k++){h^=h<<13;h|=0;h^=h>>>17;h^=h<<5;h|=0;sum+=(h>>>0)/4294967296;}
  return cellNoise*(width/GRID_W)*(sum-6);
};
const scenePixel=(x:number,y:number,width:number,height:number)=>{
  const cx=Math.floor(x*GRID_W/width),cy=Math.floor(y*GRID_H/height);
  const base=faint!==null?faint+((cx+cy)%2?-1:1)
    :flat?128
    :thin?(cy<Math.round(GRID_H*7/8)?150:(cx%3===0?96:116))
    :(()=>{const col=(cx+shift)%GRID_W;
       return col>=10&&col<=17&&cy>=Math.floor(GRID_H/3)&&cy<=Math.floor(2*GRID_H/3)?180:40+Math.round(col*80/31);})();
  return Math.max(0,Math.min(255,Math.round(base+noiseAt(x,y,width))));
};
// What size the driver last asked the stillness canvas for. A case at the
// bottom reads this: the whole of issue #62 turns on the buffer being finer
// than the grid, and nothing else in this file would notice if it stopped being.
let lastSample:{width:number;height:number}|null=null;
const ctxStub={ drawImage(){},
  getImageData:(_x:number,_y:number,width:number,height:number)=>{
    if(width!==320&&height!==320)lastSample={width,height};
    const data=new Uint8ClampedArray(width*height*4);
    for(let y=0;y<height;y++)for(let x=0;x<width;x++){
      const v=scenePixel(x,y,width,height),i=(y*width+x)*4;
      data[i]=data[i+1]=data[i+2]=v;data[i+3]=255;
    }
    return {data};
  }, createImageData:(width:number,height:number)=>({data:new Uint8ClampedArray(width*height*4)}), putImageData(){},
};
// `blind` fails ONLY the stillness canvas. The capture canvas losing its
// context is a different defect with a cue of its own, and a stub that failed
// both could not tell the two apart. It is told apart by SIZE, so this tracks
// whatever size the driver draws the stillness sample at (issue #62 round 1
// moved it off the grid). The capture canvas is 320x240, so the two are the
// same size at CELL_SAMPLES exactly 10 and telling them apart this way holds
// only below that - the assertion below says so out loud rather than letting a
// raised CELL_SAMPLES silently blind both canvases at once (the check itself
// sits with the LUMA_W/LUMA_H declaration below, which is where those exist).
let blind=false;
w.HTMLCanvasElement.prototype.getContext=function(this:{width:number;height:number}){
  return blind&&this.width===LUMA_W&&this.height===LUMA_H?null:ctxStub;
};
w.HTMLCanvasElement.prototype.toDataURL=()=>'data:image/png;base64,';
Object.defineProperty(w.document,'visibilityState',{get:()=>hidden?'hidden':'visible',configurable:true});
// The interval fallback is a path under test, not noise to be swallowed: keep
// the callback so a test without requestVideoFrameCallback can drive it.
let intervalFn:(()=>void)|null=null;
g.setInterval=(fn:()=>void)=>{intervalFn=fn;return 1;};
g.clearInterval=()=>{intervalFn=null;};
// Getters, not fields: the two track states are driven by the flags above, and
// `addEventListener` stays a no-op, so a track that has ENDED here fires no
// 'ended' event and `sourceHealthy` never learns of it. That is the point - it
// isolates the media gate's own track test from the lifecycle flag, which has
// cases of its own.
const track={stop(){},getSettings:()=>({deviceId:'main'}),addEventListener(){},
  get readyState(){return trackLost?'ended':'live';},get muted(){return trackMuted;}};
Object.defineProperty(w.navigator,'mediaDevices',{value:{
  enumerateDevices:async()=>[{kind:'videoinput',deviceId:'main',label:'Back main wide camera'}],
  getUserMedia:async()=>({getTracks:()=>[track],getVideoTracks:()=>[track]}),
},configurable:true});
let clock=10000;
Object.defineProperty(performance,'now',{value:()=>clock,configurable:true});
Object.defineProperty(Date,'now',{value:()=>clock,configurable:true});

const { PhotosphereSweep, OVERHEAD_BAND } = await import('../photosphere');
// The capture's own azimuth convention, so the pose an outlier case reads back
// out of the alignment report is measured the way the driver measured it.
const { skyAngles } = await import('../photosphereGeometry');
// The witness's own scale, so a case can state where its fixture sits against
// the floor instead of asserting a number copied into a comment.
const { VisualStability, gradient, noiseGradient, GRADIENT_FLOOR, GRADIENT_HYSTERESIS, GRID_W, GRID_H, CELL_SAMPLES }
  = await import('../photosphereStability');
/** The size the driver draws the stillness sample at, derived the same way the
 *  driver derives it, so this file cannot drift from it silently. */
const LUMA_W=GRID_W*CELL_SAMPLES, LUMA_H=GRID_H*CELL_SAMPLES;
// `blind` tells the stillness canvas from the 320x240 capture canvas by size,
// and at CELL_SAMPLES 10 they are the same size. Fail loudly here rather than
// silently blinding both at once in one case and neither in another.
if(LUMA_W===320&&LUMA_H===240)throw new Error(
  `CELL_SAMPLES ${CELL_SAMPLES} makes the stillness canvas ${LUMA_W}x${LUMA_H}, the same size as the `
  +'capture canvas, and `blind` can no longer tell them apart by size');

/** The dimensions the WITNESS was last handed, which is a different fact from
 *  the size the canvas was drawn at and the one that actually decides whether
 *  `noiseGradient` can do anything. A driver that drew at 96x72 and then called
 *  `observe(at, luma, GRID_W, GRID_H)` would make the correction inert again
 *  while every canvas-side assertion stayed green - `observe`'s own guard is
 *  `luma.length < width*height`, and 6912 >= 768 passes it. So the case below
 *  asserts both ends: what the driver asked the canvas for, and what it told the
 *  module the buffer was. */
let lastObserved:{width:number;height:number}|null=null;
/** Read through a call, so narrowing the module-level `let` to null at the top
 *  of a case does not make every later use of it unreachable to the checker. */
const observedSize=()=>lastObserved;
const sampledSize=()=>lastSample;
const realObserve=VisualStability.prototype.observe;
VisualStability.prototype.observe=function(this:InstanceType<typeof VisualStability>,
    at:number,luma:Uint8Array|Uint8ClampedArray,width:number,height:number):void{
  lastObserved={width,height};
  realObserve.call(this,at,luma,width,height);
};

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
 *  JITTER_GAP_MS and past JITTER_SEPARATION_DEG, so the APPROACH enters the
 *  jitter branch. It is not the only way in: a case can deliver a pair of its
 *  own with `aim`, which is what the outlier case below does at the default
 *  step, and that one reaches the branch through a refuted pair rather than a
 *  moving one.
 *  `lag` models a camera PIPELINE DELAY, and it is the default for the returned
 *  tick's own optional argument. The two paths express the same delay with the
 *  two different things each of them has:
 *   - rVFC: the frame is presented at `clock`, as before, but carries
 *     `captureTime = clock - lag` - the instant the camera saw the scene in it.
 *     The scene content stays tied to the tick, so `shift` still describes what
 *     the camera saw at the moment the frame was captured.
 *   - the interval fallback: there is no frame metadata to carry a capture time,
 *     so the delay shows in the CONTENT instead. The tick presents the scene as
 *     it stood `lag` ms before it fired - the picture the camera took then, now
 *     arriving - while the driver still stamps the observation with the read
 *     instant, which is the whole of issue #48. The harness can do this because
 *     `shift` is its own state: `scene` below samples it at every tick, so the
 *     reconstruction is piecewise constant between ticks and a delay presents
 *     the scene as of the last tick at or before the capture instant, i.e.
 *     `ceil(lag / 350)` ticks back through the 350 ms hold. The override lasts
 *     only for the duration of the tick, so `beat` and anything else that drives
 *     `intervalFn` directly sees the live scene.
 *     The consequence, because the cases below are read as arithmetic: this has
 *     NO sub-interval resolution. It rounds every delay UP to a whole number of
 *     intervals - 1 ms and 350 ms are the same fixture, 351 ms is the next one
 *     - so a case here can sit on a band's edge but can never exhibit a band's
 *     interior, and the number in a case's name is a plausible phone rather
 *     than the quantity under test. Modelling a delay finer than this would
 *     mean timestamping `shift` itself, which is a `let` several cases mutate
 *     directly.
 *  At the default 0 every frame is stamped when it is presented, the newest
 *  sample is the tick's own, and nothing above this line changes. */
async function approachAndHold(rvfc=true,step=2,lag=0){
  shift=0;hidden=false;blind=false;flat=false;faint=null;thin=false;cellNoise=0;noiseFrame=0;intervalFn=null;mediaTime=0;paused=false;stalled=false;ended=starved=trackLost=trackMuted=false;
  const video=w.document.createElement('video');
  let frame:((now:number,metadata:unknown)=>void)|undefined;
  if(rvfc){
    video.requestVideoFrameCallback=(fn:typeof frame)=>{frame=fn;return 1;};
    video.cancelVideoFrameCallback=()=>{};
  }
  const sweep=new PhotosphereSweep();
  await sweep.start(video,w.document.createElement('canvas'));
  const cell=sweep.cells.find((c)=>c.alt>20&&c.alt<60)!;
  // What the scene was when, seeded before the first tick so a delay reaching
  // back past the start of the fixture has something to present.
  const scene:{at:number;shift:number}[]=[{at:clock,shift}];
  const tick=rvfc
    ? (lagMs=lag)=>{clock+=100;mediaTime+=0.1;frame!(clock,{captureTime:clock-lagMs,mediaTime,presentationTime:clock,
        expectedDisplayTime:clock,width:640,height:480,presentedFrames:1});}
    : (lagMs=lag)=>{
        clock+=350;if(!paused&&!stalled)mediaTime+=0.35;
        scene.push({at:clock,shift});
        const shown=[...scene].reverse().find((s)=>s.at<=clock-lagMs)??scene[0];
        const live=shift;shift=shown.shift;
        try{intervalFn!();}finally{shift=live;}
      };
  /** Present the SAME frame to the callback again: the wall clock moves on, so
   *  the 350 ms grab cadence lets a second grab through, but the media clock
   *  does not - the driver is handed a picture it has already been handed.
   *  700 ms, so the second grab clears the 600 ms registration throttle too and
   *  the only thing left standing between it and the mosaic is frame identity. */
  const represent=rvfc?()=>{clock+=700;frame!(clock,{captureTime:clock,mediaTime,presentationTime:clock,
      expectedDisplayTime:clock,width:640,height:480,presentedFrames:1});}:()=>{};
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
  // `step` degrees in one frame interval, which is the only pair the APPROACH
  // produces close enough together to reach the jitter branch. A case may make
  // its own, and the outlier case does.
  aim(0,0);shift++;
  // From here the browser sends no orientation event ever again.
  return {sweep,cell,tick,aim,represent,silentFrom:clock};
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
  // 25 ticks and not 5: at 5 the reading was 500 ms old and SENSOR_SILENCE_MS
  // made it ready on freshness alone, whatever the video said, so the starting
  // state was not the vouched hold this case claims to stop from (issue #49
  // item 4). Instants are the timeline the three cases below spell out: frames
  // at s+100, s+200, ..., the still run opens at s+100 and settles at the frame
  // at s+600, and the break {from: s, to: s+100} reaches the reading at s
  // inside CONTINUITY_SLOP_MS = 150. So at s+2500 the reading is 2500 ms old,
  // past SENSOR_SILENCE_MS = 2000, and the video is the only thing that can
  // still be holding it. Then 3000 ms with no frame at all: the newest frame is
  // 3000 ms old against STALE_FRAME_MS = 1000 and the witness reads 'stale'.
  // Mutation: make `viewVouchesFor` return false. Observed red on the FIRST
  // assertion: "the hold should be believed while the video is watching".
  const {sweep,tick,silentFrom}=await approachAndHold();
  for(let i=0;i<25;i++)tick();
  assert.equal(clock-silentFrom,2500,'the vouched hold did not last past SENSOR_SILENCE_MS, so freshness could be holding the reading');
  assert.equal(sweep.compassReady,true,'the hold should be believed while the video is watching');
  clock+=3000;                             // the stream stops; no frame callback at all
  assert.equal(clock-silentFrom,5500);
  assert.equal(sweep.compassReady,false,'a stopped video vouched for a 3 s silence it never saw');
  assert.match(sweep.captureCue,/Waiting for the compass/,`cue was: "${sweep.captureCue}"`);
  sweep.stop();
});

/** The three cases below share one timeline, so it is computed once here.
 *  `silentFrom` (call it s) is the last orientation event of the approach, and
 *  it shares its instant with the frame tick before it. The harness's rVFC tick
 *  advances 100 ms and delivers one frame, so frames land at s+100, s+200, ...
 *  The scene is shifted once more after that last reading, so the frame at
 *  s+100 is the last MOVING one; the still run opens there and every frame
 *  after it is identical. The witness settles SETTLE_MS = 500 ms later, at the
 *  frame at s+600, and the break it keeps is {from: s, to: s+100}, which
 *  reaches back to the reading at s inside CONTINUITY_SLOP_MS = 150 - so from
 *  s+600 the video vouches for the reading itself and not merely for now.
 *  25 ticks therefore land at s+2500 with the cell captured (the grab at s+800
 *  is the first one with a settled view; `capturedAfter` in the first case of
 *  this file measures the same 800 ms) and with the silence at 2500 ms, past
 *  SENSOR_SILENCE_MS = 2000 - so `compassReady` there is the video vouching,
 *  never freshness, which is what makes it a usable starting state for a case
 *  about what happens when the video stops being able to say anything. */
await test('A silent hold on featureless sky keeps the compass ready and says the view has nothing to track (issue #41)',async()=>{
  // 25 textured ticks to s+2500, then 30 featureless ticks to s+5500: the
  // camera is still delivering (each tick is a frame) and every frame has a
  // gradient of 0, so the witness reads 'featureless' rather than 'stale'.
  // Mutation: reverting `compassReady`/`tiltReady` to `this.vouched(...)` alone
  // - the gate before this change - makes `compassReady` false at s+5500.
  // Observed red: "a featureless view read as a lost compass (issue #41)".
  const {sweep,tick,silentFrom}=await approachAndHold();
  for(let i=0;i<25;i++)tick();
  assert.equal(clock-silentFrom,2500,'the textured hold did not last the 2500 ms every instant below is measured from');
  assert.equal(sweep.compassReady,true,'the hold was not ready before the view went blank, so nothing below is about the view');
  const captured=sweep.frameCount;
  assert.ok(captured>0,'the textured hold captured nothing, so the flat phase below cannot show capture stopping');
  flat=true;
  for(let i=0;i<30;i++)tick();
  assert.equal(clock-silentFrom,5500);
  assert.equal(sweep.compassReady,true,'a featureless view read as a lost compass (issue #41)');
  assert.notEqual(sweep.aimTarget,null,'the dome blanked on a view that was merely unjudgeable');
  assert.match(sweep.captureCue,/nothing to track/,`cue was: "${sweep.captureCue}"`);
  assert.doesNotMatch(sweep.captureCue,/move the phone/i,`cue was: "${sweep.captureCue}"`);
  // Capture is NOT relaxed by any of this, and this is the assertion that says
  // so: the relaxed rule needs a continuity, which a featureless frame never
  // produces, and the strict rule needs a sample within 250 ms, which a 5.5 s
  // silence does not have. The dome and the cue changed; the mosaic did not.
  assert.equal(sweep.frameCount,captured,'a featureless view was captured under a reading nothing witnessed');
  flat=false;
  sweep.stop();
});

await test('Stopped video during silence still reads the compass as lost, however featureless its last frame was',async()=>{
  // The featureless branch must not swallow the stale one. `stalled` is the
  // harness's frozen media clock and the rVFC path never consults it - there
  // the callback IS the delivery - so a stopped stream is modelled here the way
  // the browser stops it: the callback no longer fires.
  // Instants: 25 textured ticks to s+2500, then 5 featureless ticks to s+3000
  // so the newest frame is one the witness cannot judge, then 3000 ms with no
  // frame at all. At s+6000 the newest frame is 3000 ms old against
  // STALE_FRAME_MS = 1000, so the witness reads 'stale'.
  // Mutation: swapping the first two lines of `VisualStability.witness` so the
  // featureless test runs before the staleness test. Observed red: "a stopped
  // video vouched for a silence it never saw, behind the featureless branch".
  const {sweep,tick,silentFrom}=await approachAndHold();
  for(let i=0;i<25;i++)tick();
  flat=true;
  for(let i=0;i<5;i++)tick();
  assert.equal(sweep.compassReady,true,'the featureless hold was already lost, so stopping the video below proves nothing');
  clock+=3000;
  assert.equal(clock-silentFrom,6000);
  assert.equal(sweep.compassReady,false,'a stopped video vouched for a silence it never saw, behind the featureless branch');
  assert.equal(sweep.aimTarget,null,'the aim dot survived a camera that had stopped');
  assert.match(sweep.captureCue,/Waiting for the compass/,`cue was: "${sweep.captureCue}"`);
  flat=false;
  sweep.stop();
});

await test('A reading delivered over a featureless view is not held by it: the compass still reads lost',async()=>{
  // A pan over blank sky is invisible to the video, so the movement here is
  // reported by the SENSOR: the phone turns 10 degrees, says so once, and goes
  // quiet again over the same blank sky. What the featureless branch stands on
  // is a memory of ONE reading - the one that was still standing at the last
  // moment the view could be judged - and this new reading arrived after that
  // moment, so nothing ever witnessed it and it expires like any other.
  // Instants: 25 textured ticks to s+2500 (the last judgeable frame, and so the
  // only instant the memory can hold), 5 featureless ticks to s+3000, `aim`
  // advances 100 ms and delivers the reading at s+3100, then 25 featureless
  // ticks to s+5600 - 2500 ms of silence, past SENSOR_SILENCE_MS = 2000, with
  // the memory standing at s+2500, which is before the reading at s+3100.
  // This case passes before the change too; it is what stops the change
  // over-reaching. Mutation: dropping `stoodAt >= at` from `readingStands`, so
  // any old memory holds any reading. Observed red: "a featureless view held a
  // reading it never witnessed".
  const {sweep,tick,aim,silentFrom}=await approachAndHold();
  for(let i=0;i<25;i++)tick();
  flat=true;
  for(let i=0;i<5;i++)tick();
  aim(10);
  const turnedAt=clock;
  assert.equal(turnedAt-silentFrom,3100,'the turn did not land where the instants below assume');
  for(let i=0;i<25;i++)tick();
  assert.equal(clock-turnedAt,2500);
  assert.equal(sweep.compassReady,false,'a featureless view held a reading it never witnessed');
  assert.equal(sweep.aimTarget,null,'the aim dot rode a direction nothing can vouch for');
  // What the cue says in this state is the subject of its own case below.
  flat=false;
  sweep.stop();
});

await test('A reading the video last saw MOVING is not held by the blank sky that follows',async()=>{
  // Review A1. The memory `readingStands` reaches back to has to be a memory of
  // the VIDEO vouching. Written on `vouched` instead, its first disjunct is
  // bare freshness, so a reading needed only to be under SENSOR_SILENCE_MS old
  // at some judgeable frame - whatever that frame said about the view - and a
  // reading the video had just measured as MOVING was then held for as long as
  // the blank sky lasted. The field shape is the one SENSOR_SILENCE_MS's own
  // comment names: a wedged magnetometer while the user pans up out of the
  // treeline into smooth sky.
  // Instants: 10 ticks to s+1000, settled (the run opens at s+100 and settles
  // at s+600), so the video vouches for the reading at s and the memory stands
  // at s+1000. `aim` then advances 100 ms and delivers a reading at s+1100, and
  // ONE textured frame at s+1200 with the scene moved 4 px makes the witness
  // read 'moving' at the only judgeable frame that reading will ever see. Then
  // 30 featureless ticks to s+4200: 3100 ms of silence, past
  // SENSOR_SILENCE_MS = 2000, with the memory still standing at s+1000, which
  // is before the reading at s+1100.
  // Mutation: write the memory on `this.vouched(this.headingAt)` /
  // `this.vouched(this.tiltAt)` again, as it was before this round. Observed
  // red: "a reading the video measured as moving was held by the blank sky".
  const {sweep,tick,aim,silentFrom}=await approachAndHold();
  for(let i=0;i<10;i++)tick();
  assert.equal(sweep.compassReady,true,'the approach never settled, so there is no memory for the rest to be about');
  aim(10);
  shift+=4;tick();
  assert.equal(clock-silentFrom,1200,'the moving frame did not land where the instants below assume');
  flat=true;
  for(let i=0;i<30;i++)tick();
  assert.equal(clock-silentFrom,4200);
  assert.equal(sweep.compassReady,false,'a reading the video measured as moving was held by the blank sky');
  assert.equal(sweep.aimTarget,null,'the aim dot rode a heading nothing ever vouched for');
  flat=false;
  sweep.stop();
});

await test('A lost compass over a featureless view names the sky, not the compass (issue #63)',async()=>{
  // The state the case above leaves behind, read from the other end: the dome
  // is blank because the reading is genuinely lost, and the reason it is lost
  // is that the view cannot witness anything. 'Waiting for the compass. Keep
  // the camera open and move the phone gently' is wrong twice over here - it
  // names only half of what is known, and over blank sky the movement it asks
  // for buys 2 s of freshness and then loses the reading again, because the
  // view still cannot vouch for whatever the phone reports next.
  // Same instants as the case above: the reading lands at s+3100 over an
  // already-featureless view and the cue is read at s+5600, 2500 ms of silence
  // later, with frames still arriving every 100 ms (so the witness reads
  // 'featureless' and not 'stale') and no sample inside `sensorQuiet`'s 250 ms.
  // Mutation: restore the `this.cameraBasis &&` precondition to the single
  // featureless cue branch, so a lost reading falls through to the compass
  // line. Observed red: the assertion that the cue names the sky.
  const {sweep,tick,aim}=await approachAndHold();
  for(let i=0;i<25;i++)tick();
  flat=true;
  for(let i=0;i<5;i++)tick();
  aim(10);
  for(let i=0;i<25;i++)tick();
  assert.equal(sweep.compassReady,false,'the reading was held, so this is not the state the case is about');
  assert.match(sweep.captureCue,/nothing to track/,`cue was: "${sweep.captureCue}"`);
  assert.match(sweep.captureCue,/compass has gone quiet/,`cue was: "${sweep.captureCue}"`);
  assert.doesNotMatch(sweep.captureCue,/move the phone/i,`cue was: "${sweep.captureCue}"`);
  flat=false;
  sweep.stop();
});

/** A sweep with NO absolute bearing at all, pointed straight up: the shape of
 *  the overhead cell, which is one of the low-texture views issue #41 names and
 *  the one place `cameraBasis` hangs off `tiltReady` alone (photosphere.ts:
 *  `this.tiltReady && this.altitude >= 85 ? this.basis : null`).
 *  One RELATIVE event and then frames. `deviceorientation` with
 *  `absolute: false` and no webkitCompassHeading makes `cameraPose` return null
 *  and `ScanPoseSource.accept` refuse it - there is no absolute reading for the
 *  yaw to anchor to - so `hasOrientation` stays false and `compassReady` with
 *  it, for the whole fixture. `cameraElevation` still reads beta 180, gamma 0
 *  as asin(-cos 180) = 90 degrees up, which is the zenith branch of the heading
 *  handler, and that is what sets `basis` and `altitude` here.
 *  Nothing calls `begin()`: that gate is `compassReady`, and nothing in this
 *  fixture needs it - the dome, the bearing readout and `currentBand` all hang
 *  off `tiltReady` before a single frame is recorded, and the frame callback
 *  watches the view whether or not the sweep is recording.
 *  The reading is delivered with no advance of its own, so it lands on the
 *  frame tick before it exactly as `approachAndHold`'s last reading does: that
 *  first frame is the one that records the break the continuity has to reach
 *  back across (a frame with nothing before it breaks at its own instant), and
 *  the reading sits at that same instant. So with the scene held still the run
 *  opens at t0 and settles 500 ms later, and from there the video vouches for
 *  the reading itself. */
async function overheadHold(){
  shift=0;hidden=false;blind=false;flat=false;faint=null;thin=false;cellNoise=0;noiseFrame=0;intervalFn=null;mediaTime=0;paused=false;stalled=false;ended=starved=trackLost=trackMuted=false;
  const video=w.document.createElement('video');
  let frame:((now:number,metadata:unknown)=>void)|undefined;
  video.requestVideoFrameCallback=(fn:typeof frame)=>{frame=fn;return 1;};
  video.cancelVideoFrameCallback=()=>{};
  const sweep=new PhotosphereSweep();
  await sweep.start(video,w.document.createElement('canvas'));
  const tick=()=>{clock+=100;mediaTime+=0.1;frame!(clock,{captureTime:clock,mediaTime,presentationTime:clock,
    expectedDisplayTime:clock,width:640,height:480,presentedFrames:1});};
  const tilt=(advanceMs=100)=>{
    const ev=new w.Event('deviceorientation');
    clock+=advanceMs;Object.defineProperty(ev,'timeStamp',{value:clock});
    Object.assign(ev,{alpha:0,beta:180,gamma:0,absolute:false});
    w.dispatchEvent(ev);
  };
  tick();tilt(0);
  return {sweep,tick,tilt,silentFrom:clock};
}

await test('The overhead cell keeps its tilt reading over a featureless view (issue #41)',async()=>{
  // Review A2: `tiltReady` carries the same rule as `compassReady` and nothing
  // pinned it - reverting it alone left all 111 photosphere cases green. This
  // is the view it matters on: straight up, where the sky fills the frame and
  // there is no bearing at all, so `cameraBasis` and `currentBand` ride
  // `tiltReady` by itself.
  // Instants, from `silentFrom` = t0 (the frame tick the reading lands on):
  // frames at t0+100, t0+200, ... identical, so the still run opens at t0 and
  // settles at t0+500, and the break {from: t0, to: t0} reaches the reading.
  // 25 ticks -> t0+2500, 2500 ms of silence, past SENSOR_SILENCE_MS = 2000, so
  // the video is what is holding the reading there. Then `flat` and 30 ticks ->
  // t0+5500, every frame delivered and featureless, 5500 ms of silence.
  // Mutation: revert `tiltReady` alone to `this.vouched(this.tiltAt)`. Observed
  // red: "the overhead cell lost its tilt over a view that was merely
  // unjudgeable".
  const {sweep,tick,silentFrom}=await overheadHold();
  for(let i=0;i<25;i++)tick();
  assert.equal(clock-silentFrom,2500);
  assert.equal(sweep.compassReady,false,'a bearing arrived, so this case is no longer about tiltReady alone');
  assert.equal(sweep.tiltReady,true,'the tilt reading was not being held before the view went blank');
  flat=true;
  for(let i=0;i<30;i++)tick();
  assert.equal(clock-silentFrom,5500);
  assert.equal(sweep.tiltReady,true,'the overhead cell lost its tilt over a view that was merely unjudgeable');
  assert.equal(sweep.currentBand,OVERHEAD_BAND,`the band readout went to ${sweep.currentBand}`);
  assert.notEqual(sweep.cameraBasis,null,'cameraBasis rides tiltReady at the zenith, and it went null');
  flat=false;
  sweep.stop();
});

await test('A view sitting on GRADIENT_FLOOR holds the compass steady instead of flipping it frame by frame (issue #75)',async()=>{
  // The Major of the whole-branch review, one layer above the stability case
  // that pins the witness. Three correct changes composed into this: the floor
  // was a bare threshold on the current frame (#38); a pair whose predecessor
  // could not witness breaks at its own instant (#49); a reading is held while
  // the witness says 'featureless' (#41). So over a view sitting on the floor
  // the witness answered featureless, moving, featureless, moving, and this
  // getter answered with it - true on the featureless frames, where the memory
  // stands, false on the moving ones, where a break drops it. Everything
  // downstream flipped too: the dome, the aim dot, the Start scan gate, and a
  // cue that landed on "Waiting for the compass ... move the phone gently"
  // every other frame over a phone that was holding perfectly still.
  // The fixture is `faint`, a one-cell checkerboard on a base the case
  // alternates between 167 and 143. On the 32x24 stillness grid that is a
  // gradient of exactly 2/base: 0.011976 and 0.013986, one either side of
  // GRADIENT_FLOOR (0.0128) and both under the hysteresis exit (0.016). The
  // two frames differ by 0.001005 against a movement bound of 0.003473, so
  // this is a view changing brightness rather than a view moving, and the
  // floor is the only thing that can separate the frames. It is not a
  // contrived scene: GRADIENT_FLOOR's own comment measures a thin treeline
  // under a smooth sky crossing the floor on nothing but its sensor noise.
  // Instants: 25 textured ticks to s+2500 (past SENSOR_SILENCE_MS = 2000, so
  // the video and not freshness is what holds the reading), then 30 ticks on
  // the alternating view to s+5500.
  // Mutation: take the band out of `VisualStability.observe` - one threshold
  // again, `this.canWitness = g >= GRADIENT_FLOOR` - rather than setting
  // GRADIENT_HYSTERESIS to 0, which would redden the premise above before the
  // body was ever reached. Observed red: "compassReady flipped across 3 s of a
  // view sitting on the floor: 101010101010101010101010101010".
  const grid=(base:number)=>{
    const g=new Float64Array(GRID_W*GRID_H);
    for(let y=0;y<GRID_H;y++)for(let x=0;x<GRID_W;x++)g[y*GRID_W+x]=(base+((x+y)%2?-1:1))/base;
    return g;
  };
  // The premise, graded by the same `gradient` the driver uses: the two frames
  // really do straddle the floor, and the brighter one really is inside the
  // band rather than past it. Without this the case could pass on a fixture
  // that had quietly fallen under the floor at both bases, which is the state
  // the case above already covers.
  const low=gradient(grid(167)),high=gradient(grid(143));
  assert.ok(low<GRADIENT_FLOOR,`the dim frame's gradient is ${low}, not under the floor ${GRADIENT_FLOOR}`);
  assert.ok(high>GRADIENT_FLOOR,`the bright frame's gradient is ${high}, not over the floor ${GRADIENT_FLOOR}`);
  assert.ok(high<GRADIENT_FLOOR*(1+GRADIENT_HYSTERESIS),
    `the bright frame's gradient is ${high}, already past the hysteresis exit, so the view never sits on the floor`);
  const {sweep,tick,silentFrom}=await approachAndHold();
  for(let i=0;i<25;i++)tick();
  assert.equal(clock-silentFrom,2500,'the textured hold did not last the 2500 ms the instants below are measured from');
  assert.equal(sweep.compassReady,true,'the hold was not ready before the view reached the floor, so nothing below is about the view');
  const readings:boolean[]=[];
  for(let i=0;i<30;i++){faint=i%2?143:167;tick();readings.push(sweep.compassReady);}
  faint=null;
  assert.equal(clock-silentFrom,5500);
  assert.deepEqual([...new Set(readings)],[true],
    `compassReady flipped across 3 s of a view sitting on the floor: ${readings.map((r)=>r?'1':'0').join('')}`);
  assert.notEqual(sweep.aimTarget,null,'the aim dot blanked on a view that is merely unjudgeable');
  assert.match(sweep.captureCue,/nothing to track/,`cue was: "${sweep.captureCue}"`);
  assert.doesNotMatch(sweep.captureCue,/move the phone/i,`cue was: "${sweep.captureCue}"`);
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

await test('P1: the interval fallback learns nothing from a stalled, still-playing element',async()=>{
  // The other half of the case above, and the one the element's own flags cannot
  // answer: nothing here is paused, ended or short of data - `paused` is false,
  // `readyState` is 2, the track is live and unmuted - and the timer keeps
  // firing. The ONLY thing that says the camera has stopped delivering is that
  // the media clock has not moved, so this is the case that pins that test. A
  // stall like this is what a real element does when the camera is preempted or
  // the decoder wedges: it keeps its last decoded image, which reads as a
  // perfectly steady view for as long as we keep re-reading it.
  const {sweep,tick}=await approachAndHold(false);
  stalled=true;
  for(let i=0;i<10;i++)tick();                  // 3.5 s of timer ticks on one stale image
  assert.equal(sweep.frameCount,0,'re-reading one stalled frame earned a hold');
  assert.equal(sweep.compassReady,false,'a stalled video vouched for the compass');
  stalled=false;
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

/** The media gate has six conditions and two of them had a case: `paused`, and
 *  a media clock that has stopped. The other four - the element ENDED, the
 *  element with no decoded frame to show, the TRACK ended, the track MUTED -
 *  were exercised by nothing, so deleting any one of them was invisible to the
 *  suite (issue #49 item 3). Each gets the same two-part case, because each
 *  makes the same two claims, and the parts are worth naming:
 *   - while the condition holds the gate refuses every tick EVEN THOUGH the
 *     media clock is running. That is what makes the case about this condition
 *     rather than about the clock test, and it is why the clock is asserted to
 *     have moved rather than merely left alone;
 *   - clearing it lets the camera capture again, which is what makes the
 *     condition a gate and not a wall.
 *  Instants, from `silentFrom` (the approach's last reading, call it s): ten
 *  fallback ticks of 350 ms carry the clock to s+3500 with the condition set,
 *  and the media clock 3.5 s further on, all ten refused. The condition then
 *  clears and 6 ticks reach s+5600: frames are delivered again and nothing is
 *  captured on them, because the reading at s is 5.6 s old and no continuity
 *  reaches back across the interval nothing watched. `aim(0)` then speaks at
 *  s+5700 and up to 6 further ticks - 2.1 s, room for a 500 ms settle inside
 *  the 1.5 s acceptance budget - capture the cell.
 *  The condition is cleared in a `finally`, so a case that fails part way
 *  through cannot leave the flag standing for the cases after it. */
async function mediaGateCase(set:()=>void,clear:()=>void){
  const {sweep,cell,tick,aim,silentFrom}=await approachAndHold(false);
  const mediaAt=mediaTime;
  try{
    set();
    for(let i=0;i<10;i++)tick();
    assert.equal(clock-silentFrom,3500,'the refused phase did not last the 3.5 s the instants assume');
    assert.ok(mediaTime-mediaAt>3.4,
      `the media clock stopped as well (${(mediaTime-mediaAt).toFixed(2)} s), so the clock test could be what refused these ticks`);
    assert.equal(sweep.frameCount,0,'a tick the camera delivered no frame for was captured');
    assert.equal(sweep.compassReady,false,'the refused ticks vouched for the compass');
    clear();
    for(let i=0;i<6;i++)tick();
    assert.equal(sweep.frameCount,0,'the old reading was certified across an interval nothing watched');
    aim(0);
    const from=clock;
    let capturedAfter:number|null=null;
    for(let i=0;i<6&&capturedAfter===null;i++){
      tick();
      if(sweep.cells.find((c)=>c.id===cell.id)?.captured)capturedAfter=clock-from;
    }
    assert.notEqual(capturedAfter,null,'the condition cleared and nothing was ever captured again');
    assert.ok(capturedAfter!<=1500,`took ${capturedAfter} ms after the condition cleared and the sensor spoke`);
  } finally { clear(); sweep.stop(); }
}

await test('P1: the interval fallback learns nothing from an ENDED element (issue #49)',async()=>{
  // An element at the end of its stream keeps its last picture and goes on
  // reporting a currentTime, so the clock test alone would let the timer
  // re-read that picture for as long as the user held the phone.
  // Mutation: delete `video.ended ||` from `newMediaFrame`. Observed red: "a
  // tick the camera delivered no frame for was captured".
  await mediaGateCase(()=>{ended=true;},()=>{ended=false;});
});

await test('P1: the interval fallback learns nothing from an element with no decoded frame (issue #49)',async()=>{
  // readyState 1, HAVE_METADATA: the element knows the shape of the stream and
  // has nothing to draw. `videoWidth` and `videoHeight` come from the metadata
  // and are set, so `grabFrame`'s own no-image test cannot see this - which is
  // exactly why the gate tests readyState and not the dimensions.
  // Mutation: delete `video.readyState < 2` from `newMediaFrame`. Observed red:
  // "a tick the camera delivered no frame for was captured".
  await mediaGateCase(()=>{starved=true;},()=>{starved=false;});
});

await test('P1: the interval fallback learns nothing from an ENDED TRACK (issue #49)',async()=>{
  // The track behind the element, not the element: the camera is gone, and the
  // element is still playing whatever it last decoded. The harness's track
  // fires no 'ended' event, so `sourceHealthy` stays true and this is the media
  // gate's own test answering, not the lifecycle flag - which is the state a
  // browser leaves behind when the track dies without an event to say so.
  // Mutation: delete `track.readyState !== "live"` from `newMediaFrame`.
  // Observed red: "a tick the camera delivered no frame for was captured".
  await mediaGateCase(()=>{trackLost=true;},()=>{trackLost=false;});
});

await test('P1: the interval fallback learns nothing from a MUTED track (issue #49)',async()=>{
  // `muted` on a video track is the OS saying the camera is producing no
  // frames right now - another app took it, or the privacy shutter closed -
  // while the track stays live and the element keeps its last picture. It is
  // also the condition that would carry stall detection on a browser where
  // `video.currentTime` turns out to be a graph clock that keeps running
  // through a stall (issue #48, Firefox Android), so it is the one of the four
  // that has a second job.
  // Mutation: delete `track.muted` from `newMediaFrame`. Observed red: "a tick
  // the camera delivered no frame for was captured".
  await mediaGateCase(()=>{trackMuted=true;},()=>{trackMuted=false;});
});

await test('A frozen fallback preview is counted in the alignment report (issue #46)',async()=>{
  // The gate above is right to refuse these frames, but refusing was all it
  // did: `observeStillness` is never reached, so `stillnessReadFailures` stays
  // 0 and this session - which captured nothing whatsoever - reported exactly
  // the envelope a healthy one reports (issue #46).
  // Instants, from the last reading: the element is paused before the first
  // tick, so each of the 10 ticks that follow, at +350 ms through +3500 ms,
  // finds the media clock exactly where the tick before it left it. Ten
  // refusals, the sixth of them - MEDIA_GATE_BLIND_AFTER - at +2100 ms.
  // Mutation: delete the `this.mediaGateRefusals++; this.mediaGateRefusalRun++;`
  // arm in grabFrame (counter never incremented). Observed red: "the report
  // recorded 0 media-gate refusals over 10 refused ticks".
  const {sweep,tick}=await approachAndHold(false);
  paused=true;
  for(let i=0;i<10;i++)tick();
  assert.equal(sweep.frameCount,0,'a frozen preview captured, so this is no longer the blind session under test');
  const diagnostic=JSON.parse(sweep.alignmentReport());
  assert.equal(diagnostic.stillnessReadFailures,0,
    'the pixels were readable throughout, so the other counter must not be the one carrying this');
  assert.equal(diagnostic.mediaGateRefusals,10,
    `the report recorded ${diagnostic.mediaGateRefusals} media-gate refusals over 10 refused ticks`);
  assert.ok(diagnostic.mediaGateRefusalRun>=6,
    `the consecutive run was ${diagnostic.mediaGateRefusalRun}, short of the 6 the cue needs`);
  paused=false;
  sweep.stop();
});

await test('After a run of refused ticks the cue names the camera image, not the compass (issue #46)',async()=>{
  // The state a user reaches by opening the camera on a browser with no
  // requestVideoFrameCallback, finding the preview frozen, and pressing Start
  // scan anyway: every tick so far returned at `not-recording`, so no capture
  // was ever attempted and `imageGate` has never been set. The refusal counter
  // is the only thing that knows the camera has stopped, and this is the cue it
  // produces.
  // Instants: the preview is frozen from the moment it opens, so six ticks at
  // 350 ms carry the clock to +2100 ms and are MEDIA_GATE_BLIND_AFTER refusals
  // exactly. The reading 100 ms after that is fresh (SENSOR_SILENCE_MS is
  // 2000), so `begin()` passes its compassReady gate, and the cue is read
  // before the seventh tick.
  // 2.1 seconds in the sentence is that same arithmetic, read back out: six
  // refused ticks of a 350 ms timer. The number is the reason this cue and the
  // stale-image one are no longer near-duplicates of each other.
  // Mutations: MEDIA_GATE_BLIND_AFTER = 1e9, and separately deleting the
  // increment. Observed red under both: the cue was "Hold here… capturing this
  // patch."
  shift=0;hidden=false;blind=false;flat=false;faint=null;thin=false;cellNoise=0;noiseFrame=0;intervalFn=null;mediaTime=0;paused=false;stalled=false;ended=starved=trackLost=trackMuted=false;
  const video=w.document.createElement('video');   // no requestVideoFrameCallback: the fallback path
  const sweep=new PhotosphereSweep();
  await sweep.start(video,w.document.createElement('canvas'));
  // The fallback's own 350 ms timer, with no media clock advance: the element
  // is paused, so every tick reads back the picture the one before it read.
  const tick=()=>{clock+=350;intervalFn!();};
  assert.ok(intervalFn,'the interval fallback was never started');
  const cell=sweep.cells.find((c)=>c.alt>20&&c.alt<60)!;
  paused=true;                                     // frozen before the first tick fires
  for(let i=0;i<6;i++)tick();
  assert.equal(sweep.captureLog.at(-1)?.outcome,'not-recording',
    `the grab reached the image gate (${sweep.captureLog.at(-1)?.outcome}), so imageGate and not the counter would be speaking`);
  const ev=new w.Event('deviceorientationabsolute');
  clock+=100;Object.defineProperty(ev,'timeStamp',{value:clock});
  Object.assign(ev,{alpha:(360-cell.az)%360,beta:90+cell.alt,gamma:0,absolute:true});
  w.dispatchEvent(ev);
  sweep.begin();
  assert.equal(sweep.isRecording,true,'the scan never started, so the cue below is the not-recording one');
  assert.equal(sweep.captureCue,
    'No new camera image has arrived for 2.1 seconds. Close the scan and open the camera again.',
    `cue was: "${sweep.captureCue}"`);
  assert.doesNotMatch(sweep.captureCue,/compass/i,'the cue blamed the compass for a camera that has stopped');
  assert.doesNotMatch(sweep.captureCue,/move the phone/i,'the cue asked for movement, which cannot make a stopped camera deliver');
  paused=false;
  sweep.stop();
});

await test('Frames flowing again end the refusal run, and the total remembers it (issue #46)',async()=>{
  // A counter, not a latch: one delivered frame ends the run and the cue with
  // it, while the total keeps the session's record - a scan that stuttered once
  // must stay distinguishable from one whose camera stopped.
  // Instants, from the last reading: 10 paused ticks to +3500 ms (10 refusals,
  // run 10), then one tick at +3850 ms with the media clock moving again, which
  // the gate accepts. Nothing is captured on it - the reading is 3.85 s old and
  // no continuity reaches back across the freeze - so the cue is the ordinary
  // compass one, which is the point: the camera is no longer what is wrong.
  // Mutation: delete `this.mediaGateRefusalRun = 0;` on the accepted frame
  // (counter not reset on recovery). Observed red: "a delivered frame did not
  // end the run" (and, with that assertion removed, the cue assertion too - the
  // recovered camera goes on being blamed).
  const {sweep,tick}=await approachAndHold(false);
  paused=true;
  for(let i=0;i<10;i++)tick();
  assert.equal(JSON.parse(sweep.alignmentReport()).mediaGateRefusalRun,10,
    'the freeze was not counted, so there is no run here to clear');
  paused=false;
  tick();                                          // one delivered frame
  const diagnostic=JSON.parse(sweep.alignmentReport());
  assert.equal(diagnostic.mediaGateRefusalRun,0,'a delivered frame did not end the run');
  assert.equal(diagnostic.mediaGateRefusals,10,
    `the running total forgot the freeze (${diagnostic.mediaGateRefusals})`);
  assert.doesNotMatch(sweep.captureCue,/camera image/,
    `the camera is delivering again and the cue still said: "${sweep.captureCue}"`);
  assert.match(sweep.captureCue,/Waiting for the compass/,`cue was: "${sweep.captureCue}"`);
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
  const samples=JSON.parse(sweep.alignmentReport()).samples;
  assert.ok(samples.length,'the capture recorded no diagnostic sample, so the pose it was worn at cannot be read');
  const wornAz=skyAngles(samples.at(-1).sensorBasis.forward).az;
  const off=Math.abs(((wornAz-cell.az+540)%360)-180);   // wrap-safe, for a cell near due north
  // Mutation that reddens this (issue #42 item 4): remove the refutation
  // branch in photospherePose.forFrame (the `if(previous && ... )` block that
  // arbitrates the jittery pair against the reading before it), leaving
  // `pose` as the unconditional `latest`. Observed red: "the photograph was
  // worn at 164.0 degrees, 20.0 off the pre-outlier 144".
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

await test('The interval fallback still captures behind a 300 ms camera pipeline delay (issue #48)',async()=>{
  // The same delay on the path that has no capture time to stamp with. Every
  // observation here carries the READ instant, so a delayed camera does not
  // move the stamp - it moves the CONTENT, which puts the break that ended the
  // approach a whole tick after the reading it has to reach back across.
  // Instants from the last reading s, which shares its instant with the last
  // approach tick (the phone comes to rest as the reading arrives: the scene is
  // shifted once more immediately after that tick and never again):
  //  - ticks land at s+350, s+700, s+1050, s+1400, ... and each presents the
  //    scene as of the last tick at or before 300 ms earlier, which through the
  //    350 ms hold is the tick before it. So s+350 shows the scene as it stood
  //    at s - still the moving approach - and s+700 shows the scene at s+350,
  //    at rest, as does every tick after it.
  //    The quantity under test is therefore ONE INTERVAL of delay and not
  //    300 ms: the harness samples the scene only at ticks, so any lag from 1
  //    to 350 presents the tick before and behaves identically here, and 351
  //    presents the tick before that and flips the verdict. The name says
  //    300 ms because that is a plausible phone; the arithmetic below is the
  //    arithmetic of a delay of one interval, and there is no sub-interval
  //    resolution anywhere in this fixture (see `approachAndHold`).
  //  - the pair (s+350, s+700) is therefore the last one that moves:
  //    lastBreak = {from: s+350, to: s+700}, a full interval later than s.
  //  - the still run opens at s+700 and settles SETTLE_MS = 500 ms later, so
  //    the tick at s+1400 is the first with a settled view (at s+1050 the run
  //    is only 350 ms old), and the grab on that tick is the one that captures,
  //    1400 ms into the hold, inside the 1500 ms acceptance budget.
  // Vouching asks lastBreak.from <= s + slop. At CONTINUITY_SLOP_MS = 150 that
  // is s+350 <= s+150 - false then and false forever, because a still run never
  // rewrites its break: the deadlock of issue #48. At the fallback's own
  // Math.max(CONTINUITY_SLOP_MS, GRAB_INTERVAL_MS) = 350 it is s+350 <= s+350,
  // the exact boundary of the widened margin, which is what this case pins.
  // The dome has to agree with the mosaic, which is the second assertion: at
  // s+3150 the reading is 3150 ms old, so `vouched`'s freshness disjunct
  // (SENSOR_SILENCE_MS = 2000) is long spent and only the witness can answer.
  // A driver that captured under a reading while telling the user the compass
  // was lost would be two answers to one question - it is the same margin or
  // it is not one decision.
  // Mutations, both observed red on this case:
  //  - the timer path passing the default slop (`this.vouchSlopMs =
  //    CONTINUITY_SLOP_MS` in the timer branch of `start()`): "DEADLOCK: a
  //    still phone never captured behind a 300 ms camera pipeline on the
  //    fallback path".
  //  - the session margin dropped from `vouched` alone
  //    (`viewVouchesFor(at, this.stability.continuity(now))` at the one call
  //    site there, leaving capture widened): "the dome said the heading was
  //    lost while a frame went into the mosaic".
  const {sweep,tick,silentFrom}=await approachAndHold(false,2,300);
  for(let i=0;i<9;i++)tick();                    // 3150 ms of hold, well past the s+1400 capture
  assert.equal(clock-silentFrom,3150,'the hold did not last the 3 s the instants above are measured over');
  assert.equal(sweep.frameCount,1,
    'DEADLOCK: a still phone never captured behind a 300 ms camera pipeline on the fallback path');
  assert.ok(sweep.compassReady,'the dome said the heading was lost while a frame went into the mosaic');
  assert.ok(sweep.tiltReady,'the dome said the tilt was lost while a frame went into the mosaic');
  sweep.stop();
});

await test('The interval fallback margin is ONE interval: two intervals of camera delay do not vouch (issue #48)',async()=>{
  // The upper edge, and with the case above it is the pin on the SIZE of the
  // widened margin rather than on its existence. The pair brackets the margin:
  // the 300 ms case reddens for any slop below 350, this one for any slop of
  // 700 or more, so together they hold 350 <= slop < 700 - one interval, and
  // not the two, three or blanket margins a single case admits.
  // 400 ms of delay, which the harness models as TWO intervals (a lag of 351
  // to 700 presents the tick before the tick before): the delay is past the
  // 350 ms the fix buys and the hold must therefore never capture.
  // Instants from s, each tick showing the scene as of the last tick at or
  // before 400 ms earlier - two ticks back through the 350 ms hold:
  //  - the tick at s and the tick at s+350 both show the scene as of s-450,
  //    the same mid-approach picture; s+700 shows the scene at s; s+1050 shows
  //    the scene at s+350, at rest, as does every tick after it.
  //  - so the two pairs that move are (s+350, s+700) and (s+700, s+1050), and
  //    the last break is {from: s+700, to: s+1050} - two intervals after the
  //    reading at s. (The identical pair at s+350 opens a run at s, but that
  //    run is 350 ms old at s+350, the only moment it is ever asked, and the
  //    observation at s+700 breaks it long before SETTLE_MS, so it vouches for
  //    nothing on its way past.)
  //  - the run that opens at s+1050 settles 700 ms later, at s+1750, and from
  //    there the view is settled for the rest of the hold.
  //  - vouching asks s+700 <= s + 350, which is false, and the break of a run
  //    that is never interrupted again never moves. Nothing captures.
  // Mutations, both observed red on this case:
  //  - `this.vouchSlopMs = 700` in the timer branch of `start()` - two
  //    intervals, the smallest margin that would vouch here; it captures at
  //    s+1750. This is the mutation the case exists for: at 700 the whole file
  //    was green before this case was written this way.
  //  - `this.vouchSlopMs = 1e9`, a margin that vouches for a reading of any
  //    age. Both give: "two intervals of camera delay were vouched for across
  //    an interval of unwatched view". 1e9 also reddens "P1: a frozen fallback
  //    video recovers once frames flow and a fresh reading arrives", which is
  //    the point: a blanket margin is not a wider margin, it is no margin, and
  //    it re-opens a P1 this file already guards.
  const {sweep,tick,silentFrom}=await approachAndHold(false,2,400);
  for(let i=0;i<9;i++)tick();                    // 3150 ms of hold, past the s+1750 a two-interval margin captures at
  assert.equal(clock-silentFrom,3150,'the hold did not last the 3 s the instants above are measured over');
  assert.equal(sweep.frameCount,0,
    'two intervals of camera delay were vouched for across an interval of unwatched view');
  sweep.stop();
});

await test('A lagged fallback hold that goes featureless keeps the heading the wider margin earned it (issue #48)',async()=>{
  // The memory in `noteReadingsStand` is the third site that takes the session
  // margin, and this is what holds it: the memory is written on the VIDEO
  // vouching, so on a lagged fallback it is written only if it is written on
  // the same widened margin that the dome and the capture use. Written on the
  // default margin it is never written at all here, and the moment the view
  // stops being judgeable the reading it was holding is gone - the issue #41
  // behaviour, lost on exactly the phones issue #48 is about.
  // Instants: the 300 ms fixture again, so the hold is settled from s+1400 and
  // every observation from there writes the memory. 9 textured ticks to
  // s+3150, then the phone is pointed at smooth sky: 9 featureless ticks to
  // s+6300, where the frames keep arriving (so the witness reads 'featureless'
  // and not 'stale'), no continuity exists, and the reading is 6300 ms old,
  // far past SENSOR_SILENCE_MS = 2000. Nothing but the memory can answer.
  // Mutations, both observed red here:
  //  - the margin dropped from the two `noteReadingsStand` lines: the memory
  //    is never written during the lagged hold, so the second assertion fails
  //    with "a featureless view lost the heading the widened margin had been
  //    vouching for".
  //  - the margin dropped from `vouched`: the first assertion fails, because
  //    at s+3150 freshness is spent and only the witness can answer.
  const {sweep,tick,silentFrom}=await approachAndHold(false,2,300);
  for(let i=0;i<9;i++)tick();
  assert.equal(clock-silentFrom,3150);
  assert.ok(sweep.compassReady,'the lagged hold was already lost before the view went blank, so nothing below is about the memory');
  flat=true;
  for(let i=0;i<9;i++)tick();
  assert.equal(clock-silentFrom,6300);
  assert.ok(sweep.compassReady,'a featureless view lost the heading the widened margin had been vouching for');
  assert.notEqual(sweep.aimTarget,null,'the dome blanked on a view that was merely unjudgeable');
  flat=false;
  sweep.stop();
});

/** The timer firing on an element that is still PLAYING, while the sensor
 *  chatters a couple of tenths of a degree. Two readings 125 ms apart and then
 *  the timer callback, so the newest reading is exactly as old as the grab -
 *  which is what the strict pose path wants (readings no more than 250 ms
 *  apart covering 500 ms, all within 1.5 degrees). `tick()` cannot express
 *  this: it advances 350 ms and only then fires, so the newest reading would
 *  always be 350 ms stale and the pose would be refused for a reason that has
 *  nothing to do with the image. The media clock advances with the beat unless
 *  the element is stalled, exactly as the tick does. */
const beat=(aim:(offset:number,advanceMs?:number)=>void)=>{
  aim(0.2,125);aim(0,125);
  if(!paused&&!stalled)mediaTime+=0.25;
  intervalFn!();
};

await test('P1: a frozen media clock is not captured, however fresh and steady the sensor is',async()=>{
  // Review 17, P1. The media gate protected the stillness WITNESS and nothing
  // else: with the element playing, the track live and the clock frozen, a
  // sensor reporting a steady direction satisfied the strict pose path all by
  // itself, and the retained pixels were stored under the phone's current
  // direction. The witness was never consulted because it had nothing to say.
  const {sweep,aim}=await approachAndHold(false);
  stalled=true;                                 // playing, live, last picture retained
  for(let i=0;i<20;i++)beat(aim);
  // The pose is good - this is the whole point. If it were not, the case would
  // pass without an image gate at all.
  assert.ok(sweep.aimTarget,'no pose: this case would pass even with no image gate');
  assert.equal(sweep.compassReady,true,'the sensor was not being believed, so nothing here is about the image');
  assert.equal(sweep.frameCount,0,'retained pixels were captured under the direction the phone points now');
  const tail=sweep.captureLog.slice(-5).map((r)=>r.outcome);
  assert.deepEqual([...new Set(tail)],['stale-image'],`the last outcomes were ${JSON.stringify(tail)}`);
  // Every one of those twenty ticks was refused by the media gate as well as
  // recorded at the image gate, so both sentences about this camera are live
  // and the cue has to pick one. It picks the counter's, which is the review's
  // M1 ruling: a RUN of refusals is stronger evidence than one attempt's
  // record, and the counter is the only one of the two that can say how long -
  // twenty ticks of a 350 ms timer, 7.0 seconds. The stale-image sentence it
  // displaces is still reached and still pinned, by the overhead case below,
  // where a manual press never asks the media gate and the run stays 0.
  // Mutation: put the media-gate branch back below the two `imageGate` lines.
  // Observed red: cue was "The camera image is not updating. Close the scan and
  // open the camera again."
  assert.equal(sweep.captureCue,
    'No new camera image has arrived for 7.0 seconds. Close the scan and open the camera again.',
    `cue was: "${sweep.captureCue}"`);
  stalled=false;
  sweep.stop();
});

await test('P1: a paused preview is not captured by the overhead button either',async()=>{
  // The manual path skips every pose and stability test by design - the user is
  // pointing the camera up and saying so - which left it the one path with no
  // requirement at all on the picture it was storing.
  const {sweep}=await approachAndHold(false);
  paused=true;
  clock+=3000;
  assert.equal(sweep.captureOverhead(),false,'a paused preview was captured as an overhead photograph');
  assert.equal(sweep.frameCount,0);
  assert.equal(sweep.overheadCaptured,false);
  assert.match(sweep.captureCue,/not updating/,`cue was: "${sweep.captureCue}"`);
  paused=false;
  sweep.stop();
});

await test('P1: once the camera delivers again, the same chattering sensor captures',async()=>{
  // The gate has to be a gate, not a wall: the recovery is what says the fix
  // refuses a frozen camera rather than refusing the fallback path.
  const {sweep,cell,aim}=await approachAndHold(false);
  stalled=true;
  for(let i=0;i<10;i++)beat(aim);
  assert.equal(sweep.frameCount,0,'the frozen element captured');
  stalled=false;                                // frames flow again
  const from=clock;
  let capturedAfter:number|null=null;
  for(let i=0;i<12&&capturedAfter===null;i++){
    beat(aim);
    if(sweep.cells.find((c)=>c.id===cell.id)?.captured)capturedAfter=clock-from;
  }
  assert.notEqual(capturedAfter,null,'the camera recovered and nothing was ever captured again');
  assert.ok(capturedAfter!<=1500,`took ${capturedAfter} ms after the camera resumed`);
  sweep.stop();
});

await test('A manual press is not refused because the stillness witness already read that frame',async()=>{
  // Freshness is not consumption. The witness reads each delivered frame once;
  // the user pressing the button moments later is capturing the picture on the
  // screen, and must not be told the camera has stopped because something else
  // looked at it first.
  const {sweep,tick}=await approachAndHold();    // rVFC: the witness reads every presented frame
  for(let i=0;i<6;i++)tick();
  clock+=100;                                    // the press lands between frames, on the one just read
  assert.equal(sweep.captureOverhead(),true,'the press was refused for a frame the witness had seen');
  assert.equal(sweep.captureLog.at(-1)?.outcome,'accepted');
  assert.equal(sweep.overheadCaptured,true);
  sweep.stop();
});

await test('One delivered frame is captured once, however many times the grab runs on it',async()=>{
  // Freshness alone cannot see this: the frame IS recent, the camera IS
  // delivering, and the callback path is the one that knows the frame's own
  // identity. Without the identity clause the same picture goes into the
  // mosaic twice - a duplicate the panorama would weight as two observations.
  const {sweep,cell,tick,represent}=await approachAndHold();
  let captured=false;
  for(let i=0;i<20&&!captured;i++){tick();captured=!!sweep.cells.find((c)=>c.id===cell.id)?.captured;}
  assert.ok(captured,'nothing was captured, so there is no second grab to judge');
  assert.equal(sweep.captureLog.at(-1)?.outcome,'accepted');
  const frames=sweep.frameCount;
  represent();                                   // the very same frame, 700 ms later
  assert.equal(sweep.captureLog.at(-1)?.outcome,'frame-already-captured',
    `the second grab on one frame recorded ${sweep.captureLog.at(-1)?.outcome}`);
  assert.equal(sweep.frameCount,frames,'one delivered frame entered the mosaic twice');
  sweep.stop();
});

await test('The driver samples the preview finer than the grid, or the noise correction is dead code (issue #62)',async()=>{
  // Round 1 of #62. `noiseGradient` can only separate sensor noise from
  // cell-scale scene structure by the fact that a box average divides one by
  // sqrt(samples per cell) and leaves the other alone. The driver used to draw
  // the preview at exactly GRID_W x GRID_H, one sample per cell, where that
  // ratio does not exist - so the correction returned 0 on every real phone
  // while the unit tests, which hand the module 320x240, exercised a path
  // nothing took. A correction that is structurally 0 in production is a claim
  // nothing keeps, and nothing else in this file would notice it coming back.
  lastSample=null;lastObserved=null;
  const {sweep,tick}=await approachAndHold();
  for(let i=0;i<4;i++)tick();
  // What the canvas was drawn and read at. Necessary - the buffer's CONTENTS
  // have to carry sub-cell detail - but not sufficient on its own.
  assert.ok(sampledSize(),'the driver never sampled the preview at all');
  assert.equal(sampledSize()!.width,LUMA_W,'the stillness sample is not the width the module asks for');
  assert.equal(sampledSize()!.height,LUMA_H,'the stillness sample is not the height the module asks for');
  assert.ok(CELL_SAMPLES>1,`CELL_SAMPLES is ${CELL_SAMPLES}: at one sample per cell noiseGradient is 0 by construction`);
  assert.equal(sampledSize()!.width%GRID_W,0,'the sample width is not a whole number of samples per cell');
  assert.equal(sampledSize()!.height%GRID_H,0,'the sample height is not a whole number of samples per cell');
  // And what the WITNESS was handed, which is the fact `noiseGradient` acts on.
  // `perCell` is computed from these two numbers and nothing else, so a driver
  // that drew finely and then described the buffer as grid-sized would put the
  // correction back to sleep with every assertion above still green.
  assert.ok(observedSize(),'the witness was never handed a frame at all');
  assert.equal(observedSize()!.width,LUMA_W,'the witness was told the buffer was a different width than it is');
  assert.equal(observedSize()!.height,LUMA_H,'the witness was told the buffer was a different height than it is');
  assert.ok((observedSize()!.width*observedSize()!.height)/(GRID_W*GRID_H)>1,
    'the witness was handed one sample per cell, where noiseGradient is 0 by construction');
  // And the factor has to be big enough to be honest, which is a separate
  // claim from being bigger than 1. The Laplacian behind `noiseGradient` spans
  // 3 samples, so at 2 per cell it can never sit inside a cell and every
  // response it makes is a cell edge - it then reports STRUCTURE as noise and
  // subtracts it. A one-cell checkerboard with no noise on it whatsoever is
  // where that shows worst, and it is not hypothetical: `faint` is that
  // checkerboard and three cases above use it to sit on GRADIENT_FLOOR.
  // Measured: 0 at CELL_SAMPLES 3, and 0.003718 at 2 - a quarter of the floor,
  // invented, which would drop `faint` at base 143 from 0.01399 to 0.01029 and
  // take it under a floor it is supposed to be sitting on.
  faint=150;
  const board=new Uint8Array(LUMA_W*LUMA_H);
  for(let y=0;y<LUMA_H;y++)for(let x=0;x<LUMA_W;x++)board[y*LUMA_W+x]=scenePixel(x,y,LUMA_W,LUMA_H);
  faint=null;
  assert.equal(noiseGradient(board,LUMA_W,LUMA_H),0,
    `at ${CELL_SAMPLES} samples per cell the estimator invents noise on a frame that has none, and subtracts it`);
  sweep.stop();
  // Three mutations, all observed, and the third is the one the canvas-side
  // assertions alone would have missed:
  //   * `const LUMA_W = GRID_W, LUMA_H = GRID_H` in photosphere.ts - the shape
  //     the driver must never go back to. Red: "the stillness sample is not the
  //     width the module asks for".
  //   * `CELL_SAMPLES = 2`. Red: "at 2 samples per cell the estimator invents
  //     noise on a frame that has none, and subtracts it".
  //   * draw and read at LUMA_W x LUMA_H but call
  //     `observe(at, this.luma, GRID_W, GRID_H)`. Every canvas-side assertion
  //     above stays green and the correction is inert again. Red: "the witness
  //     was told the buffer was a different width than it is".
  // What this case does NOT prove on its own: that the finer buffer CHANGES a
  // verdict. That is the case below, and the two are separate because this one
  // is about the driver and that one is about the module.
});

await test('The same scene, the same camera: one sample per cell vouches for a view the real driver refuses (issue #62)',async()=>{
  // The counterfactual the case above cannot state, and the reason it is here
  // rather than in photosphereStability.test.ts: it is about the SAMPLING the
  // driver chooses, so it has to be written against the driver's own scene.
  // `thin` is a mostly-sky upper band - horizontal pairs of exactly zero on 21
  // of 24 rows - which is the shape sensor noise lifts. Clean it measures
  // 0.011688, under GRADIENT_FLOOR: it cannot witness, and must not pretend to.
  // `cellNoise` is 0.5 luma levels reaching each cell. That is a noisier camera
  // than the sigma-3-at-320x240 the rest of the record uses (which is 0.30 at
  // the cell), and deliberately so: at 0.30 the lift is 0.0012 and a fixture
  // would have to straddle the floor inside that, where the hysteresis band
  // latches it featureless and the case would be about issue #75 instead. At
  // 0.5 there is room for a margin on both sides. A phone at dusk with its gain
  // up is the camera this models, which is when this app is used.
  const render=(width:number,height:number,frame:number)=>{
    noiseFrame=frame;
    const px=new Uint8Array(width*height);
    for(let y=0;y<height;y++)for(let x=0;x<width;x++)px[y*width+x]=scenePixel(x,y,width,height);
    return px;
  };
  const verdicts=(width:number,height:number)=>{
    // Entered from the ordinary textured scene, because the floor is a BAND: a
    // frame entering from nothing is held to the exit at 0.016 and neither
    // sampling would admit this one. The hole #62 leaves is on the RETENTION
    // side, where a witness already open goes on witnessing down to the floor.
    const s=new VisualStability();
    let i=0;
    thin=false;
    for(;i<20;i++)s.observe(i*100,render(width,height,i),width,height);
    thin=true;
    const seen=new Set<string>();
    for(let k=0;k<30;k++,i++){const at=i*100;s.observe(at,render(width,height,i),width,height);seen.add(s.witness(at));}
    thin=false;
    return seen;
  };
  // The premise: clean, the scene cannot witness, and both samplings agree.
  // `finally`, because these are module-level flags the whole file shares and a
  // thrown assertion would otherwise leave the next case looking at this scene.
  let coarse:Set<string>,fine:Set<string>;
  try{
    cellNoise=0;
    assert.deepEqual([...verdicts(GRID_W,GRID_H)],['featureless'],'the clean scene should not witness at one sample per cell');
    assert.deepEqual([...verdicts(LUMA_W,LUMA_H)],['featureless'],'the clean scene should not witness at the real sampling');
    // The body: the same scene and the same camera, sampled two ways.
    cellNoise=0.5;
    coarse=verdicts(GRID_W,GRID_H);fine=verdicts(LUMA_W,LUMA_H);
  } finally { thin=false;cellNoise=0;noiseFrame=0; }
  assert.ok(coarse.has('still'),
    `one sample per cell should vouch for this view on its own noise, and read {${[...coarse]}} instead - `
    +'if this ever goes green the fixture has stopped exhibiting the defect and the assertion below proves nothing');
  assert.deepEqual([...fine],['featureless'],
    `the real sampling should refuse a view with 0.0117 of structure, and read {${[...fine]}}`);
  // Measured, corrected, over 40 frames: at one sample per cell 0.014815 to
  // 0.015733, all of it over the 0.0128 floor and none of it real; at
  // CELL_SAMPLES 0.010557 to 0.011765, all of it under, against a noise-free
  // 0.011688. So the correction recovers the scene to within 0.0012.
  // Mutation that reddens this: drop the subtraction in `observe`
  // (`const g = gradient(grid)`). Observed red: "the real sampling should refuse
  // a view with 0.0117 of structure, and read {moving,still}". The mutation in
  // the case above - putting the driver back on the grid - does NOT redden this
  // one, because this case builds its own buffers; that is the division of
  // labour between the two and the reason neither replaces the other.
});

console.log(`photosphereStillnessDom.test: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};
if(failed)process.exitCode=1;
