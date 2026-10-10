// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Replay a recorded case through a real scanner.
//
//   node --import tsx src/next/hubs/sky/sheets/__sim__/replay.ts <abs case dir> [--scanner legacy|pano]
//
// The point of the whole exercise is that nothing here is a model of the
// scanner. The scanner is imported from production, started in a browser
// (harness.ts) whose clock, camera and sensor are the case's own recording, and
// driven through the `ScannerUnderTest` seam (scannerUnderTest.ts) that an
// adapter per scanner implements over its public surface: `legacy` is the
// shipped `PhotosphereSweep`. What it writes into `result/` is therefore a
// measurement of the shipped code, and the scorer that reads those files has
// never seen this directory.
//
// The driver reads `input/observations.jsonl`, `input/actions.jsonl`,
// `input/scanner.json` and `input/frames/*.png`, and nothing else - not even
// `manifest.json`, whose camera block would only repeat the width and height
// every frame observation already carries. The reference data beside them
// belongs to the scorer alone: a driver able to see it could reach the right
// answer for the wrong reason, and no test of the result could tell.
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { PANO_H, PANO_W, type ScannerOptions, type Stats } from '../pano/types';
import { decodePng, encodePng, type Raster } from './png';
import { createHarness, referenceLoopMs, type OrientationEventType } from './harness';
import type { LiveSnapshot, ScannerUnderTest } from './scannerUnderTest';
import { SCANNERS } from './scanners';

/** What the frame callback cost, per scanner other than `legacy` (13.7).
 *  `callback` is wall time in ms around each `deliverFrame`; `callback_ref` is
 *  the same samples in units of `ref_unit_ms`, the machine's own time for the
 *  pinned reference loop, so two machines can be compared. */
export interface CostSummary { callback: Stats; callback_ref: Stats; ref_unit_ms: number }

export interface Summary {
  frames_delivered: number;
  events_delivered: number;
  frames_accepted: number;
  /** Null for a scanner that does not tile the dome into cells (13.7). */
  cells_total: number | null;
  cells_covered: number | null;
  elapsed_ms: number;
  app_commit: string | null;
  /** Written for every scanner except `legacy`, whose summary keeps exactly
   *  the keys it always had. */
  cost_ms?: CostSummary;
}

/** The summary of a `legacy` replay, which always counts its cells. */
export interface LegacySummary extends Summary {
  cells_total: number;
  cells_covered: number;
}

export interface FrameObservation {
  kind: 'frame';
  frame_id: string;
  /** Null when the frame's metadata carried no `captureTime` (13.1). */
  t_capture_ms: number | null;
  t_present_ms: number;
  width: number;
  height: number;
  file: string;
}
export interface OrientationObservation {
  kind: 'orientation';
  /** Absent in the legacy files, which carry only `deviceorientationabsolute`. */
  event?: OrientationEventType;
  t_event_ms: number;
  t_receive_ms: number;
  /** Null for a browser that blocks the sensor but still fires the event. */
  alpha: number | null;
  beta: number | null;
  gamma: number | null;
  absolute: boolean;
}
/** The second witness (issue #105). Emitted on every tick of its grid rather
 *  than on change, which is the whole difference from `orientation`: a phone
 *  holding still keeps producing these, and that is what lets a reading be
 *  vouched for during a hold. `rate` is null for an event with no rotation
 *  rate, and a component is null where the browser delivered null. */
export interface MotionObservation {
  kind: 'motion';
  t_event_ms: number;
  t_receive_ms: number;
  rate: { alpha: number | null; beta: number | null; gamma: number | null } | null;
}
/** The screen turned to `angle` degrees. Optional in a case (13.1). */
export interface ScreenObservation {
  kind: 'screen';
  t_event_ms: number;
  t_receive_ms: number;
  angle: number;
}
export type Observation = FrameObservation | OrientationObservation | MotionObservation | ScreenObservation;

/** Decoded frames held back from the garbage collector. Sequential delivery
 *  reads each frame once, so this is a guard against a case that revisits one,
 *  not a speed-up - and it is small because a 480 x 640 RGBA raster is 1.2 MB. */
