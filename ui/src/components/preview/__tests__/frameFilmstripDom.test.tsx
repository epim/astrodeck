// frameFilmstripDom.test.tsx — which tile the frame strip says is on screen.
//
//   Run directly:  npx tsx src/components/preview/__tests__/frameFilmstripDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The strip is a `role="listbox"` of `role="option"` tiles — a radio group, so
// exactly ONE option must be engaged and it must name the frame the stage is
// actually painting. The defect this file pins down is a group with ZERO
// engaged options: a pin survives in `selectedPreviewId` after the rig has
// trimmed that frame out of the ring, LivePreview falls back to painting the
// newest entry, and the strip goes on being handed the dead id. No tile matches
// it, every tile reads aria-selected="false", and auto-follow (which is gated
// on the same id) never scrolls the live end back into view.
//
// WHAT THIS CANNOT DO: jsdom lays nothing out, so scrollWidth/scrollLeft are
// not real. The auto-follow assertions below therefore instrument the property
// itself and assert that the component WROTE to it, not that anything moved.

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

// Scroll instrumentation. jsdom's scrollLeft setter is a no-op with no layout,
// so record the writes instead: `following` is internal state and the only
// observable consequence of it is this assignment.
const scrolls: { role: string | null; value: number }[] = [];
Object.defineProperty(win.HTMLElement.prototype, "scrollWidth", {
  configurable: true, get() { return 4096; },
});
Object.defineProperty(win.HTMLElement.prototype, "scrollLeft", {
  configurable: true,
  get() { return (this as any).__scrollLeft ?? 0; },
  set(v: number) {
    (this as any).__scrollLeft = v;
    scrolls.push({ role: this.getAttribute?.("role") ?? null, value: v });
  },
});

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "Image", "localStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
  "matchMedia", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
// The strip suppresses auto-scroll for ~1s after a user touch, measured from a
// `lastTouch` that starts at 0 — so with a real clock this whole file would be
// racing the process start time. Pin it well past the grace window.
Object.defineProperty(g, "performance", {
  value: { now: () => 60_000 }, writable: true, configurable: true,
});
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { FrameFilmstrip } = await import("../FrameFilmstrip");
type PreviewInfo = import("../../../types").PreviewInfo;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// ------------------------------------------------------------------ fixtures
/** Only the three fields the strip reads: id, ts, hfr. */
function frames(from: number, to: number): PreviewInfo[] {
  const out: PreviewInfo[] = [];
  for (let id = from; id <= to; id++) {
    out.push({ id, ts: 1_700_000_000 + id, hfr: 3.2 } as unknown as PreviewInfo);
  }
  return out;
}
// A full ring: 24 frames, newest #33. A frame pinned at #5 was trimmed out of
// it long ago — about two minutes back on a 5s focus loop.
const RING = frames(10, 33);

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(el: any): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(el); });
}
function strip(p: { previews: PreviewInfo[]; shownId: number | null; liveId: number | null }): any {
  return createElement(FrameFilmstrip, {
    previews: p.previews, shownId: p.shownId, liveId: p.liveId,
    hfrGood: 3.5, hfrWarn: 5, onSelect: () => {},
  });
}
const tiles = (): any[] => [...container.querySelectorAll('[role="option"]')];
const engaged = (): any[] => tiles().filter((t) => t.getAttribute("aria-selected") === "true");
/** The frame id a tile stands for, read off the title the component writes. */
function tileId(t: any): number {
  const m = /#(\d+)/.exec(t.getAttribute("title") || "");
  return m ? Number(m[1]) : -1;
}

// ---------------------------------------------------------------------- tests
test("a pin the ring still holds is the one engaged tile", () => {
  // The precondition for everything below: when the id names a frame that is
  // present, exactly that tile is engaged. Without this, "one tile is engaged"
  // later would not distinguish a fix from a component that always engages one.
  mount(strip({ previews: RING, shownId: 20, liveId: 33 }));
  assert(tiles().length === RING.length, `the strip rendered ${tiles().length} tiles, not ${RING.length}`);
  assert(engaged().length === 1, `expected exactly one engaged tile, got ${engaged().length}`);
  assert(tileId(engaged()[0]) === 20, `the engaged tile is #${tileId(engaged()[0])}, not the pinned #20`);
});

test("a pin trimmed out of the ring engages the frame the stage fell back to", () => {
  mount(strip({ previews: RING, shownId: 5, liveId: 33 }));
  assert(tiles().some((t) => tileId(t) === 33), "the newest frame has no tile, so this proves nothing");
  assert(!tiles().some((t) => tileId(t) === 5), "frame #5 is still in the ring — it is not a stranded pin");
  assert(engaged().length === 1,
    `a listbox with ${engaged().length} engaged options over a stage that is plainly showing one of them: ` +
    "the pinned frame aged out of the ring and nothing re-pointed the selection at what LivePreview fell back to");
  assert(tileId(engaged()[0]) === 33,
    `the engaged tile is #${tileId(engaged()[0])}, not the newest frame #33 that the stage is painting`);
  assert((engaged()[0].textContent || "").includes("LIVE"),
    "the engaged tile is not the one wearing the LIVE chip — the strip is contradicting its own label");
});

test("a stranded pin resumes auto-follow; a live pin still suppresses it", () => {
  // Negative half FIRST, so the positive half cannot pass by scrolling always.
  scrolls.length = 0;
  mount(strip({ previews: RING, shownId: 20, liveId: 33 }));
  assert(scrolls.filter((s) => s.role === "listbox").length === 0,
    "the strip scrolled to the live end while holding a valid pin — the pinned tile is dragged off screen");
  scrolls.length = 0;
  mount(strip({ previews: RING, shownId: 5, liveId: 33 }));
  const wrote = scrolls.filter((s) => s.role === "listbox");
  assert(wrote.length > 0,
    "auto-follow stayed dead under a stranded pin — the LIVE tile sits off the right end of the strip and is " +
    "never scrolled back to");
  assert(wrote[wrote.length - 1].value === 4096,
    `auto-follow scrolled to ${wrote[wrote.length - 1].value}, not to the live end`);
});

test("with no pin at all the live tile is engaged", () => {
  mount(strip({ previews: RING, shownId: null, liveId: 33 }));
  assert(engaged().length === 1, `expected exactly one engaged tile, got ${engaged().length}`);
  assert(tileId(engaged()[0]) === 33, `the engaged tile is #${tileId(engaged()[0])}, not live #33`);
});

test("a ring with no live id yet still engages the frame on the stage", () => {
  // Post-reconnect: the store can hold frames before livePreviewId latches, and
  // LivePreview paints previews[last]. The strip must agree rather than engage
  // nothing.
  mount(strip({ previews: RING, shownId: null, liveId: null }));
  assert(engaged().length === 1, `expected exactly one engaged tile, got ${engaged().length}`);
  assert(tileId(engaged()[0]) === 33,
    `the engaged tile is #${tileId(engaged()[0])}, not the newest frame #33 the stage falls back to`);
});

test("an empty ring renders the empty state, not a headless listbox", () => {
  mount(strip({ previews: [], shownId: 7, liveId: null }));
  assert(tiles().length === 0, "tiles rendered for an empty ring");
  assert(/Frame history/.test(container.textContent || ""), "no empty state rendered");
});

// ------------------------------------------------------------------- report
act(() => { rootRef!.unmount(); });
const total = passed + failed;
console.log(`frameFilmstripDom: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
