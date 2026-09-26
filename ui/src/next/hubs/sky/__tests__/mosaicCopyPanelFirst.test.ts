// mosaicCopyPanelFirst.test.ts - every sentence the Sky hub says about the ORDER
// a mosaic's panels are shot in, pinned to what the engine does today (#154,
// spec I-08, Revision 2 ruling 4).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/sky/__tests__/mosaicCopyPanelFirst.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT HAPPENS TODAY. GENERATE FLOW saves a flow for the framing centre and
// queues the panels as classic Plan targets sharing one `mosaic_group`
// (`quick.tsx`, `addTargetsToPlan`). A `mosaic_group` with no `groups` entry is
// scheduled like any other target: `_run_scheduled` runs `_setup_target` and then
// `_run_steps` to completion, and only then removes it and picks the next. So the
// panels are shot one at a time, each to completion, and a night cut short
// leaves the last panels short.
//
// WHAT THE COPY USED TO SAY. Four places promised the opposite - that the panels
// "cycle every pass", that the engine "shoots a pass at each panel in turn", so
// "a clouded-out night still leaves every panel with data". Nothing kept that
// claim; it is the class "a claim nothing keeps". The rotating order is real in
// the mosaic build (the TARGET block's loop wire compiles to `mode = "rotate"`),
// and it reaches the Sky through S6's "Send to Flow Wizard" (#196), which retires
// the Plan door these strings describe. Until then the strings describe
// panel-first, and S6 deletes them with the door rather than relaxing this test.
//
// THE BEHAVIOUR THIS PINS TO is T10's engine test "a mosaic_group with no group
// stays panel-first" (S2 in the spec, section 8). While that test holds, a
// sentence here that promises cycling is a promise the engine breaks; if that
// test ever changes, so must these strings and this file.
//
// WHY A SCANNER AND NOT VERBATIM STRINGS. The negative half is a list of the
// promise's own words, run over every mosaic string the Sky says, so a new
// sentence that re-promises cycling is caught without anyone having to add it
// here. The scanner is checked against the pre-#154 strings themselves (the
// known positives stay positive), so it cannot pass by matching nothing. The
// positive half pins the four facts each sentence must carry.
//
// THE TOAST TOO (#275). SkyHub.tsx's "Framing kept" toast used to be written
// inline in a callback and end "as plan targets, one pass each", which is the
// same broken promise in other words. It is `quickCopy.framingKeptDetail` now,
// held to both halves like the others, "one pass each" is one of PROMISES, and
// SkyHub.tsx is read as source only to check that the toast shows that
// function's words and says nothing of its own about the panels. The S2 review
// moved it there (spec S2 item 8: "the Sky copy is corrected to describe
// panel-first behaviour").
//
// MUTATION RECORD, 2026-09-25, each run in a private scratch copy of ui/ (never
// the shared tree) and restored byte-for-byte after. Output verbatim; a quoted
// sentence is cut at "[...]" where it runs on.
//
//   M1 "restore FRAMING_NOTE's 'cycles panels every pass'" - the pre-#154
//   constant put back in `FramingCard.tsx`:
//     mosaicCopyPanelFirst.test: 11/13 passed
//       x FRAMING_NOTE promises no cycling: FRAMING_NOTE promises cycling the
//         panels, "every pass", "in turn", every panel left with data, which the
//         engine does not keep - a Plan mosaic_group is shot panel-first: "Drag
//         the sky to shift the frame, turn the dial to rotate the camera. DONE
//         keeps the framing: it stays on the sky and goes into the flow. Panels
//         overlap 15%; the flow centres on each panel in turn and cycles panels
//         every pass, so a clouded-out night still leaves every panel with data.
//         The dashed outline is the object's catalogued extent."
//       x FRAMING_NOTE says the panels go to the Plan and are shot panel-first:
//         FRAMING_NOTE does not say the panels go to the Plan: "Drag the sky
//         [...]"
//
//   M2 "restore mosaicPlanNote's 'a pass at each panel in turn'":
//     mosaicCopyPanelFirst.test: 11/13 passed
//       x mosaicPlanNote promises no cycling, at every grid the picker offers:
//         mosaicPlanNote(2, 2, 1) promises "in turn", a pass at each panel, every
//         panel left with data, which the engine does not keep [...]
//       x mosaicPlanNote says the panels go to the Plan and are shot
//         panel-first: mosaicPlanNote(6, 3, 2) does not say each panel is shot
//         to completion before the next: "Framed as a 3×2 mosaic. [...]"
//
//   M3 "restore withMosaicCard's 'cycle panels each pass'":
//     mosaicCopyPanelFirst.test: 11/13 passed
//       x the MOSAIC lane card promises no cycling, in its summary or its
//         footnote: withMosaicCard's sum promises cycling the panels, "each
//         pass", which the engine does not keep - a Plan mosaic_group is shot
//         panel-first: "6 panels · 15% overlap · centre per panel · cycle panels
//         each pass"
//       x the MOSAIC lane card says the panels are shot panel-first: the summary
//         does not say the order: "6 panels · 15% overlap · centre per panel ·
//         cycle panels each pass"
//
//   M4 "restore fov.ts header 'panels cycle every pass'":
//     mosaicCopyPanelFirst.test: 12/13 passed
//       x fov.ts's comments no longer promise the order panelOrder was written
//         for: a comment in fov.ts promises cycling the panels, "every pass",
//         which the engine does not keep - a Plan mosaic_group is shot
//         panel-first: "fov.ts - field-of-view, sampling and mosaic math [...]"
//
//   M8 "restore panelOrder's README quote" (the doc's "cycles panels every pass
//   so a shortened night leaves every panel with data"):
//     mosaicCopyPanelFirst.test: 12/13 passed
//       x fov.ts's comments no longer promise the order panelOrder was written
//         for: a comment in fov.ts promises cycling the panels, "every pass",
//         every panel left with data, which the engine does not keep [...]:
//         "Row-major panel indices for a `cols` x `rows` mosaic, [...]"
//
//   M6 "restore MOSAIC_FOOTNOTE's 'the panels are plan targets, not a flow
//   stage'" (the card then never says where the panels go or what a short
//   night costs):
//     mosaicCopyPanelFirst.test: 12/13 passed
//       x the MOSAIC lane card says the panels are shot panel-first:
//         withMosaicCard (summary and footnote) does not say they are one mosaic
//         group: "6 panels · 15% overlap · one panel at a time, each to
//         completion the panels are plan targets, not a flow stage"
//
//   M5 (control) "drop the overlap from withMosaicCard's sum":
//     mosaicCopyPanelFirst.test: 12/13 passed
//       x control: the strings still carry the overlap and the panel count: the
//         lane summary lost the overlap: "6 panels · one panel at a time, each
//         to completion"
//
//   M7 (control) "drop the panel count from mosaicPlanNote":
//     mosaicCopyPanelFirst.test: 12/13 passed
//       x control: the strings still carry the overlap and the panel count:
//         mosaicPlanNote lost the panel count: "Framed as a 3×2 mosaic. GENERATE
//         FLOW saves the flow for the framing centre and queues all the panels
//         as plan targets in one mosaic group, [...]"
//
//   M10 and M11 graded the inline-toast case that #275 replaced (a scan of
//   SkyHub.tsx's literals with "plan targets" as its non-vacuity), and are
//   superseded by M13 to M18 below.
//
//   M12 (non-vacuity) "the picker offers one mosaic grid" (MOSAIC_CHOICES cut
//   to 1x1 and 2x1):
//     mosaicCopyPanelFirst.test: 12/13 passed
//       x mosaicPlanNote promises no cycling, at every grid the picker offers:
//         the picker offers 1 mosaic grids - the loop would read almost nothing
//
// The SkyHub case was added at verification, and every mutant above was re-run
// against the 13 cases then, so each tally is out of 13.
//
// #275, at the S2 review, 2026-09-25: the toast moved into `quickCopy.ts`, the
// inline case above replaced by three cases and a control, 16 in all, so each
// tally below is out of 16. Same scratch-copy rule, output verbatim.
//
//   M13 "restore the toast's 'one pass each'" (framingKeptDetail's mosaic
//   branch back to the pre-#275 sentence):
//     mosaicCopyPanelFirst.test: 14/16 passed
//       x framingKeptDetail promises no cycling, at every grid the picker offers
//         (#275): framingKeptDetail(2) promises "one pass each", which the engine
//         does not keep - a Plan mosaic_group is shot panel-first: "GENERATE FLOW
//         queues all 2 panels as plan targets, one pass each."
//       x framingKeptDetail says the panels go to the Plan and are shot
//         panel-first (#275): framingKeptDetail(6) does not say they are one
//         mosaic group: "GENERATE FLOW queues all 6 panels as plan targets, one
//         pass each."
//
//   M14 "SkyHub inlines its old toast again" (the pre-#275 ternary back in
//   place of the call):
//     mosaicCopyPanelFirst.test: 15/16 passed
//       x SkyHub.tsx's 'Framing kept' toast shows framingKeptDetail's words and
//         none of its own (#275): SkyHub.tsx's 'Framing kept' toast does not take
//         its detail from framingKeptDetail(panels.length)
//
//   M14b "another SkyHub literal makes the promise" (the call kept, the title
//   given ", panels one pass each."):
//     mosaicCopyPanelFirst.test: 15/16 passed
//       x SkyHub.tsx's 'Framing kept' toast shows framingKeptDetail's words and
//         none of its own (#275): a SkyHub.tsx string promises "one pass each",
//         which the engine does not keep - a Plan mosaic_group is shot
//         panel-first: "`Framing kept - ${frameText(cols, rows,
//         f.rotation_deg)}, panels one pass each.`"
//
//   M15 "the toast is always the one-panel sentence" (`framingKeptDetail(1)`):
//     mosaicCopyPanelFirst.test: 15/16 passed
//       x SkyHub.tsx's 'Framing kept' toast shows framingKeptDetail's words and
//         none of its own (#275): SkyHub.tsx's 'Framing kept' toast does not take
//         its detail from framingKeptDetail(panels.length)
//
//   M16 (control) "the one-panel sentence reworded":
//     mosaicCopyPanelFirst.test: 15/16 passed
//       x control: a single frame's toast still says where the night is
//         centred: the one-panel toast: expected GENERATE FLOW centres the night
//         here instead of on the catalogue position., got GENERATE FLOW centres
//         the night on this framing.
//
//   M17 (non-vacuity) "'one pass each' dropped from PROMISES":
//     mosaicCopyPanelFirst.test: 15/16 passed
//       x the scanner flags every sentence #154 found, so it cannot pass by
//         matching nothing: the pre-#275 toast, read alone: expected "one pass
//         each", got
//
//   M18 "the toast loses what a short night costs" ("costs the last panels"):
//     mosaicCopyPanelFirst.test: 15/16 passed
//       x framingKeptDetail says the panels go to the Plan and are shot
//         panel-first (#275): framingKeptDetail(6) does not say a night cut short
//         leaves the last panels short: "GENERATE FLOW queues all 6 panels as
//         plan targets in one mosaic group, each shot to completion before the
//         next, so a night cut short costs the last panels."
//
// Convention: inline test()/eq() helpers, printed tally plus the
// { passed, failed, total } export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// `FramingCard.tsx` and `flowLane.ts` are reached through modules that pass
// `api.ts` / `lib/base.ts`, which read `window.location` AT MODULE SCOPE. So the
// browser globals go in first and the imports are dynamic - the convention
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

