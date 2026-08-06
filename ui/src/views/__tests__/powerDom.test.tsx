// powerDom.test.tsx — PowerView MOUNTED, tapped and dragged.
//
//   Run directly:  npx tsx src/views/__tests__/powerDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Two defects, both invisible to a single-render tree inspection:
//
//   #78  a UPB toggle is not a register write — the driver walks the box with
//        1+5N sequential serial GETs. The LED, the border and the ON/OFF word
//        all sat unchanged for the whole round trip, so the row read as if the
//        tap had missed, and extra taps queued extra full cycles.
//   #24  a drag the browser takes away (a scroll gesture claiming the pointer)
//        delivers pointercancel INSTEAD of pointerup, so the commit never ran
//        and the port stayed latched in `draggingRef` — the thumb sat forever
//        on a level that was never sent, and the 5 s poll could not correct it.
//
// Each test proves the state it is about to deny was actually reached: a
// "nothing was sent" assertion is trivially true of a control nobody touched.

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};
win.Element.prototype.hasPointerCapture = function () { return false; };

// The power box, behind the one network seam. GET /api/switch/ports answers
// from `ports`, so a test can move server truth the way the 5 s poll does; POST
// /api/switch/set is HELD OPEN until `release()` is called, because the whole
// of #78 happens DURING that round trip.
type Port = {
  id: number; name: string; value: number; min: number; max: number;
  unit: string; can_write: boolean; is_boolean: boolean;
};
let ports: Port[] = [
  { id: 0, name: "Cam power", value: 0, min: 0, max: 1, unit: "", can_write: true, is_boolean: true },
  { id: 1, name: "Mount power", value: 1, min: 0, max: 1, unit: "", can_write: true, is_boolean: true },
  { id: 2, name: "Dew A", value: 0, min: 0, max: 100, unit: "%", can_write: true, is_boolean: false },
];
const posts: { path: string; body: any }[] = [];
let release: (() => void) | null = null;
win.fetch = async (input: any, init: any = {}) => {
  const path = String(input);
  const ok = {
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: async () => ports,
    text: async () => JSON.stringify(ports),
  };
  if ((init.method ?? "GET") === "POST") {
    const body = init.body ? JSON.parse(init.body) : null;
    posts.push({ path, body });
    // The serial walk. Nothing resolves until the test says so.
    await new Promise<void>((res) => { release = () => { release = null; res(); }; });
    ports = ports.map((p) => (p.id === body.port_id ? { ...p, value: body.value } : p));
  }
  return ok;
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "WebSocket", "fetch",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const PowerView = (await import("../PowerView")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

useStore.setState({
  status: {
    connected: { switch: { connected: true, name: "UPB" } },
    looping: false, mode: "sim",
  },
  principal: {
    role: "operator", email: null,
    caps: ["view.status", "control.power", "control.capture"],
  },
} as never);

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(PowerView)); });

async function settle(): Promise<void> {
  await act(async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); });
}
const text = () => String(container.textContent ?? "");
const row = (name: string) =>
  [...container.querySelectorAll("button")]
    .find((b: any) => b.textContent?.includes(name)) as any;
const slider = () => container.querySelector('input[type="range"]') as any;

