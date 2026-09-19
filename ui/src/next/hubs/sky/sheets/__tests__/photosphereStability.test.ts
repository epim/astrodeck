import assert from 'node:assert/strict';
import { VisualStability, SETTLE_MS, STALE_FRAME_MS, GRID_W, GRID_H } from '../photosphereStability';

// The video is the only witness to a still phone: the orientation sensor goes
// silent when nothing moves, so silence proves nothing on its own. These cases
// pin what the witness may and may not say. Brightness drift is not motion; a
// one-pixel shift is; and a stopped stream is UNKNOWN, never still.
//
// The cases at the real input size pin the things a frame-to-frame comparison
// alone gets WRONG, all measured against this module at that size:
//
//   * it is a RATE test. A pan slow enough to stay under the per-frame limit
//     accumulates without bound while every single frame reads as still - a
//     320x240 treeline panning one pixel a frame is 7.5 deg/s and moved 0.0142
//     per frame against a bound of 0.0202, so two seconds of that read "steady"
//     and the phone had swept 15 degrees. The anchor - the frame the settle
//     began on, kept and re-compared - is what turns a speed limit into a bound
//     on total drift.
//   * it answers about the SCENE, not the phone. A view with nothing in it
//     cannot witness anything, and "unchanged" there is not evidence of a hold.
//     Both bounds are therefore in CELLS OF APPARENT MOTION - a multiple of the
//     frame's own spatial gradient G - and a frame whose G is under
//     GRADIENT_FLOOR witnesses nothing at all. What decides is the gradient and
//     not the variance: a frame can vary enormously and still have nowhere for
//     a shift to show (issue #38).
//   * the two directions are separate questions. G is min(Gh, Gv), because a
//     frame can only witness a shift along an axis it has structure on: a
//     vertical ramp has all the gradient anyone could want and still cannot see
//     a sideways pan at all.
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
/** Smooth graded sky (base 150, spread 20 down the frame) over the top seven
 *  eighths, with the `treeline` canopy squeezed into the bottom eighth: the
 *  scene issue #38 was written about. A large smooth remainder keeps the
 *  frame's VARIANCE respectable - 0.030, three times the 0.01 variance floor
 *  that used to guard this - while only about one row of the 24-row grid ever
 *  changes. Measured through this module's own grid at 320x240: Gv is 0.0411,
 *  almost all of it the sky's own vertical grade, and Gh is 0.0109, so
 *  min(Gh, Gv) is 0.0109 and over a 1 px/frame pan it stays between 0.0103 and
 *  0.0116 - under GRADIENT_FLOOR throughout. Its anchor diff saturates near
 *  0.022 and never reaches the 0.04 the old fixed bound asked for. */
const SKY_TOP=Math.round(PH*7/8);
function thinTreeline(shift=0):Uint8Array{
  const px=new Uint8Array(PW*PH);
  for(let x=0;x<PW;x++){
    const sx=x+shift;
    const canopy=120+Math.round(26*Math.sin(sx/11)+14*Math.sin(sx/3.3)+8*Math.sin(sx/1.7));
    const top=SKY_TOP+Math.round(canopy*(PH-SKY_TOP)/PH);
    for(let y=0;y<PH;y++)px[y*PW+x]=y<top?150+Math.round(20*y/PH):(Math.floor(sx/17)%3===0?65:28);
  }
  return px;
}
/** Two flat halves, 75 and 125. Variance 0.0625 - six times the variance floor
 *  that used to decide whether a frame could witness anything - against a
 *  gradient of exactly 0: Gh is 0.0161, from the one edge down the middle, and
 *  Gv is 0, because no row differs from the row above it. Nothing about this
 *  frame changes when it slides up or down, so it cannot witness, and the
 *  min(Gh, Gv) rule says so without needing a floor to catch it. Measured. */
