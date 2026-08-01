// Unit tests for lib/atlasFov.ts — the live "where is the scope actually
// pointing" footprint.
//
// The centrepiece is the FALSIFICATION test (§B): a scripted slew that FAILS,
// asserting the frame stays where the hardware is. A frame that arrives anyway
// is the hack this feature exists to refuse — it would make the Atlas agree
// with the plan at exactly the moment the plan stopped being true.
//
// Same tiny inline-assert harness as framing.test.ts (no vitest in this UI):
//   npx tsx src/lib/__tests__/atlasFov.test.ts

import {
  skyToView,
  angularSepDeg,
  fovCornersSky,
  pointingFov,
  pointingCaption,
  type AtlasViewGeom,
  type PointingInputs,
  type MountSample,
  type RotatorSample,
  type ViewPoint,
} from "../atlasFov";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function near(a: number, b: number, tol: number, msg = ""): void {
  if (Math.abs(a - b) > tol) throw new Error(`${msg} expected ~${b}, got ${a}`);
}
function dist(a: ViewPoint, b: ViewPoint): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

// ----------------------------------------------------------------- fixtures
// M42, a real target on a real night. The Atlas is centred on it at a 2° zoom.
const M42 = { ra_hours: 5.5881, dec_deg: -5.3911 };
const VIEW = 1000;
const ZOOM_DEG = 2;
const geom: AtlasViewGeom = {
  centerRaHours: M42.ra_hours,
  centerDecDeg: M42.dec_deg,
  pxPerDeg: VIEW / ZOOM_DEG,
  view: VIEW,
};
// An APS-C-ish rig: 0.72° × 0.48°.
const FOV_X = 0.72;
const FOV_Y = 0.48;

// Where the scope was actually parked when the user hit "go to target": well
// away from M42, and it is going to stay there in §B.
const PARKED: MountSample = { ra_hours: 3.1, dec_deg: 24.4, slewing: false };

const syncedRotator = (skyDeg: number, moving = false): RotatorSample => ({
  sky_deg: skyDeg,
  synced: true,
  moving,
});

function readout(
  mount: MountSample | null,
  rotator: RotatorSample | null,
  fovX = FOV_X,
  fovY = FOV_Y,
  g: AtlasViewGeom = geom,
) {
  const inp: PointingInputs = { mount, rotator, fovXDeg: fovX, fovYDeg: fovY };
  return pointingFov(inp, g);
}

// ======================================================== §A  projection
test("the view centre projects to the middle of the viewBox", () => {
  const p = skyToView(M42.ra_hours, M42.dec_deg, geom);
  near(p.x, VIEW / 2, 1e-6, "centre x");
  near(p.y, VIEW / 2, 1e-6, "centre y");
});

// East-LEFT / North-up is the Atlas's mapping (lib/tileView.tileMesh draws the
// survey tiles with it). Getting this backwards would glue the footprint to a
// mirrored sky — it would track pans perfectly and still be in the wrong place.
test("screen mapping is North-up / East-left, matching the survey tiles", () => {
  const east = skyToView(M42.ra_hours + 0.1 / 15, M42.dec_deg, geom); // +RA = East
  const north = skyToView(M42.ra_hours, M42.dec_deg + 0.1, geom);
  assert(east.x < VIEW / 2, `East must render LEFT of centre, got x=${east.x}`);
  near(east.y, VIEW / 2, 1, "East is horizontal");
  assert(north.y < VIEW / 2, `North must render ABOVE centre, got y=${north.y}`);
  near(north.x, VIEW / 2, 1, "North is vertical");
});

test("angularSepDeg is wrap-aware across the 0h/24h seam", () => {
  near(angularSepDeg({ ra_hours: 23.99, dec_deg: 0 }, { ra_hours: 0.01, dec_deg: 0 }),
       0.3, 1e-6, "0.02h at the equator = 0.3°");
  near(angularSepDeg(M42, M42), 0, 1e-9, "a point is 0° from itself");
});

