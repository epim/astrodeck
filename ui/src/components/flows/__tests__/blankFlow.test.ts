// blankFlow.test.ts — START BLANK draws a TARGET and a CAPTURE LOOP made the way
// a palette drop makes them, in both UIs.
//
//   Run directly:  npx tsx src/components/flows/__tests__/blankFlow.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Until the integration of mosaic S3 both START BLANK buttons (the classic
// FlowWizard.tsx and the next UI's create/wizardModel.ts) drew a TARGET and a
// SLEW + CENTER, each from the MISSING-KEY defaults (#340):
//
//   * SLEW is a legacy type since S3 (spec 1.7): the palette no longer offers
//     it, and the doctor's L1 tells the operator to delete it, so a flow
//     opened blank arrived with a note about a stage nobody drew.
//   * The TARGET's missing-key defaults are M31's name and coordinates and
//     "Every sub taken", so a blank flow run as it opened slewed to Andromeda
//     (#190's defect through another door) and counted rejected subs.
//
// A node being CREATED takes `createParams` (the "Created as" column over the
// defaults), as a palette drop does; the one kept, loaded or shown never does.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// FlowWizard.tsx is a component module: its imports reach the store, which
// reads `window` when it is built.
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq(got: unknown, want: unknown, msg: string): void {
  const a = JSON.stringify(got);
  const b = JSON.stringify(want);
  if (a !== b) throw new Error(`${msg} expected ${b}, got ${a}`);
}

const { LEGACY_TYPES, createParams } = await import("../nodeDefs");
const classic = await import("../FlowWizard");
const next = await import("../../../next/hubs/session/flows/create/wizardModel");

const BUILDERS: ReadonlyArray<[string, () => Array<{ type: string; params: Record<string, unknown> }>]> = [
  ["FlowWizard.tsx", classic.blankNodes],
  ["wizardModel.ts", next.blankNodes],
];

// RED under mutant "START BLANK draws a SLEW" (in a private scratch copy of
// ui/src, wizardModel.ts's `{ type: "capture", x: 320, y: 120 }` put back to
// `{ type: "slew", x: 320, y: 120 }`), observed:
//
//   x START BLANK draws a TARGET and a CAPTURE LOOP, and no legacy type:
//     wizardModel.ts: the blank flow's stages expected ["target","capture"],
//     got ["target","slew"]
test("START BLANK draws a TARGET and a CAPTURE LOOP, and no legacy type", () => {
  for (const [where, build] of BUILDERS) {
    const types = build().map((n) => n.type);
    eq(types, ["target", "capture"], `${where}: the blank flow's stages`);
    const legacy = types.filter((t) => (LEGACY_TYPES as readonly string[]).includes(t));
    eq(legacy, [], `${where}: a legacy type the palette no longer offers`);
  }
});

// RED under mutant "START BLANK builds from the missing-key defaults" (in a
// private scratch copy of ui/src, FlowWizard.tsx's `createParams(n.type)` put
// back to `{ ...NODE_DEFS[n.type].params }`), observed:
//
//   x each blank node is created as a palette drop creates it: FlowWizard.tsx:
//     target's params expected {"name":"","ra":"","dec":"","rotation":-1,...,
//     "counts":"Accepted subs","frameAnchor":"","angle":"Any angle"}, got
//     {"name":"M31 - Andromeda","ra":"00h 42m 44s",...,"counts":"Every sub
//     taken","frameAnchor":""}
test("each blank node is created as a palette drop creates it", () => {
  for (const [where, build] of BUILDERS) {
    for (const n of build()) {
      eq(n.params, createParams(n.type as any), `${where}: ${n.type}'s params`);
    }
    const target = build().find((n) => n.type === "target")!;
    eq([target.params.name, target.params.ra, target.params.dec, target.params.counts],
      ["", "", "", "Accepted subs"],
      `${where}: a blank TARGET names no object and counts accepted subs`);
  }
});

test("CONTROL: each call builds fresh nodes, with ids of their own", () => {
  for (const [where, build] of BUILDERS) {
    const a = build();
    const b = build();
    const ids = [...a, ...b].map((n: any) => n.id);
    eq(new Set(ids).size, ids.length, `${where}: every id unique`);
    const before = b[0].params.name;
    (a[0].params as any).name = "edited";
    eq(b[0].params.name, before, `${where}: an edit to one build reached another`);
  }
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`blankFlow.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
