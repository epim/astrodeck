// The replay driver, checked on its own three moving parts: the PNG codec, the
// area-averaging resampler the canvas stub is built on, and the driver's
// determinism on a case small enough to build here.
//
// Determinism is the load-bearing one. A scored replay is a measurement, and a
// measurement that moves when nothing moved cannot be compared between two
// commits - a regression and a reshuffled Map iteration would look the same.
// So the same case directory, replayed twice in one process, has to produce
// byte-identical files.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { deflateSync } from 'node:zlib';
import { decodePng, encodePng, pngChunk, PNG_SIGNATURE } from '../__sim__/png';
import { createHarness, resample } from '../__sim__/harness';
import { mergeObservations, replayCase, type Observation } from '../__sim__/replay';
import { DOME_CELLS } from '../photosphereGeometry';
import { endsOverlapRun, LENS_DOUBT_AFTER, type CaptureOutcome } from '../photosphere';

let passed = 0, failed = 0, skipped = 0;
function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  return Promise.resolve().then(fn).then(
    () => { passed++; console.log(`PASS ${name}`); },
    (e: Error) => { failed++; console.log(`FAIL ${name}\n     ${e.message.split('\n')[0]}`); },
  );
}
/** Not a pass. A case whose recording is not on this machine was NOT measured,
 *  and the tally below must not imply it was. */
function skip(name: string, why: string): void {
  skipped++;
  console.log(`SKIP ${name}\n     ${why}`);
}

await test('png: a 5 x 3 RGBA gradient survives encode and decode byte for byte', () => {
  const width = 5, height = 3;
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = (y * width + x) * 4;
    pixels[i] = x * 51; pixels[i + 1] = y * 85; pixels[i + 2] = 255 - x * 51; pixels[i + 3] = x === 0 ? 0 : 255;
  }
  const decoded = decodePng(encodePng(pixels, width, height));
  assert.equal(decoded.width, width);
  assert.equal(decoded.height, height);
  assert.deepEqual(Array.from(decoded.pixels), Array.from(pixels));
});

await test('png: a 4 x 4 RGB PNG written by another encoder decodes to the right corners', () => {
  // Generated once with the system Python, and pasted:
  //   python -c "from PIL import Image; import io, base64;
  //   px=[(255,0,0),(10,20,30),(40,50,60),(0,255,0),(70,80,90),(100,110,120),
  //   (130,140,150),(160,170,180),(190,200,210),(220,230,240),(5,15,25),
  //   (35,45,55),(0,0,255),(65,75,85),(95,105,115),(255,255,255)];
  //   im=Image.new('RGB',(4,4)); im.putdata(px); b=io.BytesIO();
  //   im.save(b,'PNG'); print(base64.b64encode(b.getvalue()).decode())"
  // It uses filter 1 (Sub) on its second scanline and filter 0 elsewhere, so
  // this is a foreign encoder's bytes, not a round trip through our own.
  const fixture = 'iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAN0lEQVR4nGP4z8DAJSKnYWTD8J'
    + '+B0S0gSg4GGPaduHTn2QdWfkllXXMGBob/jt6h8ZnF////BwBclBCmbKfz9gAAAABJRU5ErkJggg==';
  const image = decodePng(Buffer.from(fixture, 'base64'));
  assert.equal(image.width, 4);
  assert.equal(image.height, 4);
  const at = (x: number, y: number) => Array.from(image.pixels.slice((y * 4 + x) * 4, (y * 4 + x) * 4 + 4));
  assert.deepEqual(at(0, 0), [255, 0, 0, 255], 'top left');
  assert.deepEqual(at(3, 0), [0, 255, 0, 255], 'top right');
  assert.deepEqual(at(0, 3), [0, 0, 255, 255], 'bottom left');
  assert.deepEqual(at(3, 3), [255, 255, 255, 255], 'bottom right');
});

