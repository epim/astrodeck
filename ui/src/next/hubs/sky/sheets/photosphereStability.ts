// Is the CAMERA VIEW holding still? The orientation sensor cannot answer that:
// Chromium only emits deviceorientation on a change of 0.1 degree, so a phone
// held still emits nothing and silence is indistinguishable from a dead sensor.
// The video stream is a second, independent witness. It reports three states,
// and the third one matters: still, moving, and unknown. A stopped stream is
// unknown - it must never be mistaken for a steady view, and neither must a
// view with too little spatial gradient in it for a shift to show. Those two
// unknowns are not the same situation, so `witness` names which one this is:
// a caller may keep believing a sensor reading through a blank view and must
// not through a stopped one (issue #41).
// No DOM here: the caller samples the pixels, this only measures them.

/** How long the view must hold still before stillness is believed. */
export const SETTLE_MS = 500;
/** Beyond this age the newest frame vouches for nothing: the video may have
 *  stopped, the page may be hidden. Stability becomes unknown, not false. */
export const STALE_FRAME_MS = 1000;
/** Ceiling for "still", in CELLS OF APPARENT MOTION rather than in luminance:
 *  the mean absolute difference between two consecutive mean-normalised frames
 *  must stay under this many times the frame's own spatial gradient `G` (see
 *  `gradient`). How much ROTATION a given diff stands for depends entirely on
 *  how much structure the frame has, which is issue #38, and G is the scale
 *  that takes most of that dependence out: a bound of `STILL_CELLS * G` means
 *  roughly the same amount of real movement on a treeline and on a thin
 *  treeline under a smooth sky, where a fixed luminance bound differed by 7x.
 *  Roughly, and no more than roughly. What one cell of real shift costs in the
 *  diff, divided by G, is measured at 2.0 on the treeline, 1.4 on the 32x24
 *  `scene` fixture and 1.2 on the thin treeline - a factor of 1.7 across the
 *  three, so this is a per-scene SCALE and not an invariant, and it must not be
 *  read as an angle.
 *  The value: the treeline scene has G = 0.069766 measured at 320x240, and the
 *  multiple that reproduces the 0.02 this replaces is 0.2867; 0.29 rounds that
 *  to two figures and lands 1.2 percent above, at 0.020232. On that scene a
 *  1 px/frame pan (7.5 deg/s at 30 fps over a 60 degree short axis) moves the
 *  grid by 0.014206 a frame, 70 percent of the bound, so one pixel a frame
 *  still reads as a hold and two do not (0.028825).
 *  The other end is sensor noise, and it is what GRADIENT_FLOOR is derived
 *  from: per-pixel gaussian noise of sigma 3 luma levels moves a HELD frame's
 *  grid by 0.00348 on average and 0.00369 at worst over 30 frames, measured. */
export const STILL_CELLS = 0.29;
/** Ceiling for the diff against the ANCHOR - the frame the current settle
 *  began on - in the same cells of apparent motion. The consecutive-frame test
 *  above is a RATE test and nothing more: a pan slow enough to stay under it
 *  accumulates without bound while every single frame reads as still. Holding
 *  the anchor bounds the TOTAL drift since the settle instead of the rate.
 *  Twice STILL_CELLS, which on the treeline is 0.040464 against the 0.04 it
 *  replaces: the 1 px/frame pan crosses it at 3 frames, 0.10 s (measured: 2
 *  frames 0.028825 is inside, 3 frames 0.042692 is outside; the run opens at
 *  33.3 ms and breaks at 100.0 ms), far inside SETTLE_MS, so it never reads as
 *  still at all. A genuine hold has room: normalisation already removes an
 *  exposure change (a 30 percent step on that scene is 0.001073, measured), and
 *  sensor noise at sigma 3 moves a held frame 0.00363 from its anchor at worst
 *  over 30 frames, which is the quantity this bound sees. */
