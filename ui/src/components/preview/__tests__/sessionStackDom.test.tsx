// sessionStackDom.test.tsx: the Session stack panel, MOUNTED and SWITCHED.
//
//   Run directly:  npx tsx src/components/preview/__tests__/sessionStackDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// What is worth asserting here is what a static read of the component cannot
// tell you:
//
//   * the switch is wired to the SERVER's state, not to a local boolean. A
//     toggle that flips a useState and never posts looks identical on screen
//     and does nothing at all;
//   * the picture's URL carries `seq`. The route answers `no-store`, but a
//     browser never re-requests an <img> whose src has not changed, so without
//     that key the panel shows frame one for the rest of the night;
//   * the per-filter breakdown is the SERVER's channel list. A panel that says
//     "12 frames" without saying which filters they were through is the number
//     an operator cannot act on;
//   * a viewer cannot press any of it.
//
// WHAT THIS CANNOT DO: jsdom fetches no images, so the <img> is asserted by its
// src and alt text rather than by anything being painted.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Before any import: lib/base reads window.location at module scope.
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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

// ------------------------------------------------------------- fake server
type Chan = { channel: string; frames: number; integrated_s: number; rejected: number };
function status(over: Partial<any> = {}): any {
  const channels: Chan[] = over.channels ?? [];
  return {
    enabled: false, target: "", seq: 0, channels,
    frames: channels.reduce((a, c) => a + c.frames, 0),
    integrated_s: channels.reduce((a, c) => a + c.integrated_s, 0),
    rejected: 0, mode: null, downsample: 4,
    has_image: channels.length > 0, render_age_s: null,
    ...over,
  };
}

const LIVE: Chan[] = [
  { channel: "R", frames: 12, integrated_s: 1440, rejected: 1 },
  { channel: "G", frames: 9, integrated_s: 1080, rejected: 0 },
  { channel: "B", frames: 7, integrated_s: 840, rejected: 0 },
];

const posts: string[] = [];
let server: any = status();

win.fetch = async (input: any, init?: any) => {
  const path = String(input);
  const method = (init?.method ?? "GET").toUpperCase();
  if (method === "POST") {
    posts.push(path);
    if (path.endsWith("/start")) server = { ...server, enabled: true };
    if (path.endsWith("/stop")) server = status();
    if (path.endsWith("/reset")) {
      server = { ...status(), enabled: true, target: server.target };
    }
  }
  return {
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: async () => server,
    text: async () => JSON.stringify(server),
  };
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "Image", "localStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
  "matchMedia", "ResizeObserver", "fetch",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const SessionStack = (await import("../SessionStack")).default;
const { fmtIntegration } = await import("../../../api/sessionStack");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  return Promise.resolve()
    .then(fn)
    .then(() => { passed++; })
    .catch((e) => { failed++; failures.push(`x ${name}: ${(e as Error).message}`); });
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;

function setPrincipal(caps: string[]): void {
  useStore.setState({
    principal: { id: "u1", name: "tester", role: "operator", caps } as any,
  } as any);
}

/** Mount and let the on-mount status fetch resolve. */
async function mount(): Promise<void> {
  if (rootRef) await act(async () => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  await act(async () => { rootRef!.render(createElement(SessionStack)); });
  await settle();
}

/** Drain the microtask queue AND one macrotask turn, so the status fetch's
 *  `.then` chain has actually re-rendered before anything is asserted. Two
 *  microtask flushes are not enough: the api wrapper awaits `res.json()`. */
async function settle(): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const text = (): string => container.textContent || "";
const toggleEl = (): any => container.querySelector('[role="switch"]');
const img = (): any => container.querySelector("img");
const resetBtn = (): any =>
  [...container.querySelectorAll("button")].find(
    (b: any) => /reset/i.test(b.textContent || ""),
  );

async function click(el: any): Promise<void> {
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  });
  await settle();
}

// ---------------------------------------------------------------------- tests
await test("off by default, and it says so rather than showing an empty frame", async () => {
  setPrincipal(["control.capture"]);
  server = status();
  posts.length = 0;
  await mount();
  assert(toggleEl() != null, "no switch rendered");
  assert(toggleEl().getAttribute("aria-checked") === "false",
    "the switch reads ON against a server that says the stack is off");
  assert(/not being stacked/i.test(text()),
    `no explanation of the off state: ${JSON.stringify(text().slice(0, 160))}`);
  assert(img() == null, "an image is shown while nothing is stacked");
});

