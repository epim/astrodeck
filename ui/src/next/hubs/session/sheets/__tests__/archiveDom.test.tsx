// archiveDom.test.tsx - SESSION > ARCHIVE (the frame library), MOUNTED.
//
//   Run directly:  npx tsx src/next/hubs/session/sheets/__tests__/archiveDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc`.
//
// WHAT THIS FILE EXISTS FOR. The sheet had no DOM test at all, and the two
// defects below are exactly the kind a source read misses:
//
//  1. REBUILD PREVIEWS DECLARED NO CAPABILITY (review #72). Every other write
//     on this screen is gated - `downloadReason` on `view.media`,
//     `deleteReason` on `control.capture` - and this one was not, while
//     `POST /api/gallery/thumbs/backfill` is `CAP_CONTROL_CAPTURE` on the
//     server (`app.py:7295`). A viewer holds `view.preview`, so the Frames tab
//     renders for them in full: the button was live, undimmed, and answered a
//     press with a 403 toast. That is the 403-on-tap that non-negotiable 6
//     exists to prevent.
//  2. THE JPEG VIEWER WAS GATED ON THE WRONG CAPABILITY (review #74).
//     `FrameViewer` fetches `/api/gallery/view` and `/api/gallery/thumb`, both
//     `CAP_VIEW_PREVIEW`. Gating the tile's OPEN on `view.media` withheld a
//     picture the server would have served - which is the half of the FITS
//     deviation that says JPEG previews stay available to everyone.
//
// The sheet is tablet-and-desktop only, so `matchMedia` answers the 768px
// query true. A phone fixture would render the honest-absence card and every
// assertion below would be vacuous - the first test says so out loud.
//
// Convention: shell-and-tests.md section 4 - jsdom by hand, createRoot + act,
// native events, printed tally plus the `{ passed, failed, total }` export.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/session/gallery/archive", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = (q: string) => ({
  matches: /min-width:\s*768px/.test(String(q)),
  addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.IntersectionObserver = class {
  constructor(private cb: any) {}
  observe(el: any) { this.cb([{ target: el, isIntersecting: true }]); }
  unobserve() {}
  disconnect() {}
};
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLAnchorElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "IntersectionObserver", "requestAnimationFrame",
  "cancelAnimationFrame", "Image",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Asked { method: string; url: string }
const asked: Asked[] = [];
const NIGHT = "2026-09-08";

const FRAMES = [
  {
    path: "M31/L_0001.fits", name: "L_0001.fits", folder: "M31", night: NIGHT,
    ts: 1_757_000_001, local_date: NIGHT, local_clock: "22:10", target: "M31",
    filter: "L", frame_type: "Light", exposure_s: 60, bytes: 10 * 1024 * 1024,
    mtime: 1_757_000_001,
  },
  {
    path: "M31/L_0002.fits", name: "L_0002.fits", folder: "M31", night: NIGHT,
    ts: 1_757_000_002, local_date: NIGHT, local_clock: "22:12", target: "M31",
    filter: "L", frame_type: "Light", exposure_s: 60, bytes: 10 * 1024 * 1024,
    mtime: 1_757_000_002,
  },
];

const ok = (data: unknown) => ({
  ok: true, status: 200, statusText: "OK", json: async () => data,
});

