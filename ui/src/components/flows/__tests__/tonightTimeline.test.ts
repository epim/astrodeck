// tonightTimeline.test.ts — the rules that decide what the Tonight timeline
// draws, and more importantly what it REFUSES to draw.
//   Run:  npx tsx src/components/flows/__tests__/tonightTimeline.test.ts   (from ui/)
//
// Two kinds of assertion, and the second kind is the point.
//
// The first kind pins the placement rule: the axis spans the server's own
// dusk→dawn with a layout gutter, twilight is the two gaps between dusk/dark
// and dark-end/dawn, and an altitude sample lands where its degrees say.
//
// The second kind pins ABSENCE. Every one of these is a case where the server
// legitimately returns null and the prototype drew something anyway — a moon
// bar running to the edge of a night nobody measured, a flats block for a
// window that could not be parsed, an equal share of the sky for a target with
// no coordinates. A timeline that invents those renders beautifully and lies,
// and nothing about it fails. Only a test that asserts the element is MISSING
// can see it.
/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- globals first
// The module under test reaches only React's jsx runtime and lib/visibility, but
// the flows test idiom installs these before the dynamic import so a future
// import that does touch lib/base.ts (which reads window.location at module
// scope) does not turn this file red for an unrelated reason.
(globalThis as any).window = {
  location: { pathname: "/", origin: "http://local" },
};
(globalThis as any).localStorage = {
  getItem: () => null, setItem() {}, removeItem() {},
};

const { timelineGeometry, moonPercent, TL_W } = await import("../TonightTimeline");
type TonightTimelineProps = import("../TonightTimeline").TonightTimelineProps;
type TlGeometry = import("../TonightTimeline").TlGeometry;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function near(a: number, b: number, eps: number, msg: string): void {
  assert(Math.abs(a - b) <= eps, `${msg} (got ${a}, want ~${b})`);
}

// A fixture night: dusk at an arbitrary instant, dawn eight hours later, with
// astronomical dark starting 40 min after dusk and ending 40 min before dawn.
const DUSK = 1_700_000_000;
const H = 3600;
const DAWN = DUSK + 8 * H;
const DARK_START = DUSK + 40 * 60;
const DARK_END = DAWN - 40 * 60;

/** X of an instant under the rule the geometry uses, for expectations. */
function expectX(t: number): number {
  const pad = (DAWN - DUSK) * 0.03;
  return ((t - (DUSK - pad)) / (DAWN - DUSK + 2 * pad)) * TL_W;
}

function props(over: Partial<TonightTimelineProps> = {}): TonightTimelineProps {
  return {
    night: {
      dusk_unix: DUSK, dawn_unix: DAWN,
      dark_start_unix: DARK_START, dark_end_unix: DARK_END,
    },
    flats: null,
    moon: null,
    targets: [],
    ...over,
  };
}

function geo(over: Partial<TonightTimelineProps> = {}): TlGeometry {
  const g = timelineGeometry(props(over));
  if (!g) throw new Error("expected geometry, got null");
  return g;
}

const rectFor = (g: TlGeometry, key: string) => g.rects.find((r) => r.key === key);
const dashFor = (g: TlGeometry, key: string) => g.dashes.find((d) => d.key === key);
const labelText = (g: TlGeometry, key: string) =>
  g.labels.find((l) => l.key === key)?.text;

// ───────────────────────────────────────────────────────────── the axis rule

test("no dusk and no dawn is null, not an empty axis", () => {
  assert(timelineGeometry(props({
    night: { dusk_unix: null, dawn_unix: null, dark_start_unix: null, dark_end_unix: null },
  })) === null, "a night with no bounds has no timeline");
  assert(timelineGeometry(props({
    night: { dusk_unix: DAWN, dawn_unix: DUSK, dark_start_unix: null, dark_end_unix: null },
  })) === null, "dawn before dusk is not a span");
});