export const ANCHOR_CELLS = 0.58;
/** Floor on the frame's own spatial gradient, below which the frame cannot
 *  witness motion AT ALL: a shift of the scene would not change it enough to
 *  see, so "unchanged" is a statement about the SCENE and not about the phone.
 *  Stability is unknown there, never still (spec 2.6: preserve uncertainty).
 *  The value is the one that makes the per-frame bound self-consistent with the
 *  noise it has to absorb, and it is derived as exactly that:
 *
 *      GRADIENT_FLOOR = noise diff / STILL_CELLS = 0.0037 / 0.29 = 0.01276
 *
 *  rounded up to 0.0128. The noise diff is measured, not assumed: per-pixel
 *  gaussian noise of sigma 3 luma levels, averaged over the 10x10 block behind
 *  one grid cell, moves a held treeline's grid by 0.00348 on average and
 *  0.00369 at worst over 30 frames. Any lower floor and `STILL_CELLS * G` for a
 *  frame sitting on the floor would be under that, so a phone that was actually
 *  still would read as MOVING on its own sensor noise, and the cue shown - hold
 *  still - would be the one thing that could not clear it.
 *  That self-consistency is 99 in 100, not always, and the margin is worth
 *  stating because the 0.0037 above is a worst-of-30 sample. Over 300 held
 *  pairs the same noise averages 0.003496 and reaches 0.003831, and 3 of the
 *  300 exceed the 0.003712 bound - so a frame sitting EXACTLY on the floor
 *  loses about one settle in seven, and the user waits and holds again. The
 *  direction is safe (a broken settle is a refusal, never a wrong pose) and the
 *  band is narrow: at 1.1 times the floor the bound is 0.00408 and nothing in
 *  300 pairs reaches it.
 *  This replaces a floor on the grid's VARIANCE, which answers a different
 *  question - how much the frame varies - and gets exactly the case in issue
 *  #38 wrong: two flat halves have variance 0.0625, six times the old 0.01
 *  floor, and a gradient of 0, because a horizontal pan moves one edge and a
 *  vertical one moves nothing. Variance says how much there is; gradient says
 *  whether a shift would be visible, and only the second is the question.
 *  Measured at 320x240, min(Gh, Gv), on NOISE-FREE fixtures: treeline against
 *  sky 0.0698; a thin treeline under a smooth sky 0.0103 to 0.0116 over a pan,
 *  under the floor - that is the scene issue #38 was written about; smooth
 *  overcast (base 90, spread 20) 0.0017; two flat halves 0; a vertical ramp 0;
 *  a blank wall 0.
 *  A real camera changes that picture and the file should not pretend
 *  otherwise. Noise has a gradient of its own, and the lift it adds is an upper
 *  bound rather than a constant offset: up to 0.0036 where the frame's pairs
 *  are flat and the noise has the field to itself (two flat halves 0 -> 0.0036,
 *  a vertical ramp 0 -> 0.0030, both still far under the floor and still
 *  unknown at sigma 6), about 0.0022 where the structure is patchy (the thin
 *  treeline 0.0109 -> 0.0130, which CROSSES), and nil where every pair already
 *  exceeds the noise (the fine-texture fixture, 0.000015).
 *  So the thin treeline is under the floor clean and just over it on a real
 *  camera. It is not that such a frame cannot witness; it is that it is
 *  admitted with almost nothing to witness with, and then the bounds are what
 *  refuse the pan, on a bound of 0.58 x 0.0123 = 0.0071 rather than the 0.0150
 *  the two directions pooled would have given it. Measured over that scene's
 *  1 px/frame pan with noise on it: the verdicts are unknown at sigma 1, unknown
 *  and moving at sigma 2 and 3, moving at sigma 4, and still at no sigma. */
export const GRADIENT_FLOOR = 0.0128;

/** The comparison grid. Small on purpose: this runs on the UI thread on a
 *  phone, once per video frame. */
export const GRID_W = 32, GRID_H = 24;

/** Box-average `luma` into the fixed comparison grid, so two frames are always
 *  compared cell for cell even if the video size changes mid-scan. The live
 *  caller hands this an already-downscaled GRID_W x GRID_H buffer - `drawImage`
 *  does the filtering on the GPU, which is far cheaper than reading a full
 *  frame back - so in production this is a same-size guard, not a filter. It
 *  does the real work for any other size, which is what the tests feed it. */
function resample(luma:Uint8Array|Uint8ClampedArray,width:number,height:number):Float64Array {
  const grid=new Float64Array(GRID_W*GRID_H);
  for(let gy=0;gy<GRID_H;gy++){
    const y0=Math.floor(gy*height/GRID_H),y1=Math.min(height,Math.max(y0+1,Math.floor((gy+1)*height/GRID_H)));
    for(let gx=0;gx<GRID_W;gx++){
      const x0=Math.floor(gx*width/GRID_W),x1=Math.min(width,Math.max(x0+1,Math.floor((gx+1)*width/GRID_W)));
      let sum=0,count=0;
      for(let y=y0;y<y1;y++)for(let x=x0;x<x1;x++){sum+=luma[y*width+x];count++;}
      grid[gy*GRID_W+gx]=count?sum/count:0;
    }
  }
  return grid;
}

/** Divide by the frame's own mean, so auto-exposure and a passing cloud change
 *  every pixel together without reading as movement. A black frame stays zero. */
