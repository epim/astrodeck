// Replay a recorded case through the real scanner.
//
//   node --import tsx src/next/hubs/sky/sheets/__sim__/replay.ts <abs case dir>
//
// The point of the whole exercise is that nothing here is a model of the
// scanner. `PhotosphereSweep` is imported from production and driven through
// its public surface only, in a browser (harness.ts) whose clock, camera and
// sensor are the case's own recording. What it writes into `result/` is
// therefore a measurement of the shipped code, and the scorer that reads those
// files has never seen this directory.
//
// The driver reads `input/observations.jsonl`, `input/actions.jsonl` and
// `input/frames/*.png`, and nothing else - not even `manifest.json`, whose
// camera block would only repeat the width and height every frame observation
// already carries. The reference data beside them belongs to the scorer alone:
// a driver able to see it could reach the right answer for the wrong reason,
// and no test of the result could tell.
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { decodePng, encodePng, type Raster } from './png';
import { createHarness } from './harness';

export interface Summary {
  frames_delivered: number;
  events_delivered: number;
  frames_accepted: number;
  cells_total: number;
  cells_covered: number;
  elapsed_ms: number;
  app_commit: string | null;
}

export interface FrameObservation {
  kind: 'frame';
  frame_id: string;
  t_capture_ms: number;
  t_present_ms: number;
  width: number;
  height: number;
  file: string;
}
export interface OrientationObservation {
  kind: 'orientation';
  t_event_ms: number;
  t_receive_ms: number;
  alpha: number;
  beta: number;
  gamma: number;
  absolute: boolean;
}
export type Observation = FrameObservation | OrientationObservation;

const PANORAMA_W = 1080, PANORAMA_H = 300;
/** Decoded frames held back from the garbage collector. Sequential delivery
 *  reads each frame once, so this is a guard against a case that revisits one,
 *  not a speed-up - and it is small because a 480 x 640 RGBA raster is 1.2 MB. */
const FRAME_CACHE = 8;

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
  const rank = (item: Observation) => (item.kind === 'orientation' ? 0 : 1);
  return observations
    .map((item, index) => ({ item, index }))
    .sort((a, b) => deliveredAt(a.item) - deliveredAt(b.item)
      || rank(a.item) - rank(b.item)
      || a.index - b.index)
    .map(entry => entry.item);
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