await test("the switch posts to the server, it does not just flip a local flag", async () => {
  setPrincipal(["control.capture"]);
  server = status();
  posts.length = 0;
  await mount();
  await click(toggleEl());
  assert(posts.some((p) => p.endsWith("/api/sequence/stack/start")),
    `nothing was posted; the switch is decorative. posts=${JSON.stringify(posts)}`);
  assert(toggleEl().getAttribute("aria-checked") === "true",
    "the switch did not adopt the server's reply");

  await click(toggleEl());
  assert(posts.some((p) => p.endsWith("/api/sequence/stack/stop")),
    `switching off posted nothing. posts=${JSON.stringify(posts)}`);
  assert(toggleEl().getAttribute("aria-checked") === "false", "the switch stuck on");
});

await test("on with nothing stacked yet says so instead of showing a broken image", async () => {
  setPrincipal(["control.capture"]);
  server = status({ enabled: true });
  await mount();
  assert(img() == null, "an <img> was rendered with no composite to load");
  assert(/waiting for the first accepted sub/i.test(text()), text().slice(0, 200));
});

await test("a live stack shows the composite, keyed on seq so it refreshes", async () => {
  setPrincipal(["control.capture"]);
  server = status({
    enabled: true, target: "NGC 6946", seq: 41, mode: "rgb", channels: LIVE,
  });
  await mount();
  const el = img();
  assert(el != null, "no composite rendered for a stack with 28 frames in it");
  const src = String(el.getAttribute("src"));
  assert(src.includes("/api/sequence/stack/preview.jpg"), src);
  assert(/[?&]seq=41(\D|$)/.test(src),
    `the image URL carries no seq (${src}): the browser will never refetch it, ` +
    "so the panel freezes on the first frame of the run");
  assert(/NGC 6946/.test(String(el.getAttribute("alt"))),
    "the composite has no alt text naming what it is of");
});

await test("the readout names every filter, its frames and its integration", async () => {
  setPrincipal(["control.capture"]);
  server = status({
    enabled: true, target: "NGC 6946", seq: 41, mode: "rgb", channels: LIVE,
    rejected: 1,
  });
  await mount();
  const t = text();
  for (const c of LIVE) {
    assert(t.includes(c.channel), `channel ${c.channel} is missing from the readout`);
    assert(t.includes(`${c.frames} frame`),
      `${c.channel}'s frame count is missing: ${t.slice(0, 300)}`);
    assert(t.includes(fmtIntegration(c.integrated_s)),
      `${c.channel}'s integration time is missing`);
  }
  // 1440 + 1080 + 840 = 3360s = 56m
  assert(t.includes("56m"), `no total integration time: ${t.slice(0, 300)}`);
  assert(t.includes("28"), "no total frame count");
  assert(/colour/i.test(t), "the composite does not say what kind of image it is");
  assert(/4x/.test(t), "the binning is not disclosed, so the image looks full-res");
});

await test("reset posts, and is dead while there is nothing to reset", async () => {
  setPrincipal(["control.capture"]);
  server = status({ enabled: true });
  await mount();
  assert(resetBtn() != null, "no reset control");
  assert(resetBtn().disabled === true,
    "reset is live with an empty stack, so pressing it does nothing visible");

  server = status({ enabled: true, target: "NGC 6946", seq: 41, mode: "rgb", channels: LIVE });
  await mount();
  posts.length = 0;
  assert(resetBtn().disabled === false, "reset is dead over a 28-frame stack");
  await click(resetBtn());
  assert(posts.some((p) => p.endsWith("/api/sequence/stack/reset")),
    `reset posted nothing. posts=${JSON.stringify(posts)}`);
  assert(img() == null, "the old composite is still on screen after a reset");
});

await test("a viewer sees the stack but cannot touch it", async () => {
  setPrincipal(["view.status", "view.preview"]);
  server = status({ enabled: true, target: "NGC 6946", seq: 41, mode: "rgb", channels: LIVE });
  await mount();
  assert(toggleEl().disabled === true, "a viewer can switch the stack off");
  assert(resetBtn().disabled === true, "a viewer can throw away the stack");
  assert(/access/i.test(text()), "nothing tells the viewer why the controls are dead");
  assert(img() != null, "a viewer cannot see the picture either");
});

// ------------------------------------------------------------------- report
// Let the last mount's in-flight status fetch settle before tearing the tree
// down, or React reports the resolved setState as an un-acted update.
await settle();
await act(async () => { rootRef!.unmount(); });
const total = passed + failed;
console.log(`sessionStackDom: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
