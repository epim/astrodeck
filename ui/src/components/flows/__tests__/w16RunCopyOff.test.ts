// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16RunCopyOff.test.ts - RUN says what an Off flow does, and `repeat` is gone
// from DUSK WINDOW's client vocabulary (#195 part D, backlog WP-118; spec 2.4,
// Revision 2 ruling 7).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w16RunCopyOff.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS GUARDED
//
//   1. runCopy's `notice`: "" for a flow that resumes (and for a caller that
//      does not say), `RESUME_OFF_LINE` on RUN and on CONTINUE for a flow whose
//      Automatic resume is Off, never on STOP; and the button's own `text`
//      never moves with it, because the button says what the press does and
//      the notice says what the NEXT night will not.
//   2. The sentence itself says what an Off flow does (a subsequent night does
//      not resume it by itself, CONTINUE is the way), as the Target modal's
//      campaign line does, and names no retired word.
//   3. START OVER's confirm carries it for an Off flow and is the old
//      sentence, byte for byte, for every other.
//   4. `graphResumes` reads the flow the way the compile does: only an explicit
//      Off in the first DUSK WINDOW, and a flow with no DUSK WINDOW, a stored
//      `repeat`, a blank or an unknown value all resume.
//   5. `repeat` is retired: DUSK WINDOW declares no such param here or in
//      nodes.py, has no rowless param, and its card footer says "nightly" for
//      no stored value; Off says "one night".
//
// NAMED MUTANTS (each run from a byte backup inside the worktree, restored
// and sha256-compared, the mutant text grepped out afterwards; the first
// failure is quoted):
//
//   "RUN notice dropped": runCopy.ts's `const notice = resumes ? "" :
//     RESUME_OFF_LINE` made `const notice = ""`. RED, observed:
//       w16RunCopyOff.test: 8/10 passed
//       x runCopy: an Off flow's RUN carries the notice, and so does its
//         CONTINUE: RUN   expected ["RUN","Automatic resume is off ..."]
//         got ["RUN",""]
//       x startOverBody: the notice is added for an Off flow and nothing
//         else changes
//   "notice on STOP": the `verb === "STOP" ? "" : notice` gate removed. RED,
//     observed: 9/10, x runCopy: STOP carries no notice, because it starts
//     nothing: STOP  expected ["STOP",""]  got ["STOP","Automatic resume is
//     off ..."]
//   "graphResumes reads repeat": `graphResumes` answering false for a DUSK
//     whose `repeat` is not stored as a campaign word, i.e. the 0.3.40 defect
//     on the client. RED, observed: 8/10, x graphResumes: only an explicit
//     Off in the first DUSK WINDOW is Off: a DUSK with no autoResume
//     expected ["a DUSK with no autoResume",true] got [..., false]
//   "nightly tail returns": nodeDefs.ts's DUSK `sum` adding " · nightly" for a
//     stored `repeat` other than "Single night" again. RED, observed: 9/10,
//     x the card's footer says 'one night' for Off and nothing for a stored
//     repeat: a stored repeat of Nightly until pool complete is not read
//     expected "Astro dusk -30m → dawn" got "Astro dusk -30m → dawn · nightly"
//   "repeat declared again": `repeat: "Single night"` back in the DUSK params
//     in nodeDefs.ts. RED, observed: 9/10, x DUSK WINDOW declares no `repeat`
//     here, in nodes.py, or as a rowless param: `repeat` is still a param in
//     nodeDefs.ts

// @ts-ignore  no @types/node guaranteed; tsx supplies fs at runtime
import { readFileSync } from "node:fs";

