// Camera capture retains a bounded colour panorama and an editable horizon
// draft. Phone sensor pose and lens angles remain estimates for user review.
import { DOME_CELLS, SkyPanorama, orientationBasis, pixelBlueness, pixelLuminance, skyAngles, cameraLens, targetCell, transferBasis, type CameraBasis } from './photosphereGeometry';
import { CameraPoseHistory, ScanPoseSource, poseSeparation, viewVouchesFor, CONTINUITY_SLOP_MS, type PoseEvidence } from './photospherePose';
import { registerFrame } from './photosphereRegistration';
import { VisualStability, GRID_W, GRID_H, STALE_FRAME_MS } from './photosphereStability';

export interface PhotosphereSupport {
  supported: boolean;
  reason: string | null;
}

/** Every branch `grabFrame` can return from, named in the order the gates
 *  appear. This is the minimal diagnostic recorder of doc 14 section 4.3:
 *  it exists so a session with zero accepted frames is still diagnosable. */
export type CaptureOutcome =
  | 'accepted'
  | 'not-recording'
  | 'not-ready'
  | 'unhealthy'
  | 'no-image'
  | 'alignment-wait'
  | 'overlap-wait'
  | 'no-target'
  | 'already-captured'
  | 'too-soon'
  | 'below-horizon'
  | 'stale-image'
  | 'frame-already-captured'
  | 'read-failed';

export interface CaptureRecord {
  at: number;
  outcome: CaptureOutcome;
  cell?: number;
  basis?: CameraBasis;
  sensorBasis?: CameraBasis;
  adjusted?: boolean;
}

/** Does this outcome end a run of `overlap-wait` refusals (see
 *  `LENS_DOUBT_AFTER`)? Everything does EXCEPT the four that end a grab BEFORE
 *  the overlap test is ever reached, none of which is evidence that the view
 *  now matches:
 *
 *    `alignment-wait`  the pose has not settled - most of a sweep's attempts;
 *    `too-soon`        the 600 ms rate limit on registration;
 *    `no-target`       the aim is between dome cells, which on a sweep is most
 *                      of the time (18 of 264 records on the wrong-lens
 *                      recording);
 *    `below-horizon`   the phone is pointed at the ground.
 *
 *  Only what the overlap test itself decides ends a run: `accepted` (the view
 *  matched), `already-captured` (it matched and the patch was already held),
 *  and the whole-session outcomes above the pose tests, which say the scan or
 *  the camera has changed state.
 *
 *  Being strict here is not a stricter rule, it is a dead branch, and how dead
 *  depends on how many of the four are counted. `too-soon` is recorded
 *  immediately AFTER most overlap-waits - the refused attempt set
 *  `lastRegistrationAt` on its way in, so the next grab inside 600 ms is
 *  rate-limited - and four `alignment-wait` records sit between successive
 *  registration attempts on a moving phone. So: reset by literally every other
 *  outcome, a run never exceeds ONE on either recording, and no threshold above
 *  one can fire at all. Resetting on `no-target` as well as the rest, the
 *  wrong-lens recording reaches six only at 89.6 s of a 105.2 s scan, on its
 *  24th refusal out of 25 - the cue arrives fifteen seconds before the end,
 *  having stayed silent through twenty-three refusals, which is the complaint
 *  issue #52 was filed about. With all four neutral it arrives on the ninth.
 *
 *  Exported so the replay test can count runs by this rule rather than keep a
 *  second copy of it that could quietly disagree with this one. */
export function endsOverlapRun(outcome: CaptureOutcome): boolean {
  return outcome !== 'overlap-wait' && outcome !== 'alignment-wait'
    && outcome !== 'too-soon' && outcome !== 'no-target' && outcome !== 'below-horizon';
}

/** The log records; it decides nothing. Bounded so a long-running scan
 *  cannot grow this without limit. */
const CAPTURE_LOG_LIMIT = 4096;

/** Secure context + `getUserMedia` - what CAPTURE PHOTOSPHERE needs before it
 *  can even ask for a camera. Same reason string as the AR camera/gyro
 *  fallback (B.10) - one sentence for "this needs HTTPS", not three. */
export function checkPhotosphereSupport(): PhotosphereSupport {
  const insecure = typeof window === "undefined" || !window.isSecureContext;
  const md = typeof navigator === "undefined"
    ? undefined
    : (navigator as Navigator & { mediaDevices?: MediaDevices }).mediaDevices;
  if (insecure || !md || typeof md.getUserMedia !== "function") {
    return {
      supported: false,
      reason: "Photosphere capture needs a secure connection - set one up in Connection.",
    };
  }
  return { supported: true, reason: null };
}

/** Rec. 601 luma from an 8-bit RGB triple, and the blueness beside it. Both
 *  are the panorama's own definitions rather than a second copy of the
 *  coefficients: a pixel must not read one way here and another way there. */
export const luminance = pixelLuminance;
export const blueness = pixelBlueness;

/** The value at percentile `p` (0..1), nearest-rank. Empty input is 0 - a
 *  column with nothing sampled must never read as a bright sky. */
export function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.floor(p * (sorted.length - 1))));
  return sorted[idx];
}

/** A robust spread: the median absolute deviation, scaled so that on normal
 *  noise it reads as a standard deviation. Robust because the pool it is asked
 *  about is not pure sky - a roof, a disc or a chart marking in the top rows
 *  must widen the sky's tolerance by nothing at all. */
export function robustSpread(values: number[]): number {
  if (values.length === 0) return 0;
  const middle = percentile(values, .5);
  return 1.4826 * percentile(values.map(v => Math.abs(v - middle)), .5);
}

/** `skyLum` for `autoTraceSkyline`: the 80th percentile luminance of the top
 *  10% of rows across every sampled column (plan B.14 step 3) - read off
 *  whatever the top of THIS sweep actually was, so an overcast sky and a
 *  blue one both produce a usable threshold. */
export function skyLumFromColumns(columns: number[][]): number {
  const topRows: number[] = [];
  for (const col of columns) {
    const take = Math.max(1, Math.round(col.length * 0.1));
    for (let r = 0; r < take && r < col.length; r++) topRows.push(col[r]);
  }
  return percentile(topRows, 0.8);
}

/** Which azimuth bin (0..bins-1) a heading falls in. */
export function binForHeading(headingDeg: number, bins: number): number {
  const h = ((headingDeg % 360) + 360) % 360;
  return Math.min(bins - 1, Math.floor((h / 360) * bins));
}

export interface SweepFrame {
  bin: number;
  column: number[];
  /** The same rows' blueness, where the frame was read in colour. Optional
   *  because a frame fabricated by a test carries brightness only, and the
   *  tracer has to work from brightness alone when that is all there is. */
  blue?: number[];
  altitude?: number;
  verticalFov?: number;
  band?: number;
  manualOverhead?: boolean;
}

/** Three overlapping elevation rings plus the single shared zenith point.
 * Coverage is earned by aiming at each ring, never inferred from elapsed time. */
export const SWEEP_BANDS = [
  { altitude: 0, label: "Low" },
  { altitude: 35, label: "Middle" },
  { altitude: 70, label: "High" },
] as const;
export const OVERHEAD_BAND = SWEEP_BANDS.length;

export function bandForAltitude(alt: number): number | null {
  if (alt >= 85) return OVERHEAD_BAND;
  const band = SWEEP_BANDS.findIndex(b => Math.abs(alt - b.altitude) <= 12);
  return band < 0 ? null : band;
}

/** Project every retained elevation into a common 90..-10 degree column.
 * NaN is unknown, not open sky. Overlapping frames favor their central rows;
 * an overhead sample covers only the shared zenith, not an invented sky cap.
 * Lens field of view is still an estimate and must be reviewed by the user. */
export function projectSweepColumns(frames: SweepFrame[], bins: number,
  channel: 'column' | 'blue' = 'column'): number[][] {
  const cols = Array.from({ length: bins }, () => Array<number>(101).fill(NaN));
  const weights = Array.from({ length: bins }, () => Array<number>(101).fill(Infinity));
  for (const frame of frames) {
    // One projection, either channel, so a row's colour and its brightness can
    // never be projected from two different places in the frame. A frame with
    // no colour sample projects to NaN, which the tracer reads as "no colour
    // evidence here" - not as grey.
    const source = channel === 'blue' ? frame.blue : frame.column;
    if (!source?.length) continue;
    if (frame.band === OVERHEAD_BAND) {
      const sample = source[Math.floor(source.length / 2)];
      for (let bin = 0; bin < bins; bin++) { cols[bin][0] = sample; weights[bin][0] = -Infinity; }
      continue;
    }
    if (frame.bin < 0 || frame.bin >= bins) continue;
    const center = frame.altitude ?? 0, fov = frame.verticalFov ?? 45;
    for (let row = 0; row <= 100; row++) {
      const alt = 90 - row, fraction = .5 + (center - alt) / fov;
      if (fraction < 0 || fraction > 1) continue;
      const weight = Math.abs(fraction - .5);
      if (weight >= weights[frame.bin][row]) continue;
      const sample = source[Math.round(fraction * (source.length - 1))];
      if (!Number.isFinite(sample)) continue;
      cols[frame.bin][row] = sample; weights[frame.bin][row] = weight;
    }
  }
  return cols;
}

export interface SkyTrace {
  points: { az: number; alt: number }[];
  uncertainBins: number[];
}

/** One sampled column of sky: per-row luminance, and the blueness of the same
 *  rows where the source was read in colour. `blue` may be shorter than `lum`
 *  or all NaN - that is "no colour evidence", never "grey". */
export interface SkyColumn { lum: number[]; blue: number[] }
/** What one azimuth bin offers the tracer: the columns sampled ACROSS it, the
 *  bin's CENTRE column first. A bin is 12 degrees wide at the shipped 30 and
 *  the product publishes one number for all of it, so the honest number is the
 *  bin's worst case, which one ray through its centre cannot see. */
export type SkyBin = SkyColumn[];

/** Columns sampled across each azimuth bin. Five, so the samples sit 2.4
 *  degrees apart at 30 bins: finer than anything the product can resolve (the
 *  scorer calls an obstacle narrower than one bin unresolvable), so nothing
 *  the answer is graded on can hide between two samples. Odd, so one sample
 *  lands exactly on the bin centre - the column `centreColumns` publishes and
 *  the uncertainty rule reads, unchanged. */
export const BIN_SAMPLES = 5;
/** The rows of accepted sky a row is tested against - the sky model is the
 *  median of the last twelve, per channel, so it FOLLOWS the column instead of
 *  standing still. A real sky is not flat: airlight brightens it toward the
 *  horizon and whitens it, and the sweep re-exposes between elevation bands.
 *  Against one global level, each of those is a departure that lasts all the
 *  way down, and a sky with nothing in it publishes a horizon (issue #73: a
 *  brightening from 100 to 180 published 45 degrees, an ordinary clear sky
 *  with its chroma gradient 51, both with no bin marked uncertain).
 *
 *  Twelve rows lag the trend by about six, so a gradient costs six rows of
 *  slope: 5 luminance units on the steepest sky above, against a tolerance
 *  that starts at 32 of them. Below `SKY_WINDOW_MIN` accepted rows - the top
 *  of the column - the pooled top-rows statistics stand in, which is where
 *  they came from. */
const SKY_WINDOW = 12, SKY_WINDOW_MIN = 4;
/** How far a row may sit from the sky model and still be sky: this fraction of
 *  the model's own level, or three robust deviations of the model's spread,
 *  whichever is larger.
 *
 *  The fraction is an EXPOSURE allowance, and it is bracketed by two facts
 *  that one test each pins. Above 0.30, because the phone re-exposes between
 *  elevation bands and a 30 per cent step at a band seam is ordinary, not a
 *  wall. At or below 0.35, because the dim wall the old 0.7 rule did see - 130
 *  against a sky of 200 - is 0.35 away and must stay seen. Nothing in the
 *  simulator pins it: the chart yard's own numbers are identical at 0.28 and
 *  at 0.36. A grey wall between 30 and 32 per cent darker than the sky is
 *  therefore indistinguishable from an exposure step by this rule (measured:
 *  found at 32.5 per cent, lost at 32.0), and no per-column rule can separate
 *  them - they are the same signal. The evidence that would is cross-column: a
 *  seam is one row across the whole mosaic at one ratio, a wall is local in
 *  azimuth. That is not built, and it is the surviving half of issue #74
 *  together with an edge softer than about ten rows (see `PERSIST_ROWS` and
 *  `columnBoundary`). */
