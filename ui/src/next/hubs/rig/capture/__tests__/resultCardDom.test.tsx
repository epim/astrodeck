// resultCardDom.test.tsx - the capture result card, MOUNTED, pressed, refused
// and degraded (T-U7b-3, D-SES-4).
//
//   Run directly:  npx tsx src/next/hubs/rig/capture/__tests__/resultCardDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT THIS FILE IS FOR, and what goes wrong without each:
//
//  1. A PRECONDITION MARKER, and a POSITIVE CONTROL. Every test below runs
//     after `capture-result` has been found, and the refusal tests run after
//     SAVE TO GALLERY has been seen on screen with a live frame - so "no SAVE
//     button" can only mean "the refusal suppressed it", never "this card never
//     draws one" (`verify-on-the-real-thing`).
//  2. THE frame_id INTERLOCK IS ON THE WIRE. The press must name the frame the
//     card is showing. Drop it and a stale card silently writes a newer
//     exposure the operator never saw, with a success message over it.
//  3. THE SECOND PRESS AFTER A SAVE POSTS NOTHING - twice over: the control is
//     gone once the frame is in the library, and two clicks inside one tick
//     still produce one write (React has not flushed `saving` between them, so
//     the guard cannot be state).
//  4. THE REFUSALS ARE READ BY CODE. The `already_saved` fixture is worded
//     UNLIKE the sentence the card shows, so a branch that matched the server's
//     prose would fall through here rather than on the rig.
//  5. DEGRADING TO WHAT SHIPPED. An engine with no promote route answers a bare
//     404, and the card must render exactly the RE-SHOOT AND SAVE it shipped
//     with - not a SAVE button pointing at a route that does not exist.
//  6. HONEST-DISABLED: a viewer sees SAVE TO GALLERY with the reason on it and
//     writes nothing.
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally plus
// the `{ passed, failed, total }` export (shell-and-tests.md section 4).

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "localStorage", "sessionStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string; body: any }
interface Reply { status: number; body: any }
const asks: Ask[] = [];

const HELD = {
  available: true, id: 4, ts: 1757500000, exposure_s: 30, gain: 120,
  binning: 1, frame_type: "Light", target: "NGC 7331", filter: "L",
  saved: false,
};
const WROTE = {
  saved: true, path: "2026-09-10/NGC 7331_L_30s_0004.fits", filter: "L",
  id: 4, target: "NGC 7331", ts: 1757500000,
};

/** Queued answers for `GET /api/capture/last`; the last one repeats. */
let lastQueue: Reply[] = [];
/** Queued answers for `POST /api/capture/last/save`; the last one repeats. */
let saveQueue: Reply[] = [];

function nextOf(q: Reply[]): Reply {
  return q.length > 1 ? (q.shift() as Reply) : q[0];
}

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asks.push({ url: u, method, body });
  const r = /\/api\/capture\/last\/save/.test(u)
    ? nextOf(saveQueue)
    : nextOf(lastQueue);
  return {
    ok: r.status < 400,
    status: r.status,
    statusText: `HTTP ${r.status}`,
    headers: { get: () => "application/json" },
    json: async () => r.body,
    text: async () => JSON.stringify(r.body),
  };
};