// A PNG container holding scanlines someone else filtered. The rows below are
// literal bytes, computed once outside this file and pasted, so nothing here
// re-derives the filter arithmetic the decoder is being graded on: a shared
// sign error in a test that computed its own input would cancel out and pass.
// The 3 x 3 source image is FILTER_IMAGE; each row was put through one filter
// with a short Python script (the report for task 6 carries it verbatim).
const FILTER_IMAGE = [
  10, 20, 30, 255, 200, 100, 50, 255, 0, 255, 128, 255,
  12, 25, 35, 255, 190, 110, 55, 255, 5, 250, 130, 255,
  250, 5, 60, 255, 30, 40, 50, 255, 100, 100, 100, 255,
];
const FILTERED: Record<string, number[][]> = {
  'Up (2)': [
    [0, 10, 20, 30, 200, 100, 50, 0, 255, 128],
    [2, 2, 5, 5, 246, 10, 5, 5, 251, 2],
    [2, 238, 236, 25, 96, 186, 251, 95, 106, 226],
  ],
  'Average (3)': [
    [3, 10, 20, 30, 195, 90, 35, 156, 205, 103],
    [3, 7, 15, 20, 84, 48, 13, 166, 68, 39],
    [3, 244, 249, 43, 66, 239, 249, 83, 211, 10],
  ],
  'Paeth (4)': [
    [4, 10, 20, 30, 190, 80, 20, 56, 155, 78],
    [4, 2, 5, 5, 246, 10, 5, 5, 251, 2],
    [4, 238, 236, 25, 36, 186, 246, 95, 106, 226],
  ],
};

/** A PNG built from already-filtered scanlines, through the codec's own chunk
 *  writer. `colourType` 2 is RGB, 6 is RGBA. */
function containerFor(width: number, height: number, colourType: number, rows: number[][]): Buffer {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4);
  header[8] = 8; header[9] = colourType; header[10] = 0; header[11] = 0; header[12] = 0;
  return Buffer.concat([
    Buffer.from(PNG_SIGNATURE),
    pngChunk('IHDR', header),
    pngChunk('IDAT', deflateSync(Buffer.from(rows.flat()))),
    pngChunk('IEND', Buffer.alloc(0)),
  ]);
}

for (const [name, rows] of Object.entries(FILTERED)) {
  await test('png: filter ' + name + ' decodes to the image it was filtered from', () => {
    const image = decodePng(containerFor(3, 3, 2, rows));
    assert.equal(image.width, 3);
    assert.equal(image.height, 3);
    assert.deepEqual(Array.from(image.pixels), FILTER_IMAGE);
  });
}

await test('png: a chunk whose CRC does not match its bytes is refused', () => {
  const good = containerFor(3, 3, 2, FILTERED['Up (2)']);
  // The IHDR CRC sits after 8 signature bytes, 4 of length, 4 of type, 13 body.
  const torn = Buffer.from(good);
  torn[8 + 4 + 4 + 13] ^= 0x01;
  assert.throws(() => decodePng(torn), /IHDR fails its CRC/);
  // And a body byte the CRC then disagrees with, which is the failure that
  // actually happens to a file: a torn read, not a torn checksum.
  const bent = Buffer.from(good);
  bent[8 + 4 + 4] ^= 0x01;
  assert.throws(() => decodePng(bent), /IHDR fails its CRC/);
});

await test('resample: a 4 x 4 checkerboard box-averages to 2 x 2', () => {
  const src = new Uint8ClampedArray(4 * 4 * 4);
  for (let y = 0; y < 4; y++) for (let x = 0; x < 4; x++) {
    const v = (x % 2 === y % 2) ? 0 : 200, i = (y * 4 + x) * 4;
    src[i] = src[i + 1] = src[i + 2] = v; src[i + 3] = 255;
  }
  const out = resample(src, 4, 4, 2, 2);
  assert.equal(out.length, 2 * 2 * 4);
  for (let p = 0; p < 4; p++) {
    assert.equal(out[p * 4], 100, `cell ${p} red`);
    assert.equal(out[p * 4 + 1], 100, `cell ${p} green`);
    assert.equal(out[p * 4 + 2], 100, `cell ${p} blue`);
    assert.equal(out[p * 4 + 3], 255, `cell ${p} alpha`);
  }
});