import {
  RESUME_OFF_LINE, graphResumes, runCopy, startOverBody,
} from "../runCopy";
import { NODE_DEFS, ROWLESS_PARAMS, duskAutoResume } from "../nodeDefs";
import type { FlowGraphRec, FlowNodeRec } from "../flowsTypes";
import type { FlowProgress } from "../../../lib/flowsApi";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  if (g !== w) throw new Error(`${msg}\n    expected ${w}\n    got      ${g}`);
}
function assert(cond: unknown, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ---------------------------------------------------------------- fixtures
const FIXTURE = "../../../../../server/tests/fixtures/flow_progress_continue.json";
const PROGRESS = (JSON.parse(readFileSync(new URL(FIXTURE, import.meta.url), "utf8") as string)
  .response) as FlowProgress;
if (!PROGRESS?.session) throw new Error("the recorded progress answer holds no session");
const DORMANT = PROGRESS;
const NO_SESSION: FlowProgress = { ...PROGRESS, session: null };

const node = (id: string, type: string, params: Record<string, string | number> = {}): FlowNodeRec =>
  ({ id, type: type as FlowNodeRec["type"], x: 0, y: 0, params });
const graph = (...nodes: FlowNodeRec[]): FlowGraphRec => ({ nodes, edges: [] });

// ------------------------------------------------------------- 1. notice
// MUTANT "RUN notice dropped" (`const notice = ""`). Observed, w16RunCopyOff.test:
//   x runCopy: an Off flow's RUN carries the notice, and so does its CONTINUE: RUN
//     expected ["RUN","Automatic resume is off for this flow, so a subsequent night does not resume it by itself: CONTINUE it by hand."]
//     got      ["RUN",""]
test("runCopy: an Off flow's RUN carries the notice, and so does its CONTINUE", () => {
  const run = runCopy("M31", NO_SESSION, false, false);
  eq([run.verb, run.notice], ["RUN", RESUME_OFF_LINE], "RUN");
  const cont = runCopy("M31", DORMANT, false, false);
  eq([cont.verb, cont.notice], ["CONTINUE", RESUME_OFF_LINE], "CONTINUE");
});

// MUTANT "notice on STOP" (the STOP gate removed). Observed:
//   x runCopy: STOP carries no notice, because it starts nothing: STOP
//     expected ["STOP",""]
//     got      ["STOP","Automatic resume is off for this flow, so a subsequent night does not resume it by itself: CONTINUE it by hand."]
test("runCopy: STOP carries no notice, because it starts nothing", () => {
  const stop = runCopy("M31", DORMANT, true, false);
  eq([stop.verb, stop.notice], ["STOP", ""], "STOP");
});

test("runCopy: a flow that resumes, and a caller that does not say, carry no notice", () => {
  for (const [what, args] of [
    ["resumes", [true]], ["did not say", []],
  ] as const) {
    for (const [progress, live] of [[NO_SESSION, false], [DORMANT, false], [DORMANT, true]] as const) {
      const c = (runCopy as (...a: unknown[]) => ReturnType<typeof runCopy>)(
        "M31", progress, live, ...args);
      eq(c.notice, "", `${what}, ${c.verb}`);
    }
  }
});

test("runCopy: the notice never changes what the button says", () => {
  for (const [progress, live] of [[NO_SESSION, false], [DORMANT, false], [DORMANT, true]] as const) {
    const on = runCopy("M31", progress, live, true);
    const off = runCopy("M31", progress, live, false);
    eq({ ...off, notice: "" }, { ...on, notice: "" },
      `the button's own words moved with the flag (${on.verb})`);
  }
});

// -------------------------------------------------------- 2. the sentence
test("RESUME_OFF_LINE says what an Off flow does and names no retired word", () => {
  assert(/subsequent night does not resume it by itself/.test(RESUME_OFF_LINE), RESUME_OFF_LINE);
  assert(/CONTINUE it by hand/.test(RESUME_OFF_LINE), RESUME_OFF_LINE);
  assert(/Automatic resume is off/.test(RESUME_OFF_LINE), RESUME_OFF_LINE);
  assert(!/[^\x20-\x7e]/.test(RESUME_OFF_LINE), "no non-ASCII in the sentence");
  assert(!/repeat/i.test(RESUME_OFF_LINE), "the retired word is not in the sentence");
});

// ------------------------------------------------------- 3. START OVER
test("startOverBody: the notice is added for an Off flow and nothing else changes", () => {
  const on = startOverBody(runCopy("M31", DORMANT, false, true));
  const off = startOverBody(runCopy("M31", DORMANT, false, false));
  assert(!on.includes(RESUME_OFF_LINE), "a flow that resumes was told it does not");
  eq(off, `${on} ${RESUME_OFF_LINE}`, "an Off flow's START OVER confirm is the old sentence plus the notice");
});

// -------------------------------------------------------- 4. graphResumes
// MUTANT "graphResumes reads repeat" (the DUSK's autoResume test and-ed with a
// `repeat` that is not "Single night", so a default DUSK reads Off). Observed:
//   x graphResumes: only an explicit Off in the first DUSK WINDOW is Off: a DUSK with no autoResume
//     expected ["a DUSK with no autoResume",true]
//     got      ["a DUSK with no autoResume",false]
test("graphResumes: only an explicit Off in the first DUSK WINDOW is Off", () => {
  const on: Array<[string, FlowGraphRec | null | undefined]> = [
    ["no graph", null], ["undefined", undefined],
    ["no DUSK WINDOW", graph(node("t", "target"))],
    ["a DUSK with no autoResume", graph(node("d", "dusk"))],
    ["autoResume On", graph(node("d", "dusk", { autoResume: "On" }))],
    ["autoResume blank", graph(node("d", "dusk", { autoResume: "" }))],
    ["autoResume unknown", graph(node("d", "dusk", { autoResume: "Sometimes" }))],
    ["a stored repeat of 'Single night'", graph(node("d", "dusk", { repeat: "Single night" }))],
    ["a stored repeat of 'Nightly x30'", graph(node("d", "dusk", { repeat: "Nightly x30" }))],
    ["a stored repeat, with On", graph(node("d", "dusk", { repeat: "Single night", autoResume: "On" }))],
  ];
  for (const [what, g] of on) eq([what, graphResumes(g)], [what, true], what);
  const off: Array<[string, FlowGraphRec]> = [
    ["autoResume Off", graph(node("d", "dusk", { autoResume: "Off" }))],
    ["Off beside a stored campaign repeat",
      graph(node("d", "dusk", { repeat: "Nightly until pool complete", autoResume: "Off" }))],
  ];
  for (const [what, g] of off) eq([what, graphResumes(g)], [what, false], what);
  // The compile takes the FIRST DUSK WINDOW, and so does this.
  eq(graphResumes(graph(node("a", "dusk", { autoResume: "Off" }), node("b", "dusk"))), false,
    "the first DUSK WINDOW decides");
  eq(graphResumes(graph(node("a", "dusk"), node("b", "dusk", { autoResume: "Off" }))), true,
    "the first DUSK WINDOW decides, not the last");
});

test("duskAutoResume agrees with graphResumes on a node's params", () => {
  for (const p of [undefined, null, {}, { autoResume: "On" }, { autoResume: "" },
                   { autoResume: "Off" }, { repeat: "Nightly x30" }] as const) {
    eq(duskAutoResume(p as Record<string, string> | null | undefined),
      graphResumes(graph(node("d", "dusk", (p ?? {}) as Record<string, string>))),
      JSON.stringify(p));
  }
});

// ------------------------------------------------------- 5. repeat retired
// MUTANT "repeat declared again" (`repeat: "Single night"` back in the DUSK
// params). Observed:
//   x DUSK WINDOW declares no `repeat` here, in nodes.py, or as a rowless param: `repeat` is still a param in nodeDefs.ts
test("DUSK WINDOW declares no `repeat` here, in nodes.py, or as a rowless param", () => {
  assert(!("repeat" in NODE_DEFS.dusk.params), "`repeat` is still a param in nodeDefs.ts");
  assert(!NODE_DEFS.dusk.fields.some((f) => f.key === "repeat"), "`repeat` still has a row");
  assert(!(ROWLESS_PARAMS.dusk ?? []).includes("repeat"), "`repeat` is still a rowless param");
  const py = readFileSync(new URL("../../../../../server/astrodeck/flows/nodes.py", import.meta.url),
    "utf8") as string;
  const dusk = py.slice(py.indexOf('"dusk": NodeDef('), py.indexOf('"target": NodeDef('));
  const params = dusk.slice(dusk.indexOf("params={"));
  assert(params.length > 0 && !/"repeat"\s*:/.test(params),
    "nodes.py still declares `repeat` in DUSK's params");
  assert(/"autoResume"\s*:\s*"On"/.test(params), "nodes.py's DUSK params lost autoResume");
});

// MUTANT "nightly tail returns". Observed:
//   x the card's footer says 'one night' for Off and nothing for a stored repeat: a stored repeat of Nightly until pool complete is not read
//     expected "Astro dusk -30m → dawn"
//     got      "Astro dusk -30m → dawn · nightly"
test("the card's footer says 'one night' for Off and nothing for a stored repeat", () => {
  const sum = NODE_DEFS.dusk.sum;
  const base = { ...NODE_DEFS.dusk.params };
  eq(sum(base), "Astro dusk -30m → dawn", "the default says nothing");
  eq(sum({ ...base, autoResume: "Off" }), "Astro dusk -30m → dawn · one night", "Off");
  for (const repeat of ["Single night", "Nightly until pool complete", "Nightly x30"]) {
    eq(sum({ ...base, repeat }), "Astro dusk -30m → dawn",
      `a stored repeat of ${repeat} is not read`);
    eq(sum({ ...base, repeat, autoResume: "Off" }), "Astro dusk -30m → dawn · one night",
      `Off wins over a stored repeat of ${repeat}`);
  }
});

// ------------------------------------------------------------------ tally
const total = passed + failed;
console.log(`w16RunCopyOff.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