function halves():Uint8Array{
  const px=new Uint8Array(PW*PH);
  for(let y=0;y<PH;y++)for(let x=0;x<PW;x++)px[y*PW+x]=x<PW/2?75:125;
  return px;
}
/** A vertical ramp, 60 at the top to 180 at the bottom. Gv is 0.0417 and Gh is
 *  exactly 0: it has structure, plenty of it, but all of it is across rows, so
 *  sliding it sideways does not change one count of it. The frame CANNOT
 *  witness a horizontal pan, and a rule that averaged the two directions would
 *  hand it a gradient of 0.0207, admit it as a witness, and let it vouch for a
 *  sideways pan of any speed for as long as it lasted. Measured. */
function verticalRamp():Uint8Array{
  const px=new Uint8Array(PW*PH);
  for(let y=0;y<PH;y++)for(let x=0;x<PW;x++)px[y*PW+x]=60+Math.round(120*y/(PH-1));
  return px;
}
/** A deterministic 32-bit xorshift. Each pixel of each frame gets its own draw
 *  from it, so the noise below is per-pixel independent - which a linear
 *  function of the pixel index is not: it cycles through its residues inside
 *  every block and cancels in the average, leaving a tenth of the noise its
 *  amplitude claims. */
function xorshift32(seed:number):()=>number{
  let x=seed|0;
  if(x===0)x=0x9e3779b9;
  return()=>{x^=x<<13;x|=0;x^=x>>>17;x^=x<<5;x|=0;return (x>>>0)/4294967296;};
}
/** Put per-pixel sensor noise of `sigma` luma levels on a frame, in place. The
 *  default is sigma 3, the noise GRADIENT_FLOOR is derived from and the
 *  convention the brief's own arithmetic uses. Twelve summed uniforms is the
 *  standard unit-variance approximation of a gaussian; times sigma gives sigma.
 *  Deterministic in the frame number, so nothing here passes or fails by luck.
 *  Measured through this module's grid at sigma 3: it moves a HELD frame by
 *  0.00348 on average and 0.00369 at worst over 30 pairs, and it lifts the
 *  frame's gradient by up to 0.0036 - most where the frame's pairs are flat,
 *  almost nothing where they already exceed the noise. That lift is why the
 *  noisy case below exists: it is enough to carry the thin treeline over
 *  GRADIENT_FLOOR. */
function withNoise(px:Uint8Array,frame:number,sigma=3):Uint8Array{
  const next=xorshift32(Math.imul(frame+1,2654435761));
  for(let i=0;i<px.length;i++){
    let sum=0;
    for(let k=0;k<12;k++)sum+=next();
    px[i]=Math.max(0,Math.min(255,Math.round(px[i]+sigma*(sum-6))));
  }
  return px;
}
/** A held treeline with that noise on it. */
const noisyTreeline=(frame:number):Uint8Array=>withNoise(treeline(0),frame);

test('One pixel of pan is under the frame-to-frame limit: only the anchor sees it',()=>{
  // The premise of the case below, pinned so it cannot quietly stop holding.
  // If this ever goes red the per-frame limit has been tightened instead, and
  // that limit also has to pass a real phone's sensor noise in low light.
  const s=new VisualStability();
  for(let at=0;at<=600;at+=100)s.observe(at,treeline(0),PW,PH);
  assert.equal(s.stableAt(600),true,'the setup frames should have settled');
  s.observe(700,treeline(1),PW,PH);
  assert.equal(s.stableAt(700),true,'one pixel in one frame is below the rate limit, as measured');
  // Mutation that reddens this (issue #42 item 4): STILL_CELLS = 0.145, which
  // halves the per-frame bound to 0.010116 against the measured 0.014206.
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
  // Mutation that reddens this (issue #42 item 4): ANCHOR_CELLS = 1e9, which
  // leaves only the per-frame rate test, and 0.0142 a frame is under it.
});