await test('resample: a ratio that does not divide splits a source pixel by area', () => {
  // 4 columns into 3 is the case 640 rows into 24 is: every output straddles a
  // source boundary, so a nearest-neighbour pick and an area average differ.
  const src = new Uint8ClampedArray(4 * 1 * 4);
  [0, 120, 240, 60].forEach((v, x) => { src[x * 4] = src[x * 4 + 1] = src[x * 4 + 2] = v; src[x * 4 + 3] = 255; });
  const out = resample(src, 4, 1, 3, 1);
  assert.deepEqual([out[0], out[4], out[8]], [30, 180, 105]);
});

await test('merge: a reading and a frame in the same millisecond arrive reading first', () => {
  // A browser drains the task queue, where the orientation event lands, before
  // the rendering steps, where requestVideoFrameCallback runs. Handing the
  // frame over first gives forFrame a pose history one sample short of the one
  // a real page would have had.
  const reading: Observation = {
    kind: 'orientation', t_event_ms: 480, t_receive_ms: 500,
    alpha: 1, beta: 90, gamma: 0, absolute: true,
  };
  const frame: Observation = {
    kind: 'frame', frame_id: 'f000005', t_capture_ms: 440, t_present_ms: 500,
    width: 4, height: 4, file: 'frames/f000005.png',
  };
  assert.deepEqual(mergeObservations([frame, reading]).map(o => o.kind), ['orientation', 'frame']);
  assert.deepEqual(mergeObservations([reading, frame]).map(o => o.kind), ['orientation', 'frame']);
});

await test('harness: dispose puts the clocks and the DOM globals back', () => {
  const before = Date.now();
  assert.ok(before > 1_700_000_000_000, 'the wall clock was already replaced before this test ran');
  const harness = createHarness({ videoWidth: 8, videoHeight: 8 });
  harness.setClock(1234);
  assert.equal(Date.now(), 1234);
  assert.equal(performance.now(), 1234);
  harness.dispose();
  assert.ok(Date.now() >= before, 'Date.now stayed frozen after dispose');
  assert.notEqual(performance.now(), 1234, 'performance.now stayed frozen after dispose');
  assert.equal('window' in globalThis, false, 'a jsdom window was left on globalThis');
  assert.equal('document' in globalThis, false, 'a jsdom document was left on globalThis');
});

await test('harness: a canvas nothing has been drawn to reads back transparent black', () => {
  const harness = createHarness({ videoWidth: 8, videoHeight: 8 });
  try {
    const context = harness.canvas.getContext('2d')!;
    const { data } = context.getImageData(0, 0, 4, 3);
    assert.equal(data.length, 4 * 3 * 4);
    assert.ok(Array.from(data).every(v => v === 0), 'an undrawn canvas returned camera pixels');
  } finally {
    harness.dispose();
  }
});

// A whole case, small enough to build in the test: 30 frames of the gradient
// and block the DOM harness uses, with the block moving for the first eight and
// then perfectly still, and eight orientation readings closing on one dome cell
// before the sensor goes quiet - the shape the scanner is built around.
const SCENE_W = 480, SCENE_H = 640;
function scenePixel(x: number, y: number, shift: number): number {
  const col = Math.round(((x + shift) % SCENE_W) * 31 / (SCENE_W - 1));
  const block = col >= 10 && col <= 17 && y >= Math.floor(SCENE_H / 3) && y <= Math.floor(2 * SCENE_H / 3);
  return block ? 180 : 40 + Math.round(col * 80 / 31);
}
function sceneFrame(shift: number): Uint8ClampedArray {
  const pixels = new Uint8ClampedArray(SCENE_W * SCENE_H * 4);
  for (let y = 0; y < SCENE_H; y++) for (let x = 0; x < SCENE_W; x++) {
    const v = scenePixel(x, y, shift), i = (y * SCENE_W + x) * 4;
    pixels[i] = pixels[i + 1] = pixels[i + 2] = v; pixels[i + 3] = 255;
  }
  return pixels;
}