const reads = () => asks.filter((a) => /\/api\/capture\/last$/.test(a.url));
const writes = () => asks.filter((a) => /\/api\/capture\/last\/save$/.test(a.url));

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { ResultCard, NOT_SAVED_NOTE, PROMOTABLE_NOTE } = await import("../ResultCard");
const {
  ALREADY_SAVED_NOTE, FRAME_MOVED_NOTE, NOTHING_TO_PROMOTE_NOTE,
  NOT_BUFFERED_REASON,
} = await import("../lastFrame");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
let root: any = null;
const q = (sel: string) => container.querySelector(sel) as any;
const id = (name: string) => q(`[data-testid="${name}"]`);
const press = async (el: any) => {
  await act(async () => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

const spy = { reshoot: 0, open: 0, retake: 0, flow: 0 };

function baseProps(): Record<string, unknown> {
  return {
    preview: null, saved: false, count: 1, exposureS: 30, filter: "L", gain: 120,
    onReshootAndSave: () => { spy.reshoot++; },
    onOpenLibrary: () => { spy.open++; },
    onRetake: () => { spy.retake++; },
    onMakeFlow: () => { spy.flow++; },
    actionReason: null, flowReason: null, onExplain: () => {},
  };
}

/** Fresh mount: a new card per test, because the card's MOUNT is what reads the
 *  buffer (one landed shot, one read). */
async function mount(
  props: Record<string, unknown> = {},
  principal: unknown = OPERATOR,
): Promise<void> {
  if (root) await act(async () => { root.unmount(); });
  useStore.setState({
    principal, equipConnected: true, wsPhase: "up",
    status: { connected: { camera: { connected: true, name: "sim" } } },
    toasts: [],
  } as never);
  asks.length = 0;
  spy.reshoot = 0; spy.open = 0; spy.retake = 0; spy.flow = 0;
  root = createRoot(container);
  await act(async () => {
    root.render(createElement(ResultCard as any, { ...baseProps(), ...props }));
  });
  await settle();
}

// ------------------------------------------------ 1. the marker, and the offer
lastQueue = [{ status: 200, body: HELD }];
saveQueue = [{ status: 200, body: WROTE }];
await mount();

await test("the card rendered, read the buffer once, and offers SAVE TO GALLERY", async () => {
  assert(id("capture-result") != null,
    "no capture-result marker: the fixture is wrong, not the component");
  eq(reads().length, 1, "the card did not read GET /api/capture/last exactly once");
  const save = id("result-save-gallery");
  assert(save != null,
    "SAVE TO GALLERY is not drawn for a rig that says it is holding the frame - "
    + "every assertion below about it being absent would be vacuous");
  eq(save.getAttribute("aria-disabled"), null,
    "an operator with a camera found SAVE TO GALLERY locked");
  eq(save.getAttribute("data-kind"), "primary",
    "SAVE TO GALLERY is not the primary verb while the rig holds the frame");
  eq(id("result-save").getAttribute("data-kind"), "secondary",
    "RE-SHOOT AND SAVE did not step back to secondary");
  assert(container.textContent.includes(PROMOTABLE_NOTE),
    "the card still says the only way to keep the frame is another exposure");
  assert(!container.textContent.includes(NOT_SAVED_NOTE),
    "both notes are on screen at once - they contradict each other");
});

// ------------------------------------------ 2. the write, and what it carries
await test("SAVE posts once, names the frame it is looking at, and flips the card",
  async () => {
    await press(id("result-save-gallery"));
    const w = writes();
    eq(w.length, 1, "SAVE did not post exactly once to /api/capture/last/save");
    eq(w[0].method, "POST", "the promote was not a POST");
    eq(w[0].body.frame_id, 4,
      "the frame_id interlock was not sent - a stale card can now write a "
      + "newer exposure the operator never saw");
    assert(!("target" in w[0].body),
      `an override nobody asked for was forged: ${JSON.stringify(w[0].body)}`);
    assert(id("result-open") != null,
      "the card did not flip to its saved face - OPEN IN GALLERY is missing");
    assert(container.textContent.includes("CAPTURED · IN THE LIBRARY"),
      "the header still says the frame was not saved");
    assert(container.textContent.includes(WROTE.path),
      "the card does not say where the frame went - 'saved' and 'here is where' "
      + "are different facts");
  });

await test("the second press after a save posts nothing: the control is gone", async () => {
  assert(id("result-save-gallery") == null,
    "SAVE TO GALLERY survived the save - a second press would ask the rig to "
    + "write a copy it has already refused");
  await press(id("result-retake"));
  eq(writes().length, 1, "something wrote a second time after the frame was saved");
});

// --------------------------------- 3. two clicks in one tick are still one write
await test("a double press inside one tick is a single write", async () => {
  lastQueue = [{ status: 200, body: HELD }];
  saveQueue = [{ status: 200, body: WROTE }];
  await mount();
  const save = id("result-save-gallery");
  // Both clicks land before React flushes anything, which is exactly the case a
  // `saving` state flag cannot catch.
  await act(async () => {
    save.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    save.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  });
  await settle();
  eq(writes().length, 1, "a fast double tap wrote the frame twice");
});

// ------------------------------------------------------- 4. already_saved (409)
await test("409 already_saved is read by CODE, flips the card, and stops", async () => {
  lastQueue = [{ status: 200, body: HELD }];
  // Deliberately NOT the sentence the card shows: a branch matching the
  // server's prose would find nothing here.
  saveQueue = [{ status: 409, body: { detail: {
    detail: "that exposure is already on disk", code: "already_saved" } } }];
  await mount();
  await press(id("result-save-gallery"));
  eq(writes().length, 1, "the refused press did not post exactly once");
  assert(id("result-open") != null,
    "an already-saved frame left the card pressable - the file is on disk");
  eq(id("result-promote-note").textContent, ALREADY_SAVED_NOTE,
    "the card did not say why a second copy is not coming");
  assert(id("result-save-gallery") == null, "SAVE TO GALLERY is still pressable");
  await press(id("result-retake"));
  eq(writes().length, 1, "the card wrote again after being told it was saved");
});

// ---------------------------------------------------- 5. nothing_to_promote (404)
await test("404 nothing_to_promote says so and leaves RE-SHOOT reachable", async () => {
  lastQueue = [{ status: 200, body: HELD }];
  saveQueue = [{ status: 404, body: { detail: {
    detail: "no unsaved frame to save", code: "nothing_to_promote" } } }];
  await mount();
  await press(id("result-save-gallery"));
  eq(id("result-promote-note").textContent, NOTHING_TO_PROMOTE_NOTE,
    "the card did not say the buffer had moved on");
  const reshoot = id("result-save");
  assert(reshoot != null, "RE-SHOOT AND SAVE was taken away with the buffer");
  eq(reshoot.getAttribute("aria-disabled"), null,
    "the only way forward was locked as well");
  await press(reshoot);
  eq(spy.reshoot, 1, "RE-SHOOT AND SAVE did nothing");

  const save = id("result-save-gallery");
  assert(save != null,
    "SAVE TO GALLERY vanished, taking its explanation with it");
  eq(save.getAttribute("aria-disabled"), "true",
    "SAVE TO GALLERY still looks live over an empty buffer");
  eq(save.getAttribute("title"), NOT_BUFFERED_REASON,
    "the locked SAVE button does not say the rig let the frame go");
  assert(save.hasAttribute("disabled") === false,
    "the native disabled attribute took the reason out of the a11y tree");
  await press(save);
  eq(writes().length, 1, "a locked SAVE reached the rig anyway");
});

// -------------------------------------------------- 6. frame_id_mismatch (409)
await test("409 frame_id_mismatch re-reads the buffer, and the next press names "
  + "the frame the rig has now", async () => {
    lastQueue = [
      { status: 200, body: HELD },
      { status: 200, body: { ...HELD, id: 7, target: "NGC 7331" } },
    ];
    saveQueue = [
      { status: 409, body: { detail: {
        detail: "that frame is no longer the one being held (asked for 4, holding 7)",
        code: "frame_id_mismatch" } } },
      { status: 200, body: { ...WROTE, id: 7 } },
    ];
    await mount();
    await press(id("result-save-gallery"));
    eq(id("result-promote-note").textContent, FRAME_MOVED_NOTE,
      "the interlock fired and the card said nothing about it");
    eq(reads().length, 2,
      "the card did not re-read the buffer, so the next press would name the "
      + "same stale id and be refused forever");
    await press(id("result-save-gallery"));
    const w = writes();
    eq(w.length, 2, "the second press did not reach the rig");
    eq(w[1].body.frame_id, 7,
      "the second press still named the frame the rig had already replaced");
    assert(id("result-open") != null, "the second press did not save the frame");
  });

// -------------------------------------------------- 7. an engine without the route
await test("a bare 404 from the buffer leaves the shipped card exactly as it was",
  async () => {
    lastQueue = [{ status: 404, body: { detail: "Not Found" } }];
    saveQueue = [{ status: 200, body: WROTE }];
    await mount();
    assert(id("capture-result") != null, "the card did not render at all");
    assert(id("result-save-gallery") == null,
      "an engine with no promote route was offered SAVE TO GALLERY");
    const reshoot = id("result-save");
    assert(reshoot != null, "RE-SHOOT AND SAVE is missing on an older engine");
    eq(reshoot.getAttribute("data-kind"), "primary",
      "RE-SHOOT AND SAVE is no longer the primary verb where it is the only one");
    assert(container.textContent.includes(NOT_SAVED_NOTE),
      "the card promises a buffered frame this engine does not keep");
    eq(writes().length, 0, "the card posted to a route it had just been told is absent");
  });

// --------------------------------------------- 8. a frame saved at shutter time
await test("a frame written at shutter time never reads the buffer", async () => {
  lastQueue = [{ status: 200, body: HELD }];
  await mount({ saved: true });
  eq(reads().length, 0,
    "the card asked what is buffered for a frame that is already on disk");
  assert(id("result-open") != null, "the saved face lost OPEN IN GALLERY");
  assert(id("result-save-gallery") == null,
    "a frame already in the library was offered a save");
});

// ------------------------------------------------------- 9. the target override
await test("SAVE AS is offered only for a different bench target, and sends it",
  async () => {
    lastQueue = [{ status: 200, body: HELD }];
    saveQueue = [{ status: 200, body: { ...WROTE, target: "M 31" } }];
    await mount({ captureTarget: "M 31" });
    const as = id("result-save-as-target");
    assert(as != null, "the override was not offered for a different target");
    assert(/SAVE AS M 31/.test(as.textContent),
      `the override does not name the target it would use: ${as.textContent}`);
    assert(container.textContent.includes("NGC 7331"),
      "the card does not say which name the frame carries today");
    await press(as);
    const w = writes();
    eq(w.length, 1, "SAVE AS did not post exactly once");
    eq(w[0].body.target, "M 31", "the override never reached the wire");
    eq(w[0].body.frame_id, 4, "SAVE AS dropped the interlock the plain save sends");
  });

await test("SAVE AS is absent when the bench target is the frame's own", async () => {
  lastQueue = [{ status: 200, body: HELD }];
  saveQueue = [{ status: 200, body: WROTE }];
  await mount({ captureTarget: "NGC 7331" });
  assert(id("result-save-gallery") != null,
    "precondition: the promote surface is not on screen at all");
  assert(id("result-save-as-target") == null,
    "a SAVE AS that would change nothing was offered anyway");
});

// ------------------------------------------------------------- 10. the viewer
await test("a viewer sees the reason on SAVE TO GALLERY and writes nothing", async () => {
  lastQueue = [{ status: 200, body: HELD }];
  saveQueue = [{ status: 200, body: WROTE }];
  await mount({}, VIEWER);
  const save = id("result-save-gallery");
  assert(save != null, "SAVE TO GALLERY was hidden from a viewer instead of locked");
  eq(save.getAttribute("aria-disabled"), "true", "a viewer's SAVE button looks live");
  eq(save.getAttribute("title"), "needs operator or admin access",
    "the viewer's SAVE button carries no reason, or the wrong one");
  await press(save);
  eq(writes().length, 0, "a viewer's press reached the rig");
  const toasts = (useStore.getState() as any).toasts as Array<{ title?: string }>;
  assert(toasts.some((t) => t.title === "needs operator or admin access"),
    "the reason was swallowed - the press did nothing and said nothing");
});

await act(async () => { root.unmount(); });

const total = passed + failed;
console.log(`resultCardDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
