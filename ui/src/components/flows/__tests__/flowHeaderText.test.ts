// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// flowHeaderText.test.ts — the four things the Flows toolbar could print untrue.
//   Run:  npx tsx src/components/flows/__tests__/flowHeaderText.test.ts   (from ui/)
//
// FlowHeader is mostly markup and needs no test. These formatters are not:
// each one stands where a header could state something the rig never said.
//
//   * formatEta      — the header's ETA is the rig's `progress.eta_s` while the
//                      run is this flow's (#189 S5, `useFlowRunReadouts`) and
//                      `run.etaS`, which has NO publisher (§G-1), otherwise, so
//                      a null is common. The one behaviour worth pinning is
//                      that a missing number renders as a missing number,
//                      because FlowRunState's own comment says "an ETA the
//                      client invented looks identical to one the rig computed,
//                      and the operator cannot tell which they are being
//                      shown." Where the ETA comes from is graded mounted, in
//                      next/hubs/session/flows/canvas/__tests__/
//                      phoneReadouts.test.tsx.
//   * RunWords       — the RUN button's words (#189 S5): RUN and STOP exactly
//                      as they always read, and CONTINUE's line with the
//                      flow's name as the only part a narrow button may cut.
//   * checksLabel    — the chip is the only always-visible verdict on a graph.
//   * runBlockedReason — an honest-disabled control whose reason came back null
//                      would be a control that silently does nothing.
//   * providerPill   — #129: a fully real, tracking rig was badged SIMULATOR on
//                      the one chip that says whether commands reach the sky.
//   * the ETA's source comment — FlowHeader.tsx says where the header's ETA
//                      comes from; it must cite the U-07 plan, which is what
//                      S5 built, and not a §G-1 ruling nobody made (#510, B17).
/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  no @types/node guaranteed; tsx supplies fs at runtime
import { readFileSync } from "node:fs";
import type { FlowProgress } from "../../../lib/flowsApi";

// ------------------------------------------------------------- globals first
// FlowHeader -> store -> lib/api -> lib/base, and base.ts reads
// `window.location.pathname` AT MODULE SCOPE. `../ui` reaches Overlay, which
// calls matchMedia. Both have to exist before the dynamic import. Nothing here
// fakes behaviour the assertions then read back.
(globalThis as any).window = {
  location: { pathname: "/", origin: "http://local" },
  addEventListener() {}, removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  setTimeout: (fn: () => void, ms?: number) => setTimeout(fn, ms),
  clearTimeout: (id: any) => clearTimeout(id),
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
  getElementById: () => null,
  createElement: () => ({ style: {}, classList: { add() {} }, appendChild() {} }),
  body: { appendChild() {} },
};
(globalThis as any).matchMedia = (globalThis as any).window.matchMedia;