function buildCase(root: string, options: { readings?: boolean; manifestCamera?: { width: number; height: number } } = {}): void {
  const input = join(root, 'input'), frames = join(input, 'frames');
  mkdirSync(frames, { recursive: true });
  const observations: string[] = [];
  const cell = DOME_CELLS.find(c => c.alt > 20 && c.alt < 60)!;
  // Eight readings closing on the cell, the last at 720 ms, then silence.
  // `readings: false` builds the same night with a sensor that never speaks,
  // which is the case where the scan can never start.
  for (let k = 0; options.readings !== false && k < 8; k++) {
    const offset = 14 - k * 2;
    observations.push(JSON.stringify({
      kind: 'orientation', t_event_ms: k * 100 + 20, t_receive_ms: k * 100 + 40,
      alpha: (360 - (cell.az + offset)) % 360, beta: 90 + cell.alt, gamma: 0, absolute: true,
    }));
  }
  for (let i = 0; i < 30; i++) {
    // One grid cell of movement per frame for eight frames, then nothing.
    const name = `f${String(i).padStart(6, '0')}`;
    writeFileSync(join(frames, `${name}.png`), encodePng(sceneFrame(Math.min(i, 8) * 15), SCENE_W, SCENE_H));
    observations.push(JSON.stringify({
      kind: 'frame', frame_id: name, t_capture_ms: i * 100, t_present_ms: i * 100 + 60,
      width: SCENE_W, height: SCENE_H, file: `frames/${name}.png`,
    }));
  }
  observations.sort((a, b) => {
    const at = JSON.parse(a), bt = JSON.parse(b);
    return (at.t_present_ms ?? at.t_receive_ms) - (bt.t_present_ms ?? bt.t_receive_ms);
  });
  writeFileSync(join(input, 'observations.jsonl'), observations.join('\n') + '\n');
  writeFileSync(join(input, 'actions.jsonl'),
    JSON.stringify({ t_ms: 0, action: 'begin' }) + '\n' + JSON.stringify({ t_ms: 4000, action: 'finish' }) + '\n');
  // A real case directory has one, so this one does too - but the driver does
  // not read it. `manifestCamera` is how the test below says so: a manifest
  // that disagrees with the frames must not reach the harness.
  const camera = options.manifestCamera ?? { width: SCENE_W, height: SCENE_H };
  writeFileSync(join(root, 'manifest.json'), JSON.stringify({
    schema: 1, case_id: 'synthetic', seed: 1, scene: 'synthetic', route: 'synthetic',
    camera: { ...camera, fov_short_deg: 60 }, fps: 10, expected: 'positive',
    profile: 'synthetic', hashes: {}, versions: {},
  }));
}

/** Every outcome `grabFrame` can return, and whether it ENDS a run of overlap
 *  refusals (issue #52; `endsOverlapRun` is the rule, this is the table that
 *  grades it). One table with two jobs: the KEYS are what a replay's capture
 *  records are checked against below, and the VALUES are what `endsOverlapRun`
 *  is checked against at the bottom of the file.
 *
 *  A `Record<CaptureOutcome, boolean>` and not a bare list, so tsc refuses this
 *  file when the union grows and the new outcome is not named here: a fifteenth
 *  gate cannot join `grabFrame` and quietly pass both cases. It WAS a bare
 *  `Set`, and it was two short - `stale-image` and `frame-already-captured`
 *  were missing, so a recording that produced either would have been reported
 *  as an outcome the scanner cannot return. Nothing could have noticed. */