const FRAME_CACHE = 8;

/** The scanner a replay drives when none is named. */
const DEFAULT_SCANNER = 'legacy';

function readJsonl<T>(path: string): T[] {
  return readFileSync(path, 'utf8').split('\n').filter(line => line.trim().length > 0).map(line => JSON.parse(line) as T);
}

function writeJson(path: string, value: unknown): void {
  writeFileSync(path, JSON.stringify(value) + '\n', 'utf8');
}

function writeJsonl(path: string, rows: unknown[]): void {
  writeFileSync(path, rows.map(row => JSON.stringify(row)).join('\n') + (rows.length ? '\n' : ''), 'utf8');
}

/** Delivery time: when the page was handed the thing, not when the world
 *  produced it. A frame is delivered when it is presented and a reading when it
 *  is received; the earlier stamps each carries are the scanner's to reason
 *  about, and it does. */
function deliveredAt(item: Observation): number {
  return item.kind === 'frame' ? item.t_present_ms : item.t_receive_ms;
}

/** Merge the observations into the order the page would have seen them.
 *
 *  A tie goes to the reading. A browser drains its task queue - where a
 *  `deviceorientationabsolute` event lands - before it runs the rendering
 *  steps, and `requestVideoFrameCallback` runs with the rendering steps, so a
 *  reading and a frame stamped with the same delivery millisecond arrive
 *  reading first. Delivering the frame first instead hands `forFrame` a pose
 *  history one sample short of the one the browser would have had, and on the
 *  recorded cases that changes the pose worn by two frames apiece. Equal kinds
 *  keep file order, so the merge is stable and a replay repeats exactly. */
export function mergeObservations(observations: Observation[]): Observation[] {
  // Readings before frames, and the reading kinds keep file order between
  // themselves. A browser drains its task queue - where an orientation, a
  // `devicemotion` and a screen `change` all land - before the rendering steps,
  // so every one of them outranks a frame stamped at the same millisecond.
  const rank = (item: Observation) => (item.kind === 'frame' ? 1 : 0);
  return observations
    .map((item, index) => ({ item, index }))
    .sort((a, b) => deliveredAt(a.item) - deliveredAt(b.item)
      || rank(a.item) - rank(b.item)
      || a.index - b.index)
    .map(entry => entry.item);
}

/** The options of `input/scanner.json` (13.6), with the defaults of an absent
 *  file: the focal prior as it stands, a scanner that aligns, no declination.
 *  Every field is optional; a field that is present must be of its type, and a
 *  file that is not an object or not JSON is an error rather than the defaults,
 *  because a case that declares an option and silently gets another is a
 *  measurement of a scanner nobody configured. An error names the field, never
 *  its value. Unknown keys are ignored: the file is the case writer's, and the
 *  keys this driver does not know are not its to refuse. */
export function readScannerOptions(inputDir: string): ScannerOptions & { declinationDeg: number | null } {
  const options = { focalPriorScale: 1, sensorOnly: false, declinationDeg: null as number | null };
  const path = join(inputDir, 'scanner.json');
  if (!existsSync(path)) return options;
  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(path, 'utf8'));
  } catch (e) {
    throw new Error(`${path} is not valid JSON: ${(e as Error).message}`);
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed))
    throw new Error(`${path} must hold a JSON object`);
  const given = parsed as Record<string, unknown>;
  if (given.focal_prior_scale !== undefined && given.focal_prior_scale !== null) {
    const scale = given.focal_prior_scale;
    if (typeof scale !== 'number' || !Number.isFinite(scale) || scale <= 0)
      throw new Error(`${path}: focal_prior_scale must be a positive finite number`);
    options.focalPriorScale = scale;
  }
  if (given.sensor_only !== undefined && given.sensor_only !== null) {
    if (typeof given.sensor_only !== 'boolean') throw new Error(`${path}: sensor_only must be true or false`);
    options.sensorOnly = given.sensor_only;
  }
  if (given.declination_deg !== undefined && given.declination_deg !== null) {
    const declination = given.declination_deg;
    if (typeof declination !== 'number' || !Number.isFinite(declination))
      throw new Error(`${path}: declination_deg must be a finite number or null`);
    options.declinationDeg = declination;
  }
  return options;
}