const {
  formatEta, checksLabel, runBlockedReason, librarySubline, providerPill, RunWords,
} = await import("../FlowHeader");
const { accessPhrase } = await import("../../../lib/caps");
const { runCopy } = await import("../runCopy");
const { createElement } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq(a: unknown, b: unknown, msg: string): void {
  assert(Object.is(a, b), `${msg} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
}

// ------------------------------------------------------------------- the ETA
test("a null ETA renders as a dash, never as a number", () => {
  eq(formatEta(null), "—",
    "a header that printed 0:00 for an unknown ETA would be indistinguishable "
    + "from one the rig computed, and the operator would plan around it");
  eq(formatEta(undefined), "—", "an absent field is the same claim as null");
});

test("a nonsense ETA is refused rather than rendered", () => {
  eq(formatEta(Number.NaN), "—", "NaN would print 'NaN:NaN' straight onto the toolbar");
  eq(formatEta(Number.POSITIVE_INFINITY), "—", "Infinity would print 'Infinity:NaN'");
  eq(formatEta(-4), "—",
    "a negative remaining time is not a countdown, it is a bug upstream — "
    + "printing '-1:56' would ask the operator to believe it");
});

test("the ETA is m:ss with a zero-padded seconds field", () => {
  // Capture 07 shows ETA 0:12 mid-run.
  eq(formatEta(12), "0:12", "capture 07's own value");
  eq(formatEta(9), "0:09", "a bare '0:9' reads as nine minutes at a glance");
  eq(formatEta(60), "1:00", "the minute boundary");
  eq(formatEta(4385), "73:05", "minutes are unbounded — nothing is truncated away");
  eq(formatEta(11.6), "0:12", "fractional seconds round rather than truncate");
});

// --------------------------------------------------------- validation chip
test("the chip's three strings are the design's three strings", () => {
  eq(checksLabel(0), "GRAPH VALID", "§C.3's clean state");
  eq(checksLabel(1), "1 OPEN CHECK", "singular is spelled out separately in §C.3");
  eq(checksLabel(4), "4 OPEN CHECKS", "plural");
});

test("the chip never reads valid for a negative count", () => {
  // Defensive: issues.length cannot go negative, but "GRAPH VALID" is the one
  // string that must never appear by accident.
  eq(checksLabel(-1), "GRAPH VALID",
    "a negative count is impossible; the point is that it does not print "
    + "'-1 OPEN CHECKS'");
});

// -------------------------------------------------------- the honest button
test("RUN names the capability the server actually requires", () => {
  const r = runBlockedReason(false, true, false);
  assert(r != null, "a viewer pressing RUN must be told why, not silently ignored");
  assert(r!.includes(accessPhrase("control.mount")),
    `the reason must name the real gate (app.py:3713 requires CAP_CONTROL_MOUNT): ${r}`);
});

test("RUN names the missing camera, and STOP does not", () => {
  eq(runBlockedReason(true, false, false),
    "No camera is connected, so there is nothing to run this flow on.",
    "hub.require('camera') at app.py:3791 is the second real guard");
  eq(runBlockedReason(true, false, true), null,
    "refusing to STOP a live run because no camera is attached would leave a "
    + "moving mount running while the UI explains itself");
});

test("STOP is still gated on the capability its route requires", () => {
  const r = runBlockedReason(false, true, true);
  assert(r != null && r.includes(accessPhrase("control.mount")),
    "/api/sequence/abort is CAP_CONTROL_MOUNT too — a STOP that looked live and "
    + `then returned 403 is worse than one that says why: ${r}`);
});

test("a live control returns no reason at all", () => {
  eq(runBlockedReason(true, true, false), null, "an operator with a camera can run");
  eq(runBlockedReason(true, true, true), null, "and can stop");
});

// -------------------------------------------------------------- the sub-line
test("the library sub-line agrees with itself about number", () => {
  eq(librarySubline(5), "Automation library · 5 saved flows", "capture 01's line");
  eq(librarySubline(1), "Automation library · 1 saved flow", "'1 saved flows' is wrong");
  eq(librarySubline(0), "Automation library · 0 saved flows", "zero takes the plural");
});

// ----------------------------------------------------------- provider badge
test("only the simulator is badged SIMULATOR", () => {
  eq(providerPill("sim")?.label, "SIMULATOR", "the design's word for the sim rig");
  eq(providerPill("sim")?.variant, "prov-sim",
    "dashed border + hollow dot — the shape rule that survives night mode");
});

test("a real rig is never badged as the simulator (#129)", () => {
  // The failure this pins: a rig built from per-role hardware drivers reports
  // its backend NAME ("zwo-usb"), which matched no branch and fell through to a
  // SIM default — so a fully real, tracking rig was badged SIMULATOR.
  for (const mode of ["nina", "alpaca", "native", "zwo-usb", "player-one"]) {
    const pill = providerPill(mode);
    assert(pill != null, `${mode} must still get a badge`);
    assert(pill!.label !== "SIMULATOR",
      `${mode} is a real backend and must not claim to be the simulator`);
    eq(pill!.variant, "prov-ext",
      `${mode} takes the external-backend shape, not the simulator's dashes`);
  }
});

test("an unreported backend shows no badge rather than a guess", () => {
  eq(providerPill(undefined), null, "pre-first-poll there is nothing true to say");
  eq(providerPill(""), null, "an empty mode is not a backend");
  eq(providerPill("none"), null, "'none' is the no-backend mode");
});

