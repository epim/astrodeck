// flowWire.test.ts — the wire layer's decisions, without a DOM.
//   Run:  npx tsx src/components/flows/__tests__/flowWire.test.ts   (from ui/)
//
// Three kinds of assertion, and the second and third are the ones that would
// otherwise fail silently.
//
// 1. LANE AND STATE. Which colour, width and dash a wire gets. Getting this
//    wrong is visible, so it is the cheap half.
//
// 2. THE SILHOUETTE RULE. Status on this surface is never colour alone: the
//    night palette pulls --accent and --warn toward one another and
//    prefers-reduced-motion stops the march, so the DASH PATTERN is what still
//    says flow-vs-event and live-vs-resting. A change that made two states share
//    a pattern would look fine in a screenshot taken in day mode and lose the
//    distinction entirely for the reader who needs it most.
//
// 3. THE REMOVE CONTROL SITS ON THE CURVE. §C.6 claims the straight-line
//    midpoint of the two port anchors IS B(0.5) of the bezier. That is an
//    identity for the two path forms we emit, but it is an identity about a
//    string produced three files away — so the test parses the path `edgePath`
//    actually returned, evaluates the cubic at t=0.5, and compares. If anyone
//    ever makes the control offsets asymmetric, the ✕ drifts off its wire and
//    nothing else notices.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- globals first
// FlowWireLayer imports the store, and store.ts -> lib/base.ts reads
// `window.location.pathname` AT MODULE SCOPE to derive the relay mount, while
// the store body reads localStorage and the documentElement class list for the
// night/touch prefs. So the import has to happen after those exist, which means
// a dynamic import. Nothing here fakes behaviour the tests then assert on — no
// request is made and no component is rendered.
(globalThis as any).window = {
  location: { pathname: "/", origin: "http://local" },
  addEventListener() {}, removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
};
(globalThis as any).localStorage = {
  getItem: () => null, setItem() {}, removeItem() {},
};
(globalThis as any).document = {
  documentElement: {
    classList: { add() {}, remove() {}, toggle() {} },
    style: { setProperty() {} },
  },
  addEventListener() {}, removeEventListener() {},
};
(globalThis as any).matchMedia = (globalThis as any).window.matchMedia;

const {
  wireLane, wireAnchors, wireMidpoint, wireStroke, wireWidth, wireDash,
  isRunning, isWireActive, HIT_W_CANVAS, HIT_W_AUTO,
} = await import("../FlowWireLayer");
const { edgePath } = await import("../geometry");
type FlowNodeRec = import("../flowsTypes").FlowNodeRec;
type FlowEdgeRec = import("../flowsTypes").FlowEdgeRec;
type Point = import("../geometry").Point;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// The M16 pair §D.3 works out by hand: dusk at (30,50), dome at (260,50).
const node = (id: string, type: string, x: number, y: number): FlowNodeRec =>
  ({ id, type: type as FlowNodeRec["type"], x, y, params: {} });
const edge = (from: string, fromPort: string, to: string, toPort: string): FlowEdgeRec =>
  ({ id: "e1", from, fromPort, to, toPort });

const DUSK = node("n1", "dusk", 30, 50);
const DOME = node("n16", "dome", 260, 50);
// CLOUD WATCH's "clouds in" is an EVENT output; HOLD / RESUME's "pause" is an
// event input. This is the pair capture 02 draws in amber.
const CLOUD = node("n8", "cloudwatch", 200, 400);
const HOLD = node("n9", "holdresume", 500, 420);

// ───────────────────────────────────────────────────────────────────── lanes

test("the lane is the SOURCE port's kind", () => {
  const nodes = [DUSK, DOME, CLOUD, HOLD];
  assert(wireLane(edge("n1", "window", "n16", "run"), nodes) === "flow",
    "dusk's 'window opens' is a flow output, so the wire is a flow wire");
  assert(wireLane(edge("n8", "in", "n9", "pause"), nodes) === "event",
    "cloud watch's 'clouds in' is an event output, so the wire is an event wire");
});