// The whole promise of "it matches BECAUSE the hardware got there": a live
// footprint at the view centre at PA θ must land ON the planned box, which
// FovOverlay draws as `rotate(θ)` applied to a ±halfW/±halfH rectangle. If the
// two conventions ever drift, a correct slew would look wrong.
test("a centred footprint at PA θ coincides with FovOverlay's rotated rectangle", () => {
  const theta = 37;
  const r = readout({ ...M42, slewing: false }, syncedRotator(theta));
  assert(r.outline !== null, "outline must exist with optics + a synced rotator");
  const halfW = (FOV_X * geom.pxPerDeg) / 2;
  const halfH = (FOV_Y * geom.pxPerDeg) / 2;
  const t = (theta * Math.PI) / 180;
  // SVG rotate(θ) is clockwise in screen coords: [cos −sin; sin cos].
  const planned = [
    [-halfW, -halfH], [halfW, -halfH], [halfW, halfH], [-halfW, halfH],
  ].map(([x, y]) => ({
    x: VIEW / 2 + x * Math.cos(t) - y * Math.sin(t),
    y: VIEW / 2 + x * Math.sin(t) + y * Math.cos(t),
  }));
  // Same 4 points, up to which corner is listed first.
  for (const p of planned) {
    const best = Math.min(...r.outline!.map((q) => dist(p, q)));
    assert(best < 0.2, `planned corner (${p.x.toFixed(1)},${p.y.toFixed(1)}) has no live twin (nearest ${best.toFixed(2)}px)`);
  }
});

test("the outline is a proper quad: adjacent corners span the sensor's own sides", () => {
  const r = readout({ ...M42 }, syncedRotator(0));
  const o = r.outline!;
  near(dist(o[0], o[1]), FOV_X * geom.pxPerDeg, 0.5, "top edge = frame width");
  near(dist(o[1], o[2]), FOV_Y * geom.pxPerDeg, 0.5, "left edge = frame height");
  near(dist(o[2], o[3]), FOV_X * geom.pxPerDeg, 0.5, "bottom edge = frame width");
  near(dist(o[3], o[0]), FOV_Y * geom.pxPerDeg, 0.5, "right edge = frame height");
});

test("corners stay finite and RA stays in [0,24) across the 0h seam at high dec", () => {
  const corners = fovCornersSky({ ra_hours: 0.02, dec_deg: 82 }, 1.5, 1.0, 33);
  for (const c of corners) {
    assert(Number.isFinite(c.ra_hours) && Number.isFinite(c.dec_deg), "finite corner");
    assert(c.ra_hours >= 0 && c.ra_hours < 24, `RA ${c.ra_hours} out of [0,24)`);
  }
});

// ============================================ §B  IT MOVES ONLY IF HARDWARE DID
//
// The scripted failure. The user frames M42 and taps "go to this target"; the
// mount refuses/fails and never leaves the park position. Every sample below is
// what the hub actually publishes in that case — and the frame must sit on the
// park position in all of them, never on M42.
test("FALSIFICATION: a slew that fails leaves the frame where the scope is", () => {
  const timeline: MountSample[] = [
    { ...PARKED },                          // before the tap
    { ...PARKED, slewing: true },           // command accepted, nothing has moved yet
    { ...PARKED, slewing: false },          // driver error / refused — motion never happened
  ];
  const parkedPx = skyToView(PARKED.ra_hours, PARKED.dec_deg, geom);
  for (const [i, m] of timeline.entries()) {
    const r = readout(m, syncedRotator(120));
    near(r.axis!.x, parkedPx.x, 1e-9, `sample ${i} axis x`);
    near(r.axis!.y, parkedPx.y, 1e-9, `sample ${i} axis y`);
    // and emphatically NOT at the target the user asked for
    assert(dist(r.axis!, { x: VIEW / 2, y: VIEW / 2 }) > 100,
           `sample ${i}: the frame arrived at the target the mount never reached`);
  }
});