// ------------------------------------------------------------- the RUN words
// The markup `RunWords` draws, rendered to a string: no DOM needed, and the
// string is the whole claim.
//
// THE DORMANT ANSWER IS THE RECORDED FILE, READ, NOT COPIED (#510, B19):
// server/tests/fixtures/flow_progress_continue.json, the progress route's
// answer for the eighth Example with a dormant session of two observing
// nights, 194 of 480 subs (test_s5_recorded_state.py rebuilds it byte for
// byte). Until S7 this was a literal typed "as the recorded progress answer
// has it", which nothing held to the file. The expected lines stay literals,
// so a re-recording that moves a count turns these cases red instead of
// leaving them green on a copy of an answer the route no longer gives. A
// missing or unreadable file FAILS the whole file.
//
// MUTANT "the fixture's count moved" (S7, scratchpad S7-URUN-mut; the scratch
// copy's fixture with the block's `"banked": 194` made 195). Observed,
// flowHeaderText 15/17:
//   x CONTINUE's line cuts the name and never the parenthetical: the parenthetical never shrinks or wraps: <span class="flex items-baseline gap-[0.5em] min-w-0" data-testid="run-copy"><span aria-hidden="true" class="shrink-0">▶</span> <span class="shrink-0 whitespace-nowrap" data-testid="run-copy-verb">CONTINUE</span> <span class="min-w-0 truncate" data-testid="run-copy-name">M31 MOSAIC</span> <span class="shrink-0 whitespace-nowrap" data-testid="run-copy-detail">(night 3, 195/480 subs)</span></span>
//   x the recorded answer is the line these cases expect: the recorded dormant session, as the button words it — got "CONTINUE M31 MOSAIC (night 3, 195/480 subs)", want "CONTINUE M31 MOSAIC (night 3, 194/480 subs)"
const FIXTURE = "../../../../../server/tests/fixtures/flow_progress_continue.json";
let DORMANT: FlowProgress;
try {
  DORMANT = JSON.parse(readFileSync(new URL(FIXTURE, import.meta.url), "utf8") as string).response;
} catch (e) {
  throw new Error(`cannot read ${FIXTURE}, the recorded answer the RUN words are graded against: `
    + `${(e as Error).message}`);
}
if (!DORMANT?.session || DORMANT.session.status !== "dormant") {
  throw new Error(`${FIXTURE} does not hold a dormant session`);
}
const words = (copy: ReturnType<typeof runCopy>, compact = false): string =>
  renderToStaticMarkup(createElement(RunWords, { copy, compact }));

// Mutants run in scratchpad S5-RUNUI-mut (a private copy of ui/, #254), from
// a byte backup of FlowHeader.tsx or runCopy.ts, restored and hash-checked.
//
// MUTANT "RUN through the CONTINUE row" (RunWords' plain branch taken for
// STOP alone, so RUN falls through to the CONTINUE markup). Observed,
// flowHeaderText 14/15:
//   x RUN and STOP read exactly as they always did: RUN's markup — got "<span class=\"flex items-baseline gap-[0.5em] min-w-0\" data-testid=\"run-copy\"><span aria-hidden=\"true\" class=\"shrink-0\">▶</span> <span class=\"shrink-0 whitespace-nowrap\" data-testid=\"run-copy-verb\">RUN</span></span>", want "<span aria-hidden=\"true\">▶</span> RUN"
// MUTANT "STOP forgotten" (runCopy.ts `if (live) return plain("STOP")`
// removed). Observed, flowHeaderText 14/15:
//   x RUN and STOP read exactly as they always did: STOP's markup — got "<span class=\"flex items-baseline gap-[0.5em] min-w-0\" data-testid=\"run-copy\"><span aria-hidden=\"true\" class=\"shrink-0\">▶</span> <span class=\"shrink-0 whitespace-nowrap\" data-testid=\"run-copy-verb\">CONTINUE</span> <span class=\"min-w-0 truncate\" data-testid=\"run-copy-name\">M31 MOSAIC</span> <span class=\"shrink-0 whitespace-nowrap\" data-testid=\"run-copy-detail\">(night 3, 194/480 subs)</span></span>", want "<span aria-hidden=\"true\">■</span> STOP"
test("RUN and STOP read exactly as they always did", () => {
  // The markup the header and the MONITOR tab drew before S5, byte for byte:
  // other surfaces' tests and the parity captures read these two words.
  eq(words(runCopy("M31 mosaic", null, false)), '<span aria-hidden="true">▶</span> RUN', "RUN's markup");
  eq(words(runCopy("M31 mosaic", DORMANT, true)), '<span aria-hidden="true">■</span> STOP', "STOP's markup");
  eq(words(runCopy("M31 mosaic", null, false), true), '<span aria-hidden="true">▶</span> RUN',
    "the phone header's RUN is the same");
});