test('A NOISE-FREE thin treeline under a smooth sky reads UNKNOWN while panning, never still (issue #38)',()=>{
  // The scene a bound in IMAGE units cannot see: its anchor diff saturates near
  // 0.022, so against a fixed 0.04 this view read "still" throughout a
  // 1 px/frame pan and throughout a 4 px/frame one (30 deg/s).
  // The verdict on clean frames, and which of the two acceptable answers it is:
  // UNKNOWN. Its horizontal gradient is 0.0103 to 0.0116 across the pan, under
  // GRADIENT_FLOOR, so the frame has nothing to see a sideways shift with and
  // says so. Clean frames only: with a real camera's noise on them this scene
  // crosses the floor and is admitted - that is the case below, and it is the
  // one closer to the phone.
  // What happens when it IS admitted, measured over origins 0 to 49 of the pan
  // at the shipped constants, because the bound depends on which gradient
  // admitted it:
  //   * with min(Gh, Gv) the bound is 0.58 x 0.0109 = 0.0063 and the longest
  //     unbroken run from any origin is 5 frames, 167 ms - well inside the 15
  //     frames a settle needs, so the anchor bound refuses the hold by itself.
  //   * with the two directions POOLED, as they were before that rule, the
  //     smooth sky's vertical grade inflates G to 0.0259 and the bound to
  //     0.0150, and the longest run is 16 frames, 533 ms - just OUTSIDE a
  //     settle. There the anchor bound is NOT sufficient on its own, and what
  //     keeps this particular pan from vouching is where the breaks happen to
  //     fall. Observed, not argued: the pooled-mean mutation reddens the
  //     unknown-throughout assertion below and leaves the never-still one
  //     green. Pooling inflates the bound worst on exactly the scene issue #38
  //     is about, which is half the argument for taking the smaller axis.
  const s=new VisualStability();
  let vouched=false;
  const verdicts=new Set<string>();
  for(let i=0;i*FRAME_MS<=2000;i++){s.observe(i*FRAME_MS,thinTreeline(i),PW,PH);const v=s.stableAt(i*FRAME_MS);verdicts.add(String(v));if(v===true)vouched=true;}
  assert.equal(vouched,false,'a 1 px/frame pan of a low-gradient view read as still at some point');
  assert.deepEqual([...verdicts],['null'],'the 1 px/frame pan should be unjudgeable throughout, not merely not-still');
  const fast=new VisualStability();
  let vouchedFast=false;
  for(let i=0;i*FRAME_MS<=2000;i++){fast.observe(i*FRAME_MS,thinTreeline(i*4),PW,PH);if(fast.stableAt(i*FRAME_MS)===true)vouchedFast=true;}
  assert.equal(vouchedFast,false,'a 4 px/frame pan (30 deg/s) of a low-gradient view read as still at some point');
  // Two mutations redden this, one per assertion. GRADIENT_FLOOR = 0 admits the
  // scene as a witness and the verdicts become `false`, so the second assertion
  // fails. GRADIENT_FLOOR = 0 together with ANCHOR_CELLS = 1e9 removes the
  // second defence as well and the 1 px pan reads `true`, which is the defect
  // itself. ANCHOR_CELLS = 1e9 alone does NOT redden this any more: the floor
  // stops the scene before the bound is ever consulted.
  // The 4 px assertion is a second instance of the same claim and not an
  // independently reddenable one: with the floor and the anchor both removed it
  // is still caught by the per-frame RATE test, because 4 px a frame moves the
  // grid by 0.005870 at worst against a bound of 0.002978 (measured). No single
  // constant isolates it, and the assertion the two mutations above reach is
  // the 1 px one, which fires first.
});