test("FALSIFICATION: an aborted slew freezes the frame at the last real position", () => {
  const half: MountSample = { ra_hours: 4.3, dec_deg: 9.5, slewing: true };
  const stopped: MountSample = { ...half, slewing: false }; // ABORT: motion ends here
  const a = readout(half, syncedRotator(120));
  const b = readout(stopped, syncedRotator(120));
  near(b.axis!.x, a.axis!.x, 1e-9, "abort must not nudge x");
  near(b.axis!.y, a.axis!.y, 1e-9, "abort must not nudge y");
  assert(a.moving && !b.moving, "the moving flag is the mount's report, not our own");
});

// The mirror image: when the hardware DOES get there, the frame gets there —
// on the sample that says so, and not one sample earlier.
test("a successful slew lands on the target only on the sample that arrives", () => {
  const track: MountSample[] = [
    { ...PARKED, slewing: true },
    { ra_hours: 4.4, dec_deg: 9.0, slewing: true },
    { ra_hours: 5.2, dec_deg: -2.0, slewing: true },
    { ...M42, slewing: false },
  ];
  const seen = track.map((m) => readout(m, syncedRotator(0)).axis!);
  const centre = { x: VIEW / 2, y: VIEW / 2 };
  for (let i = 0; i < seen.length - 1; i++) {
    assert(dist(seen[i], centre) > 10, `sample ${i} was already on target before the mount was`);
  }
  near(dist(seen[seen.length - 1], centre), 0, 1e-6, "final sample is on target");
  // strictly closing in — the frame is a readout of a real slew, not a fade
  assert(dist(seen[0], centre) > dist(seen[1], centre), "sample 1 closer than 0");
  assert(dist(seen[1], centre) > dist(seen[2], centre), "sample 2 closer than 1");
});

// Requirement 1: sky-anchored, not viewport-anchored. Panning the Atlas must
// slide the footprint across the canvas and eventually off it.
test("panning the Atlas scrolls the footprint off the view, as any sky object does", () => {
  const scope: MountSample = { ...M42 };
  const onView = readout(scope, syncedRotator(0));
  near(onView.axis!.x, VIEW / 2, 1e-6, "centred before the pan");
  assert(!onView.offView, "must not report off-view while centred");

  // pan 1.5° East of the scope (a 2° view: it goes most of the way to the edge)
  const panned = readout(scope, syncedRotator(0), FOV_X, FOV_Y, {
    ...geom,
    centerRaHours: M42.ra_hours + 1.5 / 15 / Math.cos((M42.dec_deg * Math.PI) / 180),
  });
  assert(panned.axis!.x > VIEW / 2 + 600, `expected a big shift, got x=${panned.axis!.x}`);
  near(panned.sepDeg, 1.5, 0.02, "separation tracks the pan");

  // pan further and it leaves the canvas entirely — clipped, not clamped
  const gone = readout(scope, syncedRotator(0), FOV_X, FOV_Y, {
    ...geom,
    centerRaHours: M42.ra_hours + 4 / 15 / Math.cos((M42.dec_deg * Math.PI) / 180),
  });
  assert(gone.offView, "a scope 4° outside a 2° view must report off-view");
  // panning the view East leaves the scope to its West, and West renders right
  assert(gone.axis!.x > VIEW, `clipped past the right edge, got x=${gone.axis!.x}`);
});

// ==================================================== §C  honest unknowns
test("no mount reporting: nothing is drawn, and it says why", () => {
  const r = readout(null, syncedRotator(90));
  assert(!r.known && r.axis === null && r.outline === null && r.discRPx === null,
         "an absent mount must produce no geometry at all");
  assert(/where the scope is pointing/i.test(r.caveat ?? ""), `caveat: ${r.caveat}`);
  assert(pointingCaption(r, null) === null, "no mount = no caption to write");
});

test("no optics: the axis is marked, no frame size is invented", () => {
  const r = readout({ ...M42 }, syncedRotator(90), 0, 0);
  assert(r.known && r.axis !== null, "position is still known without optics");
  assert(r.outline === null && r.discRPx === null, "no size => no footprint of any kind");
  assert(/focal length/i.test(r.caveat ?? ""), `caveat should name the missing fields: ${r.caveat}`);
});