// MUTANT "the parenthetical truncates" (RunWords' parenthetical given
// `min-w-0 truncate` in place of `shrink-0 whitespace-nowrap`). Observed,
// flowHeaderText 14/15:
//   x CONTINUE's line cuts the name and never the parenthetical: the parenthetical never shrinks or wraps: <span class="flex items-baseline gap-[0.5em] min-w-0" data-testid="run-copy"><span aria-hidden="true" class="shrink-0">▶</span> <span class="shrink-0 whitespace-nowrap" data-testid="run-copy-verb">CONTINUE</span> <span class="min-w-0 truncate" data-testid="run-copy-name">M31 MOSAIC</span> <span class="min-w-0 truncate" data-testid="run-copy-detail">(night 3, 194/480 subs)</span></span>
test("CONTINUE's line cuts the name and never the parenthetical", () => {
  const html = words(runCopy("M31 mosaic", DORMANT, false));
  assert(html.includes('<span class="min-w-0 truncate" data-testid="run-copy-name">M31 MOSAIC</span>'),
    `the name is the part that truncates: ${html}`);
  assert(html.includes('<span class="shrink-0 whitespace-nowrap" data-testid="run-copy-detail">'
    + "(night 3, 194/480 subs)</span>"), `the parenthetical never shrinks or wraps: ${html}`);
  // The phone header's compact form: the verb on screen, the whole line for
  // a screen reader.
  const compact = words(runCopy("M31 mosaic", DORMANT, false), true);
  eq(compact, '<span aria-hidden="true">▶ CONTINUE</span>'
    + '<span class="sr-only" data-testid="run-copy-text">CONTINUE M31 MOSAIC (night 3, 194/480 subs)</span>',
  "the compact CONTINUE");
});

test("the recorded answer is the line these cases expect", () => {
  // The premise, in the file's own numbers: if this fails, the file moved
  // and every literal above is stale, which is the point of reading it.
  eq(runCopy("M31 mosaic", DORMANT, false).text, "CONTINUE M31 MOSAIC (night 3, 194/480 subs)",
    "the recorded dormant session, as the button words it");
});

// ------------------------------------------------------- the ETA's source
// FlowHeader.tsx's comment on the ETA span said the source was "§G-1's
// settlement (b), ruled by #189 S5". MILESTONE2-CONTRACT's G-1 was never
// ruled; what S5 built is the plan in the spec's section 9, row U-07 (#510,
// B17). The comment is read as text: the `//` lines between the span's test
// id and its `title`.
//
// MUTANT "the §G-1 citation restored" (S7, scratchpad S7-URUN-mut;
// FlowHeader.tsx's ETA comment back to S5's three lines). Observed,
// flowHeaderText 16/17 (the comment it quotes cut here):
//   x the header's ETA comment cites the U-07 plan, not a §G-1 ruling: the ETA comment does not cite the U-07 plan: The sequence event's `progress.eta_s`, which a flow run really does emit because it runs on the same engine (§G-1's settlement (b), ruled by #189 S5), read only while that run is this flow's. ...
test("the header's ETA comment cites the U-07 plan, not a §G-1 ruling", () => {
  const src = (readFileSync(new URL("../FlowHeader.tsx", import.meta.url), "utf8") as string)
    .replace(/\r\n/g, "\n");
  const at = src.indexOf('data-testid="flow-header-eta"');
  const end = src.indexOf("title=", at);
  assert(at >= 0 && end > at, "FlowHeader.tsx has no ETA span whose comment could be read");
  const comment = src.slice(at, end).split("\n")
    .filter((l) => l.trim().startsWith("//"))
    .map((l) => l.trim().replace(/^\/\/\s?/, "")).join(" ").replace(/\s+/g, " ");
  assert(comment.includes("U-07"), `the ETA comment does not cite the U-07 plan: ${comment}`);
  assert(!/settlement \(b\)/.test(comment) && !/ruled by #189 S5/.test(comment),
    `the ETA comment still cites a §G-1 ruling nobody made: ${comment}`);
});

for (const f of failures) console.log(f);
console.log(`flowHeaderText: ${passed}/${passed + failed} passed`);
export default { passed, failed, total: passed + failed };