function normalise(grid:Float64Array):Float64Array {
  let sum=0;
  for(const v of grid)sum+=v;
  const mean=sum/grid.length;
  if(!(mean>0))return grid;
  for(let i=0;i<grid.length;i++)grid[i]/=mean;
  return grid;
}

function meanAbsDiff(a:Float64Array,b:Float64Array):number {
  let sum=0;
  for(let i=0;i<a.length;i++)sum+=Math.abs(a[i]-b[i]);
  return sum/a.length;
}

/** How visible a shift would be in this frame: the SMALLER of the mean absolute
 *  difference between horizontally adjacent cells of the NORMALISED grid and
 *  the same between vertically adjacent cells. Dimensionless, like the grid.
 *  The smaller, not the average of the two, because the phone can pan along
 *  either axis and a frame can only witness a shift along an axis it has
 *  structure on. A vertical ramp is the clean case: Gv = 0.0417, Gh = 0, and
 *  slid sideways it does not change by one count. Averaged, its G is 0.0207,
 *  which clears the floor, so it would be admitted as a witness and would then
 *  vouch for a sideways pan of any speed for as long as it lasted - which is
 *  the sentence issue #38 opens with, one axis over. The weaker axis is what
 *  the frame can honestly say. Two flat halves are the same case (Gv = 0) and
 *  come out right for the right reason rather than by squeaking under a
 *  combined floor.
 *  This is a per-scene SCALE and not an invariant: see STILL_CELLS for how far
 *  the cost of one cell of real shift wanders from it (a factor of 1.7 across
 *  the three fixtures it is measured on).
 *  Two passes over 768 cells per frame, negligible beside the `getImageData`
 *  the caller already does. */
export function gradient(grid:Float64Array):number {
  let h=0,hPairs=0,v=0,vPairs=0;
  for(let y=0;y<GRID_H;y++)for(let x=1;x<GRID_W;x++){h+=Math.abs(grid[y*GRID_W+x]-grid[y*GRID_W+x-1]);hPairs++;}
  for(let y=1;y<GRID_H;y++)for(let x=0;x<GRID_W;x++){v+=Math.abs(grid[y*GRID_W+x]-grid[(y-1)*GRID_W+x]);vPairs++;}
  return Math.min(hPairs?h/hPairs:0,vPairs?v/vPairs:0);
}

/** Since WHEN has the view been unchanged, and when was it last not?
 *  `stillSince` is the frame time the current uninterrupted still run began
 *  on: the view is KNOWN unchanged from there to the newest frame. A
 *  continuity only exists once that run has settled (see `stableAt`), because
 *  an unsettled run vouches for nothing yet.
 *  `lastBreak` is the most recent interval `[from, to]`, in frame time, in
 *  which the view moved, could not be judged, or was not observed - `null` if
 *  none has been seen since the object was cleared. A reading taken before
 *  `lastBreak.from` is NOT covered by this continuity, however long the run
 *  since has lasted: something happened between the reading and the run that
 *  this witness cannot account for.
 *  Strictly, though: the consumer compares against `from` plus a margin, not
 *  against `from`, because frame times and sensor times are not aligned to the
 *  millisecond - see CONTINUITY_SLOP_MS and `viewVouchesFor` in
 *  photospherePose.ts, which is where that comparison lives. Nothing in this
 *  file knows about readings at all. */
export interface ViewContinuity { stillSince: number; lastBreak: { from: number; to: number } | null }

/** Tracks how long the camera view has been unchanged, and when it last was
 *  not. */
export class VisualStability {
  private frame:Float64Array|null=null;
  private anchor:Float64Array|null=null;
  private frameAt=-Infinity;
  private stillSince:number|null=null;
  private lastBreak:{from:number;to:number}|null=null;
  /** Whether the newest frame has enough spatial gradient to see a shift by. */
  private canWitness=false;
  clear(){this.frame=null;this.anchor=null;this.frameAt=-Infinity;this.stillSince=null;this.lastBreak=null;this.canWitness=false;}