const OUTCOME_ENDS_RUN: Record<CaptureOutcome, boolean> = {
  'accepted': true,
  'not-recording': true,
  'not-ready': true,
  'unhealthy': true,
  'no-image': true,
  // The run itself, and the four outcomes that end a grab BEFORE the overlap
  // test is reached: the pose has not settled, the 600 ms registration rate
  // limit fired (immediately after most refusals, because the refused attempt
  // set `lastRegistrationAt` on its way in), the aim is between dome cells, or
  // the phone is pointed at the ground. None is evidence that the view now
  // matches. Counting `alignment-wait` or `too-soon` as a reset caps every run
  // at one and the lens cue is dead code; counting `no-target` as one delays
  // it on the wrong-lens recording from the 9th refusal to the 24th of 25.
  'overlap-wait': false,
  'alignment-wait': false,
  'too-soon': false,
  'no-target': false,
  'below-horizon': false,
  'already-captured': true,
  'stale-image': true,
  'frame-already-captured': true,
  'read-failed': true,
};
const OUTCOMES = new Set(Object.keys(OUTCOME_ENDS_RUN));

/** The `app_commit` the driver stamped on the determinism run below, read by
 *  the dirty-tree test after it, and that run's panorama bytes, which the
 *  manifest-independence test compares a second build against. */
let replayedCommit: string | null = null;
let referencePanorama: Buffer | null = null;

const root = mkdtempSync(join(tmpdir(), 'photosphere-replay-'));
try {
  buildCase(root);
  const first = await replayCase(root);
  replayedCommit = first.app_commit;
  const result = join(root, 'result');
  const firstSummary = readFileSync(join(result, 'summary.json'), 'utf8');
  const firstPanorama = readFileSync(join(result, 'panorama.png'));
  referencePanorama = firstPanorama;
  const second = await replayCase(root);
  const secondSummary = readFileSync(join(result, 'summary.json'), 'utf8');
  const secondPanorama = readFileSync(join(result, 'panorama.png'));

  await test('replay: the same case replayed twice produces the same summary', () => {
    assert.equal(firstSummary, secondSummary);
    assert.deepEqual(first, second);
    assert.equal(first.frames_delivered, 30);
    assert.equal(first.events_delivered, 8);
    // Without this the whole determinism case passes on a replay that recorded
    // nothing: `not-recording` is a valid outcome, the blank panorama is the
    // right size, and two blanks are identical. Determinism over an empty
    // result is not evidence about the scanner.
    assert.ok(first.frames_accepted > 0, 'the replay accepted no frames at all');
    assert.ok(first.cells_covered > 0, 'the replay covered no dome cells');
  });

  await test('replay: the same case replayed twice produces the same panorama bytes', () => {
    assert.equal(firstPanorama.length, secondPanorama.length, 'panorama length');
    assert.ok(firstPanorama.equals(secondPanorama), 'panorama bytes differ between two replays');
    const image = decodePng(firstPanorama);
    assert.equal(image.width, 1080);
    assert.equal(image.height, 300);
    let painted = 0;
    for (let i = 3; i < image.pixels.length; i += 4) if (image.pixels[i] === 255) painted++;
    assert.ok(painted > 0, 'every pixel of the panorama is transparent: nothing was ever painted');
  });

  await test('replay: one events line per delivered frame', () => {
    const lines = readFileSync(join(result, 'events.jsonl'), 'utf8').trim().split('\n');
    assert.equal(lines.length, 30);
    const first = JSON.parse(lines[0]);
    for (const key of ['t_ms', 'frame_id', 'compass_ready', 'tilt_ready', 'aim', 'basis', 'frame_count', 'cue'])
      assert.ok(key in first, `events.jsonl is missing ${key}`);
    assert.equal(first.frame_id, 'f000000');
  });

  await test('replay: every capture record names an outcome the scanner can return', () => {
    const lines = readFileSync(join(result, 'captures.jsonl'), 'utf8').trim().split('\n');
    assert.ok(lines.length >= 1, 'no capture records at all');
    for (const line of lines) {
      const record = JSON.parse(line);
      assert.ok(OUTCOMES.has(record.outcome), `unknown outcome ${record.outcome}`);
      assert.equal(typeof record.at, 'number');
      assert.equal('sensorBasis' in record, false, 'captures.jsonl must use the contract spelling');
    }
  });

  await test('replay: horizon and columns are written in the shape the scorer reads', () => {
    const horizon = JSON.parse(readFileSync(join(result, 'horizon.json'), 'utf8'));
    assert.equal(typeof horizon.bins, 'number');
    assert.equal(horizon.points.length, horizon.bins);
    assert.ok(Array.isArray(horizon.uncertain_bins));
    const columns = JSON.parse(readFileSync(join(result, 'columns.json'), 'utf8'));
    assert.equal(columns.length, horizon.bins);
    assert.equal(columns[0].length, 101);
    assert.ok(columns.flat().every((v: unknown) => v === null || typeof v === 'number'),
      'columns.json must carry NaN as null');
  });
} finally {
  rmSync(root, { recursive: true, force: true });
}

