// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w87PositionUnknownNote.test.ts - the position-unknown note is ONE constant
// both UIs show, and it advises no move (#829); and no comment names the wrong
// home of RUN_PHASE_BRIDGE_MS (#1151).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/lib/__tests__/w87PositionUnknownNote.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// disk-reads: ui/src/views/MountView.tsx, ui/src/next/hubs/rig/sheets/mount.tsx
// disk-reads: ui/src/components/flows/flowsSlice.ts, ui/src/components/flows/flowsTypes.ts
// disk-reads: ui/src/next/hubs/session/flows/canvas/FlowCanvasToolbar.tsx
//
// The note used to end "Holding a pad key still moves the tube", false in NINA
// mode where a held key moves nothing, and a sentence naming a move is advice
// to move while the position is unknown. The classic view and the new sheet
// render the SAME constant; this reads their sources so a literal copy in
// either cannot drift from the other behind a test that checks the constant.
//
// NAMED MUTANT "the note text drifts between the two UIs" (`MountView.tsx`:
// render a literal sentence in place of `{POSITION_UNKNOWN_NOTE}`). Observed
// red: "MountView.tsx does not render POSITION_UNKNOWN_NOTE".

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { POSITION_UNKNOWN_NOTE } from "../slewController";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const read = (rel: string): string =>
  readFileSync(fileURLToPath(new URL(`../../${rel}`, import.meta.url)), "utf8");

const CLASSIC = "views/MountView.tsx";
const SHEET = "next/hubs/rig/sheets/mount.tsx";

test("both UIs render the shared note, by the constant", () => {
  assert(/<[^>]*>\s*\{POSITION_UNKNOWN_NOTE\}\s*</.test(read(CLASSIC)),
    "MountView.tsx does not render POSITION_UNKNOWN_NOTE");
  assert(/text=\{POSITION_UNKNOWN_NOTE\}/.test(read(SHEET)),
    "mount.tsx does not render POSITION_UNKNOWN_NOTE");
  for (const rel of [CLASSIC, SHEET]) {
    assert(/import[^;]*\bPOSITION_UNKNOWN_NOTE\b[^;]*from\s+"[^"]*slewController"/.test(read(rel)),
      `${rel} does not import POSITION_UNKNOWN_NOTE from slewController`);
    assert(!read(rel).includes("does not know where the tube points"),
      `${rel} carries its own copy of the note's sentence`);
  }
});

test("the note advises no move while the position is unknown", () => {
  assert(!/pad key|hold|slew|go-?to|nudge|move/i.test(POSITION_UNKNOWN_NOTE),
    `the note names a move: ${POSITION_UNKNOWN_NOTE}`);
});

test("the note keeps what it must say: ordinary, and what teaches the mount", () => {
  assert(/normal/.test(POSITION_UNKNOWN_NOTE) && /start of a night/.test(POSITION_UNKNOWN_NOTE),
    "the note does not say this is the ordinary start of a night");
  assert(/TRUST POSITION/.test(POSITION_UNKNOWN_NOTE) && /solve and sync/.test(POSITION_UNKNOWN_NOTE),
    "the note does not say what teaches the mount where it points");
});

test("no comment says RUN_PHASE_BRIDGE_MS lives in flowRunControls.tsx", () => {
  const files = ["components/flows/flowsSlice.ts", "components/flows/flowsTypes.ts",
    "next/hubs/session/flows/canvas/FlowCanvasToolbar.tsx"];
  for (const rel of files) {
    const lines = read(rel).split(/\r?\n/);
    lines.forEach((line, i) => {
      if (!/RUN_PHASE_BRIDGE_MS/.test(line)) return;
      const around = lines.slice(Math.max(0, i - 1), i + 2).join("\n");
      assert(!/flowRunControls\.tsx[^\n]*RUN_PHASE_BRIDGE_MS|RUN_PHASE_BRIDGE_MS[^\n]*\(`flowRunControls\.tsx`\)/.test(around)
        && !/`flowRunControls\.tsx`'s `RUN_PHASE_BRIDGE_MS`/.test(around),
        `${rel}:${i + 1} names flowRunControls.tsx as the home of RUN_PHASE_BRIDGE_MS`);
    });
  }
});

const total = passed + failed;
console.log(`w87PositionUnknownNote.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as unknown as { process: { exitCode: number } }).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