test("an unresolvable source falls back to the flow lane, not to nothing", () => {
  // The prototype's `|| 'flow'`. It never shows on screen — wireAnchors drops
  // the same edge — but a lane function that returned undefined would push a
  // `stroke="undefined"` into the SVG rather than a colour.
  assert(wireLane(edge("ghost", "window", "n16", "run"), [DUSK, DOME]) === "flow",
    "a wire from a node that is gone still names a lane");
  assert(wireLane(edge("n1", "notaport", "n16", "run"), [DUSK, DOME]) === "flow",
    "a wire from a port the vocabulary dropped still names a lane");
});

// ─────────────────────────────────────────────────────────────────── anchors

test("the anchors are geometry's, and reproduce §D.3's worked M16 edge", () => {
  const a = wireAnchors(edge("n1", "window", "n16", "run"), [DUSK, DOME], "desktop");
  assert(a !== null, "both ends resolve");
  assert(a!.p1.x === 218 && a!.p1.y === 97,
    `dusk's out anchor is (218,97), got (${a!.p1.x},${a!.p1.y})`);
  assert(a!.p2.x === 260 && a!.p2.y === 97,
    `dome's in anchor is (260,97), got (${a!.p2.x},${a!.p2.y})`);
});

test("a phone card is 150 wide, so the OUT anchor moves with the tier", () => {
  // The out anchor is `node.x + nodeW`. A layer rendered at the wrong tier
  // detaches every outgoing wire from its dot by 38px with nothing failing.
  const a = wireAnchors(edge("n1", "window", "n16", "run"), [DUSK, DOME], "phone");
  assert(a!.p1.x === 180, `expected 30+150, got ${a!.p1.x}`);
  assert(a!.p2.x === 260, "the IN anchor is the card's left edge, tier-independent");
});

test("an edge naming a node that is gone yields null, never a plausible anchor", () => {
  // A saved graph can reference a deleted node. geometry.portPos() refuses to
  // guess; so must this, because a wire anchored 20px high sits inside the card
  // header and reads as a deliberate connection.
  assert(wireAnchors(edge("ghost", "window", "n16", "run"), [DUSK, DOME], "desktop") === null,
    "missing source node");
  assert(wireAnchors(edge("n1", "window", "ghost", "run"), [DUSK, DOME], "desktop") === null,
    "missing target node");
  assert(wireAnchors(edge("n1", "gone", "n16", "run"), [DUSK, DOME], "desktop") === null,
    "missing source port");
  assert(wireAnchors(edge("n1", "window", "n16", "gone"), [DUSK, DOME], "desktop") === null,
    "missing target port");
});

test("auto-graph positions override the stored ones, per node", () => {
  // The phone FLOW tab lays the graph out and does NOT write it back, so the
  // wire has to be anchored at the LAID-OUT position while the record keeps the
  // canvas one.
  const pos: Record<string, Point> = { n1: { x: 14, y: 18 } };
  const a = wireAnchors(edge("n1", "window", "n16", "run"), [DUSK, DOME], "phone", pos);
  assert(a!.p1.x === 164 && a!.p1.y === 65,
    `laid-out dusk anchors at (14+150, 18+47) = (164,65), got (${a!.p1.x},${a!.p1.y})`);
  assert(a!.p2.x === 260,
    "a node the map does not name keeps its stored coordinates rather than "
    + "collapsing to (0,0)");
});

// ──────────────────────────────────────────────────────────── run state

test("holding and stopping are still running — capture 07 is the hold", () => {
  assert(isRunning("running"), "running");
  assert(isRunning("holding"),
    "capture 07 IS the cloud hold and its wires march; a reading that stopped "
    + "the march there would photograph a dead graph in the one state the "
    + "harness waits 180s to reach");
  assert(isRunning("stopping"), "a stop in flight has not stopped");
  assert(!isRunning("idle"), "idle");
});

