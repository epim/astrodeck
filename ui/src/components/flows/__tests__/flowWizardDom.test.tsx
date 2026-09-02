// flowWizardDom.test.tsx — the wizard sheet's GENERATE FLOW button, MOUNTED.
//
//   Run directly:  npx tsx src/components/flows/__tests__/flowWizardDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// For its whole life this button was an honest-disabled control: the generator
// existed in server/astrodeck/flows/wizard.py and no route reached it, so the
// sheet collected three answers and could not act on them. POST /api/flows/wizard
// closed that. What this file pins is that the sheet now ASKS, and asks with the
// answers the operator gave.
//
// The failure to guard against is subtle and would look fine: a handler that
// posts defaults. Every kind and every chip is a string the server validates
// against wizard.py's own constants, so sending `kind: "Deep-sky target"` when
// the operator picked "EAA quick look" produces a valid 200 and the wrong night.
// So the test picks the NON-default kind, toggles a chip OFF and another ON, and
// asserts the exact body.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
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
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
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

// ------------------------------------------------------------------- harness
type Posted = { path: string; body: any };
let posted: Posted[] = [];
let nextId = "generated-1";

const realPost = (api as any).post;
function stubPost() {
  posted = [];
  (api as any).post = async (path: string, body?: any) => {
    posted.push({ path, body });
    return { id: nextId };
  };
}

function mount() {
  const host = document.getElementById("root")!;
  host.innerHTML = "";
  const root = createRoot(host);
  act(() => { root.render(React.createElement(FlowWizard)); });
  return { host, root };
}

function byText(_host: HTMLElement, re: RegExp): HTMLElement | null {
  const all = Array.from(document.querySelectorAll("button")) as HTMLElement[];
  return all.find((b) => re.test(b.textContent || "")) ?? null;
}

function setUp() {
  // The sheet reads `flows.ui.wizardOpen` (flowsSlice) and its capability
  // through `principal.caps` -- not a top-level `ui` or `caps` object. Setting
  // the wrong shape renders an empty overlay and every assertion below fails
  // with "no button", which looks like a broken component rather than a broken
  // fixture.
  const st = useStore.getState() as any;
  useStore.setState({
    flows: { ...st.flows, ui: { ...st.flows.ui, wizardOpen: true } },
    principal: { role: "admin", email: "t@t", caps: ["control.capture"] },
  } as any);
  stubPost();
}

// --------------------------------------------------------------------- tests
await test("GENERATE FLOW is no longer permanently blocked", async () => {
  setUp();
  const { host } = mount();
  const btn = byText(host, /GENERATE FLOW/i);
  assert.ok(btn, "the sheet has no GENERATE FLOW button");
  assert.ok(
    btn!.getAttribute("aria-disabled") !== "true" && !(btn as any).disabled,
    "GENERATE FLOW is still disabled with a route behind it",
  );
});

await test("pressing it posts to the wizard route", async () => {
  setUp();
  const { host } = mount();
  const btn = byText(host, /GENERATE FLOW/i)!;
  await act(async () => { btn.click(); });
  assert.equal(posted.length, 1, `expected one POST, saw ${posted.length}`);
  assert.ok(
    /\/flows\/wizard$/.test(posted[0].path),
    `posted to ${posted[0].path} instead of the wizard route`,
  );
});

await test("it sends the answers the operator gave, not the defaults", async () => {
  setUp();
  const { host } = mount();

  // pick the NON-default kind, and change the chip set away from its default
  const eaa = Array.from(document.querySelectorAll("button"))
    .find((b) => /EAA quick look/i.test(b.textContent || "")) as HTMLElement;
  assert.ok(eaa, "no EAA kind button");
  await act(async () => { eaa.click(); });

  const dome = Array.from(document.querySelectorAll("button"))
    .find((b) => /^\s*Dome\s*$/i.test(b.textContent || "")) as HTMLElement;
  assert.ok(dome, "no Dome chip");
  await act(async () => { dome.click(); });

  const input = document.querySelector('input[type="text"], input:not([type])') as HTMLInputElement;
  assert.ok(input, "no target field");
  // Overlay portals onto document.body, which is OUTSIDE the #root container
  // React attached its delegated listeners to, so a native `input` event never
  // reaches the component: the DOM node held "NGC 7129" while state stayed "".
  // Simulate calls the handler on the fibre directly and is immune to that.
  await act(async () => {
    input.value = "NGC 7129";
    Simulate.change(input);
  });

  const btn = byText(host, /GENERATE FLOW/i)!;
  await act(async () => { btn.click(); });

  assert.equal(posted.length, 1, "expected exactly one POST");
  const body = posted[0].body;
  assert.equal(body.kind, "EAA quick look",
    `sent kind ${JSON.stringify(body.kind)} — the operator's choice was dropped`);
  assert.ok(Array.isArray(body.options), "options must be an array");
  assert.ok(body.options.includes("Dome"),
    `Dome was toggled on but the body carried ${JSON.stringify(body.options)}`);
  assert.equal(body.target, "NGC 7129",
    `sent target ${JSON.stringify(body.target)}`);
});

await test("a failed generate surfaces instead of closing the sheet", async () => {
  setUp();
  (api as any).post = async () => { throw new Error("boom"); };
  const { host } = mount();
  const btn = byText(host, /GENERATE FLOW/i)!;
  await act(async () => { btn.click(); });
  assert.equal(
    (useStore.getState() as any).flows.ui.wizardOpen, true,
    "the sheet closed on a failed generate, losing the operator's answers",
  );
});

// -------------------------------------------------------------------- report
(api as any).post = realPost;
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nflowWizardDom.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  process.exitCode = 1;
}
