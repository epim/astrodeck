// mosaicCopyPanelFirst.test.ts - every sentence the Sky hub says about where a
// framed mosaic GOES, after S6 converged the doors on Send to Flow Wizard
// (#196, #154's door half; spec 2026-09-23 flows mosaic, section 8 S6,
// Revision 2 ruling 4, 2.4's one overlap).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/sky/__tests__/mosaicCopyPanelFirst.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THIS FILE REPLACES ITS OWN PREDECESSOR (the acceptance says so), which held
// the Sky's mosaic strings to "panel-first": until S6 a Sky mosaic reached the
// night through the quick sheet's Plan side channel, as classic Plan targets
// sharing one `mosaic_group`, each shot to completion before the next, beside
// a flow saved for the framing centre alone. S6 deleted that channel, the
// synthetic MOSAIC lane card, its footnote and the split note. The strings that
// remain describe the door that exists: SEND TO FLOW WIZARD, which writes one
// TARGET block into a flow.
//
// WHAT IS PINNED, and why a scanner rather than verbatim strings:
//
//   1. NO STRING NAMES THE RETIRED DOOR. The Plan side channel's words ("plan
//      targets", "to the Plan", "mosaic group", "queues ... panels") are run
//      over every mosaic string the Sky says, so a new sentence that
//      re-describes the side channel is caught without being listed here.
//   2. NO STRING PROMISES AN ORDER. Neither panel-first (the retired door's
//      order) nor a fixed rotation (#154's original false promise) is what a
//      wizard's TARGET block does: the engine picks each visit from what the
//      panels have banked. The acceptance deletes FramingCard's order sentence;
//      this keeps any from coming back.
//   3. EACH STRING NAMES THE DOOR, in the wizard's own title (SEND_TO_WIZARD),
//      and carries its facts (the count, the grid, the overlap).
//   4. ONE OVERLAP (spec 2.4): the Sky's constant is `DEFAULT_OVERLAP` itself,
//      and every default this hub computes with reads it. The 0.15 correction
//      is gone.
//
// Both scanners are checked against the pre-S6 strings verbatim (the known
// positives stay positive), so neither can pass by matching nothing, and
// against an honest sentence (the control), so neither passes by matching
// everything.
//
// MUTATION RECORD, 2026-09-28, each run in a private scratch copy of ui/
// (scratchpad/S6-DOORS-mut in the session scratchpad, never the shared tree,
// #254). Output verbatim; a quoted sentence is cut at "[...]" where it runs on.
//
//   MUTANT "mosaic.ts keeps 0.15" (the acceptance's named mutant: mosaic.ts's
//   `export { DEFAULT_OVERLAP as OVERLAP }` back to `export const OVERLAP =
//   0.15;`, and framingMeta's and panelRects's defaults back to `OVERLAP`).
//   Observed ("mosaicCopyPanelFirst.test: 10/12 passed"):
//     x the Sky's overlap is DEFAULT_OVERLAP itself, and the server's number: mosaic.ts's OVERLAP is not DEFAULT_OVERLAP: expected 0.25, got 0.15
//     x every overlap default this hub computes with reads DEFAULT_OVERLAP: framingMeta's default overlap: expected 2 panels · 2.9° × 1.1° · rot 30°, got 2 panels · 3.1° × 1.1° · rot 30°
//   (also red in server/tests/test_overlap_constant_one.py, recorded there).
//
//   MUTANT "fov.mosaicPitch keeps the README's 0.15" (fov.ts default). Observed
//   ("mosaicCopyPanelFirst.test: 11/12 passed"):
//     x every overlap default this hub computes with reads DEFAULT_OVERLAP: fov.mosaicPitch's default: expected 1.5 +/- 1e-12, got 1.7
//
//   MUTANT "the UI constant drifts" (lib/framing.ts: 0.25 -> 0.2). Observed
//   ("mosaicCopyPanelFirst.test: 11/12 passed"):
//     x the Sky's overlap is DEFAULT_OVERLAP itself, and the server's number: DEFAULT_OVERLAP: expected 0.25, got 0.2
//
//   MUTANT "the framing card's note keeps its order sentence" (FramingCard.tsx
//   framingNote: "and the engine shoots each panel to completion before it
//   starts the next" put back). Observed ("mosaicCopyPanelFirst.test: 11/12 passed"):
//     x the framing card's note names the wizard, the overlap, and no retired door or order: framingNote(0.25) promises an order ("to completion"), which the engine decides from what the panels have banked: "Drag the sky to shift the frame, [...] DONE keeps the framing on the sky, and the engine shoots each panel to completion before it starts the next. [...]"
//
//   MUTANT "the 'Framing kept' toast names the Plan again" (quickCopy.ts
//   framingKeptDetail's mosaic branch back to the retired sentence). Observed
//   ("mosaicCopyPanelFirst.test: 11/12 passed"):
//     x the 'Framing kept' toast names the wizard and no retired door or order, at every grid: framingKeptDetail(2) describes the retired Plan door ("plan targets", a mosaic group, queueing the panels), which S6 deleted - a Sky mosaic goes through SEND TO FLOW WIZARD now: "GENERATE FLOW queues all 2 panels as plan targets in one mosaic group, each shot to completion before the next."
//
//   MUTANT (control) "the framing note prints a fixed 15%" (framingNote's
//   `${overlapPercent(overlap)}%` back to a literal "15%"). Observed
//   ("mosaicCopyPanelFirst.test: 11/12 passed"):
//     x the framing card's note names the wizard, the overlap, and no retired door or order: framingNote(0.25) does not print the session's 25%: "Drag the sky to shift the frame, turn the dial to rotate the camera. Panels overlap 15%. [...]"
//
// Convention: inline test()/eq() helpers, printed tally plus the
// { passed, failed, total } export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// `FramingCard.tsx` is reached through modules that pass `api.ts` /
// `lib/base.ts`, which read `window.location` AT MODULE SCOPE. So the browser
// globals go in first and the imports are dynamic - the convention
// `frameModel.test.ts` and `skyCards.test.ts` use, for the same reason.
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