test("a finished stage's outgoing wires keep marching", () => {
  // `ok` counts, not just `busy`. That is what lights the whole
  // dusk → dome → duskflats → target → slew chain at once in capture 07.
  assert(isWireActive(true, "busy"), "busy");
  assert(isWireActive(true, "ok"), "ok — deliberate, §C.6");
  assert(!isWireActive(true, "idle"), "idle");
  assert(!isWireActive(true, "warn"), "warn is not a run cursor");
  assert(!isWireActive(true, "bad"), "bad is not a run cursor");
  assert(!isWireActive(false, "busy"),
    "a stale busy status left over from a finished run must not animate a "
    + "graph that is not running");
});

// ────────────────────────────────────────────────────────── colour and width

test("idle wires are the lane colour at 45%, active ones are full strength", () => {
  assert(wireStroke("flow", false, false)
    === "color-mix(in srgb, var(--accent) 45%, transparent)", "idle flow");
  assert(wireStroke("event", false, false)
    === "color-mix(in srgb, var(--warn) 45%, transparent)", "idle event");
  assert(wireStroke("flow", true, false) === "var(--accent)", "active flow");
  assert(wireStroke("event", true, false) === "var(--warn)", "active event");
});

test("every stroke is a token — a hex here would not survive night mode", () => {
  const all = [
    wireStroke("flow", false, false), wireStroke("event", false, false),
    wireStroke("flow", true, false), wireStroke("event", true, false),
    wireStroke("flow", false, true), wireStroke("event", true, true),
  ];
  all.forEach((s) => assert(/var\(--/.test(s) && !/#[0-9a-f]{3}/i.test(s),
    `stroke "${s}" must ride a CSS custom property; night remaps every one of `
    + "these and a literal colour would stay day-bright over a dark-adapted eye"));
});

test("selection outranks the lane and outranks activity", () => {
  assert(wireStroke("flow", false, true) === "var(--text)", "selected idle flow");
  assert(wireStroke("event", true, true) === "var(--text)",
    "a selected wire reads as selected even while it is carrying the run");
});

test("width thickens for both active and selected", () => {
  assert(wireWidth(false, false) === 1.8, "resting");
  assert(wireWidth(true, false) === 2.5, "active");
  assert(wireWidth(false, true) === 2.5, "selected");
});

// ────────────────────────────────────────────── the silhouette (never colour alone)

test("flow, event and live are three DIFFERENT dash patterns", () => {
  const restingFlow = wireDash("flow", false);
  const restingEvent = wireDash("event", false);
  const live = wireDash("flow", true);
  assert(restingFlow === undefined, "a resting flow wire is solid");
  assert(restingEvent === "4 5", "a resting event wire is dashed 4 5");
  assert(live === "7 6", "a live wire is dashed 7 6");
  assert(restingFlow !== restingEvent && restingEvent !== live && restingFlow !== live,
    "night mode pulls --accent and --warn together and reduced-motion stops the "
    + "march, so the PATTERN is the only cue left; two states sharing one "
    + "pattern would erase the distinction for exactly the reader who needs it");
});

test("a live event wire is drawn live, not as an event", () => {
  // The active pattern wins over the lane pattern, in both lanes, so "is this
  // wire carrying the run" reads the same everywhere.
  assert(wireDash("event", true) === "7 6", "live beats the lane pattern");
  assert(wireDash("flow", true) === "7 6", "…in the flow lane too");
});

test("selection does not take a dash of its own", () => {
  // Selection changes colour and width. If it also changed the pattern, picking
  // an event wire would make it look like a flow wire.
  assert(wireDash("event", false) === "4 5",
    "the lane still reads while a wire is selected");
});

// ─────────────────────────────────────────────────── the ✕ sits on the curve

/** The cubic at t, from the four control points. */
function bezier(p: Point[], t: number): Point {
  const u = 1 - t;
  const w = [u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t];
  return {
    x: p.reduce((s, q, i) => s + q.x * w[i], 0),
    y: p.reduce((s, q, i) => s + q.y * w[i], 0),
  };
}

/** Pull the four control points back out of an `edgePath` string. Parsing the
 *  emitted path rather than recomputing it is the point: this test is here to
 *  catch a change to the PATH, not to restate it. */
function controlPoints(d: string): Point[] {
  const n = d.match(/-?\d+(?:\.\d+)?/g);
  if (!n || n.length !== 8) throw new Error(`expected 8 numbers in "${d}"`);
  const v = n.map(Number);
  return [
    { x: v[0], y: v[1] }, { x: v[2], y: v[3] },
    { x: v[4], y: v[5] }, { x: v[6], y: v[7] },
  ];
}

test("the ✕ midpoint IS B(0.5) of the canvas bezier", () => {
  const a = wireAnchors(edge("n1", "window", "n16", "run"), [DUSK, DOME], "desktop")!;
  const mid = wireMidpoint(a.p1, a.p2);
  const half = bezier(controlPoints(edgePath(a.p1, a.p2, "canvas")), 0.5);
  assert(Math.abs(half.x - mid.x) < 1e-9 && Math.abs(half.y - mid.y) < 1e-9,
    `the remove control would sit off its own wire: midpoint (${mid.x},${mid.y}) `
    + `vs curve (${half.x},${half.y})`);
  assert(mid.x === 239 && mid.y === 97,
    `§D.4's worked value for the first M16 edge is (239,97), got (${mid.x},${mid.y})`);
});

test("…and of the phone-FLOW bezier, whose control points are offset differently", () => {
  // A long, diagonal, right-to-left span: the case where the ±18 horizontal and
  // the ±vy vertical offsets are both large, so an asymmetry would show up.
  const a = wireAnchors(edge("n8", "in", "n9", "pause"), [CLOUD, HOLD], "phone")!;
  const mid = wireMidpoint(a.p1, a.p2);
  const half = bezier(controlPoints(edgePath(a.p1, a.p2, "phone-flow")), 0.5);
  assert(Math.abs(half.x - mid.x) < 1e-9 && Math.abs(half.y - mid.y) < 1e-9,
    `phone-FLOW: midpoint (${mid.x},${mid.y}) vs curve (${half.x},${half.y})`);
});

test("the crossed-control short span still puts the ✕ on the wire", () => {
  // §D.3: for |dx| < 92 the `c = max(46, …)` floor makes the control points
  // CROSS and the wire kinks. That is the design. The midpoint identity has to
  // survive it, because the M16 edge the harness clicks is exactly this case.
  const p1 = { x: 218, y: 97 };
  const p2 = { x: 260, y: 97 };
  const cps = controlPoints(edgePath(p1, p2, "canvas"));
  assert(cps[1].x > cps[2].x, "the control points really do cross here");
  const half = bezier(cps, 0.5);
  const mid = wireMidpoint(p1, p2);
  assert(Math.abs(half.x - mid.x) < 1e-9, "…and the midpoint is still on the curve");
});

// ──────────────────────────────────────────────────────────────── hit bands

test("the hit band is far wider than the wire, and wider still on the phone", () => {
  // A 1.8px stroke is ~0.6 screen px at the reference captures' 46% zoom. The
  // transparent band is the only reason a wire can be selected at all.
  assert(HIT_W_CANVAS === 14, "canvas hit band");
  assert(HIT_W_AUTO === 16, "phone FLOW hit band");
  assert(HIT_W_AUTO > HIT_W_CANVAS, "the finger-driven surface gets the wider one");
  assert(HIT_W_CANVAS > wireWidth(true, true) * 5,
    "a hit band that tracked the stroke width would be unhittable");
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failures.length) { failures.forEach((f) => console.log(f)); process.exit(1); }
export default { passed, failed, total: passed + failed };
