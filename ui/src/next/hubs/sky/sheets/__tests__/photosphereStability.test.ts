import assert from 'node:assert/strict';
import { VisualStability, SETTLE_MS, STALE_FRAME_MS } from '../photosphereStability';

// The video is the only witness to a still phone: the orientation sensor goes
// silent when nothing moves, so silence proves nothing on its own. These cases
// pin what the witness may and may not say. Brightness drift is not motion; a
// one-pixel shift is; and a stopped stream is UNKNOWN, never still.
//
// The last four cases pin the two things a frame-to-frame comparison alone
// gets WRONG, both measured against this module at the real input size:
//
//   * it is a RATE test. A pan slow enough to stay under the per-frame limit
//     accumulates without bound while every single frame reads as still - a
//     320x240 treeline panning one pixel a frame is 7.5 deg/s and moved 0.0142
//     per frame against a 0.02 limit, so two seconds of that read "steady" and
//     the phone had swept 15 degrees. The anchor - the frame the settle began
//     on, kept and re-compared - is what turns a speed limit into a bound on
//     total drift.
//   * it answers about the SCENE, not the phone. A view with nothing in it
//     cannot witness anything, and "unchanged" there is not evidence of a hold.
let passed=0,failed=0;
function test(name:string,fn:()=>void){
  try{fn();passed++;console.log(`PASS ${name}`);}
  catch(e){failed++;console.log(`FAIL ${name}\n     ${(e as Error).message.split('\n')[0]}`);}
}

const W=32,H=24;
/** A 32x24 luminance gradient with a bright block, so a one-pixel shift moves
 *  real edges. `shift` slides the scene right; `gain` scales the exposure. */
function scene(shift=0,gain=1):Uint8Array{
  const px=new Uint8Array(W*H);
  for(let y=0;y<H;y++)for(let x=0;x<W;x++){
    const sx=x-shift;
    const block=sx>=10&&sx<=17&&y>=8&&y<=15;
    const v=block?180:40+Math.round(Math.max(0,sx)*80/(W-1));
    px[y*W+x]=Math.min(255,Math.round(v*gain));
  }
  return px;
}
const feed=(s:VisualStability,from:number,to:number,frame=scene())=>{
  for(let at=from;at<=to;at+=100)s.observe(at,frame,W,H);
};

test('With no video frame observed at all, stability is unknown, not still',()=>{
  const s=new VisualStability();
  assert.equal(s.stableAt(0),null);
  assert.equal(s.stableAt(5000),null);
});

test('An unchanging view reads as still only once it has held for the settle time',()=>{
  const s=new VisualStability();
  feed(s,0,300);
  assert.equal(s.stableAt(300),false,`${SETTLE_MS} ms of stillness is not yet proven at 300 ms`);
  feed(s,400,600);
  assert.equal(s.stableAt(600),true);
});

test('A one-pixel shift of the scene reads as motion',()=>{
  const s=new VisualStability();
  feed(s,0,500);
  assert.equal(s.stableAt(500),true,'the setup frames should have settled');
  s.observe(600,scene(1),W,H);
  assert.equal(s.stableAt(600),false);
});

test('A 30 percent exposure change of the same scene is not motion',()=>{
  const s=new VisualStability();
  feed(s,0,500);
  s.observe(600,scene(0,1.3),W,H);
  assert.equal(s.stableAt(600),true);
});

test('A stopped video stream is unknown, not still',()=>{
  const s=new VisualStability();
  feed(s,0,600);
  assert.equal(s.stableAt(600),true);
  assert.equal(s.stableAt(600+STALE_FRAME_MS),true,'the last frame is still inside the freshness window');
  assert.equal(s.stableAt(1700),null);
});

// ---------------------------------------------------------------------------
// The real input size. `resample` does its actual work here, so a one-pixel pan
// is a TENTH of a grid cell rather than a whole one, which is the regime the
// per-frame limit cannot see. 30 fps and a 60 degree short axis over 240 px
// make one pixel a frame 7.5 deg/s.
const PW=320,PH=240,FRAME_MS=1000/30;

/** A treeline against a graded sky: high contrast, non-repeating, and edges
 *  everywhere, so a sub-cell shift moves something real. Sky peaks at 180 so a
 *  30 percent exposure change does not clip - clipping destroys information and
 *  is a genuine change, which would test the fixture and not the module. */
