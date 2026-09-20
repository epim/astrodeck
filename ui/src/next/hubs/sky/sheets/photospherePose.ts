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
 * strict rule below stays in force.
 * `slopMs` is the margin the caller's own witness earns (see
 * CONTINUITY_SLOP_MS and `viewVouchesFor`): the driver knows which path
 * produced `view` and this is how it says so, because the answer differs by
 * path and nothing here can tell them apart. Omitted is the default margin, so
 * a caller that has a capture time - or a test with no path at all - is
 * unaffected. It belongs beside `view` rather than in a parameter of its own:
 * it describes the witness that produced that continuity, and the two must
 * never be handed over separately. */
export interface PoseEvidence { view?: ViewContinuity | null; sourceHealthy?: boolean; slopMs?: number }

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
 *  This is the margin for a witness that can stamp a frame with the instant the
 *  CAMERA saw it. A witness that cannot needs more, and how much more is a
 *  property of that witness rather than of the clocks: the interval fallback
 *  observes every 350 ms and stamps the read instant, so it cannot place a
 *  break finer than one of its own intervals, and the caller on that path says
 *  so by passing `slopMs` (issue #48). The cost is paid in the same coin as
 *  above: 350 ms of margin plus one 350 ms interval is about 700 ms of movement
 *  after the last reading that a dead sensor could hide there - about 21
 *  degrees of a 30 deg/s pan - against the 500 ms this margin alone gave it. */
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
 *  cannot exist.
 *  `slopMs` is that margin, defaulting to CONTINUITY_SLOP_MS, which is the
 *  clock-alignment margin and nothing more. A caller whose witness stamps its
 *  observations with the instant it READ them, rather than with the instant the
 *  camera saw them, has to add its own resolution to that - see the constant
 *  and the interval fallback in photosphere.ts (issue #48). Passing a margin
 *  here widens what this vouches for, so it is the caller's to justify: it is
 *  the size of the window in which a movement after the reading would not be
 *  challenged. */
export function viewVouchesFor(readingAt:number,view:ViewContinuity|null|undefined,slopMs:number=CONTINUITY_SLOP_MS):boolean {
  return !!view && view.lastBreak!==null && view.lastBreak.from<=readingAt+slopMs;
}

/** THE FLOOR: the rotation rate, in degrees per second, above which a pair of
 *  gyro samples BREAKS a quiet run. Magnitude over the three axes.
 *
 *  Derived from what a vouched reading may be wrong by, and stated as the
 *  arithmetic rather than asserted. The strictest statement this module makes
 *  about two poses being the same pose is `grabFrame`'s alignment gate at 1.5
 *  degrees. `QUIET_DRIFT_DEG` below takes a third of that, so a run this
 *  witness vouches for has hidden less than a third of the disagreement capture
 *  already refuses. The floor is then set so that rotation AT it is caught
 *  inside a hold: sustained, it reaches the 0.5 degree total in
 *
 *      QUIET_DRIFT_DEG / QUIET_RATE_DEG_S = 0.5 / 0.5 = 1.0 s
 *
 *  which is inside the 1.2 s hold the recorded routes use and inside the 2 s a
 *  reading stands alone for (SENSOR_SILENCE_MS). Anything faster is caught
 *  sooner, and a single pair above the floor breaks the run at once rather than
 *  waiting for the total to accumulate.
 *
 *  WHICH DIRECTION A WRONG GUESS FAILS IN, which is the property this constant
 *  is written for. Too LOW and a real phone's zero-rate output exceeds it on
 *  every pair: the run breaks continuously, the witness vouches for nothing,
 *  and the video is left as the only witness - a refusal. Too HIGH and the
 *  total still binds: at a floor of 5 deg/s a 5 deg/s turn reaches 0.5 degrees
 *  in 0.1 s. So neither error IN THIS CONSTANT vouches for a turn, and that is
 *  deliberate, because the number it would have to be measured against is not
 *  available here. The wrong guess that once did vouch was about the CHANNEL
 *  rather than the threshold - a gyro reporting exact zeros - and it is refused
 *  in `observe` (issue #106). See the note on MOTION_STALE_MS for what is not
 *  established. */
export const QUIET_RATE_DEG_S = 0.5;

/** THE TOTAL: how far a quiet run may have turned, in degrees, measured from
 *  the sample the run opened on. The direct analogue of `VisualStability`'s
 *  anchor frame, and there for the same reason its comment gives: without it
 *  this is only a speed limit, and an arbitrarily slow turn drifts arbitrarily
 *  far while every step stays under the floor.
 *
 *  A third of `grabFrame`'s 1.5 degree alignment gate (see above). The rate
 *  vector is integrated per axis and the MAGNITUDE OF THE SUM is compared, not
 *  the sum of magnitudes: a phone that wobbled away and came back still points
 *  where the reading says, exactly as a view that moved and returned still
 *  matches the anchor frame. It is a small-angle estimate - rotations do not
 *  commute - and at half a degree the error in that approximation is parts per
 *  million of the bound.
 *
 *  THIS, AND NOT THE FLOOR, IS THE NUMBER A REAL PHONE'S BIAS HAS TO BEAT. A
 *  constant zero-rate output of 0.3 deg/s never breaks a pair - it is under
 *  QUIET_RATE_DEG_S - but it reaches this total in 0.5 / 0.3 = 1.67 s, so the
 *  run breaks every 1.67 s or so and a reading older than the last break loses
 *  its vouch. Turned round, the witness holds a reading across a hold of length
 *  H only if the device's bias is under
 *
 *      QUIET_DRIFT_DEG / H
 *
 *  which for issue #63's own 2.5 s scenario is 0.2 deg/s. That is a single
 *  number issue #48's device pass can be asked for directly, and it decides
 *  whether this witness fixes #63 on a real phone or merely refuses more
 *  politely. The direction is still a refusal either way, so it is a question
 *  of usefulness rather than of correctness. */
export const QUIET_DRIFT_DEG = 0.5;

/** THE STALE BOUND: how long after its newest sample the gyro witness still
 *  says anything, in milliseconds. A gap wider than this also breaks the run,
 *  because nothing watched the phone across it.
 *
 *  At the delivery rate Chromium is reported to use for `devicemotion` - about
 *  60 Hz, continuous rather than change-driven, which is the whole reason this
 *  witness can exist - 200 ms is twelve consecutive samples missed, far past
 *  delivery jitter.
 *
 *  What it costs, in the same coin as CONTINUITY_SLOP_MS above: between the
 *  last sample and the moment this goes stale, up to 200 ms of movement passes
 *  unchallenged - about 6 degrees of a 30 deg/s pan. The video's own best path
 *  already accepts about 183 ms there, and its interval fallback about 700 ms,
 *  so this witness is the tighter of the two.
 *
 *  THE DELIVERY RATE IS ALSO AN ALIASING BOUND, and not only a staleness one.
 *  Both tests below read the rate reported AT a sample, so a turn that begins
 *  and ends between two samples is invisible to the floor and contributes
 *  nothing to the total. At 60 Hz that hides at most 0.5 degrees of a 30 deg/s
 *  slew; at 20 Hz it hides 1.5 degrees, which is `grabFrame`'s whole alignment
 *  gate and three times QUIET_DRIFT_DEG. The floor's derivation assumes the
 *  sampling resolves the turn, so the delivery rate has to be measured for that
 *  reason as well as for this constant.
 *
 *  WHAT IS NOT ESTABLISHED WITHOUT A DEVICE, and it is both of the numbers this
 *  constant and the floor are chosen against: the rate at which a real phone
 *  delivers `devicemotion` (Chromium's 60 Hz is a report, and Firefox Android's
 *  rate is unknown here) and the zero-rate output and noise floor of a real
 *  phone's gyro - plus, since issue #106, whether any target browser spells
 *  "no gyro" as a stream of exact zeros rather than as nulls. Those are exactly
 *  the measurements issue #48's device pass exists to record. Until they are
 *  taken, this witness is written so that being wrong about any of them REFUSES
 *  rather than vouches: a browser delivering slower than 200 ms breaks its run
 *  on every pair and so produces no continuity at all (it reads 'turning', not
 *  'stale' - a fresh sample always exists, it is the RUN that never opens); a
 *  gyro noisier than the floor breaks its run on its own noise; and a stream of
 *  exact zeros is not a stream of samples at all (see `observe`). In every case
 *  the video remains the only witness and nothing downstream changes. */
export const MOTION_STALE_MS = 200;

/** THE SECOND WITNESS, for the one view the first cannot see.
 *
 *  `VisualStability` vouches for a reading by watching the PICTURE, and over a
 *  patch of smooth sky there is no picture to watch: it answers 'featureless',
 *  produces no continuity, and a reading delivered after the view went blank is
 *  then held by nothing at all (issue #63). Capture meets the same wall from the
 *  other side - the zenith hold on the recorded arc routes refuses every frame
 *  of its window with no pose at all, because the sensor has gone quiet and the
 *  view cannot vouch for its last reading (issue #76).
 *
 *  The gyro can speak there. `devicemotion` is NOT change-driven the way
 *  `deviceorientation` is: Chromium delivers it continuously whether or not the
 *  phone moves, and `rotationRate` says directly whether the phone is turning.
 *  So a run of samples reporting no rotation since a reading arrived vouches for
 *  that reading on the same terms the video's still run does - a continuity
 *  reaching back across the reading, and not merely a claim about now.
 *
 *  Same shape as `VisualStability`, deliberately, so that `viewVouchesFor` can
 *  read either without knowing which it holds: a run of quiet samples with a
 *  `stillSince`, a `lastBreak` recorded the first time it sees anything and
 *  again whenever the run ends, and a stale answer when the samples stop.
 *
 *  What it is NOT: a pose. It never integrates a heading, it is never a source
 *  of direction, and nothing downstream may read it as one. It answers one
 *  question - has the phone turned since then - and the reading it vouches for
 *  is still the magnetometer's. */
export class MotionStability {
  private at = -Infinity;
  private stillSince: number | null = null;
  private lastBreak: { from: number; to: number } | null = null;
  private drift: [number, number, number] = [0, 0, 0];
  clear(){this.at=-Infinity;this.stillSince=null;this.lastBreak=null;this.drift=[0,0,0];}

  /** One `devicemotion` sample: `at` on the performance clock, `rate` the
   *  event's own `rotationRate` in degrees per second.
   *
   *  A triple that carries NO MEASUREMENT is not a sample at all: this returns
   *  having changed nothing, so it neither opens a run nor advances the instant
   *  the stale bound is measured from. There are two spellings of it and both
   *  are refused here.
   *
   *  The first is the one the spec gives a device with no rate sensor: `rate`
   *  null, or an axis the browser left null. The second is `{0, 0, 0}` - an
   *  EXACT zero on all three axes - and it is the more dangerous, because
   *  reading it as a still phone is the one wrong guess in this witness that
   *  vouched instead of refusing (issue #106). A stuck driver, an emulator or a
   *  WebView that synthesises zeros is then indistinguishable from a phone
   *  holding still, and downstream there is nothing to catch it: the video is
   *  `featureless` by construction wherever this witness is consulted, so a
   *  turning phone with a silent compass - precisely what SENSOR_SILENCE_MS
   *  exists to catch within 2 s - would be handed a false hold, and a false hold
   *  puts a frame into the mosaic at a pose the phone has left.
   *  A real MEMS gyro has a noise floor and does not report an exact zero triple
   *  twice in a row, let alone for a second; a stream that does is synthetic.
   *
   *  WHY NOT A FLAG. The obvious fix is a boolean - has this witness ever seen a
   *  non-zero sample - tested in `continuity`, scoped either to the RUN (cleared
   *  by `broken`) or to the SESSION (cleared only by `clear`). Neither was
   *  chosen, because treating the zero triple as no sample is strictly stronger
   *  than both and simpler than either:
   *    - it satisfies what a flag would (a channel of nothing but zeros never
   *      opens a run - in fact it needs TWO real samples inside the stale bound,
   *      not one);
   *    - per-session would keep vouching for a gyro that WORKED and then died
   *      stuck at zero, and so would per-run, because a stream of zeros produces
   *      no break for a per-run flag to be cleared by. Here `at` simply stops
   *      advancing, so the witness goes stale MOTION_STALE_MS later and the run
   *      it was in the middle of stops vouching. That case is caught by this and
   *      by neither flag;
   *    - it is the rule this method already applies to the null spelling, so
   *      there is one predicate for "no measurement" rather than two.
   *  What it costs, and the cost is real: a device whose noise at rest sits
   *  below one quantisation step reports exact zeros, and there the gyro never
   *  vouches at all and the video is left as the only witness. That is a
   *  refusal, which is the direction this whole witness is written to fail in,
   *  and whether any target browser behaves that way is a question for #48's
   *  device pass, recorded on #106.
   *
   *  A real stream that interleaves the occasional empty event is unharmed
   *  either way: the real samples on each side of one still bound their own
   *  interval exactly as any other pair does, and only a run of them longer than
   *  the stale bound breaks anything. */
  observe(at: number, rate: DeviceMotionEventRotationRate | null | undefined): void {
    if(!Number.isFinite(at)||at<this.at)return;
    const axes=[rate?.alpha,rate?.beta,rate?.gamma];
    if(!axes.every((v):v is number=>typeof v==='number'&&Number.isFinite(v)))return;
    const [a,b,c]=axes as number[];
    if(a===0&&b===0&&c===0)return;
    const previousAt=this.at,gap=at-previousAt;
    this.at=at;
    // Nothing watched the phone before the first sample, or across a gap wider
    // than the stale bound, so the run starts here and covers nothing earlier.
    // The first sample therefore always records a break, which is what makes
    // every continuity this produces one `viewVouchesFor` can actually use.
    if(!Number.isFinite(previousAt)||gap>MOTION_STALE_MS){this.broken(at,at);return;}
    // The turn may have begun anywhere inside the pair, so the break starts at
    // the earlier sample - the same rule, for the same reason, as the video's.
    if(Math.hypot(a,b,c)>QUIET_RATE_DEG_S){this.broken(previousAt,at);return;}
    const dt=gap/1000;
    this.drift=[this.drift[0]+a*dt,this.drift[1]+b*dt,this.drift[2]+c*dt];
    // A total caught here accumulated over an unknown part of the run, so
    // nothing before now is vouched for.
    if(Math.hypot(...this.drift)>QUIET_DRIFT_DEG){this.broken(at,at);return;}
    if(this.stillSince===null)this.stillSince=previousAt;
  }

  /** Close the current quiet run and remember the interval that ended it. */
  private broken(from:number,to:number):void {
    this.stillSince=null;this.drift=[0,0,0];this.lastBreak={from,to};
  }

  /** WHY this witness says what it says, in one word. 'stale' is tested first
   *  for the reason `VisualStability.witness` tests it first: a stream that has
   *  stopped may have missed anything, and the softer answer must never swallow
   *  the harder one. */
  witness(now:number):'quiet'|'turning'|'stale' {
    if(!Number.isFinite(this.at)||now-this.at>MOTION_STALE_MS)return 'stale';
    return this.stillSince===null?'turning':'quiet';
  }

  /** Since when has the phone been un-turned? Null unless a quiet run is under
   *  way on a fresh sample. The break is copied, so a caller holding the answer
   *  cannot edit this witness's record of it. */
  continuity(now:number):ViewContinuity|null {
    if(this.witness(now)!=='quiet'||this.stillSince===null)return null;
    return {stillSince:this.stillSince,lastBreak:this.lastBreak&&{...this.lastBreak}};
  }
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
      // `stillSince` is compared WITHOUT `slopMs`, deliberately, and the cost
      // is real rather than nil. A read-instant witness inflates `stillSince`
      // by the pipeline delay exactly as it inflates `lastBreak.from`, so on a
      // lagged fallback this gate goes false in the region the widened margin
      // now admits a pose in, and a final-pair outlier that the same fixture
      // refutes at zero lag is worn there instead (the exposure is new, because
      // that region used to produce no pose at all).
      // It is left strict because widening it does not merely relax a refusal:
      // it changes WHICH reading is worn. The gate's false branch keeps the
      // jump as the movement it looks like; its true branch hands the pair to
      // the arbitration below, which may return no pose at all. Asserting
      // coverage the witness has not demonstrated therefore risks arbitrating
      // away a genuine brisk final approach - the review 15 P2 failure this
      // whole rule was rewritten to avoid - to buy protection against an
      // outlier that registration's overlap check and the next reading already
      // bound. That trade needs a case that pins the arbitration on a lagged
      // fallback before it is made, and it is outside the margin question
      // issue #48 rules on; it is recorded there rather than guessed here.
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
      // On the caller's own margin: the reading is being worn under a witness
      // the caller chose, and the same path's margin has to decide here as
      // decides in the driver, or a phone could be told its heading stands
      // while every frame of the hold is refused a pose (issue #48).
      if(viewVouchesFor(pose.at,view,evidence.slopMs)){
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
