// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// flowSettingsParity.test.ts — the flow-level settings table, mirrored.
//
// `FLOW_SETTINGS` lives in server/astrodeck/flows/models.py and is copied into
// flowsTypes.ts because no endpoint serves it (the same §G-4 situation as the
// node table). A default that drifts between the two is the quietest failure
// this surface has: the editor would show "Shoot later targets, then come
// back" on a flow the engine runs as "Wait for the mosaic", or the reverse,
// and nothing anywhere would error. So, as nodeDefs.test.ts does for the
// vocabulary, this file PARSES models.py and compares, rather than typing the
// expectations a third time.
//
// It also holds the resolver to the server's rule (models.py
// `resolve_setting`): a missing key, null, or a value this build does not know
// all read as the default; a declared option reads as itself.
//
// Run alone:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/flowSettingsParity.test.ts
import { FLOW_SETTINGS, flowSetting } from "../flowsTypes";
import type { FlowGraphRec } from "../flowsTypes";
// @ts-ignore  no @types/node guaranteed; tsx supplies fs at runtime
import { readFileSync } from "node:fs";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}

// ================================================================ models.py
const MODELS_PY_REL = "../../../../../server/astrodeck/flows/models.py";
const modelsPy: string = (() => {
  try {
    return readFileSync(new URL(MODELS_PY_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    // A missing models.py must FAIL, never skip: a skipped cross-check reads as
    // a green run while the only guard on this mirror is gone.
    throw new Error(`cannot read ${MODELS_PY_REL}; the settings parity cannot run: ${(e as Error).message}`);
  }
})();

/** The `FLOW_SETTINGS = {...}` literal, as JSON. The server writes it JSON
 *  shaped on purpose (double quotes, lists, no comments inside), so the only
 *  conversion is dropping Python's trailing commas. Anything this cannot read
 *  throws rather than guessing. */
function parseFlowSettings(): Record<string, { default: string; options: string[] }> {
  const at = modelsPy.indexOf("FLOW_SETTINGS: dict[str, dict] = {");
  assert(at >= 0, "FLOW_SETTINGS not found in models.py — did it move or get renamed?");
  const open = modelsPy.indexOf("{", modelsPy.indexOf("=", at));
  let depth = 0;
  let inStr = false;
  let end = -1;
  for (let i = open; i < modelsPy.length; i++) {
    const c = modelsPy[i];
    if (inStr) {
      if (c === "\\") { i++; continue; }
      if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') { inStr = true; continue; }
    if (c === "#") throw new Error("a comment inside FLOW_SETTINGS; keep the literal JSON shaped");
    if (c === "{" || c === "[") depth++;
    else if (c === "}" || c === "]") { depth--; if (depth === 0) { end = i; break; } }
  }
  assert(end > open, "FLOW_SETTINGS has no closing brace");
  const json = modelsPy.slice(open, end + 1).replace(/,(\s*)([}\]])/g, "$1$2");
  return JSON.parse(json);
}
const PY = parseFlowSettings();

test("parser sanity: models.py's FLOW_SETTINGS parsed to at least one setting", () => {
  assert(Object.keys(PY).length > 0, "FLOW_SETTINGS parsed to nothing");
  eq(typeof PY.whenWaiting?.default, "string", "whenWaiting.default parsed");
});

test("the mirror has exactly the server's settings", () => {
  eq(Object.keys(FLOW_SETTINGS).sort().join(","), Object.keys(PY).sort().join(","),
    "setting keys: a key only the UI has is a control the engine ignores; one only "
    + "the server has is a behaviour the operator cannot choose");
});

test("every default and every option list matches models.py, in order", () => {
  // Mutant 'FLOW_SETTINGS default drift' (the TS whenWaiting default set to
  // "Wait for the mosaic"), observed:
  //   x every default and every option list matches models.py, in order:
  //   whenWaiting.default: the editor would show one behaviour while the
  //   engine runs another expected "Shoot later targets, then come back",
  //   got "Wait for the mosaic"
  for (const key of Object.keys(PY)) {
    const mine = FLOW_SETTINGS[key as keyof typeof FLOW_SETTINGS];
    eq(mine.default, PY[key].default,
      `${key}.default: the editor would show one behaviour while the engine runs another`);
    eq([...mine.options].join("|"), PY[key].options.join("|"), `${key}.options`);
    assert(PY[key].options.includes(PY[key].default), `${key}: the default is not an option`);
  }
});

test("ruling 1: whenWaiting defaults to shooting later targets", () => {
  // The owner's words, 2026-09-24: "No sense in wasting time due to an
  // obstruction." Pinned by value as well as by parity, so both copies cannot
  // drift together.
  eq(FLOW_SETTINGS.whenWaiting.default, "Shoot later targets, then come back", "whenWaiting default");
  eq(FLOW_SETTINGS.whenWaiting.options.join("|"),
    "Shoot later targets, then come back|Wait for the mosaic", "whenWaiting options");
});

test("the resolver mirrors resolve_setting", () => {
  // Mutant 'flowSetting ignores the stored value' (returns spec.default
  // unconditionally), observed:
  //   x the resolver mirrors resolve_setting: a stored option reads as itself
  //   expected "Wait for the mosaic", got "Shoot later targets, then come back"
  const d = "Shoot later targets, then come back";
  eq(flowSetting(undefined, "whenWaiting"), d, "a graph with no settings reads the default");
  eq(flowSetting({}, "whenWaiting"), d, "a missing key reads the default");
  eq(flowSetting({ whenWaiting: null }, "whenWaiting"), d, "null reads the default");
  eq(flowSetting({ whenWaiting: "Wait for the mosaic" }, "whenWaiting"), "Wait for the mosaic",
    "a stored option reads as itself");
  eq(flowSetting({ whenWaiting: "Teleport" }, "whenWaiting"), d,
    "a value this build does not know reads the default, as the engine will run it");
  eq(flowSetting({ whenWaiting: 3 }, "whenWaiting"), d, "a non-string reads the default");
});

test("a graph record without settings is still a graph record", () => {
  // Every graph saved before the mosaic slice, and every `{ nodes, edges }`
  // built on this side, has no `settings`. The type must keep accepting it.
  const g: FlowGraphRec = { nodes: [], edges: [] };
  eq(flowSetting(g.settings, "whenWaiting"), "Shoot later targets, then come back", "bare graph");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`flowSettingsParity.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
