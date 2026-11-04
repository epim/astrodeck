// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w2_10NewFlowNight.test.tsx - NEW FLOW asks how the night ends and whether to
// auto-resume (#718, WP 2.10, owner ruling D-G), classic sheet, MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx <this file>
//
// The test reads the request body the sheet posts. A handler that drew the two
// questions and posted the defaults would get a valid 200 describing a
// different night, so it picks the NON-default answers (auto-resume Off, a
// Clock time stop at 03:30) and asserts them, and asserts that a sheet nobody
// touched posts the three keys it always did.

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

function mount() {
  const host = document.getElementById("root")!;
  host.innerHTML = "";
  const root = createRoot(host);
  act(() => { root.render(React.createElement(FlowWizard)); });
  return { host, root };
}

/** A button whose whole text is `text`: the choices are exact strings. */
const choice = (text: string): HTMLElement | null =>
  (Array.from(document.querySelectorAll('[role="radio"]')) as HTMLElement[])
    .find((b) => (b.textContent || "").trim() === text) ?? null;
const generateButton = (): HTMLElement =>
  (Array.from(document.querySelectorAll("button")) as HTMLElement[])
    .find((b) => /GENERATE FLOW/i.test(b.textContent || ""))!;
const clockInput = (): HTMLInputElement | null =>
  document.querySelector('input[aria-label^="Stop time"]');

await test("the two new questions render, with the stepped sheet's choices, on the defaults", () => {
  setUp();
  mount();
  for (const c of [...stepped.DUSK_STOPS, ...stepped.AUTO_RESUME_CHOICES]) {
    assert.ok(choice(c), `no ${c} choice`);
  }
  assert.equal(choice("Dawn")!.getAttribute("aria-checked"), "true", "the stop opens on Dawn");
  assert.equal(choice("On")!.getAttribute("aria-checked"), "true", "auto-resume opens On");
  assert.ok(clockInput() === null, "the clock is asked only beside Clock time");
});

await test("an unanswered sheet posts exactly the three keys it always did", async () => {
  setUp();
  mount();
  await act(async () => { generateButton().click(); });
  assert.equal(posted.length, 1, `expected one POST, saw ${posted.length}`);
  assert.equal(Object.keys(posted[0].body).sort().join(","), "kind,options,target",
    "an unanswered night and resume add nothing to the body");
});

await test("auto-resume Off travels in the request body", async () => {
  setUp();
  mount();
  await act(async () => { choice("Off")!.click(); });
  assert.equal(choice("Off")!.getAttribute("aria-checked"), "true", "the choice shows");
  await act(async () => { generateButton().click(); });
  assert.equal(posted.length, 1, "expected one POST");
  assert.equal(posted[0].body.auto_resume, "Off",
    `the body carried ${JSON.stringify(posted[0].body)}: the auto-resume answer was dropped`);
  assert.equal(posted[0].body.stop, undefined, "and no stop nobody chose");
});

await test("a Clock time stop travels with its time", async () => {
  setUp();
  mount();
  await act(async () => { choice("Clock time")!.click(); });
  const input = clockInput();
  assert.ok(input, "no stop-time field beside Clock time");
  await act(async () => {
    input!.value = "03:30";
    Simulate.change(input!);
  });
  await act(async () => { choice("Off")!.click(); });
  await act(async () => { generateButton().click(); });
  assert.equal(posted.length, 1, "expected one POST");
  const body = posted[0].body;
  assert.equal(body.stop, "Clock time", `body: ${JSON.stringify(body)}`);
  assert.equal(body.stop_clock, "03:30", `body: ${JSON.stringify(body)}`);
  assert.equal(body.auto_resume, "Off", `body: ${JSON.stringify(body)}`);
});

await test("a Clock time stop with no time cannot be generated, and says why", async () => {
  setUp();
  mount();
  await act(async () => { choice("Clock time")!.click(); });
  await act(async () => { generateButton().click(); });
  assert.equal(posted.length, 0, "a stop with no time was posted");
  const toasts = (useStore.getState() as any).toasts as { title?: string }[];
  assert.ok(toasts.some((t) => t.title === stepped.NEED_STOP_CLOCK),
    `the press did not say why: ${JSON.stringify(toasts.map((t) => t.title))}`);
});

(api as any).post = realPost;
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw2_10NewFlowNight.test (classic): ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  process.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
