// captureFilterDial.test.ts — Capture's FILT ring (#181/#179).
//
//   Run directly:  npx tsx src/components/capture/__tests__/captureFilterDial.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// #181 asked for the dial on Capture. It already carried EXP/GAIN/BIN/OFFS and
// had no filter category at all, on the one screen in the app with a hand-rolled
// filter panel sitting right under it — so the gap was FILT, and #179 could not
// retire that panel until the dial could do what it does.
//
// What it does that no other filter control does is offer the BLACKOUT slots.
// The shared builder drops them deliberately, and must; this one keeps them and
// labels them, which is the whole reason it is a separate function rather than
// an argument to that one.

/* eslint-disable @typescript-eslint/no-explicit-any */

(globalThis as any).window = { location: { pathname: "/" } };

const { captureFilterDialCategory } = await import("../captureFilterDial");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// The sim rig's eight-slot wheel, in its real order. Slot 7 is the blackout.
const WHEEL = {
  names: ["L", "R", "G", "B", "Ha", "OIII", "SII", "Dark"],
  opaque: [false, false, false, false, false, false, false, true],
  position: 2,
};

function opts(cat: any): Array<{ id: string; label: string }> {
  return [...(cat.options as Array<{ id: string; label: string }>)];
}

test("every slot on the wheel is offered, including the blackout", () => {
  const cat = captureFilterDialCategory(WHEEL, () => {});
  assert(!!cat, "Capture's dial has no FILT ring — the #181 gap is still open");
  const o = opts(cat);
  assert(o.length === 8, `${o.length} of 8 slots offered`);
  assert(o.some((x) => /Dark — blackout/.test(x.label)),
    "the blackout slot was dropped. Capture is the one screen where parking on "
    + "it is a real workflow — it is how you shoot darks on a rig with a wheel — "
    + "and dropping it here loses a function the panel below still has");
});

test("a blackout slot SAYS so, so picking it is a choice and not a mistake", () => {
  const cat = captureFilterDialCategory(WHEEL, () => {})!;
  const dark = opts(cat).find((x) => x.id === "7")!;
  assert(dark.label.includes("blackout"),
    `slot 7 reads "${dark.label}" — indistinguishable from an imaging filter`);
  const l = opts(cat).find((x) => x.id === "0")!;
  assert(!l.label.includes("blackout"), `slot 0 reads "${l.label}"`);
});

test("the FIRST slot is offered — L is not swallowed", () => {
  // The 2026-08-08 report was "the dial shows R G B S H O and no L". Whatever
  // the ring does with slot 0, the builder must hand it over.
  const cat = captureFilterDialCategory(WHEEL, () => {})!;
  assert(opts(cat)[0].label === "L", JSON.stringify(opts(cat)[0]));
});

test("options are keyed by SLOT INDEX, never by name", () => {
  // Two slots may carry the same name, and the wheel only knows positions. The
  // 2026-08-02 offset bug shifted every frame's FILTER header by one slot; a
  // name-keyed control cannot even express which slot it meant.
  const picked: number[] = [];
  const cat = captureFilterDialCategory(
    { names: ["Ha", "Ha", "Dark"], opaque: [false, false, true], position: 0 },
    (s) => picked.push(s))!;
  const o = opts(cat);
  assert(o.map((x) => x.id).join(",") === "0,1,2", o.map((x) => x.id).join(","));
  cat.kind !== "entry" && (cat as any).onPick("1");
  assert(picked.length === 1 && picked[0] === 1,
    `picking the SECOND Ha commanded slot ${JSON.stringify(picked)}`);
});

test("the mark is on the slot the wheel is ON", () => {
  const cat = captureFilterDialCategory(WHEEL, () => {})!;
  assert((cat as any).selected === "2",
    `the ring marks ${JSON.stringify((cat as any).selected)} while the wheel is at 2`);
});

test("an unnamed slot is a gap in the configuration, not an option", () => {
  const cat = captureFilterDialCategory(
    { names: ["L", "", null, "  ", "Ha"], position: 0 }, () => {})!;
  assert(opts(cat).map((x) => x.label).join(",") === "L,Ha",
    opts(cat).map((x) => x.label).join(","));
  assert(opts(cat).map((x) => x.id).join(",") === "0,4",
    `blank slots were dropped but the indices shifted: ${opts(cat).map((x) => x.id)}`);
});

test("no wheel, no ring", () => {
  assert(captureFilterDialCategory(undefined, () => {}) === null, "undefined wheel");
  assert(captureFilterDialCategory({ names: [] }, () => {}) === null, "empty wheel");
  assert(captureFilterDialCategory({ names: ["", null] }, () => {}) === null,
    "a wheel with no named slots offers nothing rather than an empty ring");
});

const total = passed + failed;
console.log(`captureFilterDial.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
