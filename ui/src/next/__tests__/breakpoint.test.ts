// breakpoint.test.ts - which layout the shell picks, and whether it notices a
// rotate (ARCHITECTURE.md #4).
//
//   Run directly:  npx tsx src/next/__tests__/breakpoint.test.ts
//   Also run by `npm test` and type-checked by `tsc -b`.
//
// WHAT THIS GUARDS. Two silent failures. First, a wrong threshold: a tablet that
// reads as a phone gets the tab bar and no rail and nobody files a bug, they
// just never see the Session column. Second - and this is the one with a
// history - a subscription that only uses `addEventListener`: Safari below 14,
// which is what an old field iPad runs, has only the deprecated `addListener`,
// so the layout would be chosen once at load and never change on a rotate.
//
// The second half of the file renders the hook for real, because `subscribe` is
// module-private and "does a rotate relayout" cannot be asserted any other way.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

/** The fake viewport every query below is answered from. */
let widthPx = 400;
/** Which spelling of the listener API the fake MediaQueryList offers. */
let modernListeners = true;

const asked: string[] = [];
const lists: any[] = [];

win.matchMedia = (q: string) => {
  asked.push(q);
  const min = Number(/min-width:\s*(\d+)px/.exec(q)?.[1] ?? "0");
  const mql: any = {
    media: q,
    get matches() { return widthPx >= min; },
    _ls: [] as (() => void)[],
    fire() { for (const l of this._ls.slice()) l(); },
  };
  if (modernListeners) {
    mql.addEventListener = (_t: string, l: () => void) => { mql._ls.push(l); };
    mql.removeEventListener = (_t: string, l: () => void) => {
      const i = mql._ls.indexOf(l); if (i >= 0) mql._ls.splice(i, 1);
    };
  } else {
    mql.addListener = (l: () => void) => { mql._ls.push(l); };
    mql.removeListener = (l: () => void) => {
      const i = mql._ls.indexOf(l); if (i >= 0) mql._ls.splice(i, 1);
    };
  }
  lists.push(mql);
  return mql;
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { getBreakpoint, useBreakpoint, TABLET_MIN_PX, DESKTOP_MIN_PX } = await import("../breakpoint");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

// ------------------------------------------------------------------- values

test("the thresholds are the design's", () => {
  eq(TABLET_MIN_PX, 768);
  eq(DESKTOP_MIN_PX, 1200);
});

test("a phone, a tablet and a desktop each read as themselves", () => {
  widthPx = 390; eq(getBreakpoint(), "phone", "390:");
  widthPx = 767; eq(getBreakpoint(), "phone", "one pixel under the tablet floor:");
  widthPx = 768; eq(getBreakpoint(), "tablet", "exactly at the tablet floor:");
  widthPx = 1199; eq(getBreakpoint(), "tablet", "one pixel under the desktop floor:");
  widthPx = 1200; eq(getBreakpoint(), "desktop", "exactly at the desktop floor:");
  widthPx = 1440; eq(getBreakpoint(), "desktop", "1440:");
});

test("both queries are asked as min-width media queries", () => {
  asked.length = 0;
  widthPx = 390;
  getBreakpoint();
  assert(asked.some((q) => q.includes("1200px")), `no 1200px query asked, got ${asked.join(" | ")}`);
  assert(asked.some((q) => q.includes("768px")), `no 768px query asked, got ${asked.join(" | ")}`);
});

test("no matchMedia at all is a phone, not a crash", () => {
  const saved = win.matchMedia;
  (win as any).matchMedia = undefined;
  Object.defineProperty(g, "matchMedia", { value: undefined, writable: true, configurable: true });
  try {
    eq(getBreakpoint(), "phone");
  } finally {
    win.matchMedia = saved;
    Object.defineProperty(g, "matchMedia", { value: saved, writable: true, configurable: true });
  }
});

test("a webview that THROWS on an unsupported query is a phone, not a crash", () => {
  const saved = win.matchMedia;
  win.matchMedia = () => { throw new Error("unsupported media feature"); };
  try {
    eq(getBreakpoint(), "phone");
  } finally {
    win.matchMedia = saved;
  }
});

// -------------------------------------------------------- the live hook

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

function Probe() {
  return createElement("span", { "data-testid": "bp" }, useBreakpoint());
}

const bpText = () => (container.querySelector('[data-testid="bp"]') as any)?.textContent;

test("useBreakpoint renders the current breakpoint", () => {
  widthPx = 1440;
  lists.length = 0;
  act(() => { root.render(createElement(Probe)); });
  eq(bpText(), "desktop", "precondition: the hook read the fake 1440px viewport");
});

test("a rotate relayouts (modern addEventListener)", () => {
  assert(lists.length > 0, "precondition: the hook subscribed to at least one MediaQueryList");
  widthPx = 390;
  act(() => { for (const m of lists) m.fire(); });
  eq(bpText(), "phone", "the hook must re-read on a media change:");
});

test("a rotate relayouts on Safari's deprecated addListener too", () => {
  act(() => { root.unmount(); });
  modernListeners = false;
  lists.length = 0;
  const root2 = createRoot(container);
  widthPx = 1440;
  act(() => { root2.render(createElement(Probe)); });
  eq(bpText(), "desktop", "precondition: mounted wide");
  assert(
    lists.length > 0 && lists.every((m) => typeof m.addEventListener !== "function"),
    "precondition: this fixture offers ONLY the deprecated listener API",
  );
  widthPx = 390;
  act(() => { for (const m of lists) m.fire(); });
  eq(bpText(), "phone", "the deprecated addListener path must be wired too:");
  act(() => { root2.unmount(); });
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`breakpoint.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
