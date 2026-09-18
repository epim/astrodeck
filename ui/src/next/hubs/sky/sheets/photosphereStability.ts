// Is the CAMERA VIEW holding still? The orientation sensor cannot answer that:
// Chromium only emits deviceorientation on a change of 0.1 degree, so a phone
// held still emits nothing and silence is indistinguishable from a dead sensor.
// The video stream is a second, independent witness. It reports three states,
// and the third one matters: still, moving, and unknown. A stopped stream is
// unknown - it must never be mistaken for a steady view, and neither must a
// view with no texture in it to judge movement by.
// No DOM here: the caller samples the pixels, this only measures them.

/** How long the view must hold still before stillness is believed. */
export const SETTLE_MS = 500;
/** Beyond this age the newest frame vouches for nothing: the video may have
 *  stopped, the page may be hidden. Stability becomes unknown, not false. */
export const STALE_FRAME_MS = 1000;
/** Ceiling for "still": the mean absolute per-pixel difference between two
 *  consecutive mean-normalised frames, as a fraction of a frame's own mean
 *  luminance (dimensionless, 0.02 = 2 percent of mean luminance per pixel).
 *  Measured on the low-contrast 32x24 gradient and bright block used by the
 *  tests: a one-pixel shift is 0.037, a 30 percent exposure change 0.002. The
 *  limit sits between them with room for sensor noise, which after averaging
 *  each grid cell from hundreds of video pixels lands near 0.006 in low light. */
export const STILL_DIFF_LIMIT = 0.02;
/** Ceiling for the diff against the ANCHOR - the frame the current settle
 *  began on. The consecutive-frame test above is a RATE test and nothing more:
 *  a pan slow enough to stay under it accumulates without bound while every
 *  single frame reads as still. Measured on a 320x240 treeline scene at 30 fps
 *  and a 60 degree short axis, a 1 px/frame pan (7.6 deg/s) moves the grid by
 *  0.0142 per frame - under STILL_DIFF_LIMIT - and had swept 15 degrees
 *  after two seconds of being called "still". Holding the anchor bounds the
 *  TOTAL drift since the settle instead of the per-frame rate, and that pan
 *  now crosses this limit in 0.10 s (measured: the run opens at 33.3 ms and
 *  breaks at 100.0 ms, three frames at 30 fps), far inside SETTLE_MS, so it
 *  never reads as still at all. Twice STILL_DIFF_LIMIT leaves a genuine hold ample room:
 *  normalisation already removes an exposure change (0.002 for 30 percent),
 *  and grid-cell averaging leaves sensor noise near 0.006. */
export const ANCHOR_DIFF_LIMIT = 0.04;
/** Floor on the variance of the NORMALISED grid, below which the frame has no
 *  texture to judge movement by - a blank wall, a fogged lens, a dark dome, a
 *  frame of nothing but smooth sky. Panning such a view barely changes a pixel,
 *  so "unchanged" would be a statement about the SCENE and not about the phone.
 *  Stability is unknown there, never still (spec 2.6: preserve uncertainty).
 *  Dimensionless, because the grid is divided by its own mean, so this is a
 *  squared coefficient of variation: 0.01 is an RMS deviation of 10 percent of
 *  mean luminance across the frame. Measured at 320x240: a treeline against
 *  sky is 0.42, a smooth overcast sky (base 90, spread 20) is 0.0036 - and
 *  that overcast frame is exactly the one that read "still" through a 75 deg/s
 *  pan, because a linear ramp slid sideways is a linear ramp plus a constant
 *  and mean-normalisation removes the constant. No frame comparison can see
 *  that motion, so the honest verdict is that it cannot be judged. */
export const TEXTURE_FLOOR = 0.01;

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

/** How much the frame varies across itself. A normalised grid has mean 1, but
 *  a black frame keeps its zeros (see `normalise`), so the mean is measured
 *  rather than assumed. */
function variance(grid:Float64Array):number {
  let sum=0;
  for(const v of grid)sum+=v;
  const mean=sum/grid.length;
  let acc=0;
  for(const v of grid)acc+=(v-mean)*(v-mean);
  return acc/grid.length;
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
  private textured=false;
  clear(){this.frame=null;this.anchor=null;this.frameAt=-Infinity;this.stillSince=null;this.lastBreak=null;this.textured=false;}

  /** `luma` is one byte per pixel, row-major, `width` x `height`. */
  observe(at:number,luma:Uint8Array|Uint8ClampedArray,width:number,height:number):void {
    if(!Number.isFinite(at)||at<this.frameAt)return;
    if(!(width>0)||!(height>0)||luma.length<width*height)return;
    const grid=normalise(resample(luma,width,height));
    const previous=this.frame,previousAt=this.frameAt,gap=at-previousAt;
    this.frame=grid;this.frameAt=at;this.textured=variance(grid)>=TEXTURE_FLOOR;
    // A frame with nothing in it to judge movement by watched nothing, so the
    // run cannot reach back across it: it breaks at its own instant.
    if(!this.textured){this.broken(at,at);return;}
    // Nothing watched the view across an unobserved gap, so nothing can vouch
    // for it: start the settle over rather than crediting the missing time,
    // and let the break end HERE, so nothing before this frame is covered.
    if(!previous||gap>STALE_FRAME_MS){this.broken(at,at);return;}
    // Motion between these two frames. It may have begun anywhere inside the
    // pair, which is why the break starts at the earlier frame.
    if(meanAbsDiff(previous,grid)>STILL_DIFF_LIMIT){this.broken(previousAt,at);return;}
    // The frame the settle began on is kept and re-compared every frame. Without
    // it this is only a speed limit, and a slow pan drifts arbitrarily far while
    // each step stays under it. With it the verdict means what the caller reads
    // it as: the view has not moved since the settle started.
    if(this.stillSince===null||!this.anchor){this.stillSince=previousAt;this.anchor=previous;return;}
    // A drift caught against the anchor happened at an unknown moment of the
    // run, so nothing before now is vouched for.
    if(meanAbsDiff(this.anchor,grid)>ANCHOR_DIFF_LIMIT)this.broken(at,at);
  }

  /** Close the current still run and remember the interval that ended it. */
  private broken(from:number,to:number):void {
    this.stillSince=null;this.anchor=null;this.lastBreak={from,to};
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
   *  Public, though `continuity()` is what the driver reads: this is the only
   *  place the tri-state is graded, and its callers are `continuity()` and the
   *  tests that pin each of the three answers directly. */
  stableAt(now:number):boolean|null {
    if(!this.frame||now-this.frameAt>STALE_FRAME_MS)return null;
    if(!this.textured)return null;
    if(this.stillSince===null)return false;
    return this.frameAt-this.stillSince>=SETTLE_MS;
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