const { FRAMING_NOTE } = await import("../frame/FramingCard");
const { MOSAIC_CHOICES, OVERLAP } = await import("../frame/mosaic");
const { MOSAIC_FOOTNOTE, framingKeptDetail, mosaicPlanNote } = await import("../sheets/quickCopy");
const { withMosaicCard } = await import("../sheets/flowLane");

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

// ============================================================ the scanner

/** The cycling promise, in the words it was made in. `cycl` has no honest use
 *  in a sentence about panel order today, so it is banned outright rather than
 *  only in the phrasings that happened to ship. */
const PROMISES: readonly { re: RegExp; what: string }[] = [
  { re: /\bcycl/i, what: "cycling the panels" },
  { re: /\bevery pass\b/i, what: "\"every pass\"" },
  { re: /\beach pass\b/i, what: "\"each pass\"" },
  { re: /\bin turn\b/i, what: "\"in turn\"" },
  { re: /\bpass(es)? (at|on|over|to) (each|every) panel\b/i, what: "a pass at each panel" },
  { re: /\bevery panel\b[^.;]*\bdata\b/i, what: "every panel left with data" },
  // #275: a pass is one sub per checked filter, and a Plan mosaic gives each
  // panel every pass before the next panel starts, never one each.
  { re: /\bone pass each\b/i, what: "\"one pass each\"" },
];