  /** `luma` is one byte per pixel, row-major, `width` x `height`. */
  observe(at:number,luma:Uint8Array|Uint8ClampedArray,width:number,height:number):void {
    if(!Number.isFinite(at)||at<this.frameAt)return;
    if(!(width>0)||!(height>0)||luma.length<width*height)return;
    const grid=normalise(resample(luma,width,height));
    const previous=this.frame,previousAt=this.frameAt,gap=at-previousAt;
    // The bounds below are multiples of the CURRENT frame's gradient. The
    // anchor's would do very nearly as well and nothing here can tell the two
    // apart: on a still scene they are the same structure, and swapping this
    // for `gradient(this.anchor)` leaves every case in the suite green. The
    // current frame's is taken because it is the frame whose shift is being
    // judged and the one that exists in every branch - the first still pair has
    // no anchor yet - not because a case demands it.
    const g=gradient(grid);
    this.frame=grid;this.frameAt=at;this.canWitness=g>=GRADIENT_FLOOR;
    // A frame with nothing in it to judge movement by watched nothing, so the
    // run cannot reach back across it: it breaks at its own instant.
    if(!this.canWitness){this.broken(at,at);return;}
    // Nothing watched the view across an unobserved gap, so nothing can vouch
    // for it: start the settle over rather than crediting the missing time,
    // and let the break end HERE, so nothing before this frame is covered.
    if(!previous||gap>STALE_FRAME_MS){this.broken(at,at);return;}
    // Motion between these two frames. It may have begun anywhere inside the
    // pair, which is why the break starts at the earlier frame.
    if(meanAbsDiff(previous,grid)>STILL_CELLS*g){this.broken(previousAt,at);return;}
    // The frame the settle began on is kept and re-compared every frame. Without
    // it this is only a speed limit, and a slow pan drifts arbitrarily far while
    // each step stays under it. With it the verdict means what the caller reads
    // it as: the view has not moved since the settle started.
    if(this.stillSince===null||!this.anchor){this.stillSince=previousAt;this.anchor=previous;return;}
    // A drift caught against the anchor happened at an unknown moment of the
    // run, so nothing before now is vouched for.
    if(meanAbsDiff(this.anchor,grid)>ANCHOR_CELLS*g)this.broken(at,at);
  }

  /** Close the current still run and remember the interval that ended it. */
  private broken(from:number,to:number):void {
    this.stillSince=null;this.anchor=null;this.lastBreak={from,to};
  }

  /** WHY the view says what it says, in one word, for a caller that has to tell
   *  the two unknowns apart. `stableAt` grades these same facts into a
   *  tri-state and loses that distinction - 'featureless' and 'stale' are both
   *  `null` there - yet they are opposite situations: one is a camera working
   *  perfectly, delivering frames, pointed at a patch of sky with nothing in it
   *  a shift would move; the other is a camera that has stopped and may be
   *  showing anything at all. A caller holding a sensor reading may keep
   *  believing it through the first and must not through the second (issue
   *  #41).
   *  'stale' is tested FIRST and that order is the whole guarantee: a stopped
   *  stream whose last frame happened to be featureless is stale, not
   *  featureless, so the softer answer can never swallow the harder one.
   *  'moving' covers an unsettled run as well as a broken one, exactly as
   *  `stableAt`'s `false` does: a settle in progress is not evidence of a hold.
   *  This is the ONLY place the facts are graded. `stableAt` is a projection of
   *  it rather than a second copy, so the two cannot drift apart or disagree
   *  about a boundary. */
  witness(now:number):'still'|'moving'|'featureless'|'stale' {
    if(!this.frame||now-this.frameAt>STALE_FRAME_MS)return 'stale';
    if(!this.canWitness)return 'featureless';
    if(this.stillSince===null)return 'moving';
    return this.frameAt-this.stillSince>=SETTLE_MS?'still':'moving';
  }

  /** `true` still, `false` moving, `null` unknown (no frame, none recently, or
   *  a frame with nothing in it to judge movement by).
   *  `now` decides one thing only: whether the newest frame is fresh enough to
   *  say anything at all. The LENGTH of the still run is a property of the
   *  frames, measured between them, because they are what watched the view. A
   *  camera with a pipeline delay hands over a frame captured `lag` ms ago;
   *  crediting the run with the time between that capture and the caller's
   *  clock would settle the view on stillness nobody observed, and the longer
   *  the delay the less watching it would take.
   *  Public, though `continuity()` is what the driver reads: its callers are
   *  `continuity()` and the tests that pin each of the three answers directly. */
  stableAt(now:number):boolean|null {
    const verdict=this.witness(now);
    return verdict==='still'?true:verdict==='moving'?false:null;
  }

  /** Since when has THIS view been continuous? Null until the run has settled
   *  (see stableAt), because an unsettled run vouches for nothing yet. The
   *  break is copied: a caller holding the answer must not be able to edit the
   *  witness's own record of it, nor see it change under them. */
  continuity(now:number):ViewContinuity|null {
    if(this.stableAt(now)!==true||this.stillSince===null)return null;
    return {stillSince:this.stillSince,lastBreak:this.lastBreak && {...this.lastBreak}};
  }
}