function click(node: any): void {
  act(() => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}
function pointer(node: any, type: string): void {
  act(() => {
    const ev = new win.Event(type, { bubbles: true, cancelable: true });
    (ev as any).pointerId = 1;
    (ev as any).pointerType = "touch";
    (ev as any).isPrimary = true;
    node.dispatchEvent(ev);
  });
}
function drag(node: any, value: string): void {
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(
      win.HTMLInputElement.prototype, "value")!.set!;
    setter.call(node, value);
    node.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
}

await settle();   // let the mount GET land

// ------------------------------------------------------------- the fixture
await test("the view mounted with three ports from the power box", () => {
  // Anti-blank-page: every assertion below is about a row's word changing, and
  // "the word is not ON" is true of a page with no rows at all.
  assert(row("Cam power") != null, "no Cam power row — the ports GET never rendered");
  assert(row("Mount power") != null, "no Mount power row");
  assert(slider() != null, "no PWM slider — the dimmer port never rendered");
  assert(/OFF/.test(row("Cam power").textContent),
    "Cam power does not read OFF at value 0, so a later 'it changed' means nothing");
});

// ------------------------------------------------------ #78 per-row pending
await test("a tapped port says so for the whole serial round trip", async () => {
  posts.length = 0;
  click(row("Cam power"));
  await settle();
  // PRECONDITION: the request really is in flight and unanswered.
  assert(posts.length === 1, `tap sent ${posts.length} requests, expected 1`);
  assert(release != null, "the fixture answered the POST — there is no round trip to observe");
  const r = row("Cam power");
  assert(/→ ON/.test(r.textContent),
    `the row still reads "${r.textContent?.trim()}" while the box is being walked — ` +
    "nothing on it acknowledges the tap, which is what makes people tap again");
  assert(r.getAttribute("aria-busy") === "true",
    "the row is not aria-busy, so a screen-reader user gets no acknowledgement at all");
});

await test("the LED still reports the port's REAL state mid-cycle", () => {
  // The word carries the request; the LED must NOT, or the row would claim the
  // port had switched before the box said it had.
  const led = row("Cam power").querySelector(".led") as any;
  assert(led != null, "no LED on the row");
  assert(/led-off/.test(led.className),
    `the LED reads ${led.className} while the port is still off — the row is ` +
    "asserting a switch that has not happened");
});

await test("a second tap during the cycle queues no second walk of the box", async () => {
  assert(release != null, "the first request already resolved (precondition)");
  const before = posts.length;
  click(row("Cam power"));
  click(row("Cam power"));
  await settle();
  assert(posts.length === before,
    `${posts.length - before} extra request(s) went out while one was in flight — ` +
    "each is another full 1+5N serial walk of the power box");
});

await test("when the box answers, the row goes back to reporting state", async () => {
  release!();
  await settle();
  const r = row("Cam power");
  assert(!/→/.test(r.textContent),
    `the row is stuck mid-request at "${r.textContent?.trim()}"`);
  assert(/ON/.test(r.textContent), "the row never reflected the switch it made");
  assert(r.getAttribute("aria-busy") == null, "the row is still announced as busy");
});

// -------------------------------------------------- #24 interrupted PWM drag
await test("an interrupted drag does not latch a level that was never sent", async () => {
  const s = slider();
  posts.length = 0;
  drag(s, "70");
  // PRECONDITION: the drag really is mid-flight — the draft is showing and the
  // port is latched. Without this, "the thumb went back" proves nothing.
  assert(s.value === "70", "the drag did not move the slider");
  assert(/70%/.test(text()), "the draft level is not on screen, so no drag is in progress");
  assert(posts.length === 0, "the drag POSTed mid-gesture — it is not a draft at all");

  pointer(s, "pointercancel");   // the browser taking the gesture for a scroll
  await settle();
  assert(posts.length === 0,
    "a cancelled drag SENT the level — an abandoned gesture must not command the heater");
  assert(s.value === "0",
    `the slider is latched at ${s.value} on a level the box never heard; the 5 s ` +
    "poll cannot correct it because the port is still marked as being dragged");
});

await test("the slider still works after the cancelled drag", async () => {
  const s = slider();
  posts.length = 0;
  drag(s, "45");
  assert(s.value === "45", "the slider did not take a second drag (precondition)");
  pointer(s, "pointerup");
  await settle();
  const sent = posts.filter((p) => /switch\/set/.test(p.path));
  assert(sent.length === 1 && sent[0].body?.value === 45,
    `a normal release sent ${JSON.stringify(sent.map((p) => p.body))}, expected value 45 — ` +
    "the cancel handler ate the commit path");
  release?.();
  await settle();
});

// ------------------------------------------- a commit released during the walk
// The per-port in-flight guard exists so a second command cannot queue a second
// 1+5N walk of the box. Applied to a SLIDER commit it silently ate the value:
// the reconcile effect dropped the draft when the first walk answered and the
// thumb snapped back to the level the box was still holding, with nothing said.
await test("a dimmer level released during the walk is sent, not dropped", async () => {
  const s = slider();
  posts.length = 0;
  drag(s, "50");
  pointer(s, "pointerup");
  await settle();
  // PRECONDITION: one walk is out and the box has NOT answered it.
  assert(posts.length === 1 && posts[0].body?.value === 50,
    `the first release sent ${JSON.stringify(posts.map((p) => p.body))}, expected value 50`);
  assert(release != null, "the fixture answered the POST — there is no walk to release into");
  assert(/→ 50%/.test(text()),
    `the dimmer row reads "${text().match(/Dew A[^A-Z]*/)?.[0]?.trim()}" while the box ` +
    "is being walked — nothing on it acknowledges the release");

  // The second adjustment, mid-walk.
  drag(s, "80");
  pointer(s, "pointerup");
  await settle();
  assert(posts.length === 1,
    `${posts.length - 1} extra walk(s) went out while one was in flight`);
  assert(s.value === "80", "the thumb did not take the second adjustment at all");

  release!();            // the first walk answers, with the box at 50
  await settle();
  await settle();
  assert(posts.length === 2 && posts[1].body?.value === 80,
    `the level released during the walk was DROPPED (requests: ` +
    `${JSON.stringify(posts.map((p) => p.body?.value))}) — the thumb snaps back to ` +
    "the level the box is still holding and nothing says the command went nowhere");
  assert(s.value === "80",
    `the thumb bounced to ${s.value} between the two walks — the draft must hold ` +
    "the level that is on its way, not the one being superseded");

  release?.();           // the queued walk answers
  await settle();
  assert(s.value === "80", "the box never took the queued level");
  assert(!/→/.test(text()), `the dimmer row is stuck mid-request at "${text().slice(0, 200)}"`);
});

// ------------------------------------------------------------------ report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`powerDom: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