await test('replay: a scan that never started is an error, not a blank result', async () => {
  // begin() refuses silently when the compass is not ready and returns void, so
  // a driver that believed the call would write a well-formed, entirely
  // transparent result and exit zero: a measurement of nothing, filed and
  // scored beside real ones.
  const empty = mkdtempSync(join(tmpdir(), 'photosphere-replay-empty-'));
  try {
    buildCase(empty, { readings: false });
    await assert.rejects(replayCase(empty), /the scan never started/);
    assert.equal(existsSync(join(empty, 'result', 'summary.json')), false,
      'a result was written for a scan that never started');
    assert.equal(existsSync(join(empty, 'result', 'panorama.png')), false,
      'a panorama was written for a scan that never started');
  } finally {
    rmSync(empty, { recursive: true, force: true });
  }
});

await test('replay: a manifest that disagrees with the frames changes nothing', async () => {
  // The driver takes the video size from the frame observations, so a manifest
  // declaring the camera 8 x 8 is simply not consulted. The claim is not that
  // the replay survives it but that the result is the SAME result, byte for
  // byte, as the run above whose manifest was right: an 8 x 8 video would
  // reach the scanner as a square lens and paint the mosaic somewhere else.
  const wrong = mkdtempSync(join(tmpdir(), 'photosphere-replay-manifest-'));
  try {
    buildCase(wrong, { manifestCamera: { width: 8, height: 8 } });
    const summary = await replayCase(wrong);
    assert.equal(summary.frames_delivered, 30);
    assert.ok(summary.frames_accepted > 0, 'the replay accepted no frames at all');
    const panorama = readFileSync(join(wrong, 'result', 'panorama.png'));
    assert.ok(referencePanorama && panorama.equals(referencePanorama),
      'the manifest camera block reached the replay: the panorama moved');
  } finally {
    rmSync(wrong, { recursive: true, force: true });
  }
});

await test('replay: app_commit says when the tree it ran from was dirty', () => {
  // The summary's commit is the identity of the code that was replayed. On a
  // clean tree it is the bare hash; on an edited one it has to say so, or a
  // score carries the name of code that never ran and the diff that made the
  // difference is gone.
  const head = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  const dirty = execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8' }).trim().length > 0;
  assert.equal(replayedCommit, dirty ? `${head}-dirty` : head);
});

await test('replay: the driver never reads the reference data', () => {
  // The scorer owns the reference; a driver that could see it could agree with
  // it for the wrong reason. The Python suite greps for the same word.
  const dir = fileURLToPath(new URL('../__sim__/', import.meta.url));
  for (const name of ['png.ts', 'harness.ts', 'replay.ts'])
    assert.equal(readFileSync(join(dir, name), 'utf8').includes('tr' + 'uth'), false, `${name} names it`);
});

await test('replay: git is reachable, so app_commit is a real commit', () => {
  const head = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  assert.match(head, /^[0-9a-f]{40}$/);
});