// Same dependency-free node:fs idiom as `lib/__tests__/lazyViews.test.ts`: the
// project installs no @types/node and `tsc -b` checks everything under src/.
interface NodeFsLike { readFileSync(path: string, encoding: string): string }
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;
const pathOf = (rel: string): string =>
  decodeURIComponent(new URL(rel, import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, "$1");

const { framingNote } = await import("../frame/FramingCard");
const mosaic = await import("../frame/mosaic");
const { MOSAIC_CHOICES, framingMeta, panelRects } = mosaic;
const { FRAMING_REMOVED, SEND_TO_WIZARD, framingKeptDetail, mosaicPlanNote } = await import("../sheets/quickCopy");
const { DEFAULT_OVERLAP, mosaicTotalFov } = await import("../../../../lib/framing");
const { mosaicPitch } = await import("../../../lib/fov");

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
function near(got: number, want: number, tol: number, msg: string): void {
  if (!(Math.abs(got - want) <= tol)) throw new Error(`${msg} expected ${want} +/- ${tol}, got ${got}`);
}

// ============================================================ the scanners

/** The retired door, in the words it was described in. */
const PLAN_DOOR: readonly { re: RegExp; what: string }[] = [
  { re: /\bplan targets?\b/i, what: "\"plan targets\"" },
  { re: /\bto the Plan\b/, what: "\"to the Plan\"" },
  { re: /\bmosaic[ _]group\b/i, what: "a mosaic group" },
  { re: /\bqueues?\b[^.;]*\bpanels?\b/i, what: "queueing the panels" },
];

/** An ORDER, in the words either false promise was made in: the rotation #154
 *  found ("cycles panels every pass", "in turn") and the panel-first order the
 *  retired door described ("to completion", "one panel at a time", "the last
 *  panels short"). `cycl` has no honest use in a sentence about panels. */
const ORDER: readonly { re: RegExp; what: string }[] = [
  { re: /\bcycl/i, what: "cycling the panels" },
  { re: /\bevery pass\b/i, what: "\"every pass\"" },
  { re: /\beach pass\b/i, what: "\"each pass\"" },
  { re: /\bin turn\b/i, what: "\"in turn\"" },
  { re: /\bone pass each\b/i, what: "\"one pass each\"" },
  { re: /\bto completion\b/i, what: "\"to completion\"" },
  { re: /\bone panel at a time\b/i, what: "\"one panel at a time\"" },
  { re: /\blast panels\b/i, what: "\"the last panels\"" },
];

function hits(list: readonly { re: RegExp; what: string }[], text: string): string[] {
  return list.filter((p) => p.re.test(text)).map((p) => p.what);
}

function assertClean(label: string, text: string): void {
  const door = hits(PLAN_DOOR, text);
  assert(door.length === 0,
    `${label} describes the retired Plan door (${door.join(", ")}), which S6 deleted - a Sky `
    + `mosaic goes through ${SEND_TO_WIZARD} now: "${text}"`);
  const order = hits(ORDER, text);
  assert(order.length === 0,
    `${label} promises an order (${order.join(", ")}), which the engine decides from what `
    + `the panels have banked: "${text}"`);
}

/** The comments of a source file, one entry per block (a run of `//` lines is
 *  one block, markers stripped), so a claim wrapped across two lines reads as
 *  the sentence it is. */
function commentsOf(src: string): string[] {
  return (src.match(/(?:\/\/[^\n]*\n?[ \t]*)+|\/\*[\s\S]*?\*\//g) ?? []).map((c) => c
    .replace(/\/\*\*?|\*\//g, " ")
    .replace(/^[ \t]*(\/\/|\*)/gm, " ")
    .replace(/\s+/g, " ")
    .trim());
}

// The strings as they shipped before S6, verbatim: the known positives.
const PRE_S6 = {
  framingNote: "Drag the sky to shift the frame, turn the dial to rotate the camera. "
    + "DONE keeps the framing: it stays on the sky, and its centre and angle go into the flow. "
    + "Panels overlap 15%. GENERATE FLOW sends them to the Plan as targets in one mosaic group, "
    + "and the engine shoots each panel to completion before it starts the next, so a night "
    + "cut short leaves the last panels short. "
    + "The dashed outline is the object's catalogued extent.",
  splitNote: "Framed as a 3×2 mosaic. GENERATE FLOW saves the flow for the framing "
    + "centre and queues all 6 panels as plan targets in one mosaic group, each "
    + "carrying the camera angle above. The engine shoots each panel to completion before "
    + "it starts the next, so a night cut short leaves the last panels short. Re-framing "
    + "replaces them rather than adding a second set.",
  keptToast: "GENERATE FLOW queues all 6 panels as plan targets in one mosaic group, "
    + "each shot to completion before the next, so a night cut short leaves the last "
    + "panels short.",
  footnote: "the panels are plan targets in one mosaic group, not a flow stage - "
    + "a night cut short leaves the last panels short",
  laneSum: "6 panels · 15% overlap · one panel at a time, each to completion",
  pre154: "Panels overlap 15%; the flow centres on each panel in turn and cycles panels "
    + "every pass, so a clouded-out night still leaves every panel with data.",
};

test("the scanners flag every pre-S6 string, so they cannot pass by matching nothing", () => {
  for (const [k, s] of Object.entries(PRE_S6)) {
    assert(hits(PLAN_DOOR, s).length + hits(ORDER, s).length > 0,
      `the scanners let the pre-S6 ${k} through: "${s}"`);
  }
  // Each pattern is exercised alone, so a dead one is visible.
  eq(hits(PLAN_DOOR, "queued as plan targets").join(), "\"plan targets\"", "plan targets, alone:");
  eq(hits(PLAN_DOOR, "sends them to the Plan as rows").join(), "\"to the Plan\"", "to the Plan, alone:");
  eq(hits(PLAN_DOOR, "sharing one mosaic_group").join(), "a mosaic group", "mosaic_group, alone:");
  eq(hits(PLAN_DOOR, "GENERATE FLOW queues all 4 panels").join(), "queueing the panels", "queues panels, alone:");
  eq(hits(ORDER, "the engine shoots each panel to completion").join(), "\"to completion\"", "to completion, alone:");
  eq(hits(ORDER, "one panel at a time").join(), "\"one panel at a time\"", "one panel at a time, alone:");
  eq(hits(ORDER, "leaves the last panels short").join(), "\"the last panels\"", "last panels, alone:");
  eq(hits(ORDER, "cycle panels each pass").length, 2, "cycle and each pass, together:");
  const wrapped = commentsOf("const a = 1;\r\n// the flow shoots every\r\n// pass at the panels\r\nconst b = 2;\r\n");
  eq(wrapped.length, 1, "two adjacent // lines are one comment block:");
  eq(hits(ORDER, wrapped[0]).join(), "\"every pass\"", `a wrapped promise, read as one block ("${wrapped[0]}"):`);
});

test("control: the scanners leave an honest wizard sentence alone", () => {
  const honest = `Framed as a 3×2 mosaic of 6 panels. ${SEND_TO_WIZARD} plans all 6 panels as one `
    + "mosaic block in a flow, from this framing's centre, angle, grid and overlap. Turn the dial "
    + "to rotate the camera.";
  eq(hits(PLAN_DOOR, honest).length + hits(ORDER, honest).length, 0,
    `an honest sentence was flagged (${[...hits(PLAN_DOOR, honest), ...hits(ORDER, honest)].join(", ")}):`);
});

// ====================================================== the strings, read

const GRIDS = MOSAIC_CHOICES.filter((m) => m.cols * m.rows > 1);

test("precondition: the picker offers the mosaic grids the loops below read", () => {
  // MOSAIC_CHOICES is read off the picker itself, so a grid added there is read
  // here too; a picker cut to one grid would leave the loops reading almost
  // nothing.
  assert(GRIDS.length >= 3, `the picker offers ${GRIDS.length} mosaic grids - the loops would read almost nothing`);
});

test("the framing card's note names the wizard, the overlap, and no retired door or order", () => {
  for (const o of [DEFAULT_OVERLAP, 0.1, 0.35]) {
    const note = framingNote(o);
    assertClean(`framingNote(${o})`, note);
    assert(note.includes(SEND_TO_WIZARD), `framingNote(${o}) does not name ${SEND_TO_WIZARD}: "${note}"`);
    const pct = `${Math.round(o * 100)}%`;
    assert(note.includes(`overlap ${pct}`), `framingNote(${o}) does not print the session's ${pct}: "${note}"`);
  }
});

test("the quick sheet's kept-mosaic note names the wizard, the count and the grid, at every grid", () => {
  for (const { cols: c, rows: r } of GRIDS) {
    const note = mosaicPlanNote(c * r, c, r);
    assertClean(`mosaicPlanNote(${c * r}, ${c}, ${r})`, note);
    assert(note.includes(SEND_TO_WIZARD), `mosaicPlanNote(${c * r}, ${c}, ${r}) does not name ${SEND_TO_WIZARD}: "${note}"`);
    assert(note.includes(`${c}×${r}`), `mosaicPlanNote lost the grid ${c}×${r}: "${note}"`);
    assert(note.includes(`all ${c * r} panels`), `mosaicPlanNote lost the count ${c * r}: "${note}"`);
    assert(/GENERATE FLOW plans one target/.test(note),
      `mosaicPlanNote no longer says what GENERATE FLOW beside it plans: "${note}"`);
  }
});

test("the 'Framing kept' toast names the wizard and no retired door or order, at every grid", () => {
  for (const { cols: c, rows: r } of GRIDS) {
    const detail = framingKeptDetail(c * r);
    assertClean(`framingKeptDetail(${c * r})`, detail);
    assert(detail.includes(SEND_TO_WIZARD), `framingKeptDetail(${c * r}) does not name ${SEND_TO_WIZARD}: "${detail}"`);
    assert(detail.includes(`all ${c * r} panels`), `framingKeptDetail(${c * r}) lost the count: "${detail}"`);
  }
});

test("control: a single frame's toast names the wizard and the centre, and claims no GENERATE FLOW centring", () => {
  // The pre-S6 sentence ("GENERATE FLOW centres the night here instead of on
  // the catalogue position") was never true: the quick flow is placed at the
  // target's own coordinates. The wizard carries the framing's centre.
  const one = framingKeptDetail(1);
  assertClean("framingKeptDetail(1)", one);
  assert(one.includes(SEND_TO_WIZARD), `the one-panel toast does not name ${SEND_TO_WIZARD}: "${one}"`);
  assert(/centre/.test(one), `the one-panel toast lost where the night is centred: "${one}"`);
  assert(!/GENERATE FLOW centres/.test(one), `the one-panel toast still says GENERATE FLOW centres the night: "${one}"`);
});

test("SkyHub.tsx's 'Framing kept' toast shows framingKeptDetail's words and none of its own", () => {
  // Read as source because the toast is built inline in a callback. A toast
  // back on a literal of its own would be words nothing here checks.
  const src = fs.readFileSync(pathOf("../SkyHub.tsx"), "utf8");
  assert(/title: `Framing kept - /.test(src),
    "the 'Framing kept' toast was not found in SkyHub.tsx - the check would pass on nothing");
  assert(/detail: framingKeptDetail\(panels\.length\)/.test(src),
    "SkyHub.tsx's 'Framing kept' toast does not take its detail from framingKeptDetail(panels.length)");
  const literals = src.split(/\r?\n/)
    .filter((l) => !/^\s*(\/\/|\*|\/\*|\{\/\*)/.test(l))
    .flatMap((l) => l.match(/`[^`]*`|"[^"]*"/g) ?? [])
    .filter((s) => /\bpanels?\b/i.test(s));
  for (const s of literals) assertClean("a SkyHub.tsx string", s);
});

// The S5/S6 integration (the S6-DOORS verifier's copy finding, beside #459).
// Mutants in scratchpad/S5-FINAL-INTEG-ui-mut, each from a byte backup
// restored with its sha256 checked:
//   MUTANT "the removal toast claims a centre again" (quickCopy.ts
//   FRAMING_REMOVED back to "Framing removed - the flow centres on the
//   catalogue position."). Observed:
//     x clearing the framing claims no centre the quick flow never took, and SkyHub says it in FRAMING_REMOVED's words: the removal toast claims a centre the quick flow never took: "Framing removed - the flow centres on the catalogue position." (12/13 passed)
//   MUTANT "SkyHub keeps a removal sentence of its own" (clearFrame's toast
//   back on its old literal). Observed:
//     x clearing the framing claims no centre the quick flow never took, and SkyHub says it in FRAMING_REMOVED's words: clearFrame's toast does not show FRAMING_REMOVED: enqueueToast({ level: "info", title: "Framing removed - the flow centres on the catalogue position." }) (12/13 passed)
test("clearing the framing claims no centre the quick flow never took, and SkyHub says it in FRAMING_REMOVED's words", () => {
  // The quick flow is placed at the target's own coordinates and takes only
  // the framing's angle (#459), so clearing the framing changes the angle.
  assert(!/centre/i.test(FRAMING_REMOVED), `the removal toast claims a centre the quick flow never took: "${FRAMING_REMOVED}"`);
  assert(/angle/.test(FRAMING_REMOVED), `the removal toast does not say what clearing changes: "${FRAMING_REMOVED}"`);
  const src = fs.readFileSync(pathOf("../SkyHub.tsx"), "utf8");
  assert(/const clearFrame = useCallback/.test(src),
    "clearFrame was not found in SkyHub.tsx - the check would pass on nothing");
  const body = src.slice(src.indexOf("const clearFrame = useCallback"));
  const toast = /enqueueToast\(\{[^}]*\}\)/.exec(body)?.[0] ?? "";
  assert(/title: FRAMING_REMOVED\b/.test(toast), `clearFrame's toast does not show FRAMING_REMOVED: ${toast}`);
});

test("fov.ts's comments promise no order for panelOrder", () => {
  const comments = commentsOf(fs.readFileSync(pathOf("../../../lib/fov.ts"), "utf8"));
  assert(comments.some((c) => /panel pitch/.test(c)), "the fov.ts header was not read - the scan would pass on nothing");
  assert(comments.some((c) => /Row-major panel indices/.test(c)),
    "panelOrder's doc was not read - the scan would pass on nothing");
  // ORDER only: fov.ts's panelOrder doc NAMES the retired Plan door, as
  // history, which is not a claim about where a mosaic goes today.
  for (const c of comments) {
    const order = hits(ORDER, c).filter((w) => w !== "\"to completion\"" && w !== "\"one panel at a time\"");
    assert(order.length === 0, `a comment in fov.ts promises ${order.join(", ")}: "${c}"`);
  }
});

// ============================================================ one overlap

test("the Sky's overlap is DEFAULT_OVERLAP itself, and the server's number", () => {
  // `OVERLAP` is re-exported from lib/framing.ts, a binding, not a copy.
  eq(mosaic.OVERLAP, DEFAULT_OVERLAP, "mosaic.ts's OVERLAP is not DEFAULT_OVERLAP:");
  // The server's `framing.DEFAULT_OVERLAP` is 0.25; the server test
  // test_overlap_constant_one.py holds the UI line to it. Here it pins that
  // the Sky did not keep a correction of its own.
  eq(DEFAULT_OVERLAP, 0.25, "DEFAULT_OVERLAP:");
});

test("every overlap default this hub computes with reads DEFAULT_OVERLAP", () => {
  // framingMeta's default: the tangent-plane extent at DEFAULT_OVERLAP.
  const t = mosaicTotalFov(2, 1, DEFAULT_OVERLAP, 1.68, 1.12);
  eq(framingMeta(2, 1, 30, 1.68, 1.12),
    `2 panels · ${t.total_fov_x_deg.toFixed(1)}° × ${t.total_fov_y_deg.toFixed(1)}° · rot 30°`,
    "framingMeta's default overlap:");
  // panelRects's default pitch.
  const rects = panelRects(100, 100, 2, 1, 40, 30);
  near(rects[1].x - rects[0].x, 40 * (1 - DEFAULT_OVERLAP), 1e-9, "panelRects's default pitch:");
  // The Settings mosaic pitch.
  near(mosaicPitch(2), 2 * (1 - DEFAULT_OVERLAP), 1e-12, "fov.mosaicPitch's default:");
});

test("control: an explicit overlap still wins over the default", () => {
  near(mosaicPitch(2, 0.1), 1.8, 1e-12, "mosaicPitch(2, 0.1):");
  const rects = panelRects(100, 100, 2, 1, 40, 30, 0.1);
  near(rects[1].x - rects[0].x, 36, 1e-9, "panelRects at 10%:");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`mosaicCopyPanelFirst.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