function promisesIn(text: string): string[] {
  return PROMISES.filter((p) => p.re.test(text)).map((p) => p.what);
}

function assertNoPromise(label: string, text: string): void {
  const hits = promisesIn(text);
  assert(hits.length === 0,
    `${label} promises ${hits.join(", ")}, which the engine does not keep - a Plan `
    + `mosaic_group is shot panel-first: "${text}"`);
}

/** The comments of a source file, which is where `fov.ts` made the claim, one
 *  entry per block. A run of `//` lines is ONE block and the comment markers are
 *  stripped, so a promise wrapped across two lines ("cycles panels" / "every
 *  pass") reads as the sentence it is instead of two halves that match nothing. */
function commentsOf(src: string): string[] {
  return (src.match(/(?:\/\/[^\n]*\n?[ \t]*)+|\/\*[\s\S]*?\*\//g) ?? []).map((c) => c
    .replace(/\/\*\*?|\*\//g, " ")
    .replace(/^[ \t]*(\/\/|\*)/gm, " ")
    .replace(/\s+/g, " ")
    .trim());
}

// The strings as they shipped before #154, verbatim. They are the known
// positives: a scanner that does not flag every one of them could pass by
// matching nothing.
const PRE_154 = {
  framingNote: "Panels overlap 15%; the flow centres on each panel in turn and cycles panels "
    + "every pass, so a clouded-out night still leaves every panel with data.",
  planNote: "The engine shoots a pass at each panel in turn, so a clouded-out night still "
    + "leaves every panel with data.",
  laneSum: "6 panels · 15% overlap · centre per panel · cycle panels each pass",
  fovHeader: "//   panel pitch = FoV*(1 - overlap); panels cycle every pass",
  fovDoc: "(README: \"the flow centres on each panel and cycles panels every pass so a "
    + "shortened night leaves every panel with data\")",
};

// The toast as it shipped until #275, verbatim but for the panel count its
// template interpolated. A known positive for the phrase #154's list lacked.
const PRE_275 = {
  toast: "GENERATE FLOW queues all 6 panels as plan targets, one pass each.",
};

test("the scanner flags every sentence #154 found, so it cannot pass by matching nothing", () => {
  for (const [k, s] of Object.entries(PRE_154)) {
    assert(promisesIn(s).length > 0, `the scanner let the pre-#154 ${k} through: "${s}"`);
  }
  for (const [k, s] of Object.entries(PRE_275)) {
    eq(promisesIn(s).join(), "\"one pass each\"", `the pre-#275 ${k}, read alone:`);
  }
  // The pre-#154 plan note's promise lives in "a pass at each panel" and "every
  // panel ... data" as well as "in turn"; each pattern is exercised alone so a
  // dead one is visible.
  eq(promisesIn("the engine shoots a pass at each panel").join(), "a pass at each panel",
    "a pass at each panel, alone:");
  eq(promisesIn("so a short night leaves every panel with data").join(), "every panel left with data",
    "every panel with data, alone:");
  eq(promisesIn("cycle panels each pass").length, 2, "cycle and each pass, together:");
  // A promise wrapped across two comment lines is still one promise.
  const wrapped = commentsOf("const a = 1;\r\n// the flow shoots every\r\n// pass at the panels\r\nconst b = 2;\r\n");
  eq(wrapped.length, 1, "two adjacent // lines are one comment block:");
  eq(promisesIn(wrapped[0]).join(), "\"every pass\"", `a wrapped promise, read as one block ("${wrapped[0]}"):`);
});

test("the scanner leaves an honest panel-first sentence alone", () => {
  // The control for the scanner: the words a truthful sentence needs - panel,
  // completion, the next, rotate the CAMERA - must not trip it.
  const honest = "Drag the sky, turn the dial to rotate the camera. The engine shoots each "
    + "panel to completion before it starts the next, so a night cut short leaves the last "
    + "panels short.";
  eq(promisesIn(honest).length, 0, `an honest sentence was flagged (${promisesIn(honest).join(", ")}):`);
});

// ====================================================== no string promises it

const TARGET_ONLY = [{ id: "t1", label: "TARGET", sum: "M31", colorVar: "--accent" }];
const card6 = withMosaicCard(TARGET_ONLY, 3, 2).find((c) => c.id === "mosaic");

test("precondition: a 3x2 framing produces the MOSAIC lane card", () => {
  assert(card6 != null, "withMosaicCard(3, 2) produced no card - the fixture is wrong, not the copy");
});

test("FRAMING_NOTE promises no cycling", () => {
  assertNoPromise("FRAMING_NOTE", FRAMING_NOTE);
});

test("mosaicPlanNote promises no cycling, at every grid the picker offers", () => {
  // Every MOSAIC_CHOICES grid above one panel, read off the picker itself so a
  // grid added there is read here too. The panel count is interpolated, so
  // every shape is read, not just the one a fixture chose.
  const grids = MOSAIC_CHOICES.filter((m) => m.cols * m.rows > 1);
  assert(grids.length >= 3, `the picker offers ${grids.length} mosaic grids - the loop would read almost nothing`);
  for (const { cols: c, rows: r } of grids) {
    assertNoPromise(`mosaicPlanNote(${c * r}, ${c}, ${r})`, mosaicPlanNote(c * r, c, r));
  }
});

test("framingKeptDetail promises no cycling, at every grid the picker offers (#275)", () => {
  const grids = MOSAIC_CHOICES.filter((m) => m.cols * m.rows > 1);
  assert(grids.length >= 3, `the picker offers ${grids.length} mosaic grids - the loop would read almost nothing`);
  for (const { cols: c, rows: r } of grids) {
    assertNoPromise(`framingKeptDetail(${c * r})`, framingKeptDetail(c * r));
  }
});

test("SkyHub.tsx's 'Framing kept' toast shows framingKeptDetail's words and none of its own (#275)", () => {
  // Read as source because the toast is built inline in a callback. What it
  // must show is the function the two cases around this one read; a toast
  // back on a literal of its own would be words nothing here checks.
  const src = fs.readFileSync(pathOf("../SkyHub.tsx"), "utf8");
  assert(/title: `Framing kept - /.test(src),
    "the 'Framing kept' toast was not found in SkyHub.tsx - the check would pass on nothing");
  assert(/detail: framingKeptDetail\(panels\.length\)/.test(src),
    "SkyHub.tsx's 'Framing kept' toast does not take its detail from framingKeptDetail(panels.length)");
  // And no other literal on a code line (comment lines dropped) that names the
  // panels makes the promise, whatever it is for.
  const literals = src.split(/\r?\n/)
    .filter((l) => !/^\s*(\/\/|\*|\/\*|\{\/\*)/.test(l))
    .flatMap((l) => l.match(/`[^`]*`|"[^"]*"/g) ?? [])
    .filter((s) => /\bpanels\b/i.test(s));
  for (const s of literals) assertNoPromise("a SkyHub.tsx string", s);
});

test("the MOSAIC lane card promises no cycling, in its summary or its footnote", () => {
  assertNoPromise("withMosaicCard's sum", card6?.sum ?? "");
  assertNoPromise("withMosaicCard's footnote", card6?.footnote ?? "");
  assertNoPromise("MOSAIC_FOOTNOTE", MOSAIC_FOOTNOTE);
});

test("fov.ts's comments no longer promise the order panelOrder was written for", () => {
  const comments = commentsOf(fs.readFileSync(pathOf("../../../lib/fov.ts"), "utf8"));
  // Non-vacuity: the extraction must have reached both places #154 named - the
  // header's formula list and panelOrder's own doc.
  assert(comments.some((c) => /panel pitch/.test(c)), "the fov.ts header was not read - the scan would pass on nothing");
  assert(comments.some((c) => /Row-major panel indices/.test(c)),
    "panelOrder's doc was not read - the scan would pass on nothing");
  // Only the offending blocks are printed: the file's other comments are noise
  // in a failure that is about one sentence.
  for (const c of comments) assertNoPromise("a comment in fov.ts", c);
});

// ==================================================== each says what happens

/** The four facts of a Plan mosaic today. Each sentence must carry all four:
 *  where the panels go, that they are one group, the order, and what that order
 *  costs when the night is cut short. */
function assertPanelFirst(label: string, text: string): void {
  assert(/\bplan\b/i.test(text), `${label} does not say the panels go to the Plan: "${text}"`);
  assert(/\bone mosaic group\b/i.test(text), `${label} does not say they are one mosaic group: "${text}"`);
  assert(/\bto completion before\b|\bone panel at a time\b/i.test(text),
    `${label} does not say each panel is shot to completion before the next: "${text}"`);
  assert(/\blast panels short\b/i.test(text),
    `${label} does not say a night cut short leaves the last panels short: "${text}"`);
}

test("FRAMING_NOTE says the panels go to the Plan and are shot panel-first", () => {
  assertPanelFirst("FRAMING_NOTE", FRAMING_NOTE);
});

test("mosaicPlanNote says the panels go to the Plan and are shot panel-first", () => {
  assertPanelFirst("mosaicPlanNote(6, 3, 2)", mosaicPlanNote(6, 3, 2));
});

test("framingKeptDetail says the panels go to the Plan and are shot panel-first (#275)", () => {
  const detail = framingKeptDetail(6);
  assertPanelFirst("framingKeptDetail(6)", detail);
  assert(detail.includes("all 6 panels"), `framingKeptDetail(6) lost the panel count: "${detail}"`);
});

test("the MOSAIC lane card says the panels are shot panel-first", () => {
  // The summary is one ellipsised line on a phone, so the ORDER - the claim #154
  // is about - has to be in it; where they go and what a short night costs ride
  // in the footnote, which wraps.
  assert(/\bto completion\b|\bone panel at a time\b/i.test(card6?.sum ?? ""),
    `the summary does not say the order: "${card6?.sum}"`);
  assertPanelFirst("withMosaicCard (summary and footnote)", `${card6?.sum} ${card6?.footnote}`);
  eq(card6?.footnote, MOSAIC_FOOTNOTE, "the card's footnote is the shared constant:");
});

// ========================================= controls: what must not have moved

test("control: the strings still carry the overlap and the panel count", () => {
  const pct = `${Math.round(OVERLAP * 100)}%`;
  // Against OVERLAP itself, so a copy that drifted from the constant the engine
  // is asked for fails here rather than printing the right-looking number.
  assert(FRAMING_NOTE.includes(pct), `FRAMING_NOTE lost the ${pct} overlap: "${FRAMING_NOTE}"`);
  assert((card6?.sum ?? "").includes(`${pct} overlap`), `the lane summary lost the overlap: "${card6?.sum}"`);
  assert((card6?.sum ?? "").includes("6 panels"), `the lane summary lost the panel count: "${card6?.sum}"`);
  eq(card6?.label, "MOSAIC 3×2", "the lane label:");
  const note = mosaicPlanNote(6, 3, 2);
  assert(note.includes("6 panels"), `mosaicPlanNote lost the panel count: "${note}"`);
  assert(note.includes("3×2"), `mosaicPlanNote lost the grid: "${note}"`);
  assert(/plan targets/.test(note), `mosaicPlanNote lost "plan targets", which quickMosaicDom pins: "${note}"`);
  assert(/camera angle/.test(note), `mosaicPlanNote lost the camera angle each panel carries: "${note}"`);
  assert(/Re-framing replaces them/.test(note), `mosaicPlanNote lost the re-frame rule: "${note}"`);
});

test("control: a single frame's toast still says where the night is centred", () => {
  eq(framingKeptDetail(1),
    "GENERATE FLOW centres the night here instead of on the catalogue position.",
    "the one-panel toast:");
});

test("control: a single frame still gets no MOSAIC card, and the card still follows TARGET", () => {
  eq(withMosaicCard(TARGET_ONLY, 1, 1), TARGET_ONLY, "a 1x1 framing must pass the lane through untouched:");
  const lane = withMosaicCard(TARGET_ONLY, 2, 1);
  eq(lane.map((c) => c.id).join(" "), "t1 mosaic", "the card sits after TARGET:");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`mosaicCopyPanelFirst.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