const EXPOSURE_TOLERANCE = .32, SKY_SIGMAS = 3;
/** The same for blueness, in 8-bit channel units, with a floor: chroma
 *  subsampling and sensor noise move it a few units on their own, and a grey
 *  sky's blueness has no spread to scale by. The exposure allowance applies
 *  here too, because a brightness change scales the colour offset with it. */
const SKY_BLUE_FLOOR = 8;
/** How far a departure must persist, in rows, to be a structure rather than
 *  something the sky is carrying. A column row is one degree of altitude, so
 *  this is 12 degrees. Below it, a departure with sky under it is read as a
 *  cloud, a bird or a marking on the chart - the chart yard's discs are 2 to 3
 *  degrees tall and the dark one near the zenith is what the old rule reported
 *  as an 87 degree horizon over most of the compass. Above it, a floating
 *  obstruction is kept: the chart's roof stands 14 to 15 degrees tall with
 *  clear sky beneath it and IS an obstruction. A departure that reaches the
 *  bottom of the column needs no such length - the ground is under it. */
const PERSIST_ROWS = 12;

/** The tracer's input, whatever shape it arrived in. A plain `number[]` is a
 *  luminance-only column with no sub-samples: the frame-fold fallback and the
 *  older tests. */
function asSkyBin(entry: number[] | SkyBin): SkyBin {
  return entry.length === 0 || typeof entry[0] === 'number'
    ? [{ lum: entry as number[], blue: [] }]
    : entry as SkyBin;
}

/** Is `value` between the sky and the surface below the boundary, give or
 *  take the channel's tolerance? The transition itself is - a blended edge
 *  pixel is part sky and part wall - while a bright chart stripe that happens
 *  to abut the wall is not, and neither is anything else that overshoots past
 *  both. */
function betweenSkyAnd(value: number, sky: number, body: number, tolerance: number): boolean {
  return !Number.isFinite(value) || !Number.isFinite(body)
    || (value >= Math.min(sky, body) - tolerance && value <= Math.max(sky, body) + tolerance);
}

/** The pooled top-of-sky statistics, which seed every column's model. */
interface SkySeed { lum: number; blue: number; lumSpread: number; blueSpread: number }
/** The sky as it is at ONE row of one column: its level in both channels, how
 *  far from it still counts as sky (the exposure allowance), and how far its
 *  own samples scatter (the noise, which is what says whether a row has really
 *  left it). */
interface SkyHere {
  lum: number; blue: number;
  lumTol: number; blueTol: number;
  lumNoise: number; blueNoise: number;
}

/** The boundary in ONE column: the altitude of the highest row where the sky
 *  gives way to something that stays. 0 is open to the horizon.
 *
 *  The walk down the column carries the sky with it. Every row is measured
 *  against the median of the last `SKY_WINDOW` rows ACCEPTED AS SKY, in both
 *  channels, so a gradient or an exposure seam is absorbed - the model moves
 *  with it - while a departure is what the trend does not explain. Rows inside
 *  a departure never enter the window, so a wall with a hard edge cannot teach
 *  the model to accept itself, and a passing feature (a disc, a stripe, a
 *  bird) is stepped over without polluting it either.
 *
 *  A following model has one blind spot, and it is issue #74: an edge SOFT
 *  enough that every row of it is within tolerance walks the model down into
 *  the obstruction one row at a time, and the whole wall then reads as open
 *  sky. A six-row edge on a wall 35 per cent below its sky - an ordinary
 *  distant tree line, a ridge in haze, a blurred handheld frame - did exactly
 *  that. So the model is watched as well as used: when the model has itself
 *  drifted from the sky it had `SKY_WINDOW` rows above, and everything from
 *  here down stays away from that older sky, the column has walked into
 *  something, and the boundary is traced back to the row where it left. */
function columnBoundary(column: SkyColumn, seed: SkySeed): number {
  const { lum, blue } = column;
  let last = -1;
  for (let row = 0; row < lum.length; row++) if (Number.isFinite(lum[row])) last = row;
  if (last < 0) return 0;
  const windowLum: number[] = [], windowBlue: number[] = [];
  // The model as it stood at every row, so a row can be compared with the sky
  // as it was `SKY_WINDOW` rows above it. Indexed BY ROW, skipped runs
  // included: a gap here would measure the lag in rows visited rather than in
  // degrees of altitude.
  const history: SkyHere[] = [];
  const skyHere = (): SkyHere => {
    const settled = windowLum.length >= SKY_WINDOW_MIN;
    const level = settled ? percentile(windowLum, .5) : seed.lum;
    const levelBlue = settled && windowBlue.length ? percentile(windowBlue, .5) : seed.blue;
    const spread = settled ? robustSpread(windowLum) : seed.lumSpread;
    const spreadBlue = settled && windowBlue.length ? robustSpread(windowBlue) : seed.blueSpread;
    return {
      lum: level, blue: levelBlue,
      lumTol: Math.max(EXPOSURE_TOLERANCE * Math.abs(level), SKY_SIGMAS * spread),
      blueTol: Math.max(SKY_BLUE_FLOOR, SKY_SIGMAS * spreadBlue,
        Number.isFinite(levelBlue) ? EXPOSURE_TOLERANCE * Math.abs(levelBlue) : 0),
      lumNoise: SKY_SIGMAS * spread, blueNoise: SKY_SIGMAS * spreadBlue,
    };
  };
  // Sign agnostic, and either channel on its own is enough: a wall can be
  // darker than the sky, brighter than it, or - the case issue #58 is about -
  // the same brightness and a different colour.
  const off = (row: number, sky: SkyHere): boolean => {
    if (!Number.isFinite(lum[row])) return false;
    if (Math.abs(lum[row] - sky.lum) > sky.lumTol) return true;
    return Number.isFinite(blue[row]) && Number.isFinite(sky.blue)
      && Math.abs(blue[row] - sky.blue) > sky.blueTol;
  };
  /** Has the model been walked away from the sky it had a window ago? */
  const walked = (here: SkyHere, then: SkyHere): boolean =>
    Math.abs(here.lum - then.lum) > then.lumTol
    || (Number.isFinite(here.blue) && Number.isFinite(then.blue)
      && Math.abs(here.blue - then.blue) > then.blueTol);
  /** Is this row still the sky the transition started from? Either it sits
   *  inside that sky's own noise, or the model AT this row has not been walked
   *  away from it and the row matches THAT within the same noise. The second
   *  clause is what stops the trace-back from climbing an ordinary sky
   *  gradient: there the model follows and every row matches it. On a soft
   *  edge the model has been walked, so it cannot vouch for the rows that
   *  walked it. Both noises come from the frozen sky, because a model already
   *  inside the transition has a spread that would excuse anything. */
  const backAtSky = (row: number, frozen: SkyHere): boolean => {
    const matches = (level: number, levelBlue: number) =>
      Math.abs(lum[row] - level) <= frozen.lumNoise
      && (!Number.isFinite(blue[row]) || !Number.isFinite(levelBlue)
        || Math.abs(blue[row] - levelBlue) <= frozen.blueNoise);
    if (matches(frozen.lum, frozen.blue)) return true;
    const near = history[Math.min(row, history.length - 1)];
    return !walked(near, frozen) && matches(near.lum, near.blue);
  };
  /** From a detected departure, step UP to the row where the column left the
   *  sky - the first row of the transition, not the row where the tolerance
   *  was finally exceeded. Bounded at two windows, which is as far back as a
   *  lagged reference can see. */
  const walkBack = (from: number, frozen: SkyHere, bodyLum: number, bodyBlue: number): number => {
    let top = from;
    const limit = Math.max(0, from - 2 * SKY_WINDOW);
    while (top > limit) {
      const row = top - 1;
      if (!Number.isFinite(lum[row])) break;
      if (!betweenSkyAnd(lum[row], frozen.lum, bodyLum, 0)) break;
      if (!betweenSkyAnd(blue[row], frozen.blue, bodyBlue, 0)) break;
      if (backAtSky(row, frozen)) break;
      top = row;
    }
    return top;
  };
  for (let start = 0; start <= last; start++) {
    const here = skyHere();
    while (history.length <= start) history.push(here);
    history[start] = here;
    const lagged = history[Math.max(0, start - SKY_WINDOW)];
    if (off(start, here)) {
      // The zenith is one shared point painted across every bin, so a covered
      // or unlit one is blocked outright. DARK only, which is the sense the
      // old rule had: `projectSweepColumns` writes the overhead sample into
      // row 0 of every bin, so a sign-agnostic test here let one blown-out
      // overhead frame publish a fully blocked sky, 90 degrees in all 30 bins,
      // flagged certain (issue #73). A bright row 0 takes the ordinary path
      // below, where one row cannot persist.
      if (start === 0 && lum[0] < here.lum - here.lumTol) return 90;
      let end = start;
      while (end < last && off(end + 1, here)) end++;
      // Persistence: it reaches the bottom, or it is tall enough to be a thing.
      if (end < last && end - start + 1 < PERSIST_ROWS) {
        for (let skipped = start; skipped <= end; skipped++) {
          while (history.length <= skipped) history.push(here);
          history[skipped] = here;
        }
        start = end; continue;
      }
      const body = Math.min(end, start + PERSIST_ROWS - 1);
      const bodyLum = percentile(lum.slice(start, body + 1).filter(Number.isFinite), .5);
      const bodyBlues = blue.slice(start, body + 1).filter(Number.isFinite);
      const bodyBlue = bodyBlues.length ? percentile(bodyBlues, .5) : NaN;
      let top = walkBack(start, lagged, bodyLum, bodyBlue);
      // Where the surface actually starts, coming the other way. The rows
      // above it inside the run departed from the sky in some other direction
      // entirely - a bright chart stripe over the chart yard's wall put the
      // boundary 4 degrees too high - and a row that matches neither the sky
      // nor the surface is not where one becomes the other. The bound this
      // costs is pinned by a test: a glint on top of a dark roof is skipped
      // the same way, so the boundary can sit as far below the true top as the
      // glint is tall.
      while (top < end && !(betweenSkyAnd(lum[top], here.lum, bodyLum, here.lumTol)
        && betweenSkyAnd(blue[top], here.blue, bodyBlue, here.blueTol))) top++;
      return Math.max(0, Math.min(90, 91 - top));
    }
    if (walked(here, lagged)) {
      // The model has drifted. That is only a boundary if what is below stays
      // away from the older sky - a sky that wandered and came back has not
      // walked anywhere.
      let stays = true;
      for (let row = start; row <= Math.min(start + PERSIST_ROWS - 1, last); row++) {
        if (!off(row, lagged)) { stays = false; break; }
      }
      if (stays) return Math.max(0, Math.min(90, 91 - walkBack(start, lagged, here.lum, here.blue)));
    }
    windowLum.push(lum[start]);
    if (windowLum.length > SKY_WINDOW) windowLum.shift();
    if (Number.isFinite(blue[start])) {
      windowBlue.push(blue[start]);
      if (windowBlue.length > SKY_WINDOW) windowBlue.shift();
    }
  }
  return 0;
}

/** A boundary is a TRANSITION away from the sky AS IT IS HERE that PERSISTS
 * downward, in either direction and in either channel - not a luminance below
 * a fixed fraction of one sky level.
 *
 * The fixed fraction was the original defect: the chart yard's wall is
 * luminance 104 against a sky level of 122 and never crossed it, so 12 degrees
 * of a 25 degree wall were published as open sky, while the sky's own dark
 * noise DID cross it and published a horizon at 87 degrees over most of the
 * compass (issue #58). Real buildings are routinely brighter than an overcast
 * sky, and reporting one as open is the false open the planner would slew
 * into.
 *
 * ONE level for the whole column was the second defect (issue #73), and it is
 * the reason the sky model here is local. A real sky brightens toward the
 * horizon, whitens as it does, and carries an exposure seam wherever the
 * phone re-exposed between elevation bands; every one of those departs from a
 * single top-of-sky level and keeps departing all the way to the bottom, so an
 * empty sky published a horizon of 45 degrees and worse, with no bin marked
 * uncertain. The chart yard cannot see this - its sky is flat and grey - which
 * is exactly why it had to be measured on synthetic columns and pinned there.
 *
 * So: the top 26 rows of every sampled column seed a sky model, which each
 * column then carries down itself as the median of the last `SKY_WINDOW` rows
 * accepted as sky, in luminance and in blueness. A row is off-sky when it
 * departs from the model of EITHER channel by more than that channel's
 * tolerance - an exposure allowance on the level, or three robust deviations
 * of the model's own spread. A slow ramp of any amplitude is sky, because the
 * model follows it; only a change the trend does not explain can depart from
 * it. A departure is the boundary where it reaches the bottom of the column or
 * stands `PERSIST_ROWS` tall, with the boundary placed at the first row that
 * looks like the surface below rather than at whatever the run started with.
 *
 * Highest qualifying departure wins, including a canopy above a lower patch of
 * sky. Missing upper-sky data and a DARK zenith are conservatively blocked.
 * Each bin answers for the whole of its own width, so it reports the highest
 * boundary of the columns sampled across it. This produces the existing
 * single-height horizon, not a mask of canopy gaps. */
