// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_10TimeBound.test.tsx - the per-target time bound is offered in the
// stepped Send to Flow Wizard and in NEW FLOW (#1094, WP 3.10, owner ruling
// D-D).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx <this file>
//
// WHAT IS GUARDED
//
//   1. THE ANSWER TRAVELS. The stepped sheet's body and NEW FLOW's body carry
//      `max_time_min` when a bound was typed, and carry nothing when none was:
//      a sheet nobody touched posts the body it always did. A mosaic and a
//      pool are not asked (the server refuses the key with them).
//   2. A BOUND THAT IS NOT WHOLE MINUTES STOPS GENERATE, in words, on both
//      sheets.
//   3. THE CLASSIC NEW FLOW SHEET, MOUNTED: typing the minutes puts them in
//      the POST; choosing Best of several takes the field away.
//   4. THE EDITOR'S TARGET HAS THE PARAM, and the ceiling mirrors wizard.py.

/* eslint-disable @typescript-eslint/no-explicit-any */

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
installAutoRaf(g);
g.IS_REACT_ACT_ENVIRONMENT = true;

import { readFileSync } from "node:fs";
import { installAutoRaf } from "../../../testing/rafPolyfill";
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react";
import { Simulate } from "react-dom/test-utils";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  return Promise.resolve()
    .then(fn)
    .then(() => { passed++; })
    .catch((e) => { failed++; failures.push(`x ${name}: ${(e as Error).message}`); });
}
const assert = {
  ok(cond: unknown, msg?: string) { if (!cond) throw new Error(msg ?? "not ok"); },
  equal(a: unknown, b: unknown, msg?: string) {
    if (a !== b) throw new Error(msg ?? `${String(a)} !== ${String(b)}`);
  },
};

const { useStore } = await import("../../../store");
const { api } = await import("../../../api");
const FlowWizard = (await import("../FlowWizard")).default;
const stepped = await import("../wizard/wizardModel");
const fresh = await import("../../../next/hubs/session/flows/create/wizardModel");
const { NODE_DEFS } = await import("../nodeDefs");

// ----------------------------------------------------------- the pure rules

const prefill = (over: Record<string, unknown> = {}): any => ({
  name: "M16", ra: "18h 18m 48s", dec: "-13 49 00", rows: 1, cols: 1,
  overlapPct: null, skip: "", angleMode: null, paDeg: null,
  fovX: null, fovY: null, ...over,
});
const answers = (over: Record<string, unknown> = {}): any => ({
  ...stepped.initialAnswers(prefill()), plan: "L 60", ...over,
});

await test("the stepped sheet opens with no bound and posts none", () => {
  const a = answers();
  assert.equal(a.maxTime, "", "opens blank");
  assert.equal("max_time_min" in stepped.wizardBody(prefill(), a), false,
    "a sheet nobody touched posts no bound");
});

await test("a typed bound travels in the stepped body as whole minutes", () => {
  const body = stepped.wizardBody(prefill(), answers({ maxTime: " 60 " }));
  assert.equal(body.max_time_min, 60, `body: ${JSON.stringify(body)}`);
});

await test("a mosaic planned from a framing carries no bound", () => {
  const mosaic = prefill({ rows: 2, cols: 2, angleMode: "Rotate to PA", paDeg: 10,
                           fovX: 1, fovY: 1, overlapPct: 20 });
  const body = stepped.wizardBody(mosaic, answers({ maxTime: "60" }));
  assert.equal("max_time_min" in body, false,
    "the server refuses a bound with the Mosaic kind");
  assert.equal(stepped.stepReason("night", mosaic, answers({ maxTime: "abc" }),
    { field: { xDeg: 1, yDeg: 1 } }), null, "and an unasked answer is no gap");
});

for (const bad of ["abc", "0", "1.5", "-5", "1441", "6e1"]) {
  await test(`the stepped sheet refuses ${JSON.stringify(bad)} in words`, () => {
    const why = stepped.stepReason("night", prefill(), answers({ maxTime: bad }),
      { field: null });
    assert.equal(why, stepped.NEED_MAX_TIME, `reason for ${bad}: ${why}`);
  });
}

await test("a blank bound is no gap on the stepped sheet", () => {
  assert.equal(stepped.stepReason("night", prefill(), answers(), { field: null }), null);
});

await test("the review words the bound, and only when the sheet asks it", () => {
  const a = answers({ maxTime: "90" });
  assert.ok(stepped.nightWords(a).endsWith(", at most 90 min on the target"),
    stepped.nightWords(a));
  assert.ok(!stepped.nightWords(a, false).includes("at most"), "a mosaic says none");
  assert.ok(!stepped.nightWords(answers()).includes("at most"), "no bound, no words");
});