test("the axis spans dusk to dawn with a gutter at each end", () => {
  const g = geo();
  const pad = (DAWN - DUSK) * 0.03;
  near(g.t0, DUSK - pad, 0.5, "t0 sits one gutter before dusk");
  near(g.t1, DAWN + pad, 0.5, "t1 sits one gutter after dawn");
  // The gutter exists so the DUSK and DAWN labels, centred on their rules, are
  // not half off the edge of the label layer.
  near(dashFor(g, "dusk")!.x, expectX(DUSK), 0.01, "DUSK sits just inside the left edge");
  near(dashFor(g, "dawn")!.x, expectX(DAWN), 0.01, "DAWN sits just inside the right edge");
});

test("hour ticks are whole clock hours, labelled from the operator's clock", () => {
  const g = geo();
  const hours = g.labels.filter((l) => l.key.startsWith("hour-"));
  assert(hours.length === g.hourTicks.length, "one label per hour tick");
  assert(hours.length >= 7 && hours.length <= 9,
    `an eight-hour night carries 8ish whole hours, got ${hours.length}`);
  assert(hours.every((l) => /^\d{2}:00$/.test(l.text)),
    `every hour label is HH:00, got ${hours.map((l) => l.text).join(",")}`);
});

// ─────────────────────────────────────────────────────────────── twilight

test("twilight is the two gaps the server actually reported", () => {
  const g = geo();
  const l = rectFor(g, "tw-l")!;
  const r = rectFor(g, "tw-r")!;
  near(l.x, expectX(DUSK), 0.01, "the left band starts at dusk");
  near(l.x + l.w, expectX(DARK_START), 0.01, "…and ends at astronomical dark");
  // dark_end_unix is returned by the server and the prototype drew nothing with
  // it, starting its right-hand band at dawn instead (contract E.5).
  near(r.x, expectX(DARK_END), 0.01, "the right band starts when dark ends");
  near(r.x + r.w, expectX(DAWN), 0.01, "…and ends at dawn");
});

test("no dark boundaries means no twilight bands invented around them", () => {
  const g = geo({
    night: { dusk_unix: DUSK, dawn_unix: DAWN, dark_start_unix: null, dark_end_unix: null },
  });
  assert(!rectFor(g, "tw-l") && !rectFor(g, "tw-r"), "neither band is drawn");
  assert(!dashFor(g, "dark"), "and there is no DARK rule to put a label on");
});

// ─────────────────────────────────────────────────────────────────── moon

test("a moon with neither crossing known draws no bar at all", () => {
  // The server searches for crossings only inside [dark_start, dark_end], so a
  // moon already up at dark start returns rise AND set null while the story
  // says "up all night" — which is indistinguishable here from a moon that
  // never rose. A full-width bar would assert a moon-up night nobody measured.
  const g = geo({ moon: { illumination: 0.71, rise_unix: null, set_unix: null } });
  assert(!rectFor(g, "moon"), "no moon bar");
  assert(!dashFor(g, "moonrise"), "and no moonrise rule");
});

test("a known moonrise runs to the end of the span and carries the illumination", () => {
  const rise = DUSK + 3 * H;
  const g = geo({ moon: { illumination: 0.71, rise_unix: rise, set_unix: null } });
  const bar = rectFor(g, "moon")!;
  near(bar.x, expectX(rise), 0.01, "the bar starts at moonrise");
  near(bar.x + bar.w, TL_W, 0.01, "and does not claim a set it was not told");
  assert(labelText(g, "moonrise-label") === "☾ 71%",
    `the rule is labelled from moon.illumination, got ${labelText(g, "moonrise-label")}`);
});

test("a known moonset with no rise starts at the span's edge", () => {
  const set = DUSK + 2 * H;
  const g = geo({ moon: { illumination: null, rise_unix: null, set_unix: set } });
  const bar = rectFor(g, "moon")!;
  near(bar.x, 0, 0.01, "the moon was already up when the span opened");
  near(bar.x + bar.w, expectX(set), 0.01, "and the set is the one measured instant");
  assert(!dashFor(g, "moonrise"), "no rise, so no moonrise rule");
});