/** n, p50, p95 and max of `values`, nearest-rank: the p-th percentile is the
 *  value at ordinal rank ceil(p x n / 100). Nothing is interpolated, so every
 *  figure is a sample that happened. An empty list has no percentiles. */
export function statsOf(values: readonly number[]): Stats {
  if (values.length === 0) return { n: 0, p50: null, p95: null, max: null };
  const sorted = [...values].sort((a, b) => a - b);
  const rank = (percent: number) => sorted[Math.max(1, Math.ceil((percent * sorted.length) / 100)) - 1];
  return { n: sorted.length, p50: rank(50), p95: rank(95), max: sorted[sorted.length - 1] };
}

/** The commit the scanner was replayed at, with `-dirty` appended when the
 *  working tree carries uncommitted changes. `null` rather than a guess when
 *  git cannot answer: the scorer falls back to the manifest, and a wrong
 *  commit on a score is worse than no commit.
 *
 *  The suffix is the point of the function. A replay is a measurement of the
 *  code that ran, and a bare commit hash on a score taken from an edited tree
 *  names code that was never replayed - which is the one error a reader has no
 *  way to catch, because the hash resolves and the diff is gone. A dirty tree
 *  is normal during development; silently calling it by the last commit's name
 *  is not. `--porcelain` is empty exactly when nothing is modified, staged or
 *  untracked. */
function appCommit(): string | null {
  const cwd = dirname(fileURLToPath(import.meta.url));
  try {
    const head = execFileSync('git', ['rev-parse', 'HEAD'], { cwd, encoding: 'utf8' }).trim();
    if (!head) return null;
    const status = execFileSync('git', ['status', '--porcelain'], { cwd, encoding: 'utf8' });
    return status.trim().length > 0 ? `${head}-dirty` : head;
  } catch {
    return null;
  }
}

/** Replay `caseDir` through the scanner `o.scanner` names in the `SCANNERS`
 *  registry, `legacy` when it names none, and write the result files (13.7)
 *  into `<caseDir>/result/`. With no options it is exactly the replay it has
 *  always been. An unknown scanner name is an error before anything is read or
 *  written.
 *
 *  Three overloads stand for the one signature `(caseDir, o?: { scanner?: string
 *  }): Promise<Summary>`: a call with no options is the legacy replay, whose
 *  cell counts are always numbers (and a caller that reads `cells_covered` as
 *  one compiles), and a call with options, or with an argument that may be
 *  undefined, gets the `Summary` whose counts are null for a scanner with no
 *  cells. */
