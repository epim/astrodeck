import { dot, rotateBasis, type CameraBasis } from './photosphereGeometry';
import { STALE_FRAME_MS, type ViewContinuity } from './photosphereStability';

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
 * `view` from the video, `sourceHealthy` from the page lifecycle.
 * `view` is the video's CONTINUITY, not a verdict on the present moment:
 * since when the view has been unchanged, and the last interval in which it
 * moved, could not be judged, or was not observed. That is what lets a single
 * READING be vouched for, rather than the view being called still now - a
 * steady view that began after a movement describes a direction the phone has
 * left. `null` is the video's "cannot say" - a stopped stream, an unsettled
 * run, a frame with nothing in it to judge motion by - and it is not `false`.
 * Without a healthy source and a continuity reaching back to the reading, the
 * strict rule below stays in force. */
export interface PoseEvidence { view?: ViewContinuity | null; sourceHealthy?: boolean }

/** How long the stream must be silent before silence counts as a settle
 * rather than a lull between two orientation events. There is deliberately no
 * upper bound on that silence: under evidence the VIDEO is the freshness
 * guard, and it re-earns that verdict every frame.
 *
 * What the evidence has to claim, stated as the module that produces it
 * implements it (`photosphereStability.ts`): the view has been unchanged since
 * BEFORE the reading being worn arrived - a still run whose last break began
 * no later than that reading, give or take CONTINUITY_SLOP_MS of clock
 * alignment - and not merely that the view is unchanged now.
 * Unchanged there is measured against the ANCHOR frame the run began on, not
 * only between consecutive frames, so an arbitrarily slow pan cannot creep
 * past it. A view the witness cannot judge - a stopped stream, a blank wall, a
 * frame of smooth sky - yields no continuity at all, so silence over an
 * unjudgeable view falls back to the strict rule below. */
const SILENT_SETTLE_MS = 500;

/** A single magnetometer outlier delivered as the LAST event before the phone
 * goes quiet would otherwise become the settled pose and be worn by every frame
 * of the hold. Two readings this close together disagreeing by this much cannot
 * both describe the phone, so the pair is suspect - and the video is asked.
 * What the video can say is exactly one thing: whether the phone MOVED across
 * the pair. A still run that already covered that window says it did not, and
 * that is all "refuted" means here. It does NOT say which of the two readings
 * is the bad one. Treating it as if it did is how the older reading came to be
 * worn even when the older one was the spike and the newer one was the sensor's
 * own correction back to the truth (issue #47).
 * So the history arbitrates, and the reading BEFORE the pair is the referee:
 * whichever member of the pair still agrees with it, within this same
 * separation, is the phone's direction - the closer one if both do. Where there
 * is no reading before the pair, or neither member agrees with it, nothing here
 * knows which reading is bad, and the frame gets no pose at all.
 * That last outcome is not the old flat rate rejection. It is reached only once
 * the video AND the history have both spoken and still cannot name the bad
 * reading, and the next reading of any kind clears it, because the pair moves
 * on. The flat rejection disqualified the final pair for the whole hold and
 * blocked a genuine 100 deg/s approach forever (review 15, P2).
 * A run that does NOT cover the pair cannot say the phone held still across it,
 * so the jump is taken as the movement it looks like and kept.
 * The limit of the refutation: the video resolves motion no finer than its own
 * frame interval, so a bad reading arriving inside the last MOVING pair of an
 * approach is accepted as a movement. Registration's overlap check and the next
 * reading are the guards there.
 * This rule is reached in ordinary use, not only by bad sensors: any final step
 * of more than JITTER_SEPARATION_DEG inside JITTER_GAP_MS enters it, and a brisk
 * final approach produces exactly that (the DOM harness's 10 degrees in 100 ms
 * does). That is why the outcome here had to become refute-or-keep, arbitrated,
 * rather than refusal. */
const JITTER_GAP_MS = 150, JITTER_SEPARATION_DEG = 8;