test("moonPercent reports nothing rather than a stand-in", () => {
  assert(moonPercent(null) === null, "no moon block, no percentage");
  assert(moonPercent({ illumination: null, rise_unix: null, set_unix: null }) === null,
    "no illumination, no percentage");
  assert(moonPercent({ illumination: 0.706, rise_unix: null, set_unix: null }) === 71,
    "a real fraction rounds to whole percent");
});

// ────────────────────────────────────────────────────────────────── flats

test("a flats window with either bound missing draws nothing", () => {
  const half = geo({ flats: { start_unix: DUSK + 600, end_unix: null } });
  assert(!rectFor(half, "flats"), "an unparseable window has no clock time to draw");
  const both = geo({
    flats: { start_unix: DUSK + 600, end_unix: DUSK + 1500 },
  });
  const block = rectFor(both, "flats")!;
  near(block.x, expectX(DUSK + 600), 0.01, "…and a parsed one is drawn at its own times");
  assert(labelText(both, "flats-label") === "FLATS", "labelled FLATS in body ink");
});

// ──────────────────────────────────────────────────────────────── targets

test("an unplaceable target gets no block, no arc and no flip rule", () => {
  // `resolved: false` comes back with window null and an empty curve, and the
  // prototype would still have given it an equal share of the imaging band.
  const g = geo({
    targets: [{
      label: "NGC 6946", window: null, curve: [],
      meridian_flip_unix: DUSK + 4 * H,
    }],
  });
  assert(g.rects.every((r) => !r.key.startsWith("tgt-")), "no imaging block");
  assert(g.paths.length === 0, "no altitude arc");
  assert(!dashFor(g, "flip-0"), "and no meridian flip in a night it never enters");
});

test("a placed target is drawn at its own window, labelled, with its own curve", () => {
  const s = DUSK + 1 * H;
  const e = DUSK + 6 * H;
  const g = geo({
    targets: [{
      label: "M16",
      window: { start_unix: s, end_unix: e },
      // Includes samples outside the window: the arc is clipped to the block the
      // legend ties it to.
      curve: [[DUSK, 5], [s, 30], [s + 2 * H, 90], [e, 30], [DAWN, 5]],
      meridian_flip_unix: s + 2 * H,
    }],
  });
  const block = rectFor(g, "tgt-0")!;
  near(block.x, expectX(s), 0.01, "the block starts when the window opens");
  near(block.x + block.w, expectX(e), 0.01, "and ends when it closes");
  assert(labelText(g, "tgt-0-label") === "M16", "labelled with the server's short name");
  near(dashFor(g, "flip-0")!.x, expectX(s + 2 * H), 0.01, "the flip rule is placed");

  const pts = g.paths[0].d.replace(/^M/, "").split("L").map((p) => p.split(" ").map(Number));
  assert(pts.length === 3, `only the three in-window samples are drawn, got ${pts.length}`);
  // 0° at the band's foot (y=106), 90° at its head (y=36).
  near(pts[1][1], 36, 0.05, "the 90° sample sits at the top of the band");
  near(pts[0][1], 36 + 70 * (1 - 30 / 90), 0.05, "and 30° two-thirds of the way down");
});

test("a single-sample curve draws no arc", () => {
  const s = DUSK + 1 * H;
  const g = geo({
    targets: [{
      label: "M16", window: { start_unix: s, end_unix: s + H },
      curve: [[s, 40]], meridian_flip_unix: null,
    }],
  });
  assert(g.paths.length === 0, "one point is not a line");
});

// ──────────────────────────────────────────────────────────────── labels

test("every rule that is drawn is labelled, and none that is not", () => {
  const g = geo();
  assert(labelText(g, "dusk-label") === "DUSK", "DUSK");
  assert(labelText(g, "dark-label") === "DARK", "DARK");
  assert(labelText(g, "dawn-label") === "DAWN", "DAWN");
  assert(g.dashes.every((d) => g.labels.some((l) => l.key === `${d.key}-label`)),
    "no unlabelled dashed rule");
});

// eslint-disable-next-line no-console
console.log(`\ntonightTimeline: ${passed} passed, ${failed} failed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

const total = passed + failed;
export const result = { passed, failed, total };