export function traceSkyCoverage(columns: readonly (number[] | SkyBin)[]): SkyTrace {
  const bins = columns.map(asSkyBin);
  const lumPool: number[] = [], bluePool: number[] = [];
  for (const bin of bins) for (const column of bin) {
    for (let row = 0; row < 26 && row < column.lum.length; row++) {
      if (Number.isFinite(column.lum[row])) lumPool.push(column.lum[row]);
      if (Number.isFinite(column.blue[row])) bluePool.push(column.blue[row]);
    }
  }
  const sky = percentile(lumPool, .8);
  const seed: SkySeed = {
    lum: percentile(lumPool, .5),
    blue: bluePool.length ? percentile(bluePool, .5) : NaN,
    lumSpread: robustSpread(lumPool),
    blueSpread: robustSpread(bluePool),
  };
  const uncertainBins: number[] = [];
  const points = bins.map((bin, index) => {
    let alt = 0;
    // Unchanged, and read off the bin's CENTRE column, which is the column
    // this rule has always been read off: a short column, a gap anywhere in
    // the top 91 rows, or a sky too dark to have been measured at all.
    const complete = (column: SkyColumn) =>
      column.lum.length >= 101 && column.lum.slice(0, 91).every(Number.isFinite);
    if (!bin.length || !complete(bin[0]) || sky < 40) {
      alt = 90; uncertainBins.push(index);
    } else for (const column of bin) {
      // A sub-column with a gap in it is dropped rather than allowed to lower
      // the answer, and deliberately does NOT make the bin uncertain: the
      // uncertainty rule is the centre column's, unchanged, so a bin can be
      // certain while some of the columns beside its centre went unmeasured.
      // The cost is under-reported uncertainty relative to what is sampled;
      // the alternative would mark bins uncertain that the shipped rule calls
      // measured.
      if (complete(column)) alt = Math.max(alt, columnBoundary(column, seed));
    }
    return { az: Math.round((index + .5) / columns.length * 360), alt };
  });
  return { points, uncertainBins };
}

/** Fold captured frames into `autoTraceSkyline`'s `columns[]`: one column per
 *  azimuth bin, keeping the LAST frame seen for a bin (the most recent pass
 *  through that heading). Empty bins stay `[]`, never a zeroed column -
 *  `autoTraceSkyline` already reads a short/empty column as "no drop found",
 *  i.e. open to the bottom of frame, which is the honest default where there
 *  is no data. */
export function foldSweepColumns(frames: SweepFrame[], bins: number): number[][] {
  const out: (number[] | undefined)[] = new Array(bins);
  for (const f of frames) out[f.bin] = f.column;
  return Array.from(out, (c) => c ?? []);
}

/** Per-row samples for one already-drawn video frame: `rows` samples
 *  spaced evenly down the frame, each a full-width average so a single hot
 *  pixel cannot fake a sky-to-ground transition. Pure function of the pixel
 *  buffer, so it is testable with a fabricated `Uint8ClampedArray` and no
 *  live video element.
 *
 *  `sample` is the channel: `luminance` by default, `blueness` for the colour
 *  the tracer needs beside it. A second call is a second pass over the frame -
 *  `rows` x `width` reads of a 320 px preview, once per accepted capture -
 *  which is the price of carrying colour down the frame-fold path at all. */
export function columnFromImageData(
  data: Uint8ClampedArray,
  width: number,
  height: number,
  rows: number,
  sample: (r: number, g: number, b: number) => number = luminance,
): number[] {
  const out: number[] = [];
  for (let r = 0; r < rows; r++) {
    const y = Math.min(height - 1, Math.floor((r / Math.max(1, rows - 1)) * (height - 1)));
    let sum = 0;
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      sum += sample(data[i], data[i + 1], data[i + 2]);
    }
    out.push(sum / Math.max(1, width));
  }
  return out;
}

export interface SweepCamera { deviceId: string; label: string }

/** Rear-camera elevation depends on tilt, not compass heading. In particular,
 * a phone looking straight up can report valid tilt with no absolute bearing. */
export function cameraElevation(e: { beta: number | null; gamma: number | null }): number | null {
  if (![e.beta, e.gamma].every(v => typeof v === "number" && Number.isFinite(v))) return null;
  const rad = Math.PI / 180;
  return Math.asin(Math.max(-1, Math.min(1, -Math.cos(e.beta! * rad) * Math.cos(e.gamma! * rad)))) / rad;
}

/** Labels are vendor-dependent. Never infer lens type from device order. */
export function preferredRearCamera(cameras: SweepCamera[]): string | undefined {
  const rear = cameras.filter(c => /back|rear|environment/i.test(c.label)
    && !/ultra|telephoto|front|\b0[.,][56]\b/i.test(c.label));
  return rear.find(c => /main|wide|standard|\b1x\b/i.test(c.label))?.deviceId
    ?? (rear.length === 1 ? rear[0].deviceId : undefined);
}

/** Rear camera's viewing ray in the earth frame (W3C Z-X-Y rotation).
 * Unlike a flat-phone compass, this works with an upright/landscape camera.
 * Relative orientation alone must never be treated as geographic north. */
export function cameraPose(e: { alpha: number | null; beta: number | null;
  gamma: number | null; absolute?: boolean; webkitCompassHeading?: number }, absoluteEvent = false): { az: number; alt: number } | null {
  const compass = e.webkitCompassHeading;
  const hasCompass = typeof compass === "number" && Number.isFinite(compass);
  if (!absoluteEvent && !e.absolute && !hasCompass) return null;
  if (![e.alpha, e.beta, e.gamma].every(v => typeof v === "number" && Number.isFinite(v))) return null;
  const rad = Math.PI / 180, a = e.alpha! * rad, b = e.beta! * rad, g = e.gamma! * rad;
  const x = -Math.cos(a) * Math.sin(g) - Math.sin(a) * Math.sin(b) * Math.cos(g);
  const y = -Math.sin(a) * Math.sin(g) + Math.cos(a) * Math.sin(b) * Math.cos(g);
  const z = -Math.cos(b) * Math.cos(g);
  const correction = hasCompass ? compass! - (360 - e.alpha!) : 0;
  // At the zenith azimuth is undefined. The capture treats it as one shared
  // overhead tile; it never bins that arbitrary bearing as a horizontal view.
  return { az: Math.hypot(x, y) < 1e-6 ? 0 : ((Math.atan2(x, y) / rad + correction) % 360 + 360) % 360,
    alt: Math.asin(Math.max(-1, Math.min(1, z))) / rad };
}

/** How long a heading or tilt reading stands on its own before the video has
 *  to vouch for it. Not a staleness timeout on a change-driven stream: a still
 *  phone sends nothing and that silence is not staleness (issue #37). But
 *  `sourceHealthy` measures LIFECYCLE only, and a magnetometer that simply
 *  stops - wedged sensor, permission revoked with no lifecycle event, a stuck
 *  Chromium pump - leaves every one of those flags true. Past this window the
 *  reading is believed only while the video says the view has not moved since
 *  it arrived, so a dead sensor over a moving view is caught within 2 s. */
const SENSOR_SILENCE_MS = 2000;
/** Consecutive failures to read the preview's pixels before the cue says so.
 *  Unknown stability means the strict rule applies, which means a still phone
 *  cannot capture at all - so this failing silently is the original deadlock
 *  with no diagnosis. Five frames is a sixth of a second on the video-frame
 *  path and under two seconds on the interval fallback. */
const STILLNESS_BLIND_AFTER = 5;
/** Consecutive interval-fallback ticks whose media gate found no newly
 *  delivered frame, before the cue says so. Six ticks is 2.1 s of the 350 ms
 *  timer: one interval past the 1.5 s acceptance budget a still phone is given,
 *  so a healthy camera that merely stuttered for a tick or two never reaches
 *  it, while a preview that has genuinely stopped does within about two
 *  seconds. Only the timer path can ever reach it - the frame callback runs
 *  only for a frame the browser presented, so there the callback IS the
 *  delivery and the gate is never asked. */
const MEDIA_GATE_BLIND_AFTER = 6;
/** Consecutive `overlap-wait` refusals, in a scan with no saved lens
 *  calibration, before the cue stops repeating the aim instruction and names
 *  the camera view angle instead (issue #52). `registerFrame` fits a small
 *  rigid rotation against the mosaic, a wrong lens SCALE cannot be absorbed by
 *  a rotation, so `checkOverlap` returns `conflict` and every attempt at a
 *  patch is refused - for as long as the user is willing to keep trying, while
 *  the one control that would fix it is never mentioned.
 *
 *  Six is measured rather than chosen. The two recorded cases that differ in
 *  exactly one field - the camera's short-axis field of view, 70 degrees
 *  against the 60 the scanner assumes - over 264 grab attempts each. The
 *  numbers below are read off the cached artifact,
 *  `tools/photosphere_sim/cache/cases/<case>/result/captures.jsonl`, so a
 *  reader can check them against the file on disk; replaying that case's
 *  `input/` at b8581949, which is what the test does, reproduces every one of
 *  them exactly.
 *
 *    chartyard-arc075-70: 25 overlap-wait outcomes, runs of 1,1,1,11,11 -
 *                         reaching six 14.4 s into the first long one, at
 *                         43.3 s of a 105.3 s scan, on the 9th refusal of 25,
 *                         with 12 of the 25 carrying the cue; 9 accepted,
 *                         52/91 cells;
 *    chartyard-arc075-60:  2 overlap-wait outcomes, one run of 2, never within
 *                         reach of six; 20 accepted, 88/91 cells.
 *
 *  So six is three times the worst the RIGHT lens produces and the wrong lens
 *  clears it by five, and it puts the cue in front of the user less than halfway
 *  through the scan rather than fifteen seconds before the end. It is still a
 *  property of one recorded route, so the replay case asserts a threshold and
 *  never a count.
 *
 *  In wall time it is at least 3.0 s of trying: successive registration
 *  attempts are 600 ms apart at best (the rate limit that records `too-soon`),
 *  and on the recorded sweep above the six took 14.4 s. The issue quotes 29
 *  overlap-waits for the 70 case, measured at 230788d9; it is 25 today and the
 *  finding is unchanged.
 *
 *  What counts as consecutive is `endsOverlapRun`, and that is where the
 *  interesting part of this number lives - read it before changing either.
 *
 *  Exported, unlike its neighbours, so the replay case grades the threshold
 *  this file actually ships: raising it to 1e9 has to redden that case, and it
 *  cannot if the test carries its own 6. */
export const LENS_DOUBT_AFTER = 6;
/** How often the interval fallback grabs, and the cadence the frame-callback
 *  path throttles its own grabs to, so the two paths capture at one rate. It is
 *  also the RESOLUTION OF THE WITNESS on the fallback: that path observes only
 *  when this timer fires and stamps each observation with the read instant, so
 *  it cannot place a break, or a settle, finer than this. `vouchSlopMs` is
 *  where that second meaning is spent (issue #48), which is why the number is
 *  named rather than written three times. */
const GRAB_INTERVAL_MS = 350;

