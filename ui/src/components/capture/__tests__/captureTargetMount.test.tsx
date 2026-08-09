// captureTargetMount.test.tsx — CaptureView actually wired up (#181/#182).
//
//   Run directly:  npx tsx src/components/capture/__tests__/captureTargetMount.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The pure tests beside this one prove the target-name states and the FILT ring
// builder in isolation. Isolation is exactly what let the previous version of
// this field be wrong for months: it WAS a working text box, and it was backed
// by a `useState` inside a view that `App.tsx` remounts on every tab switch, so
// the name was correct and then silently gone.
//
// Two claims that can only be made about the MOUNTED screen:
//   * the target name lives in the store, so a remount does not eat it;
//   * the dial on Capture has five categories, the fifth being FILT — the
//     confirmed #181 gap, on the one screen in the app with a hand-rolled
//     filter panel sitting directly beneath it.

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
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "KeyboardEvent",
  "PointerEvent", "CustomEvent", "MouseEvent", "localStorage",
  "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
g.IntersectionObserver = class {
  observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
};
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
// CameraDial's bloom effect cancels its own frame on unmount, and half of what
// this file asserts happens ACROSS an unmount.
g.cancelAnimationFrame = (id: any) => clearTimeout(id);
g.IS_REACT_ACT_ENVIRONMENT = true;

// DYNAMIC imports, and that is load-bearing: a static import evaluates before
// the jsdom bootstrap above, and react-dom would then latch canUseDOM=false and
// fall back to a polyfill under which "typing" into a box does nothing at all.
const React = (await import("react")).default;
const { act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { api } = await import("../../../api");
const { useStore } = await import("../../../store");
const CaptureView = (await import("../../../views/CaptureView")).default;

(api as any).get = async () => ({});
(api as any).post = async () => ({});
(api as any).put = async () => ({});

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// The sim rig's eight-slot wheel; slot 7 is the blackout.
const STATUS = {
  busy_lanes: [],
  filterwheel: {
    position: 2,
    names: ["L", "R", "G", "B", "Ha", "OIII", "SII", "Dark"],
    opaque: [false, false, false, false, false, false, false, true],
  },
  camera: { max_bin: 4, max_gain: 500 },
} as any;
const ADMIN = {
  role: "admin", email: null,
  caps: ["view.status", "view.preview", "control.mount", "control.capture"],
};

function reset(): void {
  useStore.setState({ status: STATUS, principal: ADMIN, captureTarget: "" } as any);
}

function mount(): { dom: any; close: () => void } {
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  const root = createRoot(host);
  act(() => { root.render(React.createElement(CaptureView)); });
  return { dom: host, close: () => { act(() => { root.unmount(); }); host.remove(); } };
}

function type(el: any, value: string): void {
  Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!
    .set!.call(el, value);
  el.dispatchEvent(new win.Event("input", { bubbles: true }));
}

function click(node: any): void {
  act(() => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}

// ================================================================== the name

test("a typed target name survives the remount a tab switch causes", () => {
  reset();
  const first = mount();
  const box = first.dom.querySelector("[data-capture-field='target']");
  assert(!!box, "Capture has no target-name field at all");
  act(() => { type(box, "Veil east"); });
  assert(useStore.getState().captureTarget === "Veil east",
    `the field wrote ${JSON.stringify(useStore.getState().captureTarget)} to the `
    + "store. If it is holding the name in component state instead, App.tsx keys "
    + "<ViewBoundary> by view and a trip to the Atlas erases it.");
  first.close();

  // ...which is exactly what a tab switch does: unmount, mount again.
  const second = mount();
  const again = second.dom.querySelector("[data-capture-field='target']");
  assert(again.value === "Veil east",
    `after a remount the field reads ${JSON.stringify(again.value)} — the `
    + "operator's typed name was silently lost");
  second.close();
});

// ================================================================== the dial

function dialItems(host: any): string[] {
  return [...host.querySelectorAll("[data-dial-item]")]
    .map((n: any) => n.getAttribute("data-dial-item"));
}

test("Capture's dial carries FILT beside EXP/GAIN/BIN/OFFS", () => {
  reset();
  const m = mount();
  const disc = m.dom.querySelector("[data-dial-disc]");
  assert(!!disc, "Capture has no camera dial — #181 is still open");
  click(disc);
  const cats = dialItems(m.dom);
  m.close();
  assert(cats.includes("filter"),
    `Capture's dial offers ${JSON.stringify(cats)} and no filter category. It is `
    + "the one screen in the app with a hand-rolled filter panel under it, and "
    + "#179 cannot retire that panel until the dial can do what it does.");
  assert(cats.length === 5, `${cats.length} categories: ${JSON.stringify(cats)}`);
});

test("the dial's FILT ring offers the blackout slot, named as one", () => {
  reset();
  const m = mount();
  click(m.dom.querySelector("[data-dial-disc]"));
  const filt = [...m.dom.querySelectorAll("[data-dial-item]")]
    .find((n: any) => n.getAttribute("data-dial-item") === "filter");
  click(filt);
  // Eight slots is more than the arc can seat, so the dial swaps in the
  // full-ring picker — which is a PORTAL at screen level, deliberately drawn
  // outside the stage-sized box. Query the document, not the host.
  const faces = [...win.document.querySelectorAll("[data-dial-item]")]
    .map((n: any) => (n.textContent ?? "").trim());
  m.close();
  assert(faces.some((f) => /^L$/.test(f)),
    `the first slot is missing from the ring: ${JSON.stringify(faces)}`);
  assert(faces.some((f) => /blackout/i.test(f)),
    `no blackout slot on the ring: ${JSON.stringify(faces)}. Capture is the one `
    + "screen where parking on it is a real workflow (it is how you shoot darks "
    + "with a wheel), so dropping it here loses a function.");
});

test("the dial's box does not stretch to the settings column beside it", () => {
  // Reported from the deployed rig: "I'm not seeing the speed dials on the
  // capture screen". The dial WAS mounted, WAS visible, and sat 1595px down a
  // 900px viewport. Its wrapper is a GRID ITEM, so `align-items: stretch` made
  // it as tall as the settings column (measured 1599px against a 670px
  // preview), and CameraDial anchors its disc to `bottom: 12` of that box.
  //
  // THIS ASSERTION IS A CLASS CHECK ON PURPOSE, AND IT IS WEAKER THAN THE BUG.
  // jsdom has no layout engine: the dial's ResizeObserver never fires, `box`
  // stays null, dialRadius returns the full radius, and `fits` is true in every
  // DOM test that has ever run here. No test in this file could have caught the
  // defect, and this one cannot either -- it can only catch the FIX being
  // deleted. The real check is `probe_capture_dial.py`, which drives a browser
  // and reads getBoundingClientRect.
  const m = mount();
  const dial = m.dom.querySelector("[data-camera-dial]");
  assert(dial != null, "the camera dial is not mounted on Capture at all");
  const wrapper = (dial as any).parentElement;
  m.close();
  assert(/\bh-fit\b/.test(wrapper?.className ?? ""),
    "the dial's wrapper lost h-fit, so as a grid item it stretches to the "
    + "height of the settings column and the disc renders far below the fold. "
    + `className was: ${JSON.stringify(wrapper?.className ?? null)}`);
});

const total = passed + failed;
console.log(`captureTargetMount.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
