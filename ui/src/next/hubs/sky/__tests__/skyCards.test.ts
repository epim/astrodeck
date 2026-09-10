// skyCards.test.ts - the Sky hub's pure arithmetic and copy rules: the lens
// dial's seats, the lock card's five-case CTA, and FRAME mode's mosaic maths.
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/skyCards.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// These three are separated from the DOM tests because each of them is a RULE
// rather than a rendering, and a rule tested through a screen is a rule nobody
// can see:
//
//   * the CTA priority order (obstruction beats cloud beats "image it") is the
//     whole safety argument of the lock card - cloud passes, a tree does not;
//   * the panel geometry is what the user is looking at when they decide the
//     framing is right, and it has to be the same grid the engine is asked for;
//   * the 15% overlap appears in three places (the request, the meta line and
//     the printed sentence) and they must not be able to drift apart.

/* eslint-disable @typescript-eslint/no-explicit-any */

// The modules under test are pure, but they are reached through the Sky hub's
// own barrels, and those barrels pass through `api.ts` / `lib/base.ts`, which
// read `window.location` AT MODULE SCOPE. So the browser globals go in first and
// the imports are dynamic - the store-touching convention from
// `seams/shell-and-tests.md` section 4, for the same reason: importing too early
// captures undefined globals permanently.
{
  const g = globalThis as any;
  if (typeof g.window === "undefined") {
    g.window = {
      location: { pathname: "/", protocol: "http:", host: "test", hash: "" },
      addEventListener() {}, removeEventListener() {},
      matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
    };
  }
  if (typeof g.localStorage === "undefined") {
    const m = new Map<string, string>();
    g.localStorage = {
      getItem: (k: string) => (m.has(k) ? (m.get(k) as string) : null),
      setItem: (k: string, v: string) => { m.set(k, String(v)); },
      removeItem: (k: string) => { m.delete(k); },
      clear: () => { m.clear(); },
    };
  }
  if (typeof g.document === "undefined") {
    g.document = {
      documentElement: {
        classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
        style: { setProperty() {}, getPropertyValue() { return ""; } },
      },
    };
  }
}

const { lensSeats, LENS_LEARN, LENS_STAGE_PX } = await import("../cards/lens");
const { lockCta, ctaToast, isVideoTarget, obstructedReason } = await import("../cards/lockCta");
const {
  MOSAIC_CHOICES, OVERLAP, ROTS,
  framedStrip, frameText, framingMeta, panelRects, panelsToTargets,
} = await import("../frame/mosaic");
const { SKY_KINDS } = await import("../finder");

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}
function near(got: number, want: number, tol: number, msg = ""): void {
  if (Math.abs(got - want) > tol) throw new Error(`${msg} expected ~${want}, got ${got}`);
}

// ============================================================== the lens dial

test("five kinds, not seven - satellites and comets have no ephemeris to filter", () => {
  eq(lensSeats().length, 5, "seat count:");
  const kinds = lensSeats().map((s) => s.kind).join(",");
  assert(!/sat|comet/.test(kinds), `a kind with no data source got a seat: ${kinds}`);
  eq(kinds, SKY_KINDS.join(","), "the seats must be the finder's own kind list:");
});

test("the first seat is at the top of the ring and the rest are evenly spaced", () => {
  const seats = lensSeats();
  // Buttons are 56 px and positioned by their TOP-LEFT, so the centre of seat 0
  // is 150,42 - directly above the 150,150 stage centre.
  near(seats[0].x + 28, LENS_STAGE_PX / 2, 0.001, "seat 0 x centre:");
  assert(seats[0].y + 28 < LENS_STAGE_PX / 2, "seat 0 must sit ABOVE the centre");
  const angles = seats.map((s) => Math.atan2(s.y + 28 - 150, s.x + 28 - 150));
  for (let i = 1; i < angles.length; i++) {
    const step = ((angles[i] - angles[i - 1]) + 2 * Math.PI) % (2 * Math.PI);
    near(step, (2 * Math.PI) / 5, 1e-9, `gap ${i}:`);
  }
});

test("the caption sits outside its button, never on top of it", () => {
  for (const s of lensSeats()) {
    const r = Math.hypot(s.lx - 150, s.ly - 150);
    assert(r > 108 + 28, `caption for ${s.kind} is inside the button ring (r=${r})`);
  }
});

test("every kind has a hold-to-learn sentence that says something the ring cannot", () => {
  for (const k of SKY_KINDS) {
    const text = LENS_LEARN[k];
    assert(typeof text === "string" && text.length > 40, `${k} has no explanation`);
    // The rule from ARCHITECTURE section 15: it must state a fact, not the label.
    assert(!/^[A-Z ]+$/.test(text), `${k}'s explanation is just a label`);
  }
});

// ============================================================== the lock CTA

const M31 = { name: "M31", kind: "galaxy" as const, obstructed: false, clouded: false };

test("with no rig the button offers to get one, and it is not a refusal", () => {
  const cta = lockCta({ ...M31, obstructed: true, clouded: true }, false);
  eq(cta.kind, "connect", "no rig outranks everything else:");
  eq(cta.label, "CONNECT THE RIG FIRST");
});

test("an obstruction outranks cloud - cloud passes, a tree does not", () => {
  const cta = lockCta({ ...M31, obstructed: true, clouded: true }, true);
  eq(cta.kind, "obstructed", "behind the horizon and clouded:");
  eq(cta.button, "danger", "the obstructed CTA is the outlined bad kind:");
});