export function replayCase(caseDir: string, o: { scanner?: string }): Promise<Summary>;
export function replayCase(caseDir: string, o: { scanner?: string } | undefined): Promise<Summary>;
export function replayCase(caseDir: string, o?: undefined): Promise<LegacySummary>;
export async function replayCase(caseDir: string, o: { scanner?: string } = {}): Promise<Summary> {
  const name = o.scanner ?? DEFAULT_SCANNER;
  // An own-property test, not `name in SCANNERS`: `constructor` and `toString`
  // are not scanners, and an object's prototype must not answer for the registry.
  if (!Object.prototype.hasOwnProperty.call(SCANNERS, name))
    throw new Error(`unknown scanner "${name}": the registered scanners are ${Object.keys(SCANNERS).join(', ')}`);
  const load = SCANNERS[name];

  const root = resolve(caseDir);
  const input = join(root, 'input');
  const observations = readJsonl<Observation>(join(input, 'observations.jsonl'));
  const actions = readJsonl<{ t_ms: number; action: string }>(join(input, 'actions.jsonl'));
  const options = readScannerOptions(input);

  const beginAt = actions.find(a => a.action === 'begin')?.t_ms ?? 0;
  const finishAt = actions.find(a => a.action === 'finish')?.t_ms ?? Infinity;
  const timeline = mergeObservations(observations);

  // The video's size comes from the first frame the case delivers, not from
  // the manifest. Both say the same thing, and taking it from the observation
  // stream is what makes the driver's independence from the manifest
  // structural rather than a promise: there is no manifest read left to drift.
  const firstFrame = timeline.find((item): item is FrameObservation => item.kind === 'frame');
  if (!firstFrame)
    throw new Error(`${root} delivers no frames: there is nothing to replay`);
  const harness = createHarness({ videoWidth: firstFrame.width, videoHeight: firstFrame.height });
  let scanner: ScannerUnderTest | null = null;
  let stopped = false;
  const stopScanner = () => {
    if (!scanner || stopped) return;
    stopped = true;
    scanner.stop();
  };
  // Whatever happens below, the globals this harness replaced go back. A
  // replay that throws must not leave a frozen `Date.now` behind for the next
  // thing in the process to trip over.
  try {
    const factory = await load();
    scanner = factory.create(options);
    await scanner.start(harness.video, harness.canvas);

    const cache = new Map<string, Raster>();
    const loadFrame = (file: string): Raster => {
      const hit = cache.get(file);
      if (hit) { cache.delete(file); cache.set(file, hit); return hit; }
      // The rule that the driver reads only input/ is enforced here, at the one
      // point a case file could defeat it: `file` comes out of the case's own
      // observations, so a path with `..` in it would walk the driver into the
      // reference data the scorer owns.
      const path = resolve(input, file);
      if (!path.startsWith(input + sep))
        throw new Error(`frame path "${file}" leaves the case's input directory`);
      const decoded = decodePng(readFileSync(path));
      cache.set(file, decoded);
      if (cache.size > FRAME_CACHE) cache.delete(cache.keys().next().value as string);
      return decoded;
    };

    // Emptied before the replay, not after it: a run that throws part way must
    // not leave an older run's files behind for the scorer to read as this one's.
    // It takes a previous scorer's `scores.json` and `report.html` with it, and
    // that is intended too - they describe a replay that no longer exists here,
    // and a stale verdict sitting beside fresh evidence is worse than none.
    const out = join(root, 'result');
    rmSync(out, { recursive: true, force: true });
    mkdirSync(out, { recursive: true });

    // The cost of the frame callback is measured for every scanner and written
    // for every one but `legacy`, whose summary keeps the keys it has always
    // had. The exemption is the name `legacy`, not whichever scanner the default
    // happens to be: when the default moves, the scanner that moves into it must
    // keep its cost block. The reference loop runs once, before the first frame,
    // on the same thread and the same JIT the callbacks will run on.
    const measured = name !== 'legacy';
    const refUnitMs = measured ? referenceLoopMs() : 0;
    const callbackMs: number[] = [];

    const events: unknown[] = [];
    const live: LiveSnapshot[] = [];
    let framesDelivered = 0, eventsDelivered = 0, elapsed = 0, begun = false;
    // `begin()` refuses until the scanner can begin, and the recorded begin
    // action is at the very start of the night, before the first reading has
    // arrived. Dropping it there would leave the whole replay unrecorded behind a
    // silent no-op, so it is held and re-offered at the first moment it can be
    // accepted - which is the same gate the Start button is behind in the UI.
    let lastOffered: number | null = null;
    const tryBegin = (at: number) => {
      if (!scanner || begun || at < beginAt || !scanner.canBegin) return;
      lastOffered = at;
      scanner.begin();
      // `begin()` returns void and refuses when the scanner cannot begin, so
      // calling it proves nothing. `isRecording` is the scanner's own answer to
      // whether the scan started, and it is the only one worth believing: taking
      // the call as the answer is how a replay that recorded nothing comes to
      // write a well-formed, entirely blank result and exit zero.
      begun = scanner.isRecording;
    };

    for (const item of timeline) {
      const at = deliveredAt(item);
      if (at > finishAt) break;
      elapsed = at;
      harness.setClock(at);
      if (item.kind === 'motion') {
        eventsDelivered++;
        harness.dispatchMotion({ timeStamp: item.t_event_ms, rate: item.rate });
      } else if (item.kind === 'orientation') {
        eventsDelivered++;
        harness.dispatchOrientation({
          timeStamp: item.t_event_ms, alpha: item.alpha, beta: item.beta, gamma: item.gamma,
          absolute: item.absolute ?? null, event: item.event,
        });
      } else if (item.kind === 'screen') {
        eventsDelivered++;
        harness.setScreenAngle(item.angle);
      } else if (item.kind === 'frame') {
        framesDelivered++;
        // A frame with no capture time still has a moment it was presented, and
        // the video clock needs a moment to stand at; the metadata below is what
        // says it has no `captureTime`.
        harness.setFrame(loadFrame(item.file), item.t_capture_ms ?? item.t_present_ms);
        scanner.noteFrame?.(item.frame_id);
        // A frame nothing consumed is not a frame the scanner saw. Counting it
        // delivered anyway would report a camera that ran all night to a scanner
        // that had stopped listening, and every number below it would be about a
        // session that did not happen.
        const started = process.hrtime.bigint();
        const consumed = harness.deliverFrame({
          captureTime: item.t_capture_ms, mediaTime: (item.t_capture_ms ?? item.t_present_ms) / 1000,
          presentationTime: item.t_present_ms, expectedDisplayTime: item.t_present_ms,
          width: item.width, height: item.height, presentedFrames: framesDelivered,
        });
        callbackMs.push(Number(process.hrtime.bigint() - started) / 1e6);
        if (!consumed)
          throw new Error(`the scanner had no video-frame callback registered at ${item.frame_id} (${at} ms)`);
        const report = scanner.frameReport();
        const basis = report.basis;
        events.push({
          t_ms: at,
          frame_id: item.frame_id,
          compass_ready: report.compassReady,
          tilt_ready: report.tiltReady,
          aim: report.aim,
          basis: basis && { right: basis.right, up: basis.up, forward: basis.forward },
          frame_count: report.frameCount,
          cue: report.cue,
          ...(report.extra ? { keyframe: report.extra.keyframe, cls: report.extra.cls } : {}),
        });
        if (report.extra?.keyframe) {
          const snapshot = scanner.liveSnapshot();
          if (snapshot) live.push(snapshot);
        }
      } else {
        // A kind this driver does not know is a case from a simulator newer than
        // the driver. Skipping it would replay the night without something the
        // case says happened, and score the result as though it had not.
        throw new Error(`observation kind "${(item as { kind: string }).kind}" is not one the replay delivers`);
      }
      tryBegin(at);
    }

    if (!begun) throw new Error(lastOffered === null
      ? `the scan never started: begin() was never offered, because the scanner could not `
        + `begin (canBegin was false) at any delivery up to ${elapsed} ms`
      : `the scan never started: begin() was last offered at ${lastOffered} ms and the `
        + `scanner was still not recording`);

    // The finish action is when the user closes the scan, so the results are
    // read at that instant rather than at the last frame's.
    harness.setClock(Number.isFinite(finishAt) ? finishAt : elapsed);
    scanner.finishScan();
    const horizon = scanner.horizon();
    const mosaic = scanner.panorama();
    const counted = scanner.cells?.();
    const captures = scanner.captureLog();
    const summary: Summary = {
      frames_delivered: framesDelivered,
      events_delivered: eventsDelivered,
      frames_accepted: captures.filter(record => record.outcome === 'accepted').length,
      cells_total: counted ? counted.total : null,
      cells_covered: counted ? counted.covered : null,
      elapsed_ms: elapsed,
      app_commit: appCommit(),
      ...(measured ? {
        cost_ms: {
          callback: statsOf(callbackMs),
          callback_ref: statsOf(callbackMs.map(ms => ms / refUnitMs)),
          ref_unit_ms: refUnitMs,
        },
      } : {}),
    };

    // The raster the scorer maps directions through is 1080 x 300 RGBA. A buffer
    // of another length would fail to encode, but a first-seen map of another
    // length would be written and read as a different raster, and so would a
    // 300 x 1080 mosaic, which holds exactly the bytes of a 1080 x 300 one. All
    // three are checked here, where a scanner's output crosses into a file; the
    // literal types of `panorama()` guard a cast, and nothing else.
    if (mosaic) {
      const { width, height } = mosaic as { width: number; height: number };
      if (width !== PANO_W || height !== PANO_H)
        throw new Error(`the scanner's mosaic is ${width} x ${height}, not ${PANO_W} x ${PANO_H}`);
    }
    if (mosaic && mosaic.pixels.length !== PANO_W * PANO_H * 4)
      throw new Error(`the scanner's mosaic holds ${mosaic.pixels.length} bytes, not the `
        + `${PANO_W * PANO_H * 4} of a ${PANO_W} x ${PANO_H} RGBA raster`);
    const firstSeen = scanner.firstSeen();
    if (firstSeen && firstSeen.length !== PANO_W * PANO_H)
      throw new Error(`the scanner's first-seen map holds ${firstSeen.length} values, not `
        + `${PANO_W} x ${PANO_H}`);
    writeFileSync(join(out, 'panorama.png'),
      mosaic
        ? encodePng(mosaic.pixels, mosaic.width, mosaic.height)
        : encodePng(new Uint8ClampedArray(PANO_W * PANO_H * 4), PANO_W, PANO_H));
    writeJson(join(out, 'horizon.json'), horizon);
    // Only a scanner that tiles the dome has columns to record: the contract's
    // `columns.json` is the legacy tracer's input and means nothing to another.
    const columns = scanner.legacyColumns?.();
    if (columns) writeJson(join(out, 'columns.json'), columns);
    writeJsonl(join(out, 'events.jsonl'), events);
    writeJsonl(join(out, 'captures.jsonl'), captures);
    writeJson(join(out, 'summary.json'), summary);
    if (firstSeen) {
      // Little-endian whatever the host is: the file is read by a scorer on
      // another machine, and a `Uint16Array`'s own bytes are the host's order.
      const bytes = Buffer.alloc(firstSeen.length * 2);
      for (let i = 0; i < firstSeen.length; i++) bytes.writeUInt16LE(firstSeen[i], i * 2);
      writeFileSync(join(out, 'first_seen.bin'), bytes);
    }
    const diagnostics = scanner.diagnostics();
    if (diagnostics) writeJson(join(out, 'diagnostics.json'), diagnostics);
    if (live.length) writeJsonl(join(out, 'live.jsonl'), live);

    stopScanner();
    return summary;
  } finally {
    // A scanner that was started and then lost to an error still holds what it
    // started; stopping it is best effort, and must never hide the error.
    try { stopScanner(); } catch { /* the error already in flight is the one to report */ }
    harness.dispose();
  }
}

