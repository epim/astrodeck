// Models Chromium's own deviceorientation change threshold (spec 2.1): an
// event fires only when alpha, beta or gamma moves by at least 0.1 degree.
export interface OrientationAngles { alpha:number; beta:number; gamma:number }
export const CHANGE_THRESHOLD_DEG = 0.1;

/** Would the browser fire a deviceorientation event for this change? A null
 * `prev` means nothing has been sent yet, so the first reading always fires. */
export function orientationChanged(prev:OrientationAngles|null,next:OrientationAngles):boolean {
  if(!prev)return true;
  return Math.abs(next.alpha-prev.alpha)>=CHANGE_THRESHOLD_DEG
    || Math.abs(next.beta-prev.beta)>=CHANGE_THRESHOLD_DEG
    || Math.abs(next.gamma-prev.gamma)>=CHANGE_THRESHOLD_DEG;
}