/** Frame times and sensor times are on the same clock but not aligned to the
 *  millisecond: a frame without a capture time is stamped when the callback
 *  ran, some tens of ms after the camera saw the scene, and an orientation
 *  event carries its own latency. A break that BEGAN within this margin after
 *  a reading is the tail of the approach that produced the reading, not a
 *  movement after it. The cost, measured: a movement beginning inside this
 *  margin with a sensor that has already died is not caught, and it is seen
 *  only at the NEXT frame, so about 183 ms of it can pass unchallenged after
 *  the last reading - 150 ms of margin plus one 33 ms frame at 30 fps, which is
 *  two frame intervals of continued movement vouched for and three refused.
 *  That is about 5 degrees of a 30 deg/s pan and about 35 of a 200 deg/s flick.
 *  On the interval fallback a frame is 350 ms, so the window there is one tick. */
export const CONTINUITY_SLOP_MS = 150;

/** Does the video vouch that the view has not changed since a reading taken
 *  at `readingAt`? True only with a settled continuity whose last break began
 *  no later than the reading (plus the alignment margin). A break recorded at
 *  a single instant - an unobserved gap, an unjudgeable frame, a drift caught
 *  late - has `from === to` at its END, so a reading more than the margin
 *  older than that instant is never vouched for: nothing watched the view
 *  between the two.
 *  A continuity with NO break at all vouches for nothing either, and that is
 *  deliberate: it would vouch for a reading of any age whatsoever, and no
 *  producer emits one. `VisualStability` records a break the first time it sees
 *  a frame - there is nothing behind it to reach back across - so every real
 *  continuity carries one, and a fixture without one describes a witness that
 *  cannot exist. */
export function viewVouchesFor(readingAt:number,view:ViewContinuity|null|undefined):boolean {
  return !!view && view.lastBreak!==null && view.lastBreak.from<=readingAt+CONTINUITY_SLOP_MS;
}

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
    // Silence is trusted only when the video vouches for the READING being
    // worn - its still run reaching back to that reading - AND the page says
    // the stream is alive. Neither is a timeout: without both, the strict
    // freshness and gap rules below still decide.
    if(latest && evidence?.sourceHealthy===true && evidence.view){
      const view=evidence.view;
      let pose=latest;
      const previous=this.samples.at(-2);
      // A jump between the last two readings, closer together than any real
      // slew. If the still run already covered that window the phone did not
      // move across it, so one of the two readings is wrong - and the video
      // cannot say which. The reading before the pair decides: the member that
      // still agrees with it is the pose, and if neither does (or there is no
      // such reading) this frame gets none. If the run does NOT cover the
      // window, the video cannot say the phone held still, so the jump is
      // treated as the movement it looks like.
      if(previous && latest.at-previous.at<JITTER_GAP_MS
        && poseSeparation(latest.basis,previous.basis)>JITTER_SEPARATION_DEG
        && view.stillSince<=previous.at){
        const before=this.samples.at(-3);
        if(!before)return null;
        const toLatest=poseSeparation(latest.basis,before.basis);
        const toPrevious=poseSeparation(previous.basis,before.basis);
        const latestAgrees=toLatest<=JITTER_SEPARATION_DEG,previousAgrees=toPrevious<=JITTER_SEPARATION_DEG;
        if(!latestAgrees&&!previousAgrees)return null;
        pose=!previousAgrees||(latestAgrees&&toLatest<toPrevious)?latest:previous;
      }
      if(viewVouchesFor(pose.at,view)){
        const reference=captureTime===undefined?now:captureTime;
        const silence=reference-pose.at;
        if((captureTime===undefined||Number.isFinite(captureTime)) && reference<=now
          && silence>=SILENT_SETTLE_MS)return pose.basis;
      }
    }
    if(!latest || now-latest.at>250 || now<latest.at || now-this.orientationSince<500)return null;
    if(captureTime!==undefined){
      // How old a capture time may be and still describe this frame is one
      // constant, not two: photosphere.ts validates the same field against
      // STALE_FRAME_MS before stamping the stillness sample with it.
      if(!Number.isFinite(captureTime)||captureTime>now||now-captureTime>STALE_FRAME_MS||captureTime<this.orientationSince)return null;
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