/** Opens a visible preview; recording begins only after begin() is pressed. */
export class PhotosphereSweep {
  private stream: MediaStream | null = null;
  private video: HTMLVideoElement | null = null;
  private canvas: HTMLCanvasElement | null = null;
  private frames: SweepFrame[] = [];
  private headingHandler: ((e: Event) => void) | null = null;
  private heading = 0;
  private altitude = 0;
  // Both on the PERFORMANCE clock, the one orientation timestamps and video
  // frame times share. A wall-clock reading here cannot be compared with either.
  private tiltAt: number | null = null;
  private headingAt: number | null = null;
  /** The newest instant at which the heading (resp. tilt) reading then current
   *  was still believed WHILE THE VIEW COULD BE JUDGED. Written only by
   *  `noteReadingsStand`, read only by `readingStands`, and on the same
   *  performance clock as the two above. `null` until the first such moment. */
  private headingStoodAt: number | null = null;
  private tiltStoodAt: number | null = null;
  private hasOrientation = false;
  private basis: CameraBasis | null = null;
  private panorama: SkyPanorama | null = null;
  private coveredCells = new Set<number>();
  /** Whether an ORIENTED frame - one placed with a full basis, heading and
   *  tilt, through `panorama.add` - has been accepted with the zenith cap as
   *  its target. Since the cap shares the 11 degree aim cone with every other
   *  cell it is the nearest cell from roughly altitude 80 upward, which is
   *  below the overhead band, and `overheadCaptured` alone would then refuse
   *  the cap forever while the driver kept accepting frames for it (issue
   *  #57 again, from the other side). */
  private aimedZenith = false;
  private lastAlpha = 0;
  private imageAspect = 4 / 3;
  private shortAxisFov=60;
  private lensCalibrated=false;
  private lensProfileKey='';
  private lastCaptureAt: number | null = null;
  private poses = new CameraPoseHistory();
  private poseSource=new ScanPoseSource();
  private visualAnchor:{raw:CameraBasis;aligned:CameraBasis}|null=null;
  private lastRegistrationAt=-Infinity;
  private tilts = new CameraPoseHistory();
  private stability = new VisualStability();
  /** The margin every `viewVouchesFor` in this session is asked for, chosen in
   *  `start()` by which witness the browser gave us and used by all three
   *  callers - `vouched`, `noteReadingsStand` and the evidence handed to
   *  `forFrame` - so the dome, the memory and the capture cannot disagree about
   *  whether a reading is covered. On the frame-callback path an observation
   *  carries the instant the CAMERA saw the frame, so the default clock
   *  alignment is the whole of it. On the interval fallback it carries the READ
   *  instant, which is that instant plus however long the camera pipeline took,
   *  so the margin there is the witness's own resolution (issue #48). Not a
   *  constant, because it is a fact about this session's witness; not read off
   *  `grabTimer`, because `stop()` clears that and the two would drift. It is
   *  reset with the rest of the session state in `stop()`, so a sweep restarted
   *  on another element never begins on the margin the last one earned. */
  private vouchSlopMs = CONTINUITY_SLOP_MS;
  private lumaCanvas: HTMLCanvasElement | null = null;
  private stillnessFailures = 0;
  /** Interval-fallback ticks the media gate refused: the running total for this
   *  camera session, and the length of the run in progress. The gate is right
   *  to refuse a frame the camera never delivered, but it returns before
   *  `observeStillness`, so `stillnessFailures` stayed 0 and a session that
   *  captured nothing because its preview was frozen reported exactly the
   *  envelope of a healthy one (issue #46). The RUN is what the cue reads, and
   *  it resets on the first accepted frame; the TOTAL is what the report
   *  carries, and it stands for the whole session, so a scan that stuttered
   *  once and recovered stays distinguishable from a scan that never stuttered.
   *  Advanced only on the timer path - see `grabFrame`. */
  private mediaGateRefusals = 0;
  private mediaGateRefusalRun = 0;
  /** The run of `overlap-wait` refusals in progress, counted over the attempts
   *  that reached the overlap test (`endsOverlapRun`). Written only by
   *  `recordCapture`, which is the one funnel every outcome passes through;
   *  read only by `captureCue`; cleared with the rest of the session in
   *  `stop()`, and so by `start()`, which calls it. */
  private overlapWaitRun = 0;
  /** The media clock, in seconds, of the last reading the interval path took
   *  (see `newMediaFrame`, which consumes as it answers and advances this on
   *  every `true`). `null` until the first reading, taken in `start()` once
   *  the preview plays: a media clock is evidence only by MOVING, so that
   *  first reading establishes the baseline and claims nothing. A sentinel
   *  below every real clock value let the first tick claim a delivery for
   *  free, and a timer fired by hand could then capture the element's
   *  retained picture. The manual press never reads this: it asks only for a
   *  delivery within STALE_FRAME_MS (see `lastMediaAdvanceAt`). */
  private lastMediaTime: number | null = null;
  /** FRESHNESS and IDENTITY of the delivered image, which are not the same
   *  question as the witness's consume-on-read above. `newMediaFrame` may hand
   *  a given frame to the stillness witness once and only once; capture asks
   *  something different - has the camera delivered a picture recently, and is
   *  the picture on screen a different one from the picture already captured.
   *  Keeping them apart is what lets a manual press moments after a witness
   *  read still capture the frame the user is looking at (review 17, P1).
   *  `lastMediaAdvanceAt` is on the performance clock; the two frame ids are a
   *  frame's media time (or, on the rVFC path, its presented-frame count). */
  private lastMediaAdvanceAt = -Infinity;
  private presentedFrameId: number | null = null;
  private lastCapturedFrameId: number | null = null;
  /** Which image gate last refused, for the cue. Two different facts, and the
   *  user can act on only one of them: a camera that has stopped delivering is
   *  broken and worth saying so, while a picture already captured is a normal
   *  moment between frames and must not be dressed up as a fault. */
  private imageGate: null | 'stale-image' | 'frame-already-captured' = null;
  private luma = new Uint8Array(GRID_W*GRID_H);
  private listening = false;
  /** Recorded once in `start()`, from `"DeviceOrientationEvent" in window`.
   *  Distinct from `listening`: a browser that HAS the constructor but has not
   *  yet delivered an event is unhealthy (a stalled stream, worth reporting);
   *  a browser that never HAD the constructor has no pose stream to begin
   *  with, so there is nothing here to be healthy or unhealthy about (issue
   *  #42 item 3). See `sourceHealthy`. */
  private orientationSupported = false;
  private trackEnded = false;
  private alignmentWait = false;
  private overlapWait = false;
  private scanSamples:unknown[]=[];
  private lastDiagnosticAt=-Infinity;
  private lastSensorReading:unknown=null;
  private videoFrameHandle: number | null = null;
  private frameBasis: {basis:CameraBasis;at:number} | null = null;
  private recording = false;
  private ready = false;
  // The diagnostic recorder (doc 14 4.3): one record per grabFrame call,
  // naming the branch it returned from. Records, decides nothing.
  private captureRecords: CaptureRecord[] = [];
  // Distinct from `panorama` existing: `begin()` allocates an empty panorama
  // before any pixel has ever been written to it, and panoramaPixels must
  // stay null until a capture has actually landed, not merely been started.
  private hasCapturedFrame = false;
  private issue: string | null = null;
  private cameras: SweepCamera[] = [];
  private deviceId = "";
  private grabTimer: ReturnType<typeof setInterval> | null = null;
  private generation = 0;
  readonly bins: number;

  constructor(bins = 30) {
    this.bins = bins;
  }

  get frameCount(): number {
    return this.frames.length;
  }

  get currentHeading(): number {
    return this.heading;
  }

  /** Whether an absolute camera pose has arrived during this scan. */
  get usedOrientation(): boolean {
    return this.hasOrientation;
  }