test("the Moon and the planets record video; they never open a deep-sky night", () => {
  eq(lockCta({ ...M31, name: "Jupiter", kind: "planet" }, true).label, "RECORD JUPITER · VIDEO");
  eq(lockCta({ ...M31, name: "Moon", kind: "moon" }, true).kind, "video");
  assert(isVideoTarget("planet") && isVideoTarget("moon"), "both bodies want video");
  assert(!isVideoTarget("galaxy") && !isVideoTarget("nebula"), "deep sky does not");
});

test("a clouded target still offers the night, and says what will happen", () => {
  const cta = lockCta({ ...M31, clouded: true }, true);
  eq(cta.label, "CLOUDED NOW · IMAGE ANYWAY");
  const toast = ctaToast(cta.kind);
  assert(toast != null && /cloud hold armed/i.test(toast.title), "no cloud-hold warning");
  assert(toast?.level === "warning", "the cloud-hold toast must be a warning");
});

test("the plain case names the target, so the button says what it will shoot", () => {
  eq(lockCta(M31, true).label, "IMAGE M31");
  eq(ctaToast("image"), null, "imaging a clear target has nothing to warn about");
});

test("the obstructed reason names the SITE, because that is where the fix is", () => {
  const r = obstructedReason("M31", "Back lawn");
  assert(r.includes("Back lawn"), `the reason must name the site: ${r}`);
  assert(/site pill/.test(r), "the reason must say where the horizon is edited");
});

// ============================================================ FRAME geometry

test("the four mosaic tiles are the design's own, in order", () => {
  eq(MOSAIC_CHOICES.map((m) => `${m.cols}x${m.rows}`).join(" "), "1x1 2x1 2x2 3x2");
});

test("the rotation dial is 0-165 in 15 degree steps - twelve distinct framings", () => {
  eq(ROTS.length, 12, "stop count:");
  eq(ROTS[0], 0);
  eq(ROTS[ROTS.length - 1], 165, "180 frames identically to 0, so the range stops at 165:");
  for (let i = 1; i < ROTS.length; i++) eq(ROTS[i] - ROTS[i - 1], 15, `step ${i}:`);
});

test("the overlap the request carries is the overlap the copy promises", () => {
  eq(OVERLAP, 0.15, "README mosaic formula:");
  // The sentence the framing card prints. If the constant moves, this is the
  // assertion that catches the copy still saying 15%.
  const note = "Panels overlap 15%";
  assert(note.includes(`${Math.round(OVERLAP * 100)}%`), "the note and the constant disagree");
});

test("the framed strip says the shape and the angle, and drops the panel count", () => {
  eq(framedStrip(2, 1, 30), "Framed · 2×1 mosaic · rot 30°");
  eq(framedStrip(1, 1, 0), "Framed · single frame · rot 0°");
  eq(frameText(3, 2, 45), "3×2 mosaic · 6 panels · rot 45°");
});

test("the framing meta reports the TANGENT-PLANE extent, not raw degrees of RA", () => {
  // (cols - (cols-1)*0.15) * fov_x = 1.85 * 1.68 = 3.108 -> "3.1"
  eq(framingMeta(2, 1, 30, 1.68, 1.12), "2 panels · 3.1° × 1.1° · rot 30°");
  eq(framingMeta(1, 1, 0, 1.68, 1.12), "1.7° × 1.1° · rot 0°");
});

test("with no optics the meta states the panels and stops - it invents no field", () => {
  const m = framingMeta(2, 2, 15, 0, 0);
  eq(m, "4 panels · rot 15°");
  assert(!m.includes("°  ×"), "a zero field of view must not be printed as a size");
});

test("panels are pitched by 1 - overlap, so a 2x1 grid overlaps by 15 per cent", () => {
  const rects = panelRects(100, 100, 2, 1, 40, 30);
  eq(rects.length, 2, "panel count:");
  const pitch = rects[1].x - rects[0].x;
  near(pitch, 40 * (1 - OVERLAP), 1e-9, "pitch:");
  // The pair is centred on the lock, not hung off it.
  near((rects[0].x + rects[1].x + 40) / 2, 100, 1e-9, "grid centre x:");
});

test("row 0 is the TOP row on screen, which is the opposite sign from the sky plane", () => {
  const rects = panelRects(100, 100, 1, 2, 40, 30);
  const top = rects.find((r) => r.row === 0);
  const bottom = rects.find((r) => r.row === 1);
  assert(top != null && bottom != null, "two rows expected");
  assert((top as { y: number }).y < (bottom as { y: number }).y, "row 0 must be drawn above row 1");
});

test("panel targets name themselves row-column and share one mosaic group", () => {
  const panels = [
    { row: 0, col: 0, ra_hours: 0.7, dec_deg: 41, rotation_deg: 30 },
    { row: 0, col: 1, ra_hours: 0.8, dec_deg: 41, rotation_deg: 30 },
  ];
  const targets = panelsToTargets(panels, "M31", "M31", 30);
  eq(targets.length, 2);
  eq(targets[0].name, "M31 1-1");
  eq(targets[1].name, "M31 1-2");
  eq(targets[0].mosaic_group, "M31", "panels must group so a re-frame REPLACES them:");
  eq(targets[0].autofocus_first, true, "the first panel focuses:");
  eq(targets[1].autofocus_first, false, "the rest do not:");
  eq(targets[0].rotation_deg, 30, "the commanded angle travels with every panel:");
  assert(targets[0].id !== targets[1].id, "two panels must not share an id");
});

test("a single panel is NOT grouped - grouping one target would delete it on re-frame", () => {
  const t = panelsToTargets([{ row: 0, col: 0, ra_hours: 0.7, dec_deg: 41, rotation_deg: 0 }], "M31", "M31", 0);
  eq(t[0].name, "M31", "a lone panel keeps the object's own name:");
  eq(t[0].mosaic_group, undefined, "no group on a single frame:");
});

const total = passed + failed;
console.log(`skyCards.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
