// radarMapDom.test.tsx — the radar panel, MOUNTED, refreshed, and starved.
//
//   Run directly:  npx tsx src/components/weather/__tests__/radarMapDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Two defects, both invisible without a DOM:
//
//  * `refresh` fetched nothing for up to four minutes at a time. The tile URLs'
//    cache-buster was a 240s BUCKET INDEX, so inside a bucket the recomputed
//    value was identical: same key, same src, no remount, no onLoad — while the
//    `setTileStatus({})` beside it had just discarded the health of a map that
//    was already painted. The badge then sat on "loading…" over correct
//    imagery. Nothing about that is visible in a rendered tree; it needs two
//    renders and the URLs compared across them.
//  * With no observing site there is no centre, so no tile is ever requested —
//    and the panel sat at "loading…" over an empty box forever, beside a
//    bare-disabled `recenter` that never said what was missing.
//
// WHAT THIS CANNOT DO: jsdom fetches nothing, so `onLoad` is dispatched by hand
// below rather than earned by a real tile. What is real is which URLs the
// component asks for, and what it says about them.

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
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};
// jsdom lays nothing out, so the map box measures 0 and no tile is ever
// computed — which would make every assertion below vacuous. Give it a width.
Object.defineProperty(win.HTMLElement.prototype, "clientWidth", {
  get: () => 512, configurable: true,
});

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const RadarMap = (await import("../RadarMap")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;

/** Somewhere in Colorado — deliberately nowhere near the real rig. */
function withSite(): void {
  act(() => {
    useStore.setState({
      site: { latitude: 40.0, longitude: -105.25, is_default: false, horizon_min_deg: 15 },
      weather: null,
      status: null,
    } as never);
  });
}
function withoutSite(): void {
  act(() => {
    useStore.setState({ site: null, weather: null, status: null } as never);
  });
}
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(RadarMap)); });
}

const tiles = (): any[] => [...container.querySelectorAll("img")];
const tileSrcs = (): string[] => tiles().map((t: any) => t.getAttribute("src"));
const badge = (): string => {
  const el = [...container.querySelectorAll("div")]
    .find((d: any) => /loading…|tiles unavailable|fetched .* ago|no observing site/.test(d.textContent || "")
      && d.className.includes("pointer-events-none"));
  return (el?.textContent || "").trim();
};
const byText = (tag: string, re: RegExp) =>
  [...container.querySelectorAll(tag)].find((n: any) => re.test(n.textContent || ""));
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
function loadAllTiles(): void {
  act(() => {
    for (const t of tiles()) t.dispatchEvent(new win.Event("load", { bubbles: false }));
  });
}

// --------------------------------------------------------------- preconditions
withSite();
mount();

test("the map mounted with a real tile grid — otherwise nothing below means anything", () => {
  assert(tiles().length > 0,
    "no tiles rendered: the box measured zero or the site never reached the store, so the fixture is wrong");
  assert(tileSrcs().every((s) => /\/api\/weather\/tile\/radar\/\d+\/\d+\/\d+\.png\?t=\d+/.test(s)),
    `tile URLs are not the proxied form — got ${tileSrcs()[0]}`);
});

test("before anything paints, the badge says so", () => {
  assert(/loading…/.test(badge()), `expected "loading…" before any tile loads — got ${JSON.stringify(badge())}`);
});

test("once tiles paint the badge states the imagery's age", () => {
  loadAllTiles();
  assert(/fetched .* ago/.test(badge()),
    `a painted map must report when it was fetched — got ${JSON.stringify(badge())}`);
});

// ------------------------------------------------------------------- THE BUG
test("refresh actually re-requests the tiles", () => {
  const before = tileSrcs();
  assert(before.length > 0, "no tiles to refresh, so this proves nothing");
  const btn = byText("button", /refresh/);
  assert(btn != null, "no refresh control on the panel");
  click(btn);
  const after = tileSrcs();
  assert(after.length === before.length, "the tile grid changed shape across a refresh");
  assert(after.every((s, i) => s !== before[i]),
    "refresh produced byte-identical tile URLs, so the browser served the same images and nothing was fetched — " +
    "the 240s bucket made this control inert for minutes at a time");
});

test("and does not strand the badge on 'loading…' over a painted map", () => {
  // The other half of the same bug: the health map is cleared on refresh, so if
  // the URLs had not changed no onLoad could ever fire again.
  assert(/loading…/.test(badge()), "a just-refreshed map should report loading until the new tiles land");
  loadAllTiles();
  assert(/fetched .* ago/.test(badge()),
    `the new tiles painted but the badge never recovered — got ${JSON.stringify(badge())}`);
});

// -------------------------------------------------------------- no site at all
test("with no observing site the badge says THAT, not 'loading…'", () => {
  withoutSite();
  mount();
  assert(tiles().length === 0, "tiles were requested with no site — the fixture is wrong");
  assert(/no observing site/.test(badge()),
    `an empty map with nothing to centre on must name the cause — got ${JSON.stringify(badge())}`);
});

test("and recenter states what is missing instead of just greying out", () => {
  const locked = [...container.querySelectorAll('span[role="button"]')]
    .find((n: any) => /recenter/.test(n.textContent || ""));
  assert(locked != null, "recenter is not the honest-disabled stand-in — a bare `disabled` states no reason");
  assert(locked.getAttribute("aria-disabled") === "true", "the recenter stand-in must announce itself disabled");
  assert(/observing site/.test(locked.getAttribute("aria-label") || ""),
    `the reason must ride in the accessible name — got ${locked.getAttribute("aria-label")}`);
  assert(/centres on the observing site/i.test(container.textContent || ""),
    "the empty map box says nothing about why it is empty");
});

// ------------------------------------------------------------------- report
act(() => { rootRef!.unmount(); });
const total = passed + failed;
console.log(`radarMapDom: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