test('The same pan with a real camera on it is ADMITTED, and still never reads still (issue #38)',()=>{
  // The case above is true of synthetic frames. A camera adds noise, noise adds
  // gradient, and this scene has the emptiest horizontal pairs of anything in
  // this file, so the noise has the field to itself there: measured, sigma 3
  // lifts its min-gradient from 0.0109 to 0.0123-0.0137, which straddles
  // GRADIENT_FLOOR. The frame is admitted as a witness with almost nothing to
  // witness with, and the whole weight then falls on the bounds.
  // They hold. Measured over this pan at sigma 1, 2, 3 and 4 the verdicts are
  // {null}, {null, false}, {false, null} and {false} - never `true` at any of
  // them, which is the claim issue #38 is actually about. Held rather than
  // panned, the same noisy scene settles to `true` at sigma 3, as it should:
  // it IS still then.
  const s=new VisualStability();
  let vouched=false;
  for(let i=0;i*FRAME_MS<=2000;i++){
    s.observe(i*FRAME_MS,withNoise(thinTreeline(i),i),PW,PH);
    if(s.stableAt(i*FRAME_MS)===true)vouched=true;
  }
  assert.equal(vouched,false,'a 1 px/frame pan of a noisy low-gradient view read as still at some point');
  // Mutation that reddens this: GRADIENT_FLOOR = 0 together with
  // ANCHOR_CELLS = 1e9. The floor is not what holds here - the noise already
  // carries the scene over it - so the mutation that matters is the one that
  // removes the bound, and the floor goes with it only to keep the frames
  // admitted at every sigma.
});