// --------------------------------------------------- issue #52: the lens cue
// Two recordings that differ in exactly one field - the camera's short-axis
// field of view, 70 degrees against 60 - replayed through the same scanner,
// which assumes 60 and starts with no saved calibration. A wrong lens SCALE is
// not something the small rigid rotation `registerFrame` fits can absorb, so
// `checkOverlap` returns `conflict` and the 70 case is refused over and over -
// and the cue used to answer every one of those refusals with an instruction
// about the user's aim, never naming the control that would fix it.
//
// This is pinned through the replay rather than the capture DOM harness
// because that harness cannot produce a conflict to pin it on: its camera
// returns a uniform grey, and `SkyPanorama.checkOverlap` reads a variance
// below 100 as 'unknown' and never as 'conflict'. Faking one would be a test
// of the fake.
//
// The recordings are made by the simulator and are NOT in the repository
// (`tools/photosphere_sim/cache/` is git-ignored), so the three cases below
// SKIP where they are absent. The table case after them runs everywhere.
const CASES = fileURLToPath(new URL('../../../../../../../tools/photosphere_sim/cache/cases/', import.meta.url));
const NO_RECORDING = 'the simulator recording is not on this machine (tools/photosphere_sim/cache is git-ignored)';

interface Replayed {
  summary: Awaited<ReturnType<typeof replayCase>>;
  captures: { at: number; outcome: CaptureOutcome }[];
  events: { t_ms: number; cue: string }[];
}

/** Replay a recorded case WITHOUT writing into it. `replayCase` empties and
 *  rewrites `<case>/result/`, and for these two cases that directory is the
 *  measurement issue #52 was filed from - so the input is copied to a
 *  temporary directory and the driver writes there. The copy is about 42 MB
 *  and a quarter of a second. A junction would be free, and would put a
 *  recursive delete one Node version away from walking into the recording. */
