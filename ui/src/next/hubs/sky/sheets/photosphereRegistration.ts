import {rotateBasis,type CameraBasis,type SkyPanorama,type OverlapCheck,cameraLens} from './photosphereGeometry';

export interface Registration {basis:CameraBasis;overlap:OverlapCheck;adjusted:boolean;evaluations:number}

/** How far the fit may turn the frame, per axis, in degrees: yaw about the
 *  world's up axis, pitch about the camera's right, roll about its forward -
 *  the three axes `rotateBasis` turns about, in that order.
 *
 *  Exported because the DRIVER derives its acceptance window for a CARRIED
 *  correction from these numbers when the lens has been calibrated
 *  (`photosphere.ts`, `carriedCorrectionMax`). Two layers holding separate
 *  numbers for how large a fit may legitimately be is the hazard issue #70 was
 *  filed about, pointing the other way: widen this box on its own and the fit
 *  spends its budget on corrections the driver will throw away. Change these
 *  and the driver's window moves with them. */
export const SEARCH_LIMITS = {yaw:6,pitch:4,roll:2} as const;

/** The largest pose change the box above can express, in degrees: the length of
 *  its rotation vector, `hypot(6, 4, 2)` = 7.4833, which is the composite angle
 *  of three small rotations about three PERPENDICULAR axes. Perpendicular is
 *  what the three parameters are for - an upright camera looking at the horizon
 *  has world-up, its own right and its own forward mutually orthogonal - and
 *  `poseSeparation` of such a composite is at most its angle.
 *
 *  Measured against the shipped `rotateBasis` and `poseSeparation`, over a grid
 *  of capturable attitudes (azimuth every 5 degrees, altitude -10 to 90 every
 *  1, roll every 5; 523584 attitudes, all eight sign corners of the box at
 *  each):
 *
 *    upright camera on the horizon   7.2070   (the attitude this number is for)
 *    minimum over all attitudes      7.0175
 *    median                          9.2178
 *    maximum                        10.4713   (azimuth 235, altitude 27, roll 270)
 *
 *  So the box can express MORE than this at attitudes where the world's up axis
 *  falls near one of the camera's own - the yaw then partly duplicates the roll
 *  instead of being perpendicular to it - and 17.9 per cent of the grid exceeds
 *  10 degrees. This constant is deliberately the orthogonal figure and not that
 *  maximum: a carried correction of 10.5 degrees is at the scorer's own overlay
 *  gate, which is exactly the coincidence #70 was filed about, and a bound has
 *  to be the size of a legitimate correction rather than the size of the worst
 *  thing the parameterisation can name. The cost is stated rather than hidden:
 *  between 7.4833 and 10.4713 the driver refuses a single fit this search could
 *  have produced, at attitudes with a large roll. That band is a refusal the
 *  user can see (`overlap-wait`, and the cue behind it), never a silent wrong
 *  overlay. */
export const SEARCH_CEILING_DEG = Math.hypot(SEARCH_LIMITS.yaw,SEARCH_LIMITS.pitch,SEARCH_LIMITS.roll);

/** How coarsely the first pass samples the box above, and the ladder the
 *  refinement then walks down from it: `SEARCH_STEP_DEG / 2`, `/ 4`, `/ 8`, so
 *  the finest step is an eighth of the coarse one and the refinement can always
 *  reach any point between two coarse samples. Written as one number and its
 *  halves rather than four literals, for the reason `SEARCH_LIMITS` is a
 *  constant: a resolution beside a derived extent is the next thing to drift.
 *
 *  The one thing it does NOT derive, and the caller's job to keep true: each
 *  extent in `SEARCH_LIMITS` must be a whole multiple of this, or the coarse
 *  grid stops short of its own edge and the corner the ceiling above is
 *  measured at is never visited. 6, 4 and 2 against a step of 2 all are. */
const SEARCH_STEP_DEG = 2;
/** Direct visual registration against the fixed panorama. Only small rigid
 * rotations are fitted; the lens and existing image are never stretched to
 * manufacture a match. Featureless/insufficient overlap retains sensor pose.
 * Work is bounded, and runs only for a held camera, not at display frequency. */
export function registerFrame(panorama:SkyPanorama,data:Uint8ClampedArray,width:number,height:number,basis:CameraBasis,lens=cameraLens(width,height)):Registration {
  const initial=panorama.checkOverlap(data,width,height,basis,lens);
  if(initial.result==='unknown' || initial.featureCorrelation==null || initial.featureCorrelation>.85)
    return {basis,overlap:initial,adjusted:false,evaluations:1};
  let evaluations=1;
  const score=(check:OverlapCheck)=>check.result==='unknown'||check.featureCorrelation==null||check.samples<initial.samples*.8
    ? -Infinity : .8*check.featureCorrelation+.2*(check.correlation??0);
  let best={basis,overlap:initial,angles:[0,0,0],score:score(initial)};
  const visit=(angles:number[])=>{
    if(Math.abs(angles[0])>SEARCH_LIMITS.yaw||Math.abs(angles[1])>SEARCH_LIMITS.pitch||Math.abs(angles[2])>SEARCH_LIMITS.roll)return;
    const candidate=rotateBasis(basis,...angles as [number,number,number]);
    const overlap=panorama.checkOverlap(data,width,height,candidate,lens);evaluations++;
    const value=score(overlap);
    if(value>best.score)best={basis:candidate,overlap,angles,score:value};
  };
  for(let yaw=-SEARCH_LIMITS.yaw;yaw<=SEARCH_LIMITS.yaw;yaw+=SEARCH_STEP_DEG)for(let pitch=-SEARCH_LIMITS.pitch;pitch<=SEARCH_LIMITS.pitch;pitch+=SEARCH_STEP_DEG)visit([yaw,pitch,0]);
  for(const step of [SEARCH_STEP_DEG/2,SEARCH_STEP_DEG/4,SEARCH_STEP_DEG/8])for(let pass=0;pass<2;pass++)for(let axis=0;axis<3;axis++){
    const centre=best.angles;
    for(const sign of [-1,1])visit(centre.map((n,i)=>n+(i===axis?sign*step:0)));
  }
  // Small accidental improvements in repeated or moving textures are not a
  // reason to move the map. Require agreement in both edges and brightness.
  if(best.overlap.result!=='agree'||(best.overlap.featureCorrelation??0)<.55||(best.overlap.correlation??0)<.65||best.score<score(initial)+.08)
    return {basis,overlap:initial,adjusted:false,evaluations};
  return {basis:best.basis,overlap:best.overlap,adjusted:true,evaluations};
}