  get previewReady(): boolean { return this.ready; }
  get isRecording(): boolean { return this.recording; }
  get cameraChoices(): SweepCamera[] { return this.cameras; }
  get activeCameraId(): string { return this.deviceId; }
  get error(): string | null { return this.issue; }
  /** The newest 4096 grabFrame outcomes, oldest first. Survives `stop()` -
   *  a finished scan must still be diagnosable - and is cleared only by a
   *  fresh `start()`. A fresh array each read: `readonly` is erased at
   *  runtime, and a consumer that memoises by reference would never see a
   *  record arrive in an array mutated in place. */
  get captureLog(): readonly CaptureRecord[] { return this.captureRecords.slice(); }
  /** A copy of the mosaic's own pixels, RGBA, or null before any frame has
   *  actually been written into it (an empty panorama from `begin()` alone
   *  does not count). Never the live buffer: the panorama keeps writing to
   *  it after this copy is taken. */
  get panoramaPixels(): { width: number; height: number; pixels: Uint8ClampedArray } | null {
    if (!this.panorama || !this.hasCapturedFrame) return null;
    return { width: this.panorama.width, height: this.panorama.height, pixels: new Uint8ClampedArray(this.panorama.pixels) };
  }
  // Ready iff a reading has arrived this session, the source is currently alive
  // (see sourceHealthy) AND that reading still stands - recent, vouched for by
  // a video that says the view has not moved since it arrived, or standing from
  // the last moment the view could be judged at all (see readingStands).
  // No bare freshness window, which is what deadlocked a still phone (issue
  // #37); no lifecycle-only test either, which cannot see a sensor that stops.
  get compassReady(): boolean { return this.hasOrientation && this.sourceHealthy && this.readingStands('heading'); }
  get tiltReady(): boolean { return this.tiltAt !== null && this.sourceHealthy && this.readingStands('tilt'); }
  /** Is a reading taken at `at` still the phone's direction? Recent enough to
   *  stand alone, or the video vouches that nothing has moved since.
   *  `now` is a parameter so one decision can be taken at one instant: a caller
   *  that also has to consult the witness must not read the clock twice and
   *  grade the two halves of its answer at two different moments. */
  private vouched(at: number | null, now = performance.now()): boolean {
    if (at === null) return false;
    return now - at < SENSOR_SILENCE_MS || viewVouchesFor(at, this.stability.continuity(now), this.vouchSlopMs);
  }
  /** Does the reading taken at `at` still describe where the phone points?
   *  `vouched` is the evidence test and it decides on its own wherever the view
   *  can say anything at all. This adds the one case where it CANNOT: a frame
   *  of smooth sky, from a camera working perfectly and delivering frames, with
   *  nothing in it a shift would move (GRADIENT_FLOOR). The witness answers
   *  'featureless' there, and reading that as staleness blanked the dome and
   *  told the user to move the phone - over a view where moving is the one
   *  thing that loses the hold, and where nothing had gone wrong at all
   *  (issue #41). So a reading that was standing when the view could last be
   *  judged goes on standing for as long as the view stays featureless.
   *  `stoodAt >= at` is what makes that a memory of THIS reading rather than a
   *  blanket licence: a reading delivered after the view went blank was never
   *  witnessed by anything, and it expires like any other.
   *  The other two unknowns are untouched, and both still read as lost within
   *  SENSOR_SILENCE_MS. A STOPPED video ('stale') may be showing anything by
   *  now; a MOVING view is a measurement that the reading is out of date.
   *  Named by kind rather than handed the two halves as a pair of numbers: the
   *  fields cross silently otherwise, and there is no type between a heading
   *  instant and a tilt instant to notice it.
   *  `stoodAt >= at` admits a memory stamped in the same millisecond as the
   *  reading, including one recorded by a frame observed just before the event
   *  arrived. That is deliberate: frame times and sensor times are on one clock
   *  but not aligned to the millisecond, which is the whole reason
   *  CONTINUITY_SLOP_MS exists one file over.
   *  The margin `vouched` applies below is `vouchSlopMs`, this session's, and
   *  not the constant: on the interval fallback a reading has to stand under
   *  the same widened margin that lets that path capture under it, or the dome
   *  would say the heading is lost while frames are going into the mosaic
   *  behind it - two answers to one question (issue #48). */
  private readingStands(kind: 'heading' | 'tilt'): boolean {
    const at = kind === 'heading' ? this.headingAt : this.tiltAt;
    if (at === null) return false;
    const now = performance.now();
    if (this.vouched(at, now)) return true;
    const stoodAt = kind === 'heading' ? this.headingStoodAt : this.tiltStoodAt;
    return stoodAt !== null && stoodAt >= at && this.stability.witness(now) === 'featureless';
  }
  /** Record, for each reading kind, the instant at which THE VIDEO last vouched
   *  for it - which is the instant `readingStands` reaches back to once the
   *  view goes blank.
   *  It must be the video and not `vouched`, whose first disjunct is bare
   *  freshness. Under `vouched` a reading needed only to be less than
   *  SENSOR_SILENCE_MS old at some judgeable frame, whatever that frame said
   *  about the view - so a reading the video had just measured as MOVING was
   *  remembered, and then held for as long as the blank sky lasted. That is the
   *  reading spending its own freshness to certify itself, which is the one
   *  thing this memory exists to prevent (review A1), and it contradicted both
   *  SENSOR_SILENCE_MS's own rule that a dead sensor over a moving view is
   *  caught within 2 s and the brief for issue #41.
   *  A continuity exists only for a settled run on a fresh, judgeable frame, so
   *  'moving', 'featureless' and 'stale' are excluded here by construction and
   *  need no test of their own; `viewVouchesFor` then adds the part that makes
   *  it about THIS reading - the run reaching back to before the reading
   *  arrived, rather than merely being under way now.
   *  Called once per observed frame, so this memory runs on the camera's clock:
   *  keeping it in the getters instead would make the dome depend on how often
   *  something reads them, and a phone whose UI skipped a render during the
   *  fresh window would lose a hold for it. */
  private noteReadingsStand(): void {
    const now = performance.now();
    const view = this.stability.continuity(now);
    if (!view) return;
    // The session's own margin, the same one `vouched` and capture use: a
    // memory written on a stricter test than the one that reads it would expire
    // a reading the rest of the driver is still standing on.
    if (this.headingAt !== null && viewVouchesFor(this.headingAt, view, this.vouchSlopMs)) this.headingStoodAt = now;
    if (this.tiltAt !== null && viewVouchesFor(this.tiltAt, view, this.vouchSlopMs)) this.tiltStoodAt = now;
  }
  /** No orientation sample recent enough for the STRICT pose rule to use: it
   *  refuses a newest reading older than 250 ms (CameraPoseHistory.forFrame).
   *  Inside that window a view the witness cannot judge is an ordinary moment
   *  between frames - capture can still happen on the sensor alone - and not a
   *  state the user needs explained. */
  private get sensorQuiet(): boolean {
    const now = performance.now();
    return !((this.headingAt !== null && now - this.headingAt <= 250)
      || (this.tiltAt !== null && now - this.tiltAt <= 250));
  }
  get currentAltitude(): number { return this.altitude; }
  get cameraBasis(): CameraBasis | null {
    const b=this.compassReady && this.frameBasis && performance.now()-this.frameBasis.at<200?this.frameBasis.basis
      :this.compassReady ? this.basis : this.tiltReady && this.altitude >= 85 ? this.basis : null;
    return b?this.correctBasis(b):null;
  }
  private correctBasis(b:CameraBasis):CameraBasis {return this.visualAnchor?transferBasis(b,this.visualAnchor.raw,this.visualAnchor.aligned):b;}
  get aspectRatio(): number { return this.video?.videoWidth && this.video.videoHeight ? this.video.videoWidth/this.video.videoHeight : this.imageAspect; }
  get cameraViewAngle():number {return this.shortAxisFov;}
  get hasLensCalibration():boolean {return this.lensCalibrated;}
  get lens() {return cameraLens(this.aspectRatio,1,this.shortAxisFov);}
  setCameraViewAngle(degrees:number):boolean {
    if(this.recording || !Number.isFinite(degrees) || degrees<35 || degrees>100)return false;
    this.shortAxisFov=degrees;this.lensCalibrated=true;
    try {if(this.lensProfileKey)localStorage.setItem(this.lensProfileKey,String(degrees));}catch { /* session-only when storage is blocked */ }
    return true;
  }
  get cells() { return DOME_CELLS.map(c => ({ ...c, captured:this.coveredCells.has(c.id) })); }
  get aimTarget(): { id: number; captured: boolean } | null {
    const basis=this.cameraBasis;if(!basis)return null;
    // The dot the user aims and the cell `grabFrame` captures are now the same
    // choice, made by one function on one forward ray (issue #57).
    const cell=targetCell(basis.forward);
    if(!cell)return null;
    // Without a heading the basis has no real azimuth, so only the zenith cap
    // can be named. A tilt-only basis exists only above a RAW tilt of 85, and
    // while the visual anchor is identity that puts the pole within 5 degrees
    // and always nearest, so this withholds nothing. With an anchor set earlier
    // in the scan (bounded at 10 degrees, `correctBasis`) the corrected forward
    // can sit lower than the raw reading and the guard does fire - which is the
    // conservative direction: it withholds a dot rather than naming a cell off
    // a bearing this basis does not have.
    if(!this.compassReady && cell.alt<89)return null;
    return {id:cell.id,captured:this.coveredCells.has(cell.id)};
  }
  get justCaptured(): boolean { return this.lastCaptureAt!==null && Date.now()-this.lastCaptureAt<1000; }
  get captureCue(): string {
    if(this.issue)return this.issue;
    if(!this.recording)return 'Tap Start scan to begin capturing.';
    if(!this.video?.videoWidth || !this.video?.videoHeight)return 'Waiting for a camera image…';
    // A frozen preview keeps its last picture, so there is something on screen
    // to look at and nothing to say it is old. Only this cue can tell the user.
    if(this.imageGate==='stale-image')return 'The camera image is not updating. Close the scan and open the camera again.';
    if(this.imageGate==='frame-already-captured')return 'That picture is already captured. The next camera frame is a moment away.';
    // The same fact as the stale-image line above, reached from the other side:
    // that one is set by a capture ATTEMPT that got as far as the image gate,
    // this one by a run of ticks the media gate refused, which is counted
    // whether or not a capture was attempted - a preview frozen before Start
    // scan is pressed never reaches the image gate at all (issue #46). Both
    // name the camera image and both give the one action that clears it, so
    // whichever of the two speaks, the user is told the same thing about the
    // same camera. Below the stale-image line because a refusal recorded by an
    // attempt is the more recent evidence of the two. No compass here, and no
    // request to move the phone: moving cannot make a stopped camera deliver.
    if(this.mediaGateRefusalRun>=MEDIA_GATE_BLIND_AFTER)
      return 'No new camera image has arrived for a couple of seconds. Close the scan and open the camera again.';
    // Not "hold still": holding still is exactly what cannot be confirmed here,
    // so asking for it would leave the user doing the one thing that can never
    // satisfy the rule. Moving produces a sensor event, which does.
    if(this.stillnessFailures>=STILLNESS_BLIND_AFTER)return 'I can’t read the camera image to tell whether the phone is holding still. Move the phone slightly to register a direction.';
    // A view with nothing in it to judge a shift by is not a lost compass
    // (issue #41), and it is worth saying whether or not the reading survived
    // it. Above the compass line for that reason, and below the blind line
    // because a canvas we cannot read at all is a different and worse state.
    // Capture is NOT relaxed here and neither sentence may imply that it is.
    // A frame the witness cannot judge yields no continuity, so nothing can
    // vouch for a silent reading and the strict rule still decides; the strict
    // rule wants a sample within 250 ms, which is exactly `sensorQuiet`. While
    // a sample IS that fresh, capture can proceed on the sensor alone and this
    // is an ordinary moment with nothing to explain.
    const blankView=this.stability.witness(performance.now())==='featureless' && this.sensorQuiet;
    const basis=this.cameraBasis;
    // The reading STANDS (see readingStands), so the dome and the aim dot are
    // up and the compass line would contradict them as well as handing the user
    // the one instruction that destroys a hold.
    if(blankView && basis)
      return 'The sky here has nothing to track, so the camera cannot tell whether the phone is holding still. Bring some terrain or a building edge into the view.';
    // The reading did NOT stand - it arrived after the view went blank, so
    // nothing ever witnessed it (issue #63). The compass really is lost, and
    // that is said; but the action below it is still terrain and not movement,
    // because over a view with nothing in it a fresh reading would be lost
    // again the moment the phone stopped.
    if(blankView)
      return 'The compass has gone quiet and the sky here has nothing to track, so nothing can vouch for the last direction. Bring some terrain or a building edge into the view.';
    if(!basis)return 'Waiting for the compass. Keep the camera open and move the phone gently.';
    if(this.alignmentWait)return 'Hold the phone still for a moment so the image and direction line up.';
    // The scanner has refused to match this view over and over, and the lens is
    // still the 60-degree estimate nobody has corrected. The line below asks the
    // user to change their aim, which is not what is wrong; this names the one
    // control that is (issue #52). It sits ABOVE the overlap line rather than
    // replacing it, because the first few refusals really are the ordinary
    // "come back to a green patch" moment - it is the REPETITION that makes the
    // lens the likelier story. And it does not repeat the aim instruction: two
    // remedies in one sentence is the user trying the wrong one first.
    // Once a view angle has been measured and saved, `hasLensCalibration` is
    // true and the ordinary line comes back - against a lens the user has
    // actually given us, a refusal means again what it used to mean.
    // It names the SETTING and quotes no control label. `setCameraViewAngle`
    // has no caller in any committed component at b8581949 - the field is in a
    // sibling session's unlanded rewrite of horizon.tsx - so a sentence
    // quoting that field's label would be pointing at something this product
    // does not have, and nothing here could keep the quote true through a
    // rename (see the issue filed against the missing control). "camera view
    // angle" is the phrase the capture-DOM case pins, and whatever lands
    // should use those words.
    // Ending a scan tears the capture panel down, so the setting is not where
    // the user is standing when this fires: the route back to it is named
    // rather than assumed.
    if(this.overlapWaitRun>=LENS_DOUBT_AFTER && !this.lensCalibrated)
      return 'I still can’t match this view. The camera view angle may be set wrong for this lens. End the scan, open the camera again, then set the camera view angle before you scan again.';
    if(this.overlapWait)return 'I can’t match this view yet. Return to a green patch, hold still, then move slowly toward the next blue dot. Keep the camera lens in the same spot.';
    if(this.justCaptured)return 'Captured. Move to another blue dot.';
    const target=this.aimTarget;
    if(target?.captured)return 'Already captured. Aim at a blue dot.';
    if(target)return 'Hold here… capturing this patch.';
    return 'Bring a blue dot into the centre ring.';
  }
  get coverageRows(): boolean[][] {
    return SWEEP_BANDS.map((_, band) => Array.from({ length: this.bins }, (_, bin) =>
      this.frames.some(f => f.band === band && f.bin === bin)));
  }
  get overheadCaptured(): boolean { return this.frames.some(f => f.band === OVERHEAD_BAND); }
  get usedManualOverhead(): boolean { return this.frames.some(f => f.manualOverhead); }
  get capturedTiles(): number {
    return this.coveredCells.size;
  }
  get totalTiles(): number { return DOME_CELLS.length; }
  get complete(): boolean { return this.capturedTiles === this.totalTiles; }
  get currentBand(): number | null { return this.tiltReady ? bandForAltitude(this.altitude) : null; }
  get nextBand(): number {
    const missing = this.coverageRows.findIndex(row => row.some(seen => !seen));
    return missing >= 0 ? missing : OVERHEAD_BAND;
  }

  begin(): void {
    if (!this.ready || !this.compassReady) return;
    // The run of overlap refusals belongs to a scan, not to a camera session,
    // and this starts one. Unreachable today - the only path that clears
    // `recording` without `stop()` records `read-failed`, which ends a run -
    // but "unreachable by luck" is how a second `begin()` on a live sweep comes
    // to open with a lens sentence before a single refusal.
    this.overlapWaitRun = 0;
    this.frames = []; this.panorama = new SkyPanorama(); this.coveredCells.clear(); this.aimedZenith=false; this.lastCaptureAt=null; this.scanSamples=[]; this.lastDiagnosticAt=-Infinity; this.hasCapturedFrame = false; this.recording = true;
  }

  /** The user explicitly aims the rear camera up. This still reads an actual
   * video frame and cannot stand in for any missing azimuth/elevation ring. */
  captureOverhead(): boolean {
    if (this.overheadCaptured) return false;
    return this.grabFrame(true);
  }

