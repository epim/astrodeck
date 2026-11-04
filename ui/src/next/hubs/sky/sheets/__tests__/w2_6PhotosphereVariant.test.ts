// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w2_6PhotosphereVariant.test.ts - WP 2.6 (#1117, #904, #1058): the scanner
// publishes a Measured bin only where a frame that was checked covered it.
//
// THE VARIANT. A chart-yard in miniature, scanned by the real `PhotosphereSweep`
// from the bottom of the sky up: textured ground, a wall 30 degrees tall between
// azimuth 84 and 144, textured sky everywhere EXCEPT a clear patch above the wall
// (azimuth 40 to 220, above altitude 26), which is a uniform gradient. A frame
// that sees only that patch has no texture for the overlap test to decide on, so
// its overlap is `unknown`: nothing has checked where it was aimed. Then the
// failure of #904 is staged: the compass of ONE such frame lies, saying the phone
// is tilted 27 degrees up when the camera is looking 58 up. Painted where the
// compass says, its blank sky covers the wall.
//
// WHAT IS HELD. The scan with an honest compass is the control: it measures most
// of the dome and the wall at its height. The scan with the lying frame must
// publish no Measured bin the control did not, and no wall bin may read open
// sky: the lying frame is held, because the sky it saw is a uniform gradient no
// overlap test can place, and a held frame paints nothing the trace reads.
//
// Named mutant (edit to photosphere.ts, from a byte backup, restored by hash):
// `vouched = true` for every frame, which is the scanner painting unknown-overlap
// frames as it did before #904. The lying frame then paints sky over the wall,
// the wall's bins publish a boundary far below the wall, Measured, and the last
// two cases fail.
import assert from 'node:assert/strict';
import { createRig, type World } from './w2_6Rig';

const D = Math.PI / 180;
const WALL_FROM = 84, WALL_TO = 144, WALL_TOP = 25;
const wallTop = (az: number) => (az >= WALL_FROM && az < WALL_TO ? WALL_TOP : 0);
/** Texture on a surface, the same function of direction wherever it is seen from. */
const texture = (az: number, alt: number) =>
  90 + 50 * Math.sin(az * D * 9 + alt * D * 7) + 35 * Math.sin(az * D * 17 - alt * D * 5) + 20 * Math.sin(alt * D * 23);
const inClearPatch = (az: number, alt: number) => az >= 40 && az < 220 && alt > 26;

const world: World = (az, alt) => {
  if (alt <= wallTop(az)) { const l = texture(az, alt); return { r: l, g: l * .85, b: l * .6 }; }
  if (inClearPatch(az, alt)) return { r: 105 - alt * .2, g: 160 - alt * .3, b: 235 - alt * .2 };
  // Cloud: enough structure for the overlap test (a luminance spread above its floor) and little enough for the
  // tracer's sky model to follow, which is how thin cloud looks to both.
  const l = 150 + 14 * Math.sin(az * D * 5 + alt * D * 3) + 10 * Math.sin(az * D * 13 - alt * D * 9) + 7 * Math.sin(alt * D * 17 + az * D * 4);
  return { r: l * .6, g: l * .85, b: Math.min(255, l * 1.3) };
};

let passed = 0, failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); console.log(`FAIL ${name}\n     ${(e as Error).message.split('\n')[0]}`); }
}

const rig = await createRig(world);
const { DOME_CELLS } = rig.geometry;
/** Which cell the lying frame is taken at: the ring at 27 degrees, at the east end of the wall. The ring is one the
 *  scan has to take a frame for, which a cell inside an earlier frame's footprint would not be. */
const liarCell = DOME_CELLS.filter(c => c.alt > 20 && c.alt < 35).reduce((a, b) =>
  Math.abs(b.az - 140) < Math.abs(a.az - 140) ? b : a);

/** Scan the dome from the horizon upward, one attempt per cell, with `liar` lying about the compass at the one cell. */
async function scan(liar: boolean) {
  const sweep = await rig.newSweep();
  rig.aim(6, 0);
  sweep.begin();
  const cells = [...DOME_CELLS].sort((a, b) => a.alt - b.alt || a.az - b.az);
  for (const cell of cells) {
    rig.aim(cell.az, cell.alt);
    if (liar && cell.id === liarCell.id) rig.seeing(cell.az, 58);
    rig.tick();
    rig.seeing(null);
  }
  const trace = rig.sweepModule.traceSweep(sweep);
  const truth = (bin: number) => { let top = 0; for (let az = bin * 12 + .5; az < bin * 12 + 12; az += 1) top = Math.max(top, wallTop(az)); return top; };
  const measured = trace.bins.map((b, i) => ({ bin: i, ...b })).filter(b => b.status === 'measured');
  sweep.stop();
  return { sweep, trace, measured, truth };
}

const honest = await scan(false);
const lying = await scan(true);
const WALL_BINS = [7, 8, 9, 10, 11];

await test('control: a scan with an honest compass measures most of the dome, and the wall at its height', async () => {
  assert.ok(honest.measured.length >= 15, `${honest.measured.length} bins Measured: the case measures nothing, so it holds nothing`);
  for (const bin of WALL_BINS) {
    const here = honest.measured.find(m => m.bin === bin);
    assert.ok(here, `wall bin ${bin} was not Measured`);
    assert.ok(Math.abs(here.alt - WALL_TOP) <= 3, `wall bin ${bin} reads ${here.alt}, the wall is ${WALL_TOP}`);
  }
});

await test('a compass that lies about one unchecked frame publishes no Measured bin the honest scan did not', async () => {
  const stray = lying.measured.filter(m => {
    const same = honest.measured.find(h => h.bin === m.bin);
    return !same || Math.abs(same.alt - m.alt) > 1.5;
  });
  assert.deepEqual(stray.map(b => `${b.bin}:${b.alt}`), [],
    'a bin was published Measured, or moved, because of a frame nothing had checked');
});

await test('no wall bin of the lying scan reads open sky as Measured, and the lying frame is held, not counted', async () => {
  const low = lying.measured.filter(m => WALL_BINS.includes(m.bin) && m.alt < WALL_TOP - 3);
  assert.deepEqual(low.map(b => `${b.bin}:${b.alt}`), [], 'a Measured wall bin reads open: a false open away from any step');
  assert.ok(lying.sweep.heldFrames >= 1, 'no frame was held, so the lying frame was believed');
});

console.log(`w2_6PhotosphereVariant: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log('  ' + f);
export const result = { passed, failed, total: passed + failed };
