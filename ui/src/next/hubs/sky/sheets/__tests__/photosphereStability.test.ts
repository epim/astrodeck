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

test("The settle is measured on the frames' clock, so a late-reporting camera cannot shorten it",()=>{
  // A camera with a pipeline delay hands over a frame the sensor saw some time
  // ago. The run is as long as the FRAMES say it is: crediting it with the gap
  // between the newest capture and the caller's clock would mean the longer a
  // camera's delay, the less stillness it takes to be believed - a 250 ms delay
  // would settle a 400 ms run, and the phone would be certified on 100 ms of
  // watching it never did.
  const s=new VisualStability();
  feed(s,0,400);
  assert.equal(s.stableAt(650),false,
    `a caller 250 ms ahead of the newest frame has still only seen 400 ms of stillness, under ${SETTLE_MS}`);
  s.observe(500,scene(),W,H);
  assert.equal(s.stableAt(750),true,'a full settle of observed frames was not believed');
  assert.equal(s.continuity(750)!.stillSince,0,'the run still begins on the frame it began on');
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

test('Continuity names the start of the still run and the last break the video saw',()=>{
  const s=new VisualStability();
  // Moving through 300 ms, then the same view held.
  for(let i=0;i<=3;i++)s.observe(i*100,scene(i),W,H);
  for(let t=400;t<=1200;t+=100)s.observe(t,scene(3),W,H);
  const c=s.continuity(1200);
  assert.ok(c,'a settled view has continuity');
  assert.equal(c!.stillSince,300,'the still run begins on the first frame of the first still pair');
  assert.deepEqual(c!.lastBreak,{from:200,to:300},'the last break is the last moving pair');
});

test('Continuity is null before the settle and null again the moment the view moves',()=>{
  const s=new VisualStability();
  for(let t=0;t<=300;t+=100)s.observe(t,scene(0),W,H);
  assert.equal(s.continuity(300),null,'not settled yet');
  for(let t=400;t<=600;t+=100)s.observe(t,scene(0),W,H);
  assert.ok(s.continuity(600),'settled');
  s.observe(700,scene(5),W,H);
  assert.equal(s.continuity(700),null,'a moved view has no continuity');
  for(let t=800;t<=1400;t+=100)s.observe(t,scene(5),W,H);
  assert.deepEqual(s.continuity(1400)!.lastBreak,{from:600,to:700});
});

test('An unobserved gap is a break at its end: nothing before the gap can be vouched for',()=>{
  const s=new VisualStability();
  for(let t=0;t<=500;t+=100)s.observe(t,scene(0),W,H);
  // Nothing for 1.5 s (over STALE_FRAME_MS), then the same view again.
  for(let t=2000;t<=2600;t+=100)s.observe(t,scene(0),W,H);
  const c=s.continuity(2600);
  assert.ok(c);
  assert.equal(c!.stillSince,2000);
  assert.deepEqual(c!.lastBreak,{from:2000,to:2000});
});

test('The first frame ever seen is a break at its own time',()=>{
  const s=new VisualStability();
  for(let t=1000;t<=1600;t+=100)s.observe(t,scene(0),W,H);
  assert.deepEqual(s.continuity(1600)!.lastBreak,{from:1000,to:1000});
});

test('A frame with no texture is a break, even between two identical textured frames',()=>{
  const s=new VisualStability();
  for(let t=0;t<=500;t+=100)s.observe(t,scene(0),W,H);
  s.observe(600,new Uint8Array(W*H).fill(120),W,H);
  for(let t=700;t<=1300;t+=100)s.observe(t,scene(0),W,H);
  const c=s.continuity(1300);
  assert.ok(c);
  assert.equal(c!.stillSince,700);
  assert.deepEqual(c!.lastBreak,{from:600,to:700},'the break spans the blank frame and the first frame after it');
});

/** A 32x24 horizontal ramp, base 86, given spread. Its variance is the only
 *  texture in it, so the spread tunes the frame straight past TEXTURE_FLOOR
 *  (0.01) while leaving consecutive frames close together. Measured through
 *  this module's own grid: spread 39 has variance 0.012017 (textured), spread
 *  33 has 0.009304 (under the floor), and the two differ by 0.011929 - inside
 *  STILL_DIFF_LIMIT (0.02) and inside ANCHOR_DIFF_LIMIT (0.04), so NOTHING but
 *  the texture floor separates them. */
function ramp(spread:number):Uint8Array{
  const px=new Uint8Array(W*H);
  for(let y=0;y<H;y++)for(let x=0;x<W;x++)px[y*W+x]=Math.round(86+spread*x/(W-1));
  return px;
}

test('A frame too flat to judge breaks the run even though no pair moved',()=>{
  // The case above cannot fail for the texture rule on its own: the blank frame
  // is also far from its neighbours, so the motion rule would record a break
  // there anyway. Here the dip happens with every pair INSIDE the movement
  // limit - a view losing the last of its contrast, not moving - so the texture
  // rule is the only thing that can see it. Without it the run reaches back
  // across the frames nothing could be judged from, and the continuity vouches
  // for a reading taken before them.
  // The premise first, pinned so it cannot quietly stop holding.
  const dim=new VisualStability();
  for(let t=0;t<=600;t+=100)dim.observe(t,ramp(33),W,H);
  assert.equal(dim.stableAt(600),null,'the near-floor frame must read as unjudgeable');
  const lit=new VisualStability();
  for(let t=0;t<=600;t+=100)lit.observe(t,ramp(39),W,H);
  assert.equal(lit.stableAt(600),true,'the textured frame must read as still');

  const s=new VisualStability();
  for(let t=0;t<=800;t+=100)s.observe(t,ramp(39),W,H);
  s.observe(900,ramp(33),W,H);            // contrast dips under the floor
  for(let t=1000;t<=1500;t+=100)s.observe(t,ramp(39),W,H);
  const c=s.continuity(1500);
  assert.ok(c,'the view is textured and settled again by 1500');
  assert.equal(c!.stillSince,900,'the run must restart at the frame that could not be judged');
  assert.deepEqual(c!.lastBreak,{from:900,to:900},'the break is that frame alone, at its own instant');
});

test('clear() forgets the last break',()=>{
  const s=new VisualStability();
  for(let t=0;t<=300;t+=100)s.observe(t,scene(t/100),W,H);
  s.clear();
  for(let t=1000;t<=1600;t+=100)s.observe(t,scene(0),W,H);
  assert.deepEqual(s.continuity(1600)!.lastBreak,{from:1000,to:1000});
});

console.log(`photosphereStability.test: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};
if(failed)process.exitCode=1;
