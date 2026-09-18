import { dot, rotateBasis, type CameraBasis } from './photosphereGeometry';

/** Anchor the gyro-relative stream to one simultaneous north reading. Do not
 * keep injecting magnetometer corrections into the camera's local sky map. */
export class ScanPoseSource {
  private absolute:{basis:CameraBasis;at:number}|null=null;
  private yaw:number|null=null;
  clear(){this.absolute=null;this.yaw=null;}
  get usesRelative(){return this.yaw!==null;}
  accept(basis:CameraBasis,absolute:boolean,at:number):{basis:CameraBasis;changedSource:boolean}|null {
    if(absolute){
      this.absolute={basis,at};
      return this.yaw===null?{basis,changedSource:false}:null;
    }
    let changedSource=false;
    if(this.yaw===null){
      if(!this.absolute||Math.abs(at-this.absolute.at)>100)return null;
      let cross=0,cos=0;
      for(const axis of ['right','up','forward'] as const){const a=this.absolute.basis[axis],r=basis[axis];cross+=a[0]*r[1]-a[1]*r[0];cos+=a[0]*r[0]+a[1]*r[1];}
      this.yaw=Math.atan2(cross,cos)*180/Math.PI;changedSource=true;
    }
    return {basis:rotateBasis(basis,this.yaw),changedSource};
  }
}

export interface TimedPose { at: number; basis: CameraBasis; screenAngle: number }
export function poseSeparation(a:CameraBasis,b:CameraBasis):number {
  return Math.max(...(['right','up','forward'] as const).map(axis=>
    Math.acos(Math.max(-1,Math.min(1,dot(a[axis],b[axis]))))*180/Math.PI));
}

/** What the caller knows about the sensor stream from OUTSIDE the stream.
 * Orientation events are change-driven: Chromium emits nothing below 0.1
 * degree, so a still phone goes silent and the samples alone cannot tell a
 * steady view from a lost sensor. These are the two independent answers -
 * `visuallyStable` from the video, `sourceHealthy` from the page lifecycle.
 * `visuallyStable` is a TRI-STATE and `null` is not `false`: the video may
 * have stopped, or the frame may hold nothing this method can judge motion by.
 * Anything short of `true` on both leaves the strict rule in force. */
export interface PoseEvidence { visuallyStable?: boolean | null; sourceHealthy?: boolean }

/** How long the stream must be silent before silence counts as a settle
 * rather than a lull between two orientation events. There is deliberately no
 * upper bound on that silence: under evidence the VIDEO is the freshness
 * guard, and it re-earns that verdict every frame.
 *
 * What that verdict actually claims, stated as the module that produces it
 * implements it (`photosphereStability.ts`): the view has not drifted from the
 * ANCHOR frame - the frame this settle began on - by more than a fixed
 * fraction of its own luminance, and has not jumped between any two
 * consecutive frames either. It is a bound on TOTAL drift since the settle, not
 * a speed limit, which is why an arbitrarily slow pan cannot creep past it.
 * And it reports `null`, not `true`, for a view it cannot judge - a stopped
 * stream, a blank wall, a frame of smooth sky - so silence over an
 * unjudgeable view falls back to the strict rule below. */
const SILENT_SETTLE_MS = 500;

/** A single magnetometer outlier delivered as the LAST event before the phone
 * goes quiet would otherwise become the settled pose and be worn by every
 * frame of the hold. Two samples this close together cannot be a real slew, so
 * disagreeing by this much is a bad reading, not a movement. Wide enough that
 * a normal decelerating approach (degrees per 100 ms, not per 50) is untouched. */
const JITTER_GAP_MS = 150, JITTER_SEPARATION_DEG = 8;

/** Match camera capture times to sensor times, never to a newer phone pose.
 * When the browser omits captureTime, require a settled orientation covering
 * 500 ms. This avoids smearing a moving view with an unknown camera delay. */
export class CameraPoseHistory {
  private samples:TimedPose[]=[];
  private orientationSince=0;
  clear(){this.samples=[];this.orientationSince=0;}
  add(sample:TimedPose){
    const last=this.samples.at(-1);
    if(last && sample.at<last.at)return;
    if(!last || last.screenAngle!==sample.screenAngle){this.samples=[];this.orientationSince=sample.at;}
    this.samples.push(sample);
    this.samples=this.samples.filter(p=>sample.at-p.at<=2000).slice(-240);
  }
  forFrame(now:number,captureTime?:number,evidence?:PoseEvidence):CameraBasis|null {
    const latest=this.samples.at(-1);
    // Declared loss - listener gone, page hidden, camera track ended - is not
    // stillness, and the correction for stillness must not make it look valid.
    if(evidence?.sourceHealthy===false)return null;
    // Silence is trusted only when the video says the view is steady AND the
    // page says the stream is alive. Neither is a timeout: without both, the
    // strict freshness and gap rules below still decide.
    if(latest && evidence?.visuallyStable===true && evidence.sourceHealthy===true){
      const previous=this.samples.at(-2);
      if(previous && latest.at-previous.at<JITTER_GAP_MS
        && poseSeparation(latest.basis,previous.basis)>JITTER_SEPARATION_DEG)return null;
      const reference=captureTime===undefined?now:captureTime;
      const silence=reference-latest.at;
      if((captureTime===undefined||Number.isFinite(captureTime)) && reference<=now
        && silence>=SILENT_SETTLE_MS)return latest.basis;
    }
    if(!latest || now-latest.at>250 || now<latest.at || now-this.orientationSince<500)return null;
    if(captureTime!==undefined){
      if(!Number.isFinite(captureTime)||captureTime>now||now-captureTime>1000||captureTime<this.orientationSince)return null;
      const nearest=this.samples.reduce((a,b)=>Math.abs(a.at-captureTime)<Math.abs(b.at-captureTime)?a:b);
      return Math.abs(nearest.at-captureTime)<=40 ? nearest.basis : null;
    }
    const start=now-500;
    const anchor=[...this.samples].reverse().find(p=>p.at<=start);
    if(!anchor || start-anchor.at>250)return null;
    const window=this.samples.filter(p=>p.at>=anchor.at);
    if(window.some((p,i)=>poseSeparation(latest.basis,p.basis)>1.5 || (i>0&&p.at-window[i-1].at>250)))return null;
    return latest.basis;
  }
}