test("no rotator: a disc the sensor fits inside, never a confident rectangle", () => {
  const r = readout({ ...M42 }, null);
  assert(r.outline === null, "an unmeasured angle must not be drawn as a rectangle");
  assert(r.paDeg === null && r.paSource === "none", "PA must read as unmeasured");
  const diagPx = (Math.hypot(FOV_X, FOV_Y) / 2) * geom.pxPerDeg;
  near(r.discRPx!, diagPx, 1e-9, "disc radius = half the frame diagonal");
  assert(/no rotator/i.test(r.caveat ?? ""), `caveat: ${r.caveat}`);
  // A rig built without a rotator is not a fault, and painting it as a warning
  // every night is how warnings stop being read.
  assert(r.caveatTone === "note", `a rig fact must not read as a fix: ${r.caveatTone}`);
});

test("the caveats a user can act on are told apart from the ones they can't", () => {
  assert(readout({ ...M42 }, syncedRotator(0), 0, 0).caveatTone === "fix", "unset optics");
  assert(readout({ ...M42 }, { sky_deg: 12, synced: false, moving: false }).caveatTone === "fix",
         "an unsynced rotator is fixable — solve and sync");
  assert(readout({ ...M42 }, syncedRotator(0)).caveatTone === null, "nothing unknown, nothing to say");
});

// The trap this task names explicitly: an unsynced rotator publishes its
// MECHANICAL angle in sky_deg (devices/base.py, offset 0 until sync). Reading it
// as a position angle would draw a frame pointing somewhere nobody measured.
test("an unsynced rotator's reading is refused, not quietly believed", () => {
  const r = readout({ ...M42 }, { sky_deg: 137, synced: false, moving: false });
  assert(r.paDeg === null, `unsynced rotator must not supply a PA, got ${r.paDeg}`);
  assert(r.outline === null, "no rectangle without a measured angle");
  assert(r.discRPx !== null, "the position is still known, so the disc is drawn");
  assert(/sync/i.test(r.caveat ?? ""), `caveat should point at syncing: ${r.caveat}`);
});

// ...and the planned PA is not smuggled in either. pointingFov has no parameter
// that could carry it; this pins that down so a future "helpful" default fails.
test("nothing in the input can supply a planned PA", () => {
  const inp = { mount: { ...M42 }, rotator: null, fovXDeg: FOV_X, fovYDeg: FOV_Y };
  const keys = Object.keys(inp);
  assert(!keys.some((k) => /target|planned|rotation_deg|pa/i.test(k)),
         `pointingFov inputs must not include a planned angle: ${keys.join(",")}`);
  assert(pointingFov(inp, geom).paDeg === null, "no rotator, no PA");
});

test("a synced rotator's angle IS the frame's angle, and it turns with it", () => {
  const angles = [0, 45, 90, 200, 359.5];
  for (const a of angles) {
    const r = readout({ ...M42 }, syncedRotator(a));
    near(r.paDeg!, a, 1e-9, `PA follows the rotator at ${a}°`);
    assert(r.paSource === "rotator", "the source is named");
  }
  // the drawn corners actually move as it turns (not just the number)
  const at0 = readout({ ...M42 }, syncedRotator(0)).outline!;
  const at90 = readout({ ...M42 }, syncedRotator(90)).outline!;
  assert(Math.min(...at90.map((q) => dist(at0[0], q))) > 10,
         "a 90° rotation must move the corners");
});

// A truncated status payload is an unknown angle, not a zero one. Drawing NaN
// corners would put an invisible path in the DOM and claim nothing; drawing 0°
// would claim north-up on no evidence.
test("a synced rotator with a non-finite angle reads as unknown, not zero", () => {
  const r = readout({ ...M42 }, { sky_deg: Number.NaN, synced: true, moving: false });
  assert(r.paDeg === null && r.outline === null, "NaN must not become a drawn angle");
  assert(r.discRPx !== null, "position is still known");
  assert((r.caveat ?? "").length > 0, "an unknown orientation must say so");
});

