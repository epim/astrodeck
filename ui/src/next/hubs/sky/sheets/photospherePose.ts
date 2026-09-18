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
 * Anything short of `true` on both leaves the strict rule in force. */
export interface PoseEvidence { visuallyStable?: boolean; sourceHealthy?: boolean }

/** How long the stream must be silent before silence counts as a settle
 * rather than a lull between two orientation events. There is deliberately no
 * upper bound on that silence: under evidence the VIDEO is the freshness
 * guard, and it re-earns that verdict every frame. While the view is
 * continuously stable the phone has not turned, so the last orientation event
 * is still the pose - at 2 seconds or at half an hour on a tripod. */
const SILENT_SETTLE_MS = 500;

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
