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
w.HTMLCanvasElement.prototype.getContext=()=>({ drawImage(){},
  getImageData:(_x:number,_y:number,width:number,height:number)=>{
    const data=new Uint8ClampedArray(width*height*4);
    for(let y=0;y<height;y++)for(let x=0;x<width;x++){
      const v=scenePixel(x,y,width,height),i=(y*width+x)*4;
      data[i]=data[i+1]=data[i+2]=v;data[i+3]=255;
    }
    return {data};
  }, createImageData:(width:number,height:number)=>({data:new Uint8ClampedArray(width*height*4)}), putImageData(){},
});
w.HTMLCanvasElement.prototype.toDataURL=()=>'data:image/png;base64,';
Object.defineProperty(w.document,'visibilityState',{get:()=>hidden?'hidden':'visible',configurable:true});
g.setInterval=()=>0;g.clearInterval=()=>{};
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
 *  the moment the approach ended: silence starts here. */
async function approachAndHold(){
  shift=0;hidden=false;
  const video=w.document.createElement('video');
  let frame:((now:number,metadata:unknown)=>void)|undefined;
  video.requestVideoFrameCallback=(fn:typeof frame)=>{frame=fn;return 1;};
  video.cancelVideoFrameCallback=()=>{};
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
  const tick=()=>{clock+=100;frame!(clock,{captureTime:clock,mediaTime:clock,presentationTime:clock,
    expectedDisplayTime:clock,width:640,height:480,presentedFrames:1});};
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

console.log(`photosphereStillnessDom.test: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};
if(failed)process.exitCode=1;
