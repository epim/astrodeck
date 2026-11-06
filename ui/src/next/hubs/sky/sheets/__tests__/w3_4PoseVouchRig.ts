// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_4PoseVouchRig.ts - the shared world and scan for the WP 3.4 (#1179) pose-vouching tests.
//
// Not a test file. Each w3_4PoseVouch*.test.ts imports it and gets its own rig: the cases were one file, and five full
// simulated scans in one file ran 15 s on a quiet desk and past the runner's 60 s per-file bound on a loaded CI box.
import { createRig, type World } from './w2_6Rig';

const D = Math.PI / 180;
const HORIZON = 15;

const texture = (az: number, alt: number) =>
  128 + 55 * Math.sin(az * D * 5 + .7) * Math.cos(alt * D * 4) + 40 * Math.sin(az * D * 11 + alt * D * 7) + 25 * Math.sin(alt * D * 13 - az * D * 3);
const ground = (az: number, alt: number) => { const l = texture(az, alt); return { r: l, g: l, b: l }; };
const SKY = { r: 110, g: 160, b: 235 };
const WALL = { r: 150, g: 140, b: 125 };
/** Textured ground under a uniform blue sky; with `walled`, between 100 and 160 degrees a uniform warm wall in place of the sky. */
let walled = false;
export function setWalled(on: boolean) { walled = on; }
const world: World = (az, alt) => alt < HORIZON ? ground(az, alt) : walled && az >= 100 && az < 160 ? WALL : SKY;

export const rig = await createRig(world);

/** One sensor sample from a phone at (az, alt): the compass, and (when `relative`) the gyro-relative stream 40 degrees behind. */
export function aim(az: number, alt: number, relative = true) {
  rig.aim(az, alt);
  rig.seeing(az, alt);
  if (!relative) return;
  const ev = new rig.window.Event('deviceorientation');
  Object.assign(ev, { alpha: (360 - (az - 40) + 720) % 360, beta: 90 + alt, gamma: 0, absolute: false });
  Object.defineProperty(ev, 'timeStamp', { value: performance.now() });
  rig.window.dispatchEvent(ev);
}

export type Sweep = Awaited<ReturnType<typeof rig.newSweep>>;
type Cell = Sweep['cells'][number];
export const capturedOf = (cells: Cell[], sweep: Sweep) => cells.filter(c => sweep.cells.find(s => s.id === c.id)!.captured).length;

/** The ring below 45 degrees scanned all round, ending at azimuth 200; then the sky cells above 50 degrees between 76 and
 *  200, westward from where the ring ended (so the last frames the images confirmed are the nearest to the first blank
 *  ones), and the zenith cap. The tracer reads a bin only when its column is painted from the zenith to the horizon. */
export async function scan(o: { relative?: boolean; idleMs?: number } = {}) {
  const relative = o.relative ?? true;
  const sweep = await rig.newSweep();
  const ring = sweep.cells.filter(c => c.alt <= 45).sort((a, b) => ((a.az - 200 + 360) % 360) - ((b.az - 200 + 360) % 360));
  const rotated = [...ring.filter(c => c.az > 200), ...ring.filter(c => c.az <= 200)];
  const sky = [...sweep.cells.filter(c => c.alt >= 50 && c.alt <= 85 && c.az >= 76 && c.az < 200).sort((a, b) => b.az - a.az), ...sweep.cells.filter(c => c.alt > 85)];
  aim(rotated[0].az, rotated[0].alt, relative);
  sweep.begin();
  for (const cell of rotated) { aim(cell.az, cell.alt, relative); rig.tick(); aim(cell.az + 5, cell.alt, relative); rig.tick(); }
  for (let i = 0; i < (o.idleMs ?? 0) / 50; i++) aim(200, 35, relative);
  const open = rig.sweepModule.traceSweep(sweep).bins.filter(b => b.status === 'unknown').length;
  const before = { open, skyCaptured: capturedOf(sky, sweep), ringCaptured: capturedOf(rotated, sweep), ring: rotated.length };
  for (const cell of sky) { aim(cell.az, cell.alt, relative); rig.tick(); aim(cell.az + 4, cell.alt, relative); rig.tick(); }
  return { sweep, sky, before };
}