export async function replayCase(caseDir: string): Promise<Summary> {
  const root = resolve(caseDir);
  const input = join(root, 'input');
  const observations = readJsonl<Observation>(join(input, 'observations.jsonl'));
  const actions = readJsonl<{ t_ms: number; action: string }>(join(input, 'actions.jsonl'));

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
  // Whatever happens below, the globals this harness replaced go back. A
  // replay that throws must not leave a frozen `Date.now` behind for the next
  // thing in the process to trip over.
  try {
    const { PhotosphereSweep, traceSkyCoverage } = await import('../photosphere');
    const sweep = new PhotosphereSweep();
    await sweep.start(harness.video, harness.canvas);

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

    const events: unknown[] = [];
    let framesDelivered = 0, eventsDelivered = 0, elapsed = 0, begun = false;
    // `begin()` refuses until the scanner has a bearing, and the recorded begin
    // action is at the very start of the night, before the first reading has
    // arrived. Dropping it there would leave the whole replay unrecorded behind a
    // silent no-op, so it is held and re-offered at the first moment it can be
    // accepted - which is the same gate the Start button is behind in the UI.
    let lastOffered: number | null = null;
    const tryBegin = (at: number) => {
      if (begun || at < beginAt || !sweep.compassReady) return;
      lastOffered = at;
      sweep.begin();
      // `begin()` returns void and refuses on `!ready || !compassReady`, so
      // calling it proves nothing. `isRecording` is the scanner's own answer to
      // whether the scan started, and it is the only one worth believing: taking
      // the call as the answer is how a replay that recorded nothing comes to
      // write a well-formed, entirely blank result and exit zero.
      begun = sweep.isRecording;
    };

    for (const item of timeline) {
      const at = deliveredAt(item);
      if (at > finishAt) break;
      elapsed = at;
      harness.setClock(at);
      if (item.kind === 'orientation') {
        eventsDelivered++;
        harness.dispatchOrientation({
          timeStamp: item.t_event_ms, alpha: item.alpha, beta: item.beta, gamma: item.gamma,
          absolute: item.absolute,
        });
      } else {
        framesDelivered++;
        harness.setFrame(loadFrame(item.file), item.t_capture_ms);
        // A frame nothing consumed is not a frame the scanner saw. Counting it
        // delivered anyway would report a camera that ran all night to a scanner
        // that had stopped listening, and every number below it would be about a
        // session that did not happen.
        if (!harness.deliverFrame({
          captureTime: item.t_capture_ms, mediaTime: item.t_capture_ms / 1000,
          presentationTime: item.t_present_ms, expectedDisplayTime: item.t_present_ms,
          width: item.width, height: item.height, presentedFrames: framesDelivered,
        })) throw new Error(`the scanner had no video-frame callback registered at ${item.frame_id} (${at} ms)`);
        const basis = sweep.cameraBasis;
        events.push({
          t_ms: at,
          frame_id: item.frame_id,
          compass_ready: sweep.compassReady,
          tilt_ready: sweep.tiltReady,
          aim: sweep.aimTarget?.id ?? null,
          basis: basis && { right: basis.right, up: basis.up, forward: basis.forward },
          frame_count: sweep.frameCount,
          cue: sweep.captureCue,
        });
      }
      tryBegin(at);
    }

    if (!begun) throw new Error(lastOffered === null
      ? `the scan never started: begin() was never offered, because the scanner's compass `
        + `was not ready at any delivery up to ${elapsed} ms`
      : `the scan never started: begin() was last offered at ${lastOffered} ms and the `
        + `scanner was still not recording`);

    // The finish action is when the user closes the scan, so the results are
    // read at that instant rather than at the last frame's.
    harness.setClock(Number.isFinite(finishAt) ? finishAt : elapsed);
    const columns = sweep.columns();
    const trace = traceSkyCoverage(columns);
    const mosaic = sweep.panoramaPixels;
    const cells = sweep.cells;
    const captures = sweep.captureLog.map(record => ({
      at: record.at,
      outcome: record.outcome,
      ...(record.cell === undefined ? {} : { cell: record.cell }),
      ...(record.basis === undefined ? {} : { basis: record.basis }),
      ...(record.sensorBasis === undefined ? {} : { sensor_basis: record.sensorBasis }),
      ...(record.adjusted === undefined ? {} : { adjusted: record.adjusted }),
    }));
    const summary: Summary = {
      frames_delivered: framesDelivered,
      events_delivered: eventsDelivered,
      frames_accepted: captures.filter(record => record.outcome === 'accepted').length,
      cells_total: cells.length,
      cells_covered: cells.filter(cell => cell.captured).length,
      elapsed_ms: elapsed,
      app_commit: appCommit(),
    };

    // A mosaic of another size would still encode, and the scorer's raster
    // mapping would then read every direction wrong while the file looked fine.
    if (mosaic && (mosaic.width !== PANORAMA_W || mosaic.height !== PANORAMA_H))
      throw new Error(`the scanner's mosaic is ${mosaic.width} x ${mosaic.height}, not the `
        + `${PANORAMA_W} x ${PANORAMA_H} raster the result contract defines`);
    writeFileSync(join(out, 'panorama.png'),
      mosaic
        ? encodePng(mosaic.pixels, mosaic.width, mosaic.height)
        : encodePng(new Uint8ClampedArray(PANORAMA_W * PANORAMA_H * 4), PANORAMA_W, PANORAMA_H));
    writeJson(join(out, 'horizon.json'), {
      bins: columns.length,
      points: trace.points,
      uncertain_bins: trace.uncertainBins,
    });
    // The contract's `columns.json` is the bin-centre luminance column, one
    // row per degree - the array issue #58 quotes. The tracer now reads five
    // columns across each bin, in two channels; recording that instead would
    // silently change what every cached column in the cache means.
    writeJson(join(out, 'columns.json'),
      sweep.centreColumns().map(column => column.map(value => (Number.isFinite(value) ? value : null))));
    writeJsonl(join(out, 'events.jsonl'), events);
    writeJsonl(join(out, 'captures.jsonl'), captures);
    writeJson(join(out, 'summary.json'), summary);

    sweep.stop();
    return summary;
  } finally {
    harness.dispose();
  }
}

async function main(): Promise<void> {
  const target = process.argv[2];
  if (!target) {
    console.error('usage: node --import tsx replay.ts <case directory>');
    process.exitCode = 1;
    return;
  }
  // `Date.now` and `performance.now` both belong to the harness's virtual
  // clock for the length of a replay, so neither can time it. `hrtime` is the
  // one clock the harness does not touch.
  const started = process.hrtime.bigint();
  const summary = await replayCase(target);
  const seconds = Number(process.hrtime.bigint() - started) / 1e9;
  console.log(JSON.stringify(summary, null, 2));
  console.log(`wall time: ${seconds.toFixed(1)} s`);
}

const isMain = import.meta.url === pathToFileURL(process.argv[1] ?? '').href;
if (isMain) await main();
