// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// wp15uBridgeConstant.test.ts - the RUN button's bridge window has ONE
// definition (#716, WP 1.5u).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/wp15uBridgeConstant.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `flowRunControls.tsx` (the RUN button) and `flowsSlice.ts` (the autosave's
// "is a run of this flow live" read) both believe the optimistic
// `flows.run.phase` for the same window (#647). The slice could not import
// the number from `flowRunControls.tsx`, which imports the slice, so it kept a
// second constant with a test that pinned the two equal. The number now lives
// in `flowRunState.ts`, the module both can import without a cycle, and this
// file pins that it is defined there and nowhere else, so a third copy cannot
// be added and drift while a value pin on the first two still passes.
//
// It reads the shipped sources rather than the modules' exports, because the
// defect is a second definition, which an export check cannot see: two
// constants with equal values are indistinguishable from one by value.
//
// NAMED MUTANT "a second constant comes back" (`flowsSlice.ts`: add
// `export const AUTOSAVE_RUN_BRIDGE_MS = 20_000;` and read it in `runLive`).
// Observed red: "the bridge window is defined once, in flowRunState.ts".
//
// disk-reads: ui/src/

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = fileURLToPath(new URL("../../../", import.meta.url));

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** Every shipped source file under `dir` (tests and declarations excluded),
 *  as a path relative to src/ with forward slashes. */
function shippedSources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== "__tests__") shippedSources(p, out);
    } else if (/\.tsx?$/.test(name) && !name.endsWith(".d.ts")) {
      out.push(relative(SRC, p).split(sep).join("/"));
    }
  }
  return out;
}

const read = (rel: string): string => readFileSync(join(SRC, rel), "utf8");
const SOURCES = shippedSources(SRC);

/** Each source file that defines a bridge-window constant, with its name:
 *  `const X_BRIDGE_MS =`, exported or not. */
function bridgeDefinitions(): string[] {
  const found: string[] = [];
  for (const rel of SOURCES) {
    for (const m of read(rel).matchAll(/\bconst\s+([A-Z0-9_]*BRIDGE_MS)\s*=/g)) {
      found.push(`${rel}:${m[1]}`);
    }
  }
  return found;
}

test("the scan reads the shipped sources it is about", () => {
  assert(SOURCES.length >= 100, `only ${SOURCES.length} sources found under src/`);
  assert(SOURCES.includes("components/flows/flowsSlice.ts")
    && SOURCES.includes("components/flows/flowRunControls.tsx"),
    "the slice or the RUN button's module was not scanned");
  assert(SOURCES.every((r) => !r.includes("__tests__")), "a test file was scanned");
});

test("the bridge window is defined once, in flowRunState.ts", () => {
  const defs = bridgeDefinitions();
  assert(defs.length === 1 && defs[0] === "components/flows/flowRunState.ts:RUN_PHASE_BRIDGE_MS",
    `the bridge window is defined once, in flowRunState.ts; found ${JSON.stringify(defs)}`);
});

test("the RUN button and the autosave both import it from flowRunState.ts", () => {
  for (const rel of ["components/flows/flowRunControls.tsx", "components/flows/flowsSlice.ts"]) {
    const text = read(rel);
    const importsIt = /import\s*\{[^}]*\bRUN_PHASE_BRIDGE_MS\b[^}]*\}\s*from\s*"\.\/flowRunState"/.test(text);
    assert(importsIt, `${rel} does not import RUN_PHASE_BRIDGE_MS from ./flowRunState`);
    assert(/\bRUN_PHASE_BRIDGE_MS\b/.test(text.replace(/import[^;]*;/g, "").replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\/\/[^\n]*/g, "")), `${rel} imports RUN_PHASE_BRIDGE_MS but never reads it`);
  }
});

test("the window is the #647 value, 20 s", () => {
  // A value pin: the two readers agree because there is one number, and this
  // is the number the RUN button's STOP label was specified to trust.
  const m = /export const RUN_PHASE_BRIDGE_MS = ([0-9_]+);/.exec(read("components/flows/flowRunState.ts"));
  assert(m !== null, "flowRunState.ts does not export RUN_PHASE_BRIDGE_MS as a plain number");
  assert(Number((m as RegExpExecArray)[1].replace(/_/g, "")) === 20_000,
    `the bridge window is ${m?.[1]}, not 20_000`);
});

console.log(`wp15uBridgeConstant.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
