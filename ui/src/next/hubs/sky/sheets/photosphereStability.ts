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
 *  otherwise. Noise has a gradient of its own, and the lift it adds to the
 *  MEASUREMENT is an upper bound rather than a constant offset: at sigma 3, up
 *  to 0.0034 where the frame's pairs are flat and the noise has the field to
 *  itself (two flat halves 0 -> 0.0034, a vertical ramp 0 -> 0.0029), about
 *  0.0021 where the structure is patchy (the thin treeline 0.0109 -> 0.0130,
 *  which CROSSES this floor), and nil where every pair already exceeds the
 *  noise (the fine-texture fixture, 0.00006).
 *  That lift is now taken off before this floor is consulted at all, by
 *  `noiseGradient` (issue #62), so what is compared here is the frame's SCENE:
 *  the same thin treeline measures 0.0099-0.0115 corrected at sigma 1, 2, 3 and
 *  4 alike against 0.0103-0.0116 clean, and is under the floor at every one of
 *  them rather than straddling it. This constant's own derivation did not
 *  change and did not need to - its input is the noise in the frame-to-frame
 *  DIFF, which is still 0.0037 and is not corrected - but its MEANING did: the
 *  self-consistency argument above is between two quantities that are now both
 *  about the scene, where before the floor was compared against a number the
 *  noise had already inflated and a frame with 0.0107 of structure was admitted
 *  in place of one with 0.0128.
 *  Held rather than panned, the thin treeline reads unknown too: on that scene
 *  the video vouches for nothing either way, the reading stands on the driver's
 *  featureless memory (issue #41) instead, and the cue names the view. */
export const GRADIENT_FLOOR = 0.0128;
/** The width of the hysteresis band on that floor, as a fraction of it: a frame
 *  ENTERS the featureless state at `GRADIENT_FLOOR` and LEAVES it only at
 *  `GRADIENT_FLOOR * (1 + GRADIENT_HYSTERESIS)` = 0.016.
 *  A bare threshold made the floor a switch that a view sitting on it flips
 *  frame by frame, and the whole driver flips with it (issue #75). The rule
 *  that refuses a pair unless BOTH frames could witness means the first frame
 *  back over the floor answers 'moving' - its predecessor watched nothing - so
 *  a view wandering across the floor reads featureless, moving, featureless,
 *  moving, and the dome, the aim dot, the Start scan gate and the cue alternate
 *  with it at frame rate, half of them landing on "move the phone gently" over
 *  a phone that is holding still. That is a worse failure than either steady
 *  answer: a wrong steady answer can be reasoned about, an alternating one
 *  cannot be acted on at all.
 *  The size is derived from whatever makes a frame's measured gradient wander
 *  frame to frame while the view is doing nothing, and that input CHANGED when
 *  `noiseGradient` landed (issue #62), so the derivation is given twice here:
 *  the old one, because the value is the same and it would otherwise look
 *  unexamined, and the one that now holds.
 *  It used to be sensor noise. On a frame whose own pairs are nearly flat -
 *  which is every frame near this floor - sigma 3 per-pixel noise puts up to
 *  0.0027 of gradient there, so for noise alone never to lift a featureless
 *  frame back over the exit the band had to be at least
 *  `0.0027 / GRADIENT_FLOOR` = 0.2109, and 0.25 was that rounded up: the whole
 *  band was the noise plus 19 percent.
 *  That term is now subtracted off rather than absorbed, and what survives it is
 *  small: over 30 frames at sigma 4, the largest corrected gradient of a frame
 *  with NO true gradient is 0.000331 (two flat halves; a vertical ramp 0.000264,
 *  a blank frame 0.000230), which on its own would ask for a band of only
 *  `0.000331 / GRADIENT_FLOOR` = 0.026. Noise no longer binds this constant.
 *  What binds it now is the scene: a view drifting across the sky changes its
 *  own TRUE gradient from frame to frame, and near this floor that is the whole
 *  of the wander. Measured on the thin treeline over 61 frames of a 1 px/frame
 *  pan, corrected, the gradient covers a range of 0.001596 at sigma 4 and
 *  0.001285 clean, the largest single-frame step being 0.000553. A view whose
 *  mean sat on the floor with that wander needs
 *
 *      GRADIENT_HYSTERESIS >= 0.001596 / GRADIENT_FLOOR = 0.1247
 *
 *  and 0.25 clears it with the width again in hand. The value is kept rather
 *  than cut to 0.15: one fixture's pan is a thin thing to size a constant to
 *  with 20 percent of headroom, the promise the paragraph below makes is a
 *  quarter and is worth keeping stable, and the only cost of the wider band is
 *  that a frame entering from the featureless state must clear 0.016 instead of
 *  0.0147.
 *  What it does NOT promise: a scene whose TRUE gradient swings by more than a
 *  quarter still crosses both edges, and then the verdict still changes. It
 *  should: that is the view changing, not the witness dithering. And the first
 *  frame of a run that does clear the exit still answers 'moving' once, for the
 *  pair rule above - one frame on a real change of scene, not a standing
 *  alternation.
 *  A witness that has seen no judgeable frame yet starts in the featureless
 *  state, so the first frame is held to the exit rather than the floor. One
 *  rule and no special case, and it errs the safe way: a marginal opening frame
 *  is unknown for a moment rather than a witness. */
export const GRADIENT_HYSTERESIS = 0.25;

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
 *  What it returns is the frame AS MEASURED, sensor noise and all, because that
 *  is all a grid can say; `observe` takes `noiseGradient` off it before judging
 *  anything, and that difference is the number the floor and the bounds are
 *  about (issue #62).
 *  Two passes over 768 cells per frame, negligible beside the `getImageData`
 *  the caller already does. */
export function gradient(grid:Float64Array):number {
  let h=0,hPairs=0,v=0,vPairs=0;
  for(let y=0;y<GRID_H;y++)for(let x=1;x<GRID_W;x++){h+=Math.abs(grid[y*GRID_W+x]-grid[y*GRID_W+x-1]);hPairs++;}
  for(let y=1;y<GRID_H;y++)for(let x=0;x<GRID_W;x++){v+=Math.abs(grid[y*GRID_W+x]-grid[(y-1)*GRID_W+x]);vPairs++;}
  return Math.min(hPairs?h/hPairs:0,vPairs?v/vPairs:0);
}

/** How many samples ACROSS one grid cell the caller should hand over, so that
 *  `noiseGradient` below has something to measure. The caller draws the video at
 *  `GRID_W * CELL_SAMPLES` by `GRID_H * CELL_SAMPLES` and passes that size; 3
 *  makes it 96x72 and puts 9 samples behind every cell.
 *  Why a number greater than 1 at all: noise is independent per sample and scene
 *  structure at cell scale is not, so the box average divides one by the square
 *  root of the samples it averaged and leaves the other intact. That ratio is
 *  the ONLY thing that tells them apart, and at one sample per cell it does not
 *  exist - which is what `photosphere.ts` used to hand over, and it made the
 *  whole correction a no-op in production while the tests, which feed 320x240,
 *  exercised a path no phone took (issue #62 round 1).
 *  Why 3 and not 2. Measured, on frames with NO noise on them at all, where the
 *  estimate must be 0 or it is subtracting scene: at 2 samples per cell the
 *  estimator reads 0.003718 of "noise" on a clean one-cell checkerboard and
 *  0.001417 on a clean treeline - a quarter of GRADIENT_FLOOR invented out of
 *  structure, which is the same defect as #62 with its sign reversed. At 3 it
 *  reads exactly 0 on every fixture in the test file. The Laplacian spans 3
 *  samples, so below 3 per cell it cannot sit inside a cell at all and every
 *  response it makes is a cell edge. 3 is the smallest honest factor, not a
 *  preference.
 *  Why not more. 4 and above are slightly more accurate and cost proportionally
 *  more; measured end to end on the real chain (a 1280x720 preview box-averaged
 *  to the buffer, 8-bit, then this module), 3 lands the thin treeline within
 *  0.00034 of its noise-free gradient and 4 within 0.00010, both far inside the
 *  floor's own margin, so the accuracy is not worth the pixels.
 *  The cost, measured, because the comment on `resample` claims this is cheap
 *  enough for a phone's UI thread and that claim has to survive the change:
 *  per frame, the luma conversion plus `observe` is 0.0169 ms at one sample per
 *  cell and 0.0541 ms at 3x3 - 0.037 ms more, which at 30 fps is 1.1 ms of CPU
 *  per second of scanning. The readback grows from 3072 to 27648 bytes, and THAT
 *  is the part no measurement here covers: `getImageData` is a GPU pipeline
 *  stall and its cost is a property of the device, not of this arithmetic. */
export const CELL_SAMPLES = 3;

/** The standard deviation of the 3x3 Laplacian response to white noise of unit
 *  standard deviation: the kernel's squared weights sum to 36. */
const LAPLACIAN_GAIN = 6;
/** The largest magnitude that kernel can produce from 8-bit samples: its
 *  positive weights sum to 8. Sizes the histogram the median is read from. */
const LAPLACIAN_MAX = 8*255;
/** median(|z|) for a standard normal. Turns a median absolute response into a
 *  standard deviation, which the MEAN cannot do here: see `noiseGradient`. */
const NORMAL_MEDIAN = 0.6744897501960817;
/** E|a - b| for two independent draws from N(0, s), in units of s: the pair
 *  difference is N(0, sqrt(2) s) and a folded normal's mean is sqrt(2/pi) times
 *  its scale, so the product is 2/sqrt(pi). `gradient` is a mean of exactly such
 *  pair differences when the frame under the noise is flat. */
const NOISE_PAIR_BIAS = 2/Math.sqrt(Math.PI);

/** How much of a frame's measured `gradient` is its own SENSOR NOISE rather
 *  than its scene, in the same dimensionless units, for subtracting off before
 *  the frame is judged (issue #62).
 *  Why anything is needed: `gradient` is a mean of absolute differences between
 *  adjacent cells, and noise has a gradient of its own. Two adjacent cells hold
 *  independent draws of it, so where the scene under them is flat the pair
 *  difference is not 0 but `NOISE_PAIR_BIAS` times the per-cell noise - which is
 *  a LIFT, always upward, never cancelling. GRADIENT_FLOOR is derived from the
 *  noise in the frame-to-frame DIFF; without this the same noise sat unaccounted
 *  for on the other side of the comparison, and a frame with 0.0107 of true
 *  structure measured 0.0129 under sigma-3 noise, cleared the floor, and was
 *  bounded by `STILL_CELLS` times the inflated number - more cells of true shift
 *  than the constant's name claims.
 *
 *  THE ESTIMATOR, and why this one. Issue #62 offers two. The difference between
 *  two consecutive frames of a still run is pure noise and needs no model at
 *  all, but it is circular: it is only pure noise while the view is still, and
 *  whether the view is still is the question this number is used to decide. On a
 *  moving view it reads motion as noise, over-subtracts, and turns a view that
 *  is plainly MOVING into one that reads 'featureless' - which is the one
 *  verdict a caller is allowed to keep believing a stale sensor reading through
 *  (issue #41). So: the frame's own high-pass residual, which is a statement
 *  about one frame and asks nothing about motion.
 *
 *  HOW. The Immerkaer 3x3 Laplacian [[1,-2,1],[-2,4,-2],[1,-2,1]] annihilates
 *  any locally planar patch, so on a noise-free frame it responds only at real
 *  detail. Its MEDIAN absolute response is taken and not its mean, and that is
 *  the load-bearing choice: the mean is dominated by the frame's edges and reads
 *  noise that is not there - measured, a clean treeline yields 0.001048 by the
 *  mean and exactly 0 by the median, and 0.001048 subtracted from every clean
 *  frame would be a second defect of the same shape as the one being fixed. The
 *  median is the quiet majority of the frame, which is where the noise shows.
 *
 *  WHY IT IS TAKEN ON THE SAMPLES AND NOT ON THE GRID. Noise is independent per
 *  SAMPLE, so the box average behind one cell divides it by the square root of
 *  the samples it averaged, while scene structure at cell scale survives intact.
 *  That ratio is the only thing that separates the two, and it does not exist at
 *  grid scale: a frame whose structure lives entirely at the cell scale is
 *  indistinguishable from noise by any spatial statistic. Measured, an estimator
 *  run on the 32x24 grid reads the `fineTexture` checkerboard - a real, static,
 *  perfectly witnessing frame - as 0.0372 of pure noise against its true
 *  gradient of 0.0175, and would subtract it to nothing.
 *  So the estimate is defined only where the caller hands over more than one
 *  sample per cell, and is 0 otherwise. The live caller hands over
 *  `CELL_SAMPLES` of them per cell for exactly this reason - see that constant
 *  for why 3 and what it costs - so the production path is now the path the
 *  tests exercise. A caller that passes a grid-sized buffer still gets 0 rather
 *  than a guess: estimating at grid scale would subtract real structure from
 *  every textured frame, which is strictly worse than estimating nothing.
 *
 *  THE ARITHMETIC, in order: median absolute response -> per-sample sigma
 *  (divide by `LAPLACIAN_GAIN * NORMAL_MEDIAN`) -> per-cell sigma (divide by the
 *  square root of the samples per cell) -> dimensionless (divide by the frame
 *  mean, because `normalise` divides the grid by it) -> a gradient (multiply by
 *  `NOISE_PAIR_BIAS`). Measured against the lift it has to cancel, over 12
 *  frames at 320x240: a blank frame's gradient rises 0 -> 0.002666 at sigma 3
 *  and the estimate is 0.002614; two flat halves 0 -> 0.003433 against 0.003346;
 *  a vertical ramp 0 -> 0.002863 against 0.002788.
 *
 *  Cost: one pass over the samples, plus a scan of a fixed histogram. The live
 *  caller's frame is 6912 samples - `CELL_SAMPLES` squared per cell, nine times
 *  the 768 cells `gradient` walks - and this is now the dominant term in
 *  `observe`: measured, the whole of `observe` goes from 0.0148 ms a frame at
 *  grid size to 0.0418 ms at 96x72. That is 1.3 ms of CPU per second of
 *  scanning at 30 fps, which is the budget this is asking for.
 *  The histogram is a module-level scratch buffer and not a per-call
 *  allocation, because a per-call `new Int32Array(2041)` is 8164 bytes of
 *  garbage every video frame - 239 KB/s at 30 fps, 478 KB/s at 60 - on a
 *  phone's UI thread, where a collection is a dropped frame. Reusing it and
 *  zeroing costs less than allocating anyway: measured, 0.0172 ms a call
 *  against 0.0211 ms. It is safe to share because this function is synchronous
 *  and calls nothing that could re-enter it. */
const laplacianHistogram = new Int32Array(LAPLACIAN_MAX+1);

export function noiseGradient(luma:Uint8Array|Uint8ClampedArray,width:number,height:number):number {
  const perCell=(width*height)/(GRID_W*GRID_H);
  if(!(perCell>1)||width<3||height<3||luma.length<width*height)return 0;
  const hist=laplacianHistogram;
  hist.fill(0);
  // The frame mean is taken over the same interior window, in the same pass.
  // The border is 4.8 percent of the live 96x72 buffer (1.45 percent at the
  // 320x240 the fixtures use), and this is a correction term rather than the
  // measurement itself, so a second full pass to include it would buy nothing:
  // measured, the interior mean differs from the whole-frame mean by 0.31
  // percent on the thin-treeline fixture and 1.09 percent on a hard
  // sky-over-ground frame, and the estimate scales linearly with it.
  let n=0,sum=0;
  for(let y=1;y<height-1;y++)for(let x=1;x<width-1;x++){
    const i=y*width+x;
    sum+=luma[i];n++;
    hist[Math.abs(luma[i-width-1]-2*luma[i-width]+luma[i-width+1]
                 -2*luma[i-1]  +4*luma[i]      -2*luma[i+1]
                 +luma[i+width-1]-2*luma[i+width]+luma[i+width+1])]++;
  }
  const mean=sum/n;
  if(!(mean>0))return 0;
  let seen=0,median=0;
  for(let a=0;a<=LAPLACIAN_MAX;a++){seen+=hist[a];if(seen*2>=n){median=a;break;}}
  return NOISE_PAIR_BIAS*(median/(LAPLACIAN_GAIN*NORMAL_MEDIAN)/Math.sqrt(perCell))/mean;
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
  /** Whether the newest frame has enough spatial gradient to see a shift by.
   *  Also the hysteresis state: which of the two thresholds the NEXT frame is
   *  held to (see GRADIENT_HYSTERESIS). False to start, so a witness that has
   *  seen nothing yet is in the featureless state. */
  private canWitness=false;
  clear(){this.frame=null;this.anchor=null;this.frameAt=-Infinity;this.stillSince=null;this.lastBreak=null;this.canWitness=false;}

  /** `luma` is one byte per pixel, row-major, `width` x `height`. */
  observe(at:number,luma:Uint8Array|Uint8ClampedArray,width:number,height:number):void {
    if(!Number.isFinite(at)||at<this.frameAt)return;
    if(!(width>0)||!(height>0)||luma.length<width*height)return;
    const grid=normalise(resample(luma,width,height));
    const previous=this.frame,previousAt=this.frameAt,gap=at-previousAt,previousWitnessed=this.canWitness;
    // The bounds below are multiples of the CURRENT frame's gradient. The
    // anchor's would do very nearly as well and nothing here can tell the two
    // apart: on a still scene they are the same structure, and swapping this
    // for `gradient(this.anchor)` leaves every case in the suite green. The
    // current frame's is taken because it is the frame whose shift is being
    // judged and the one that exists in every branch - the first still pair has
    // no anchor yet - not because a case demands it.
    // The frame's own sensor noise is taken off FIRST, so that `g` is the
    // frame's SCENE and both the floor test below and the two bounds are about
    // structure a shift could actually move (issue #62). Plain subtraction and
    // not in quadrature: measured over sigma 0 to 4, plain holds the thin
    // treeline at its noise-free 0.0103-0.0116 throughout, where quadrature
    // leaves it at 0.0133 at sigma 4 - still over the floor, which is the defect
    // unfixed.
    // WHAT IT COSTS, at its worst, which is worse than a percentage: a frame
    // whose detail lives at the SAMPLE scale is indistinguishable from noise to
    // the estimator by construction, so the estimate comes out at the size of
    // the frame's whole gradient and `Math.max(0, ...)` takes what is left to
    // ZERO. Measured at the live 96x72 sampling on a static per-sample texture -
    // a real frame, which a shift would move - G 0.008215 against an estimate of
    // 0.008712, G 0.017491 against 0.018145, both corrected to 0; at G 0.035636
    // the estimate is 0.034761 and 98 percent is removed. Such a frame reads
    // FEATURELESS, so the direction is a refusal and never a wrong pose, and
    // that is the only thing keeping this at a cost rather than a defect.
    // CELL_SAMPLES moved this boundary down one octave; it did not remove it.
    // Nothing in either test file grades it, because every fixture in both is
    // piecewise-constant at the sample scale - the 320x240 ones are built from
    // rounded ramps and 17- and 23-pixel blocks, the DOM ones are cell-constant
    // by construction - so the only sub-cell signal any case presents is
    // synthetic noise, and the estimator is graded in the world that suits it.
    // Tracked as epim/astrodeck#90, with the fixture that would grade it.
    const g=Math.max(0,gradient(grid)-noiseGradient(luma,width,height));
    // The floor is a BAND and not a switch (GRADIENT_HYSTERESIS, issue #75):
    // a frame that could witness goes on witnessing down to GRADIENT_FLOOR, and
    // one that could not has to clear the floor by a quarter to start again.
    // The threshold is chosen by the PREVIOUS frame's state, which is what
    // `previousWitnessed` still holds at this point.
    this.frame=grid;this.frameAt=at;
    this.canWitness=g>=(previousWitnessed?GRADIENT_FLOOR:GRADIENT_FLOOR*(1+GRADIENT_HYSTERESIS));
    // Nothing watched the view across an unobserved gap, so nothing can vouch
    // for it: start the settle over rather than crediting the missing time,
    // and let the break end HERE, so nothing before this frame is covered.
    if(!previous||gap>STALE_FRAME_MS){this.broken(at,at);return;}
    // A pair can only witness the interval between them if BOTH frames had
    // something in them to judge movement by: a frame with too little gradient
    // watched nothing while it was the newest, so it breaks at its own instant,
    // and so does the frame after it, whose pair spans an interval nobody
    // watched. One rule and not two, because the second half makes the first
    // unobservable: while `canWitness` is false `witness` answers 'featureless'
    // before it ever reads `stillSince`, so the state the untextured frame
    // clears cannot be seen at that frame, and the next frame that can witness
    // clears it again here. Written as two statements the earlier one had no
    // mutant left - deleting it was invisible to every suite - which is a guard
    // that reads as load-bearing and is graded by nothing.
    // The second half is what issue #49 item 1 asked for: without it the run
    // opened at the untextured frame's own instant and the settle was credited
    // one frame interval that watched nothing. It is not the motion test below
    // in disguise - a view merely losing the last of its fine detail moves by
    // far less than its own movement bound (see the `fineTexture` fixture in
    // photosphereStability.test.ts), so that test passes the pair and only this
    // one refuses it.
    if(!this.canWitness||!previousWitnessed){this.broken(at,at);return;}
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