test("a rotator sweeping past 360 wraps instead of running away", () => {
  const r = readout({ ...M42 }, syncedRotator(-15));
  near(r.paDeg!, 345, 1e-9, "negative sky_deg wraps into [0,360)");
});

test("moving is the hardware's own report — mount OR rotator", () => {
  assert(readout({ ...M42, slewing: true }, syncedRotator(0)).moving, "mount slewing");
  assert(readout({ ...M42 }, syncedRotator(0, true)).moving, "rotator turning");
  assert(!readout({ ...M42 }, syncedRotator(0)).moving, "idle rig is idle");
});

// ======================================================== §D  the caption
test("caption carries the angle and the position, not a description of the box", () => {
  const r = readout({ ...M42 }, syncedRotator(142));
  const c = pointingCaption(r, "05h35m17s +22°01′") ?? "";
  assert(c.includes("142"), `PA must appear: ${c}`);
  assert(c.includes("05h35m17s"), `formatted position must appear: ${c}`);
});

test("caption says the frame is a readout while the mount is moving", () => {
  const c = pointingCaption(readout({ ...M42, slewing: true }, syncedRotator(10)), null) ?? "";
  assert(/only when the mount does/i.test(c), `slewing caption: ${c}`);
});

test("caption reports the distance when the scope has scrolled off the map", () => {
  const far = readout({ ra_hours: M42.ra_hours + 0.4, dec_deg: M42.dec_deg }, syncedRotator(0));
  assert(far.offView, "fixture must actually be off-view");
  const c = pointingCaption(far, null) ?? "";
  assert(/off the edge/i.test(c) && /°|′/.test(c), `off-view caption should give a distance: ${c}`);
});

test("caption drops the position rather than inventing one when unformatted", () => {
  const c = pointingCaption(readout({ ...M42 }, syncedRotator(5)), null) ?? "";
  assert(!c.includes("("), `no empty parens: ${c}`);
  assert(c.includes("PA 5"), `still carries what it does know: ${c}`);
});

// ============================================ §E  the wiring, not just the math
//
// Everything above proves the FUNCTION cannot cheat. This block proves the
// CALLERS don't: the compiler cannot see "the footprint is never fed from the
// target", so the source is read instead — same idiom, and same reason, as
// lazyViews.test.ts reading App.tsx. If someone later "helpfully" seeds the
// pointing from the framing session so the box looks right immediately, this
// fails, which is the entire point of the feature.
interface NodeFsLike {
  readFileSync(path: string, encoding: string): string;
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;
const pathOf = (rel: string): string =>
  decodeURIComponent(new URL(rel, import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, "$1");

const canvasSrc = fs.readFileSync(pathOf("../../components/atlas/SkyCanvas.tsx"), "utf8");
const atlasSrc = fs.readFileSync(pathOf("../../views/AtlasView.tsx"), "utf8");

test("SkyCanvas builds the footprint from telemetry props alone", () => {
  const call = /pointingFov\(\s*\{([^}]*)\}/.exec(canvasSrc);
  assert(call !== null, "SkyCanvas must call pointingFov with an inputs object");
  const args = call![1];
  assert(/mount:\s*pointing\b/.test(args), `mount must come from the pointing prop: ${args}`);
  assert(!/rotationDeg|catalogTarget|\btarget\b/.test(args),
         `the framed target/PA must never reach the live footprint: ${args}`);
});

test("AtlasView feeds the footprint from status.mount, never from the session", () => {
  assert(/pointing=\{statusMount\}/.test(atlasSrc),
         "SkyCanvas's pointing prop must be the live mount status");
  assert(/statusMount[\s\S]{0,400}s\.status\?\.mount/.test(atlasSrc),
         "statusMount must be selected from the status feed");
  assert(!/pointing=\{[^}]*\b(center|target|framing)\b/.test(atlasSrc),
         "pointing must not be sourced from the framing session");
});

// ----------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\natlasFov.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