async function main(): Promise<void> {
  const usage = 'usage: node --import tsx replay.ts <case directory> [--scanner legacy|pano]';
  const args = process.argv.slice(2);
  let target: string | undefined, scanner: string | undefined;
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--scanner') {
      scanner = args[++i];
      if (!scanner) { console.error(usage); process.exitCode = 1; return; }
    } else if (arg.startsWith('--scanner=') && arg.length > '--scanner='.length) {
      scanner = arg.slice('--scanner='.length);
    } else if (arg.startsWith('--') || target !== undefined) {
      console.error(usage);
      process.exitCode = 1;
      return;
    } else {
      target = arg;
    }
  }
  if (!target) {
    console.error(usage);
    process.exitCode = 1;
    return;
  }
  // `Date.now` and `performance.now` both belong to the harness's virtual
  // clock for the length of a replay, so neither can time it. `hrtime` is the
  // one clock the harness does not touch.
  const started = process.hrtime.bigint();
  const summary = await replayCase(target, { scanner });
  const seconds = Number(process.hrtime.bigint() - started) / 1e9;
  console.log(JSON.stringify(summary, null, 2));
  console.log(`wall time: ${seconds.toFixed(1)} s`);
}

const isMain = import.meta.url === pathToFileURL(process.argv[1] ?? '').href;
if (isMain) await main();
