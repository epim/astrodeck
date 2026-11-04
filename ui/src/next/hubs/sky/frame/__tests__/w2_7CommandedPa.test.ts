// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w2_7CommandedPa.test.ts - a Sky framing of a grid always commands its angle (#790).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx <this file>
//
// A framing session starts at rotation 0, and `commandedPa` read that as "no
// angle chosen": right for ONE panel (a "just show me this" tap must not bolt a
// rotate loop onto the flow), wrong for a grid. A grid is laid out at an angle
// whatever the dial says, and 0 is north up, the angle most mosaics are
// planned around (#150 settled it on the server). A grid sent as "Any angle" is
// refused (M2), so a north-up 2x2 framed on the Sky never reached the
// per-panel correction that commands its corners from a block at layout 0.
//
// Named mutant: `commandedPa` back to the dead-band for every panel count.

/* eslint-disable @typescript-eslint/no-explicit-any */

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

const { commandedPa, framingPrefill } = await import("../mosaic");
const { KIND_MOSAIC, MOSAIC_ANGLES, initialAnswers, wizardBody } = await import(
  "../../../../../components/flows/wizard/wizardModel"
);

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

const FOV = { fov_x_deg: 2.0, fov_y_deg: 1.33 };
const framing = (rows: number, cols: number, rotation_deg: number) => ({
  target: { id: "probe", name: "Probe" },
  center: { ra_hours: 6.0, dec_deg: 75.0 },
  rotation_deg,
  mosaic: { rows, cols, overlap: 0.1 },
});

test("a grid at the unturned dial commands north up, not 'no angle'", () => {
  eq(commandedPa(0, 4), 0, "a 2x2 at 0:");
  eq(commandedPa(0, 2), 0, "a 2x1 at 0:");
  eq(commandedPa(0.4, 4), 0.4, "a grid has no dead-band:");
  eq(commandedPa(30, 4), 30, "a grid at a real angle:");
});

test("one panel keeps the dead-band: a fresh session commands nothing", () => {
  eq(commandedPa(0, 1), null, "a 1x1 at 0:");
  eq(commandedPa(0.4, 1), null, "a 1x1 inside the dead-band:");
  eq(commandedPa(30, 1), 30, "a 1x1 at a real angle:");
});

test("the Sky door hands a north-up 2x2 to the wizard as PA 0", () => {
  eq(framingPrefill(framing(2, 2, 0), FOV).paDeg, 0, "2x2 at 0:");
  eq(framingPrefill(framing(1, 1, 0), FOV).paDeg, null, "1x1 at 0:");
  eq(framingPrefill(framing(2, 2, 30), FOV).paDeg, 30, "2x2 at 30:");
});

test("a north-up 2x2 reaches the generator as 'Rotate to PA 0', the block that corrects per panel", () => {
  const prefill = framingPrefill(framing(2, 2, 0), FOV);
  const answers = { ...initialAnswers(prefill), angleMode: MOSAIC_ANGLES[0] };
  eq(answers.pa, "0", "the PA box arrived with the layout angle:");
  const body = wizardBody(prefill, answers);
  eq(body.kind, KIND_MOSAIC, "kind:");
  eq(body.pa_deg, 0, "pa_deg:");
  eq(body.angle_mode, MOSAIC_ANGLES[0], "angle_mode:");
  eq([body.rows, body.cols].join("x"), "2x2", "grid:");
});

const total = passed + failed;
console.log(`w2_7CommandedPa.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