  async start(video: HTMLVideoElement, canvas: HTMLCanvasElement, deviceId?: string): Promise<void> {
    this.stop();
    const generation = this.generation;
    this.video = video;
    this.canvas = canvas;
    this.frames = []; this.issue = null; this.basis = null; this.panorama = null; this.coveredCells.clear(); this.aimedZenith=false; this.lastCaptureAt=null;
    // A new scan: the diagnostic log from any earlier session is no longer
    // about this camera session, so it starts over. `stop()` never does this.
    this.captureRecords = []; this.hasCapturedFrame = false;
    this.poses.clear();this.tilts.clear();this.poseSource.clear();this.visualAnchor=null;this.lastRegistrationAt=-Infinity;this.frameBasis=null;this.alignmentWait=false;this.overlapWait=false;this.lastSensorReading=null;
    this.stability.clear();this.trackEnded=false;
    this.lastMediaTime=null;this.lastMediaAdvanceAt=-Infinity;this.presentedFrameId=null;this.lastCapturedFrameId=null;this.imageGate=null;
    this.hasOrientation = false; this.tiltAt = null; this.headingAt = null;
    this.headingStoodAt = null; this.tiltStoodAt = null;
    this.orientationSupported = typeof window !== "undefined" && "DeviceOrientationEvent" in window;
    const DOE = window.DeviceOrientationEvent as typeof DeviceOrientationEvent & { requestPermission?: () => Promise<string> };
    // Ask from the click gesture, before awaiting camera discovery (Safari).
    const motionPermission = DOE?.requestPermission?.().catch(() => "denied");
    const list = async (): Promise<SweepCamera[]> => {
      try { return (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "videoinput")
        .map((d, i) => ({ deviceId: d.deviceId, label: d.label || `Camera ${i + 1}` })); }
      catch { return []; }
    };
    this.cameras = await list();
    if (generation !== this.generation) return;
    const open = (id?: string) => navigator.mediaDevices.getUserMedia({ audio: false, video: {
      ...(id ? { deviceId: { exact: id } } : { facingMode: { ideal: "environment" } }),
      width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 24, max: 30 },
    } });
    let stream = await open(deviceId ?? preferredRearCamera(this.cameras));
    // Closing the editor while the browser permission prompt is open must
    // also release a camera granted after the editor has disappeared.
    if (generation !== this.generation) { stream.getTracks().forEach(t=>t.stop()); return; }
    this.stream = stream;
    this.cameras = await list();
    if (generation !== this.generation) return;
    const preferred = deviceId ?? preferredRearCamera(this.cameras);
    const current = stream.getVideoTracks?.()[0]?.getSettings?.().deviceId;
    if (preferred && current && preferred !== current) {
      stream.getTracks().forEach(t => t.stop());
      stream = await open(preferred);
      if (generation !== this.generation) { stream.getTracks().forEach(t => t.stop()); return; }
      this.stream = stream;
    }
    this.deviceId = stream.getVideoTracks?.()[0]?.getSettings?.().deviceId ?? preferred ?? "";
    video.srcObject = this.stream;
    try { await video.play(); }
    catch { this.stop(); throw new Error("The camera opened but its preview could not play. Try opening the camera again."); }
    if (generation !== this.generation) return;
    this.ready = true;
    // The baseline reading, taken now that the element is playing: the timer
    // path needs TWO readings to see a clock move, and this is the first one.
    // Taking it here rather than on the first tick costs no frame - the
    // preview has been running for the whole 350 ms before that tick - while
    // an element whose clock never moves still fails every comparison.
    this.lastMediaTime = Number.isFinite(video.currentTime) ? video.currentTime : null;
    this.imageAspect = video.videoWidth && video.videoHeight ? video.videoWidth/video.videoHeight : 4/3;
    // Calibration belongs to this lens and crop in this browser. It must not
    // become a universal constant for another phone, lens or video aspect.
    const crop=Math.min(this.imageAspect,1/this.imageAspect).toFixed(3);
    this.lensProfileKey=`astrodeck.photosphere.lens.${this.deviceId || 'default'}.${crop}`;
    this.shortAxisFov=60;this.lensCalibrated=false;
    try {const stored=Number(localStorage.getItem(this.lensProfileKey));if(stored>=35&&stored<=100){this.shortAxisFov=stored;this.lensCalibrated=true;}}catch { /* private browsing */ }
    stream.getVideoTracks?.().forEach(track => track.addEventListener?.("ended", () => {
      if (generation !== this.generation) return;
      this.issue = "The camera stopped. Close the scan and open it again.";
      this.ready = false; this.recording = false; this.trackEnded = true;
    }));

    if (motionPermission && await motionPermission !== "granted") {
      this.issue = "Motion access was denied. Allow motion sensors to scan, or draw the horizon by hand.";
    }
    if (generation !== this.generation) return;
    if (typeof window !== "undefined" && "DeviceOrientationEvent" in window) {
      this.headingHandler = (e: Event) => {
        const oe = e as DeviceOrientationEvent & { webkitCompassHeading?: number };
        const screenAngle = window.screen?.orientation?.angle ?? 0;
        const received=performance.now();
        // DOM event timestamps and video captureTime share the performance time
        // origin. Fall back for older implementations using epoch timestamps.
        const at=Number.isFinite(e.timeStamp)&&Math.abs(received-e.timeStamp)<2000?e.timeStamp:received;
        const elevation = cameraElevation(oe);
        if (elevation !== null) { this.altitude = elevation; this.tiltAt = at;
          this.tilts.add({at,screenAngle,basis:orientationBasis(0,oe.beta!,oe.gamma!,screenAngle)});
        }
        const pose = cameraPose(oe, e.type === "deviceorientationabsolute");
        const valid=[oe.alpha,oe.beta,oe.gamma].every(v=>typeof v==='number'&&Number.isFinite(v));
        const correction = typeof oe.webkitCompassHeading === 'number' ? oe.webkitCompassHeading - (360 - oe.alpha!) : 0;
        const accepted=valid?this.poseSource.accept(orientationBasis(oe.alpha!,oe.beta!,oe.gamma!,screenAngle,correction),!!pose,at):null;
        if (accepted) { this.heading = skyAngles(accepted.basis.forward).az;
          this.lastSensorReading={alpha:oe.alpha,beta:oe.beta,gamma:oe.gamma,absolute:oe.absolute,event:e.type,screenAngle,at};
          this.lastAlpha = oe.alpha!;
          this.basis = accepted.basis;
          if(accepted.changedSource){this.poses.clear();this.frameBasis=null;}
          this.poses.add({at,screenAngle,basis:this.basis});
          this.hasOrientation = true; this.headingAt = at;
        } else if (elevation !== null && elevation >= 85 && !this.compassReady) {
          // No absolute bearing at the zenith: retain the last azimuth frame
          // for display, but only project the single overhead pixel below.
          this.basis = orientationBasis(this.lastAlpha,oe.beta!,oe.gamma!,screenAngle);
        }
      };
      window.addEventListener("deviceorientationabsolute", this.headingHandler);
      window.addEventListener("deviceorientation", this.headingHandler);
      this.listening = true;
    }