g.fetch = async (url: any, init?: { method?: string }) => {
  const u = String(url);
  asked.push({ method: (init?.method ?? "GET").toUpperCase(), url: u });
  if (u.includes("/api/gallery/nights")) {
    return ok({
      current: NIGHT,
      nights: [{ night: NIGHT, frames: FRAMES.length, bytes: 20 * 1024 * 1024 }],
      truncated: false,
    });
  }
  if (u.includes("/api/gallery/trash")) return ok({ count: 0, items: [], bytes: 0 });
  if (u.includes("/api/gallery/thumbs/backfill")) {
    return ok({ frames: 2, rendered: 2, unrenderable: 0, truncated: false });
  }
  if (u.includes("/api/gallery/frames")) {
    return ok({
      frames: FRAMES, total: FRAMES.length,
      bytes: FRAMES.reduce((a, f) => a + f.bytes, 0),
      offset: 0, limit: 200, truncated: false, scan_ms: 3,
    });
  }
  return ok({});
};

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { accessPhrase } = await import("../../../../../lib/caps");
const { ArchiveSheet } = await import("../archive");

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
const settle = async () => {
  for (let i = 0; i < 5; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => [...container.querySelectorAll(sel)] as any[];
const byLabel = (label: string) =>
  qa("button, a, input").find((n: any) => (n.getAttribute("aria-label") ?? "") === label);
const byText = (t: string) =>
  qa("button").find((n: any) => (n.textContent ?? "").trim() === t);

const ADMIN = {
  role: "admin", email: "admin@rig",
  caps: ["view.status", "view.preview", "view.media", "control.capture", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function seed(principal: unknown): void {
  act(() => {
    useStore.setState({
      principal, wsPhase: "up", equipConnected: true,
      status: { disk: { free_gb: 412, low: false, critical: false } },
      toasts: [],
    } as never);
  });
}

async function mount(principal: unknown): Promise<void> {
  if (rootRef) act(() => { rootRef!.unmount(); });
  seed(principal);
  rootRef = createRoot(container);
  await act(async () => {
    rootRef!.render(createElement(ArchiveSheet as any, { params: {}, depth: 1 }));
  });
  await settle();
}

// ==================================================== 1. it rendered, as a grid
await mount(ADMIN);

await testAsync("precondition: the sheet rendered the frames tab with real tiles", async () => {
  assert(q('[data-testid="session-archive"]') != null,
    "no session-archive marker - the fixture is wrong, not the sheet");
  assert(byText("REFRESH") != null,
    "the actions row is missing, so the REBUILD PREVIEWS assertions below would be vacuous");
  assert(byLabel(`Select ${FRAMES[0].name}`) != null,
    "no frame tiles - a phone-width fixture would render the honest-absence card instead");
});

// ============== 2. REBUILD PREVIEWS declares control.capture (review #72)

await testAsync("an admin can rebuild previews, and it posts the backfill route", async () => {
  const btn = byText("REBUILD PREVIEWS");
  assert(btn != null, "no REBUILD PREVIEWS control");
  eq(btn!.getAttribute("aria-disabled"), null,
    "precondition: an admin found REBUILD PREVIEWS locked");
  asked.length = 0;
  await act(async () => {
    btn!.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
  eq(asked.filter((a) => a.url.includes("/api/gallery/thumbs/backfill")).length, 1,
    "REBUILD PREVIEWS did not post exactly once");
});

await testAsync("a viewer finds it honest-disabled with the route's own capability, and fires nothing", async () => {
  await mount(VIEWER);
  const btn = byText("REBUILD PREVIEWS");
  assert(btn != null,
    "the control was HIDDEN from a viewer - nothing is hidden (ARCHITECTURE section 8), "
    + "and the Frames tab renders for a viewer, who holds view.preview");
  eq(btn!.getAttribute("aria-disabled"), "true",
    "REBUILD PREVIEWS is live for a viewer - the press returns a 403, which is the "
    + "403-on-tap non-negotiable 6 exists to prevent");
  const why = btn!.getAttribute("title") ?? "";
  assert(why.includes(accessPhrase("control.capture")),
    `the reason must be derived from the ROUTE's capability (control.capture), got "${why}"`);

  asked.length = 0;
  await act(async () => {
    btn!.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
  eq(asked.filter((a) => a.url.includes("/api/gallery/thumbs/backfill")).length, 0,
    "a viewer's press reached the rig");
  assert((useStore.getState() as any).toasts.length > 0,
    "the refusal was silent - the reason never reached the user");
});

// ============ 3. the JPEG viewer opens at view.preview, not view.media (#74)

await testAsync("a viewer can open the full-size preview - the server serves it at view.preview", async () => {
  const open = byLabel(`Open ${FRAMES[0].name}`);
  assert(open != null,
    "the tile's OPEN is gated on view.media, so a viewer is refused a picture the "
    + "server would have served (`/api/gallery/view` is CAP_VIEW_PREVIEW)");
});

await testAsync("but the FITS download stays behind view.media", async () => {
  const dl = byLabel(`Download ${FRAMES[0].name}, 10.0 MB`);
  assert(dl == null,
    "a viewer was offered the raw FITS link - opening the preview must not have "
    + "widened the file download with it");
  assert(qa("a").every((a: any) => !String(a.getAttribute("href") ?? "").includes("/api/gallery/file")),
    "a /api/gallery/file link exists in a viewer's DOM");
});

if (rootRef) act(() => { rootRef!.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`archiveDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