function treeline(shift=0,gain=1):Uint8Array{
  const px=new Uint8Array(PW*PH);
  for(let x=0;x<PW;x++){
    const sx=x+shift;
    const canopy=120+Math.round(26*Math.sin(sx/11)+14*Math.sin(sx/3.3)+8*Math.sin(sx/1.7));
    for(let y=0;y<PH;y++){
      const v=y<canopy?140+Math.round(40*(1-y/canopy))
        :y<canopy+40?(Math.floor(sx/17)%3===0?65:28)
        :(Math.floor(sx/23)%4===0?48:20);
      px[y*PW+x]=Math.min(255,Math.round(v*gain));
    }
  }
  return px;
}
/** Smooth overcast: base 90, spread 20, not one edge. Sliding a linear ramp
 *  sideways is the same ramp plus a constant, and mean-normalisation removes
 *  the constant - so NO frame comparison can see this pan. */
function overcast(shift=0):Uint8Array{
  const px=new Uint8Array(PW*PH);
  for(let y=0;y<PH;y++)for(let x=0;x<PW;x++)px[y*PW+x]=Math.round(90+20*((x+shift)/PW)-4*(y/PH));
  return px;
}

test('One pixel of pan is under the frame-to-frame limit: only the anchor sees it',()=>{
  // The premise of the case below, pinned so it cannot quietly stop holding.
  // If this ever goes red the per-frame limit has been tightened instead, and
  // that limit also has to pass a real phone's sensor noise in low light.
  const s=new VisualStability();
  for(let at=0;at<=600;at+=100)s.observe(at,treeline(0),PW,PH);
  assert.equal(s.stableAt(600),true,'the setup frames should have settled');
  s.observe(700,treeline(1),PW,PH);
  assert.equal(s.stableAt(700),true,'one pixel in one frame is below the rate limit, as measured');
});

test('A textured view panning one pixel a frame is motion, not a very slow hold',()=>{
  const s=new VisualStability();
  let stillAt:number|null=null;
  for(let i=0;i*FRAME_MS<=2000;i++){
    const at=i*FRAME_MS;
    s.observe(at,treeline(i),PW,PH);
    if(s.stableAt(at)===true&&stillAt===null)stillAt=Math.round(at);
  }
  assert.equal(s.stableAt(2000),false,'7.5 deg/s of continuous pan read as a steady hold at 2 s');
  // Not just at 2000: a verdict that flickers true every second cycle would
  // still hand a capture the wrong pose, it would only do it less often.
  assert.equal(stillAt,null,`the pan first read as still at ${stillAt} ms`);
});

test('A still textured view survives a 30 percent exposure change against the anchor',()=>{
  // The anchor is the half of this that is new: a brightness step is compared
  // with a frame from BEFORE the step for as long as the hold lasts, not just
  // with its immediate neighbour, so normalisation has to hold up over time.
  const s=new VisualStability();
  for(let at=0;at<=600;at+=100)s.observe(at,treeline(0),PW,PH);
  for(let at=700;at<=2000;at+=100)s.observe(at,treeline(0,1.3),PW,PH);
  assert.equal(s.stableAt(2000),true,'a brighter but unmoved view read as motion');
});

test('A view with no texture to judge by is unknown, never still',()=>{
  const blank=new Uint8Array(PW*PH).fill(128);
  const s=new VisualStability();
  for(let at=0;at<=1000;at+=100)s.observe(at,blank,PW,PH);
  assert.equal(s.stableAt(1000),null,'a blank wall vouched for a hold it cannot see');
  // Near-flat is the same answer: read noise is not texture.
  const noisy=new VisualStability();
  for(let at=0;at<=1000;at+=100){
    const px=new Uint8Array(PW*PH);
    for(let i=0;i<px.length;i++)px[i]=128+((i*7+at)%3)-1;
    noisy.observe(at,px,PW,PH);
  }
  assert.equal(noisy.stableAt(1000),null,'sensor noise on a blank view vouched for a hold');
  // And the case this rule was measured on: a smooth overcast sky panning a
  // whole grid cell per frame, 75 deg/s, invisible to any frame comparison.
  const sky=new VisualStability();
  for(let i=0;i*FRAME_MS<=2000;i++){
    const at=i*FRAME_MS;
    sky.observe(at,overcast(i*10),PW,PH);
    assert.notEqual(sky.stableAt(at),true,`a featureless sky vouched for a 75 deg/s pan at ${Math.round(at)} ms`);
  }
});

console.log(`photosphereStability.test: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};
if(failed)process.exitCode=1;