    if(typeof video.requestVideoFrameCallback==='function'){
      // This path stamps each observation with the frame's own capture time
      // (see the `seen` line below), so what separates an observation from the
      // reading it covers is clock alignment and nothing else.
      this.vouchSlopMs=CONTINUITY_SLOP_MS;
      let lastSample=-Infinity;
      const frame:VideoFrameRequestCallback=(now,metadata)=>{
        if(generation!==this.generation)return;
        // The sensor stops talking when the phone stops moving. Ask the video
        // and the page lifecycle instead, and hand both answers to forFrame.
        // What the frame shows happened when the CAMERA saw it, not when this
        // callback ran: captureTime is tens of ms earlier, and stamping the
        // pixels with the callback time pushes every still run forward of the
        // reading it has to reach back to. A capture time later than the
        // callback, or older than a stale frame, cannot belong to this frame,
        // so it is not believed and the callback time stands. Only the STILLNESS
        // stamp is decided here: the raw captureTime below is validated again
        // inside CameraPoseHistory.forFrame, by rules of its own that err toward
        // returning no pose at all. Two validations of one field, deliberately,
        // because they answer different questions - if either is changed, read
        // the other (photospherePose.ts, the captureTime branch).
        // This callback runs only for a frame the browser actually presented,
        // so it is itself the delivery evidence for this path - and the frame
        // carries its own identity, which is what a capture is tied to.
        // Normalised to null, never undefined: an id of `undefined` would be
        // stored by the first capture and then equal every later one, refusing
        // the rest of the scan.
        this.presentedFrameId=Number.isFinite(metadata.mediaTime)?metadata.mediaTime
          :Number.isFinite(metadata.presentedFrames)?metadata.presentedFrames:null;
        this.lastMediaAdvanceAt=now;
        const capture=metadata.captureTime;
        const seen=capture!==undefined&&Number.isFinite(capture)&&capture<=now&&now-capture<=STALE_FRAME_MS?capture:now;
        this.observeStillness(video,seen);
        const evidence:PoseEvidence={view:this.stability.continuity(now),sourceHealthy:this.sourceHealthy,slopMs:this.vouchSlopMs};
        const basis=this.poses.forFrame(now,metadata.captureTime,evidence);
        if(basis)this.frameBasis={basis,at:now};
        else this.frameBasis=null;
        if(now-lastSample>=GRAB_INTERVAL_MS){lastSample=now;this.grabFrame(false,{basis,tilt:this.tilts.forFrame(now,metadata.captureTime,evidence)});}
        this.videoFrameHandle=video.requestVideoFrameCallback(frame);
      };
      this.videoFrameHandle=video.requestVideoFrameCallback(frame);
    } else {
      // No frame metadata on this path, so every observation is stamped when it
      // was READ, and a camera pipeline delay therefore stamps the break that
      // ends an approach later than the reading it has to cover (see the bands
      // in `grabFrame`). The witness here resolves nothing finer than its own
      // interval, so that interval IS the margin - with CONTINUITY_SLOP_MS kept
      // as the floor, because clock alignment does not stop mattering just
      // because something bigger has been added to it.
      this.vouchSlopMs=Math.max(CONTINUITY_SLOP_MS,GRAB_INTERVAL_MS);
      this.grabTimer = setInterval(() => this.grabFrame(), GRAB_INTERVAL_MS);
    }
  }

  /** Is the pose stream ALIVE? Measured, never inferred from event silence:
   *  both orientation listeners attached, the page visible, and no camera
   *  track ended. Read at the moment of use so a visibility change or a
   *  removed listener takes effect without waiting for an event of its own.
   *  `this.listening` is false on two different browsers, and only one of
   *  them is unhealthy: one HAS `DeviceOrientationEvent` and simply has not
   *  attached yet or has lost its listener, which is a real fault worth the
   *  name; the other never HAD the constructor, so it has no pose stream to
   *  judge at all - nothing is stalled, there is just nothing there. Without
   *  `!this.orientationSupported` as an escape, that second browser reads as
   *  permanently unhealthy, and `grabFrame` refused even a manual overhead
   *  press on it - the one path a missing compass does not need (issue #42
   *  item 3; it worked before 730a59b9). Automatic capture is untouched by
   *  this: it still needs an actual reading (`hasOrientation`), which such a
   *  browser can never produce, so only the manual press is reopened. */
  private get sourceHealthy(): boolean {
    const visibility = typeof document === "undefined" ? undefined : document.visibilityState;
    return (this.listening || !this.orientationSupported) && !this.trackEnded
      && (visibility === undefined || visibility === "visible");
  }

  /** Sample the preview into a 32x24 luminance grid, the video's own answer to
   *  "is this view holding still". A plain detached canvas rather than an
   *  OffscreenCanvas: every browser that reaches this code already has one,
   *  and 768 pixels per frame is cheap enough for the UI thread.
   *  `at` is when the CAMERA saw this frame, the meaning
   *  `VisualStability.observe` gives its own `at` - but only one caller can
   *  honour it. The rVFC path has the frame's `captureTime` and passes that.
   *  The timer path has no frame metadata at all, so `grabFrame` passes the
   *  READ instant, which is the capture time plus however long the camera
   *  pipeline took. That inflates every break's `from` on that path by the
   *  delay, and `from` is what the vouching margin is measured against - so the
   *  margin on that path is the witness's own interval rather than
   *  CONTINUITY_SLOP_MS (see `vouchSlopMs` and the bands in `grabFrame`), which
   *  is what keeps a delayed camera from losing a hold it earned (issue #48).
   *  The stamp itself is not fixable here: there is no capture time to stamp
   *  with, and a guess would be a delay measurement this code cannot make.
   *  A MANUAL overhead press never observes stillness, on either path. It does
   *  reach `grabFrame` - `captureOverhead` calls `grabFrame(true)` - but
   *  `mediaGateAsked` there excludes `manualOverhead`, so `delivered` is false,
   *  and the one call to this method inside `grabFrame` is behind `delivered`.
   *  Nor does a press enter the rVFC frame callback, which is where that path
   *  observes. So it has not observed since review 17 P1 gave the press its own
   *  freshness test (`lastMediaAdvanceAt`), not the witness's consume-on-read.
   *  That also closes issue #49 item 2, which was written against the older
   *  shape: a press on an rVFC device cannot stamp an observation with the
   *  press instant into a stream of capture-stamped frames, so it cannot trip
   *  `observe`'s `at < frameAt` guard and silently drop the frames behind it.
   *  No change here; the note stays because the two facts that make it safe
   *  sit fifty lines apart inside `grabFrame` and neither says this alone. */
  private observeStillness(video: HTMLVideoElement, at: number): void {
    if (!video.videoWidth || !video.videoHeight) return;
    try {
      if (!this.lumaCanvas) {
        this.lumaCanvas = document.createElement("canvas");
        this.lumaCanvas.width = GRID_W; this.lumaCanvas.height = GRID_H;
      }
      // This reads its own pixels back every single frame, which is the one
      // access pattern a GPU-backed canvas is worst at.
      const ctx = this.lumaCanvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) throw new Error("no 2d context for the stillness sample");
      ctx.drawImage(video, 0, 0, GRID_W, GRID_H);
      const { data } = ctx.getImageData(0, 0, GRID_W, GRID_H);
      for (let p = 0; p < this.luma.length; p++) this.luma[p] = luminance(data[p*4], data[p*4+1], data[p*4+2]);
      this.stability.observe(at, this.luma, GRID_W, GRID_H);
      this.stillnessFailures = 0;
      // Immediately after the observation, so the verdict this reads is the one
      // the frame just delivered rather than the previous frame's.
      this.noteReadingsStand();
    } catch {
      // A lost drawing context tells us nothing, so stability stays unknown -
      // and unknown means the strict rule, which means a STILL phone can never
      // capture, forever, behind a cue telling it to hold still. Swallowing
      // this reinstates the whole defect with nothing anywhere recording why,
      // so it is counted, said in the cue and carried in the alignment report.
      this.stillnessFailures++;
    }
  }

  /** The interval fallback runs on a TIMER, and a timer proves nothing about
   *  the camera: a paused or stalled element keeps its last decoded image and
   *  its dimensions, and re-reading that image every 350 ms would earn a hold
   *  the camera never witnessed (review 15, P1). A frame counts only when the
   *  element is playing with data and the media clock has moved since the last
   *  one, and the track behind it is live and not muted. */
  private newMediaFrame(video: HTMLVideoElement, now: number): boolean {
    const track = this.stream?.getVideoTracks?.()[0];
    if (video.paused || video.ended || video.readyState < 2) return false;           // 2 = HAVE_CURRENT_DATA
    if (track && (track.readyState !== "live" || track.muted)) return false;
    const t = video.currentTime;
    if (!Number.isFinite(t)) return false;
    // One reading is a number, not a delivery. The first one only says where
    // the clock stands; it takes a second, larger one to show it is running.
    if (this.lastMediaTime === null) { this.lastMediaTime = t; return false; }
    if (!(t > this.lastMediaTime)) return false;
    // The witness consumes the frame; the delivery it proves is remembered
    // separately, because capture needs the delivery and not the consumption.
    this.lastMediaTime = t; this.lastMediaAdvanceAt = now;
    return true;
  }

  /** Append one outcome to the diagnostic log. This records; it never decides
   *  anything - every gate below still returns its own `false` on its own
   *  terms, this just names which one fired. */
  private recordCapture(now: number, outcome: CaptureOutcome, extra?: { cell?: number; basis?: CameraBasis; sensorBasis?: CameraBasis; adjusted?: boolean }): void {
    // The exception to "records, never decides", and here deliberately: this is
    // the single point every outcome passes through, so the run of overlap
    // refusals the cue reads cannot miss one. Counting it at the two
    // `overlap-wait` return sites instead would leave a third site, added
    // later, silently uncounted - and the reset would have to be repeated at
    // every other `return` in `grabFrame`. It still decides nothing about THIS
    // call: the gate below has already returned on its own terms.
    if (outcome === 'overlap-wait') this.overlapWaitRun++;
    else if (endsOverlapRun(outcome)) this.overlapWaitRun = 0;
    this.captureRecords.push({ at: now, outcome, ...extra });
    if (this.captureRecords.length > CAPTURE_LOG_LIMIT) this.captureRecords.splice(0, this.captureRecords.length - CAPTURE_LOG_LIMIT);
  }

  private grabFrame(manualOverhead = false, frame?:{basis:CameraBasis|null;tilt:CameraBasis|null}): boolean {
    const { video, canvas } = this;
    const now=performance.now();
    // Browsers without requestVideoFrameCallback - Firefox Android, notably -
    // reach the preview's pixels only here, so this path takes its own sample.
    // Without it stability is permanently unknown there, the strict rule never
    // relaxes and a still phone deadlocks exactly as it did before any of this.
    // Only a NEWLY DELIVERED frame is evidence (see newMediaFrame): the timer
    // firing is not the camera producing a picture. A hold is therefore earned
    // only while the media clock is moving. While frames do keep arriving every
    // tick carries a new one - 350 ms at 24 fps is eight frames - so the gate
    // refuses nothing and the settle still lands inside the window
    // photosphereStillnessDom pins on this path, at least 500 ms of watched
    // stillness and no more than the 1.5 s acceptance budget. A frozen
    // or paused element contributes no observation at all, so stability goes
    // UNKNOWN STALE_FRAME_MS after the last real frame and the strict rule
    // takes back over - the honest outcome, and the one a timer on its own
    // could never reach.
    // What a camera PIPELINE DELAY costs here, and why the margin this path
    // vouches on is its own interval (`vouchSlopMs`) and not CONTINUITY_SLOP_MS.
    // Observations on this path carry the READ instant, not a capture time (see
    // observeStillness), so a delay L pushes the content of every observation
    // back by L while its stamp stays where it is: with the phone coming to
    // rest at M, the last observation whose content still moves lands in
    // (M + L - I, M + L] for an interval I of 350 ms, and THAT stamp is the
    // break's `from` the reading at M has to be reached across. With the margin
    // at I the bands are:
    //   L <= 350 ms       always vouched, whatever the sampling phase;
    //   350 < L <= 700    vouched or not depending on where the phase falls -
    //                     an intermittent deadlock, different hold to hold;
    //   L > 700           never vouched, and the hold can never capture.
    // They were 150 and 500 on the clock-alignment margin alone, which is the
    // Firefox Android deadlock of issue #48 - the fallback is the only path
    // that browser has. The trade, stated because it is a real cost and not a
    // free fix: the P1 window in which a movement after the last reading goes
    // unchallenged widens here by one interval, from 500 ms to about 700 ms
    // (see CONTINUITY_SLOP_MS for the same sum on the other path). The
    // alternative was an intermittent deadlock on the browsers this path exists
    // for, and a deadlock is the worse failure: it has no recovery the user can
    // find, while the widened window is bounded, still measured against the
    // VIEW, and still ends at the first frame that shows movement.
    // photosphereStillnessDom pins both edges of the band on this path: a
    // 300 ms delay captures and an 800 ms delay does not.
    // The witness CONSUMES the frame it reads, so a manual press never runs
    // this: the user pressing the button must not be charged for a frame, nor
    // refused one because the witness got there first (review 17, P1).
    // A refusal leaves no other trace: the gate returns before
    // `observeStillness`, so `stillnessFailures` stays 0 and a session frozen
    // end to end reported the envelope of a healthy one (issue #46). So it is
    // counted - but only where the gate was actually ASKED, which is the timer
    // path with an element and a live session. A hidden page or an ended track
    // is not the camera declining to deliver; that has its own outcome
    // ('unhealthy') below and is not charged here. The count runs whether or
    // not the scan is recording, because a preview frozen before Start scan is
    // pressed is the same broken camera and the report should say so.
    const mediaGateAsked = !!video && !frame && !manualOverhead && this.sourceHealthy;
    const delivered = mediaGateAsked && this.newMediaFrame(video!, now);
    if (mediaGateAsked) {
      if (delivered) this.mediaGateRefusalRun = 0;
      else { this.mediaGateRefusals++; this.mediaGateRefusalRun++; }
    }
    if (delivered) this.observeStillness(video!, now);
    // One visibility rule, not two. A second copy of the test here could
    // disagree with the sourceHealthy the evidence below is built from.
    // Split into three named outcomes rather than one combined check, so the
    // log says WHICH of these was true rather than just "some gate failed".
    if (!this.recording) { this.recordCapture(now, 'not-recording'); return false; }
    if (!this.ready || !video || !canvas) { this.recordCapture(now, 'not-ready'); return false; }
    if (!this.sourceHealthy) { this.recordCapture(now, 'unhealthy'); return false; }
    if (video.videoWidth === 0 || video.videoHeight === 0) {
      if (manualOverhead) this.issue = "Waiting for a camera image. Keep the rear camera pointing up and try again.";
      this.recordCapture(now, 'no-image');
      return false;
    }
    // An image the camera actually DELIVERED is a requirement of capture, not
    // only of the stillness witness. A frozen or paused element keeps its last
    // decoded picture and its dimensions, and a live sensor chattering a few
    // tenths of a degree satisfies the strict pose path all on its own - so
    // those retained pixels were being stored under the phone's current
    // direction, and a manual overhead press captured them outright (review
    // 17, P1). Both questions here are about the IMAGE and neither about the
    // sensor, which is why this sits above every pose test rather than beside
    // them: has the camera delivered a picture within STALE_FRAME_MS, and is
    // the one on screen a different picture from the one already captured.
    // Freshness is not consumption. The witness reads each frame once
    // (newMediaFrame), and a press moments later must still be able to capture
    // the frame the user is looking at, so this asks for a RECENT delivery -
    // not for a frame the witness has not already seen.
    // What counts as "the camera delivered this" differs by path, because what
    // each path can know differs:
    //  - rVFC: the callback only runs for a presented frame, so it IS one.
    //  - the timer: it fires whether or not the camera did anything, so the
    //    only proof is that the media clock moved on THIS tick. A camera that
    //    froze a moment ago would otherwise still be inside the freshness
    //    window below and get one last picture captured under whatever
    //    direction the sensor is currently chattering.
    //  - a manual press: the user is pointing at the picture on the screen, so
    //    the question is whether that picture is recent, not whether it is one
    //    nothing has looked at. This is the only path that uses the window.
    const frameId = frame ? this.presentedFrameId
      : Number.isFinite(video.currentTime) ? video.currentTime : null;
    const deliveredNow = frame ? true
      : manualOverhead ? now - this.lastMediaAdvanceAt <= STALE_FRAME_MS
      : delivered;
    // Two refusals, not one. No delivery means the camera has stopped and the
    // user needs to hear that; an unidentifiable frame is the same story, since
    // nothing can be said about a picture that cannot be named.
    if (!deliveredNow || frameId === null) {
      this.imageGate = 'stale-image';
      if (manualOverhead) this.issue = "The camera image is not updating. Close the scan, open the camera again, then try the overhead shot.";
      this.recordCapture(now, 'stale-image');
      return false;
    }
    // A picture that has already been captured, on a camera that is working
    // perfectly well: the next frame is milliseconds away, so this is a wait,
    // not a fault, and saying "not updating" here would be a lie.
    if (frameId === this.lastCapturedFrameId) {
      this.imageGate = 'frame-already-captured';
      if (manualOverhead) this.issue = "That picture is already captured. The next camera frame is a moment away - try again.";
      this.recordCapture(now, 'frame-already-captured');
      return false;
    }
    this.imageGate = null;
    const evidence:PoseEvidence={view:this.stability.continuity(now),sourceHealthy:this.sourceHealthy,slopMs:this.vouchSlopMs};
    const rawBasis=frame ? frame.basis : this.poses.forFrame(now,undefined,evidence);
    let basis=rawBasis?this.correctBasis(rawBasis):null;
    const tilt=frame ? frame.tilt : this.tilts.forFrame(now,undefined,evidence);
    let measured=basis?skyAngles(basis.forward):tilt?skyAngles(tilt.forward):null;
    const overhead=manualOverhead || (!!measured && bandForAltitude(measured.alt)===OVERHEAD_BAND);
    if(!manualOverhead && !basis && !(tilt&&overhead)){this.alignmentWait=true;this.recordCapture(now,'alignment-wait');return false;}
    // A timestamp does not make a frame taken during motion sharp or account
    // for an entire low-light exposure. Hold still even with frame timestamps.
    const stable=basis?this.poses.forFrame(now,undefined,evidence):this.tilts.forFrame(now,undefined,evidence);
    if(!manualOverhead && (!stable || poseSeparation(stable,(rawBasis??tilt)!)>1.5)){this.alignmentWait=true;this.recordCapture(now,'alignment-wait');return false;}
    this.alignmentWait=false;
    if(!manualOverhead && basis){
      const target=targetCell(basis!.forward);
      if(!target){this.overlapWait=false;this.recordCapture(now,'no-target');return false;}
      if(now-this.lastRegistrationAt<600){this.recordCapture(now,'too-soon');return false;}
      this.lastRegistrationAt=now;
    }
    // All pixel positions and metadata use the pose of this frame. Relative
    // tilt must never be combined with a different absolute bearing.
    if (!manualOverhead && measured!.alt < -10) { this.recordCapture(now,'below-horizon'); return false; }
    const ctx = canvas.getContext("2d");
    if (!ctx) { this.issue = "Could not read the camera image. Close the scan and try again."; this.recording = false; this.recordCapture(now,'read-failed'); return false; }
    this.imageAspect = video.videoWidth / video.videoHeight;
    canvas.width = this.imageAspect >= 1 ? 320 : Math.round(320*this.imageAspect);
    canvas.height = this.imageAspect >= 1 ? Math.round(320/this.imageAspect) : 320;
    let data: Uint8ClampedArray;
    // Filled only on the normal, non-overhead capture path below, for the
    // 'accepted' record: a manual or tilt-only overhead capture has no single
    // DOME_CELLS target, so it is logged accepted with none of these set.
    let capturedCell: number | undefined;
    let capturedBasis: CameraBasis | undefined;
    let capturedSensorBasis: CameraBasis | undefined;
    let capturedAdjusted: boolean | undefined;
    try {
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      // Unreachable while `recording` is true: `begin()` allocates the
      // panorama in the same statement it sets `recording`, and nothing
      // clears one without the other. Kept, and logged as `no-image` (the
      // nearest existing outcome - nothing to add a frame to), as a
      // defensive TS non-null guard rather than a real behavioural gate.
      if (!this.panorama) { this.recordCapture(now,'no-image'); return false; }
      const lens=cameraLens(video.videoWidth,video.videoHeight,this.shortAxisFov);
      if (basis && !manualOverhead) {
        const registration=registerFrame(this.panorama,data,canvas.width,canvas.height,basis,lens);
        const overlap=registration.overlap;
        if(registration.adjusted && rawBasis){
          if(poseSeparation(rawBasis,registration.basis)>10){this.overlapWait=true;this.recordCapture(now,'overlap-wait');return false;}
          basis=registration.basis;this.visualAnchor={raw:rawBasis,aligned:basis};
          measured=skyAngles(basis.forward);
        }
        // Keep a small, local reproduction bundle. It is downloaded only when
        // requested, never uploaded; no site coordinates or device IDs included.
        if(now-this.lastDiagnosticAt>=1000){
          this.lastDiagnosticAt=now;
          this.scanSamples.push({at:now,basis,sensorBasis:rawBasis,relativeMotion:this.poseSource.usesRelative,adjusted:registration.adjusted,lens,videoWidth:video.videoWidth,videoHeight:video.videoHeight,
            sensor:this.lastSensorReading,stillnessReadFailures:this.stillnessFailures,overlap,image:canvas.toDataURL('image/jpeg',.8)});
          if(this.scanSamples.length>16)this.scanSamples.splice(1,1);
        }
        if(overlap.result==='conflict'){this.overlapWait=true;this.recordCapture(now,'overlap-wait');return false;}
        this.overlapWait=false;
        const target=targetCell(basis!.forward);
        if(!target){this.recordCapture(now,'no-target');return false;}
        if(this.coveredCells.has(target.id)){this.recordCapture(now,'already-captured');return false;}
        this.panorama.add(data,canvas.width,canvas.height,basis,lens);
        capturedCell=target.id;capturedBasis=basis;capturedSensorBasis=rawBasis??undefined;capturedAdjusted=registration.adjusted;
        // This frame was placed with a full basis, so if the cap is what it was
        // aimed at, the cap's pixels are as well oriented as any other cell's.
        if(target.alt>89)this.aimedZenith=true;
      } else if (overhead) {
        // Without heading, an entire overhead photograph cannot be oriented.
        // Keep only its centre at the shared zenith; do not invent a sky cap.
        this.panorama.addZenith(data,canvas.width,canvas.height,tilt&&skyAngles(tilt.forward).alt>=85?tilt:null,lens);
      }
    } catch { this.issue = "Could not read the camera image. Close the scan and try again."; this.recording = false; this.recordCapture(now,'read-failed'); return false; }
    const column = columnFromImageData(data, canvas.width, canvas.height, 24);
    const blue = columnFromImageData(data, canvas.width, canvas.height, 24, blueness);

    const altitude=manualOverhead?90:measured!.alt;
    const band = overhead ? OVERHEAD_BAND : bandForAltitude(altitude) ?? -1;
    const bin = band === OVERHEAD_BAND ? 0 : binForHeading(measured!.az, this.bins);
    const targetAltitude = band === OVERHEAD_BAND ? 90 : Math.round(altitude / 5) * 5;
    const sameTile = (f:SweepFrame)=>f.bin===bin&&f.band===band&&(band===OVERHEAD_BAND||Math.round((f.altitude??0)/5)*5===targetAltitude);

    this.frames = this.frames.filter(f => !sameTile(f));
    // Browsers expose no calibrated lens FOV. This remains an editable estimate.
    this.issue = null;
    this.frames.push({ bin, band, column, blue, altitude, manualOverhead,
      verticalFov: video.videoHeight > video.videoWidth ? 60 : 45 });
    const previousCoverage=this.coveredCells.size;
    // The zenith cap is the one cell that can be painted by frames nobody
    // pointed at it: a side frame's vertical field of view reaches the pole
    // from well down the sky, and the tilt-only overhead paint (`addZenith`)
    // has no heading and so can only claim the single zenith point. So the cap
    // needs a frame that MEANT it - either the overhead band, or an oriented
    // frame accepted with the cap as its target. Before the aim cone was
    // widened those were the same thing, because the cap could only be targeted
    // from altitude 85 up; now it can be targeted from about 80, and requiring
    // the band alone left the user holding on a dot that was being captured
    // over and over and never turned green.
    for(const cell of DOME_CELLS) {
      if(this.panorama?.covered(cell) && (cell.alt < 89 || this.overheadCaptured || this.aimedZenith)) this.coveredCells.add(cell.id);
    }
    if(this.coveredCells.size>previousCoverage)this.lastCaptureAt=Date.now();
    this.hasCapturedFrame = true;
    // This capture is now associated with the frame it was taken from, so the
    // same delivered picture cannot be captured twice BY THE SAME PATH. There
    // are two identity spaces and they do not meet: the frame callback names a
    // frame by its metadata (`mediaTime`, or `presentedFrames`), while the
    // timer and the manual press name it by `video.currentTime`. A press
    // landing right after the callback path captured the frame it is showing
    // is therefore accepted. That is a judged trade, not an oversight: the
    // artefact is one duplicate zenith the user asked for by pressing, and the
    // alternative is refusing a deliberate press for a reason it cannot see.
    this.lastCapturedFrameId = frameId;
    this.recordCapture(now, 'accepted', { cell: capturedCell, basis: capturedBasis, sensorBasis: capturedSensorBasis, adjusted: capturedAdjusted });
    return true;
  }

  /** What the tracer reads: `BIN_SAMPLES` columns across each azimuth bin, in
   *  both channels, the bin's centre column FIRST so the uncertainty rule
   *  still reads the column it has always read. The frame-fold fallback has no
   *  panorama to sample across, so it offers the one column it has. */
  columns(): SkyBin[] {
    const panorama = this.panorama;
    if (panorama) {
      const fine = this.bins * BIN_SAMPLES, centre = (BIN_SAMPLES - 1) >> 1;
      const lum = panorama.columns(fine), blue = panorama.blueColumns(fine);
      const order = [centre, ...Array.from({ length: BIN_SAMPLES }, (_, k) => k).filter(k => k !== centre)];
      return Array.from({ length: this.bins }, (_, bin) =>
        order.map(k => ({ lum: lum[bin * BIN_SAMPLES + k], blue: blue[bin * BIN_SAMPLES + k] })));
    }
    const lum = projectSweepColumns(this.frames, this.bins);
    const blue = projectSweepColumns(this.frames, this.bins, 'blue');
    return lum.map((column, bin) => [{ lum: column, blue: blue[bin] }]);
  }

  /** The bin-centre luminance column per bin - the array the simulator's
   *  `columns.json` records and issue #58 cites, unchanged by the sub-sampling
   *  above so a recorded column still means what it meant. */
  centreColumns(): number[][] {
    return this.panorama?.columns(this.bins) ?? projectSweepColumns(this.frames, this.bins);
  }

  panoramaImage(): string {
    if(!this.panorama) throw new Error('No camera images have been captured yet.');
    return this.panorama.toDataURL();
  }

  alignmentReport():string {
    // `stillnessReadFailures` sits in the ENVELOPE as well as in each sample,
    // because the run this field exists to explain is the one with no samples
    // at all: blind to the pixels means the strict rule means nothing was ever
    // accepted. Spec 4.3 - a session with zero accepted frames is diagnosable.
    // `mediaGateRefusals` is the other half of that same question and had no
    // field at all: the interval fallback refuses a tick the camera delivered
    // no frame for, and that refusal happens BEFORE `observeStillness`, so a
    // frozen preview reported `stillnessReadFailures: 0`, which is exactly what
    // a healthy session reports (issue #46). Total and current run both, since
    // a scan that stuttered once is not a scan whose camera has stopped.
    return JSON.stringify({version:1,description:'Local camera samples for alignment debugging; contains photos of your surroundings.',
      browser:navigator.userAgent,stillnessReadFailures:this.stillnessFailures,
      mediaGateRefusals:this.mediaGateRefusals,mediaGateRefusalRun:this.mediaGateRefusalRun,
      samples:this.scanSamples},null,2);
  }

  stop(): void {
    this.generation++;
    this.ready = false; this.recording = false;
    if(this.videoFrameHandle!==null){this.video?.cancelVideoFrameCallback?.(this.videoFrameHandle);this.videoFrameHandle=null;}
    if (this.grabTimer != null) { clearInterval(this.grabTimer); this.grabTimer = null; }
    if (this.headingHandler && typeof window !== "undefined") {
      window.removeEventListener("deviceorientation", this.headingHandler);
      window.removeEventListener("deviceorientationabsolute", this.headingHandler);
    }
    this.headingHandler = null;
    // No listeners, no pose stream: the source is not healthy until start()
    // attaches them again, and the old view can vouch for nothing. The luma
    // canvas goes with it - it is lazy, so the next scan rebuilds it, and a
    // closed editor should not hold a canvas backing store open.
    // The memory of a reading that once stood goes with the witness that made
    // it: a new scan must not inherit one, and there is no view left to keep it.
    this.listening = false; this.stability.clear();
    this.headingStoodAt = null; this.tiltStoodAt = null;
    // The margin belongs to the witness this session had, so it ends with it.
    // Nothing is left to vouch with once `stability` is cleared, so this
    // changes no verdict today; it is here because the alternative is a field
    // that survives its own session and is right only for as long as the next
    // `start()` reaches the path choice - which an early return (a generation
    // change) or the `play()` failure that calls `stop()` and throws does not.
    this.vouchSlopMs = CONTINUITY_SLOP_MS;
    this.lumaCanvas = null; this.stillnessFailures = 0;
    // Counted per camera session, and this ends one. The run of overlap
    // refusals goes with them: a scan that ends mid-refusal must not hand the
    // next one a cue about a lens the next one has not yet been refused over.
    this.mediaGateRefusals = 0; this.mediaGateRefusalRun = 0; this.overlapWaitRun = 0;
    this.lastMediaTime = null; this.lastMediaAdvanceAt = -Infinity; this.presentedFrameId = null; this.lastCapturedFrameId = null; this.imageGate = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    if (this.video) this.video.srcObject = null;
  }
}