await test("NEW FLOW posts the bound with a single-target kind and not with a pool", () => {
  const night = { ...fresh.NIGHT_DEFAULTS, maxTime: "45" };
  const deep = fresh.wizardBody(fresh.KINDS[0], [], "M16", night);
  assert.equal(deep.max_time_min, 45, `body: ${JSON.stringify(deep)}`);
  const eaa = fresh.wizardBody(fresh.KINDS[2], [], "M16", night);
  assert.equal(eaa.max_time_min, 45, "EAA has one target too");
  const pool = fresh.wizardBody(fresh.KINDS[1], [], "M16, M17", night);
  assert.equal("max_time_min" in pool, false, "a pool picks at dusk: no bound");
  assert.equal("max_time_min" in fresh.wizardBody(fresh.KINDS[0], [], "M16", fresh.NIGHT_DEFAULTS),
    false, "an untouched sheet posts none");
});

await test("NEW FLOW refuses an unreadable bound, except where it is not asked", () => {
  const night = { ...fresh.NIGHT_DEFAULTS, maxTime: "soon" };
  assert.equal(fresh.nightGap(night, fresh.KINDS[0]), stepped.NEED_MAX_TIME);
  assert.equal(fresh.nightGap(night, fresh.KINDS[1]), null,
    "a pool hides the field, so it cannot hold a gap");
  assert.equal(fresh.nightGap(fresh.NIGHT_DEFAULTS, fresh.KINDS[0]), null);
});

await test("the editor's TARGET has the param and the ceiling is wizard.py's", () => {
  assert.equal((NODE_DEFS.target.params as any).maxTime, 0, "default 0 is no bound");
  const py = readFileSync(
    new URL("../../../../../server/astrodeck/flows/wizard.py", import.meta.url), "utf8");
  const m = /^MAX_TIME_MAX_MIN = (\d+)/m.exec(py);
  assert.ok(m, "wizard.py names no MAX_TIME_MAX_MIN");
  assert.equal(Number(m![1]), stepped.MAX_TIME_MAX_MIN, "the ceilings differ");
});

// ----------------------------------------- the classic NEW FLOW sheet, mounted

type Posted = { path: string; body: any };
let posted: Posted[] = [];
const realPost = (api as any).post;

function setUp() {
  const st = useStore.getState() as any;
  useStore.setState({
    flows: { ...st.flows, ui: { ...st.flows.ui, wizardOpen: true } },
    principal: { role: "admin", email: "t@t", caps: ["control.capture"] },
    toasts: [],
  } as any);
  posted = [];
  (api as any).post = async (path: string, body?: any) => {
    posted.push({ path, body });
    return { id: "generated-1" };
  };
}

// One root at a time: a root left mounted keeps rendering the sheet into the
// shared container beside the next test's, and a query finds the stale one.
let mounted: ReturnType<typeof createRoot> | null = null;
function mount() {
  const host = document.getElementById("root")!;
  if (mounted) act(() => { mounted!.unmount(); });
  host.innerHTML = "";
  mounted = createRoot(host);
  act(() => { mounted!.render(React.createElement(FlowWizard)); });
}

const boundInput = (): HTMLInputElement | null =>
  document.querySelector('[data-testid="flows-wiz-max-time"]');
const generateButton = (): HTMLElement =>
  (Array.from(document.querySelectorAll("button")) as HTMLElement[])
    .find((b) => /GENERATE FLOW/i.test(b.textContent || ""))!;
const kindButton = (text: string): HTMLElement =>
  (Array.from(document.querySelectorAll('[role="radio"]')) as HTMLElement[])
    .find((b) => (b.textContent || "").trim().startsWith(text))!;
const typeBound = async (value: string) => {
  const input = boundInput();
  assert.ok(input, "no bound field on the sheet");
  await act(async () => {
    input!.value = value;
    Simulate.change(input!);
  });
};

await test("the mounted NEW FLOW sheet puts the typed minutes in the POST", async () => {
  setUp();
  mount();
  await typeBound("60");
  await act(async () => { generateButton().click(); });
  assert.equal(posted.length, 1, `expected one POST, saw ${posted.length}`);
  assert.equal(posted[0].body.max_time_min, 60,
    `the body carried ${JSON.stringify(posted[0].body)}: the bound was dropped`);
});

await test("the mounted NEW FLOW sheet posts no bound nobody typed", async () => {
  setUp();
  mount();
  await act(async () => { generateButton().click(); });
  assert.equal(posted.length, 1, "expected one POST");
  assert.equal("max_time_min" in posted[0].body, false,
    "an untouched sheet adds nothing to the body");
});

await test("an unreadable bound is not posted and the press says why", async () => {
  setUp();
  mount();
  await typeBound("an hour");
  await act(async () => { generateButton().click(); });
  assert.equal(posted.length, 0, "an unreadable bound was posted");
  const toasts = (useStore.getState() as any).toasts as { title?: string }[];
  assert.ok(toasts.some((t) => t.title === stepped.NEED_MAX_TIME),
    `the press did not say why: ${JSON.stringify(toasts.map((t) => t.title))}`);
});

await test("choosing Best of several takes the bound field away", async () => {
  setUp();
  mount();
  assert.ok(boundInput(), "a deep-sky target is asked its bound");
  await act(async () => { kindButton("Best of several").click(); });
  assert.ok(boundInput() === null, "a pool is not asked");
});

(api as any).post = realPost;
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw3_10TimeBound.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  process.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