async function replayRecorded(caseId: string): Promise<Replayed | null> {
  const input = join(CASES, caseId, 'input');
  if (!existsSync(input)) return null;
  const root = mkdtempSync(join(tmpdir(), `photosphere-${caseId}-`));
  try {
    cpSync(input, join(root, 'input'), { recursive: true });
    const summary = await replayCase(root);
    const lines = (name: string) => readFileSync(join(root, 'result', name), 'utf8')
      .trim().split('\n').map(line => JSON.parse(line));
    return { summary, captures: lines('captures.jsonl'), events: lines('events.jsonl') };
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

/** The longest run of overlap refusals in a capture log, counted by the
 *  scanner's own rule for what ends one rather than a second copy of it. */
function longestOverlapRun(captures: { outcome: CaptureOutcome }[]): number {
  let run = 0, longest = 0;
  for (const record of captures) {
    if (record.outcome === 'overlap-wait') { run++; longest = Math.max(longest, run); }
    else if (endsOverlapRun(record.outcome)) run = 0;
  }
  return longest;
}

// The distinctive halves of the cue under test and of the one it displaces,
// matched as phrases rather than imported as constants: a case that compares
// the sentence with itself grades nothing.
const NAMES_THE_LENS = /camera view angle may be set wrong for this lens/;
// The setting, by the words it must be called. It is deliberately NOT a UI
// label: no committed component calls `setCameraViewAngle` at b8581949, so a
// quoted label would be a quote of something unlanded.
const NAMES_THE_SETTING = /camera view angle/;
const BLAMES_THE_AIM = /Return to a green patch/;

const wrongLens = await replayRecorded('chartyard-arc075-70');
const rightLens = await replayRecorded('chartyard-arc075-60');

const NAMED = 'replay: a 70-degree lens scanned at 60 is refused until the cue names the view angle (#52)';
if (!wrongLens) skip(NAMED, NO_RECORDING);
else await test(NAMED, () => {
  // A THRESHOLD and never a count. A later task changes targeting, which moves
  // how many refusals this route earns; what this case needs is only that the
  // branch it grades is still reachable on the recording.
  const run = longestOverlapRun(wrongLens.captures);
  assert.ok(run >= LENS_DOUBT_AFTER,
    `the worst run of overlap refusals was ${run}, short of the ${LENS_DOUBT_AFTER} the cue waits for: `
    + 'this recording can no longer reach the branch this case grades');
  const named = wrongLens.events.filter(event => NAMES_THE_LENS.test(event.cue));
  assert.ok(named.length > 0,
    'the scan was refused past the threshold with no lens calibration and the cue never mentioned the lens');
  for (const event of named) {
    assert.match(event.cue, NAMES_THE_SETTING);
    // It REPLACES the aim instruction. Two remedies in one sentence is the
    // user trying the wrong one first, which is the whole of issue #52.
    assert.doesNotMatch(event.cue, BLAMES_THE_AIM);
  }
  // Mutation: LENS_DOUBT_AFTER = 1e9. The run assertion fails first ("was 8,
  // short of the 1000000000"), and the cue never appears either. Observed red.
});

const QUIET = 'replay: the same scan with the lens the scanner assumes never mentions the view angle (#52)';
if (!rightLens) skip(QUIET, NO_RECORDING);
else await test(QUIET, () => {
  // The negative is evidence only because this scan WORKED: same seed, scene,
  // route, resolution and driver, differing in the recorded lens alone.
  assert.ok(rightLens.summary.frames_accepted > 0,
    'the control scan captured nothing, so its silence says nothing about the cue');
  const run = longestOverlapRun(rightLens.captures);
  assert.ok(run < LENS_DOUBT_AFTER,
    `the control scan's worst run of overlap refusals was ${run}, at or past the ${LENS_DOUBT_AFTER} threshold: `
    + 'the two cases no longer separate the wrong lens from the right one');
  assert.equal(rightLens.events.filter(event => NAMES_THE_LENS.test(event.cue)).length, 0,
    'a scan with the lens the scanner assumes was told its lens may be set wrong');
  // Mutation: LENS_DOUBT_AFTER = 1. This case reddens on both assertions.
  // Observed red.
});

const RESET = 'replay: an outcome other than a refusal puts the ordinary cue back (#52)';
if (!wrongLens) skip(RESET, NO_RECORDING);
else await test(RESET, () => {
  let run = 0, reached: number | null = null, cleared: number | null = null;
  for (const record of wrongLens.captures) {
    if (record.outcome === 'overlap-wait') {
      run++;
      if (run >= LENS_DOUBT_AFTER && reached === null) reached = record.at;
    } else if (endsOverlapRun(record.outcome)) {
      run = 0;
      if (reached !== null && cleared === null) cleared = record.at;
    }
  }
  assert.ok(reached !== null, 'the recording never reached the threshold');
  assert.ok(cleared !== null,
    'nothing but refusals followed the threshold in this recording, so it cannot show a run being broken');
  // The driver reads the cue straight after the frame that drove the grab, and
  // stamps both with the same virtual millisecond, so these are the cues
  // immediately after the refusal that reached the threshold and after the
  // first outcome that ended the run.
  const cueAt = (t: number): string => {
    const event = wrongLens.events.find(candidate => candidate.t_ms === t);
    assert.ok(event, `no events line at ${t} ms`);
    return event.cue;
  };
  assert.match(cueAt(reached), NAMES_THE_LENS);
  assert.doesNotMatch(cueAt(cleared), NAMES_THE_LENS);
  // Mutation: make `endsOverlapRun` return false for every outcome. The run is
  // then never broken, the cue stays the lens sentence, and the second
  // assertion here fails. Observed red.
});

await test('capture outcomes: only the waits before the overlap test keep a refusal run alive (#52)', () => {
  // OUTCOME_ENDS_RUN is exhaustive over `CaptureOutcome` by its type, so this
  // grades every outcome the scanner has and tsc reddens when it grows one.
  for (const [outcome, expected] of Object.entries(OUTCOME_ENDS_RUN))
    assert.equal(endsOverlapRun(outcome as CaptureOutcome), expected, `${outcome} is classified wrongly`);
  // Mutation: drop `&& outcome !== 'too-soon'` from `endsOverlapRun`. Red here
  // on that key, and red on the two replay cases above, whose longest runs
  // both fall to 1. Observed red.
});

console.log(`photosphereReplay.test: ${passed}/${passed + failed} passed`
  + (skipped ? ` (${skipped} skipped: ${NO_RECORDING})` : ''));
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