test('A view with structure in one direction only cannot vouch for a pan across it',()=>{
  // The residual issue #38 leaves if the two directions are averaged: a
  // vertical ramp has Gv 0.0417 and Gh 0, and a horizontal pan of it produces a
  // diff of exactly zero. Averaged, its gradient is 0.0207 - clear of any floor
  // here - so it would be admitted as a witness and would vouch for a pan of
  // any speed, for as long as the pan lasted. Taking the weaker axis, its
  // gradient is 0 and the honest answer is that it cannot be judged.
  const s=new VisualStability();
  for(let i=0;i*FRAME_MS<=2000;i++){
    const at=i*FRAME_MS;
    s.observe(at,verticalRamp(),PW,PH);
    assert.notEqual(s.stableAt(at),true,`a view with nothing across it vouched for a sideways pan at ${Math.round(at)} ms`);
  }
  assert.equal(s.stableAt(2000),null,'and the verdict is unknown, not moving: there is nothing here to see a pan with');
  // Mutation that reddens this: `gradient` returning the mean of the two
  // directions instead of the smaller - which is what it did before this case
  // existed. The frame then clears the floor and 2 s of identical frames
  // settles into `true`.
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

test('A frame with no gradient to see a shift by is unknown, never still, whatever its variance',()=>{
  // Variance says how much the frame VARIES; gradient says whether a shift
  // would be visible, and only the second is the question. Two flat halves
  // answer them opposite ways: variance 0.0625, six times the floor that used
  // to decide this, against a gradient of exactly 0 - one edge across, nothing
  // at all down. Measured.
  const s=new VisualStability();
  for(let at=0;at<=700;at+=100)s.observe(at,halves(),PW,PH);
  assert.equal(s.stableAt(700),null,'a frame whose only structure is one edge vouched for a hold it cannot witness');
  // Mutation that reddens this: GRADIENT_FLOOR = 0, which lets 700 ms of
  // identical frames settle and answer true.
});

test('A held treeline with per-pixel sensor noise still reads still',()=>{
  // The other side of the same bound: it has to absorb what a real camera does
  // to a frame that did not move, because that noise is what GRADIENT_FLOOR is
  // sized from. Per-pixel gaussian noise of sigma 3 luma levels, after the
  // 10x10 block average behind one grid cell, moves the grid by 0.00348 a frame
  // on average and 0.00369 at worst over 30 frames (measured) against a bound
  // of STILL_CELLS x 0.0698 = 0.0202 here, and against STILL_CELLS x
  // GRADIENT_FLOOR = 0.00371 for a frame sitting on the floor - which is the
  // tight end, and the reason the floor is where it is. A bound that cannot
  // absorb this would refuse every real hold.
  const s=new VisualStability();
  let last=0;
  for(let i=0;i*FRAME_MS<=800;i++){last=i*FRAME_MS;s.observe(last,noisyTreeline(i),PW,PH);}
  assert.equal(s.stableAt(last),true,'a held view with ordinary sensor noise on it read as moving');
  // Mutation that reddens this: STILL_CELLS = 0. The noise is what makes that
  // mutation bite - identical frames would differ by exactly 0 and survive it.
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
  // The frame at 700 breaks twice over - it is far from the blank frame before
  // it AND that frame witnessed nothing - and the pair rule runs before the
  // motion test, so the break is at 700 alone rather than spanning {600,700}.
  // Either way nothing at or before the blank frame is covered, which is the
  // guarantee this case is about.
  assert.deepEqual(c!.lastBreak,{from:700,to:700},'the break must end at the first frame after the blank one');
});

/** Fine texture on a flat base, at the real input size: a checkerboard on the
 *  scale of one grid cell, `levels` luma levels either side of 120. Every pixel
 *  of a 10x10 block - the block `resample` averages into one cell - moves by
 *  the whole part of `levels`, and that proportion of the block moves by one
 *  more, so the cell means land on exactly plus or minus `levels` for ANY
 *  amplitude. An earlier version bumped a proportion of the block by one level
 *  and nothing more, which silently saturated at one level: every amplitude
 *  above 1.0 produced the same frame, gradient 0.016667, and the fixture would
 *  have capped just under a floor rather than failing.
 *  A checkerboard is the one shape that can isolate the gradient rule, and the
 *  reason is worth stating: near GRADIENT_FLOOR the movement bound is small BY
 *  CONSTRUCTION, being STILL_CELLS times a gradient that is nearly the floor,
 *  so a frame can only lose its gradient without reading as movement if it
 *  loses far more gradient than frame. A checkerboard puts every pair of
 *  adjacent cells on opposite sides of the base, in both directions at once, so
 *  Gh and Gv are both 2 x levels / 120 while a change of amplitude moves the
 *  frame by only the change itself. Measured: fading 0.88 to 0.68 takes the
 *  gradient from 0.014667 to 0.011333, across GRADIENT_FLOOR (0.0128) by 15
 *  percent above and 11 percent below, while the pair differs by 0.001667 -
 *  inside the dimmer frame's own movement bound of 0.003287 and inside the
 *  brighter frame's 0.004253, so NOTHING but the gradient floor separates them.
 *  Note the variance of the lit frame: 5.4e-5, a two-hundredth of the variance
 *  floor this rule replaces. It reads as a usable witness and the two flat
 *  halves above, with a thousand times its variance, do not. That inversion is
 *  the whole of issue #38. */
function fineTexture(levels:number):Uint8Array{
  const px=new Uint8Array(PW*PH);
  const cw=PW/GRID_W,ch=PH/GRID_H;
  const whole=Math.floor(levels),bumped=Math.round((levels-whole)*cw*ch);
  for(let y=0;y<PH;y++)for(let x=0;x<PW;x++){
    const sign=(Math.floor(x/cw)+Math.floor(y/ch))%2?-1:1;
    px[y*PW+x]=120+sign*(whole+((y%ch)*cw+(x%cw)<bumped?1:0));
  }
  return px;
}

test('A frame too smooth to judge breaks the run even though no pair moved',()=>{
  // The case above cannot fail for the gradient rule on its own: the blank
  // frame is also far from its neighbours, so the motion rule would record a
  // break there anyway. Here the dip happens with every pair INSIDE the
  // movement limit - a view losing the last of its fine detail, not moving - so
  // the gradient rule is the only thing that can see it. Without it the run
  // reaches back across the frames nothing could be judged from, and the
  // continuity vouches for a reading taken before them.
  // The premise first, pinned so it cannot quietly stop holding.
  const dim=new VisualStability();
  for(let t=0;t<=600;t+=100)dim.observe(t,fineTexture(0.68),PW,PH);
  assert.equal(dim.stableAt(600),null,'the near-floor frame must read as unjudgeable');
  const lit=new VisualStability();
  for(let t=0;t<=600;t+=100)lit.observe(t,fineTexture(0.88),PW,PH);
  assert.equal(lit.stableAt(600),true,'the textured frame must read as still');

  const s=new VisualStability();
  for(let t=0;t<=800;t+=100)s.observe(t,fineTexture(0.88),PW,PH);
  s.observe(900,fineTexture(0.68),PW,PH);   // the fine detail dips under the floor
  for(let t=1000;t<=1500;t+=100)s.observe(t,fineTexture(0.88),PW,PH);
  const c=s.continuity(1500);
  assert.ok(c,'the view is textured and settled again by 1500');
  // 1000 and not 900: the frame at 1000 is the first textured one, but the
  // frame BEFORE it watched nothing, so the pair 900/1000 cannot open a run
  // either and the run opens on 1000/1100 (issue #49 item 1, and the case
  // below, which is about that one interval and nothing else).
  assert.equal(c!.stillSince,1000,'the run must restart at the first pair of frames that could both witness');
  assert.deepEqual(c!.lastBreak,{from:1000,to:1000},'the break runs to the first frame with a judgeable frame behind it');
  // Two mutations, one per half of this case, because the two halves are graded
  // by different code.
  // The PREMISES are graded by the floor: GRADIENT_FLOOR = 0 makes the
  // near-floor frame read still instead of unjudgeable, and observed red is the
  // first premise. (That mutation reddens seven cases in this file, so the floor
  // is not the thing short of mutants.)
  // The BODY - the run restarting at 1000 and the break at {1000, 1000} - is
  // graded by the one rule in `observe` that refuses a pair unless BOTH its
  // frames could witness, which is where the floor's verdict is actually spent.
  // Mutation: delete `if(!this.canWitness||!previousWitnessed)`. Observed red:
  // "the run must restart at the first pair of frames that could both witness",
  // together with the two other cases that pin that rule.
});

test('A textured pair after an untextured frame opens the run at ITSELF, not at the frame that watched nothing',()=>{
  // Issue #49 item 1. The frame at 0 has too little gradient to see a shift by,
  // so it witnessed nothing while it was the newest frame - and yet the pair
  // 0/100 read as a hold and opened the run at 0, crediting the settle with one
  // frame interval nothing watched. The pair is inside the movement bound by
  // construction (`fineTexture` above: the two frames differ by 0.001667
  // against the lit frame's bound of 0.004253), which is why the motion test
  // cannot catch this and a rule of its own has to.
  // The instants: untextured at 0, textured at 100, 200, ... 600.
  //   with the fix    the run opens on the pair 100/200, stillSince = 100, and
  //                   settles SETTLE_MS = 500 later, at the frame at 600;
  //   without it      stillSince = 0 and it settles at the frame at 500.
  // 550 is strictly inside that difference and is read with the newest frame at
  // 500, 50 ms old against STALE_FRAME_MS, so freshness decides nothing here.
  const s=new VisualStability();
  s.observe(0,fineTexture(0.68),PW,PH);
  for(let t=100;t<=500;t+=100)s.observe(t,fineTexture(0.88),PW,PH);
  assert.equal(s.stableAt(550),false,
    'the settle was credited the interval after a frame that could not witness anything');
  s.observe(600,fineTexture(0.88),PW,PH);
  assert.equal(s.stableAt(600),true,'a run opened at 100 must settle at 600');
  const c=s.continuity(600);
  assert.equal(c!.stillSince,100,'the run did not open on the first pair of judgeable frames');
  // {100,100} and not {0,100}: the frame at 100 is refused for what the frame
  // before it could see, not for how far the two differ, so this also says the
  // motion branch is not what produced the break.
  assert.deepEqual(c!.lastBreak,{from:100,to:100},'the break did not end at the first judgeable frame');
  // The control, which is the same timeline with a textured frame at 0: there
  // the run legitimately opens at 0 and 550 is past the settle. So the only
  // thing 550 is measuring above is the interval the untextured frame cost.
  const control=new VisualStability();
  for(let t=0;t<=500;t+=100)control.observe(t,fineTexture(0.88),PW,PH);
  assert.equal(control.stableAt(550),true,'the control never settled, so the case above proves nothing');
  // Mutation: delete `if(!this.canWitness||!previousWitnessed)` in `observe` -
  // one branch, because a frame that cannot witness and the frame after it are
  // one rule. Observed red: "the settle was credited the interval after a frame
  // that could not witness anything", with the two cases above red on their own
  // messages (20/23).
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
