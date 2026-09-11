// sitesDom.test.tsx - the SITES sheet, mounted and driven (T-SKY-4 plan G).
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/sitesDom.test.tsx
//   Also run by `npm test` and type-checked by `tsc -b`.
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally
// plus the `{ passed, failed, total }` export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

Object.defineProperty(win, "isSecureContext", { value: false, configurable: true });
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- network
const LOCATIONS = [
  {
    id: "loc1", name: "Back Lawn", latitude: 47.6062, longitude: -122.3321,
    elevation_m: 56, horizon_min_deg: 15, horizon_points: [[10, 20], [100, 5]],
    created_ts: 0, updated_ts: 0,
  },
  {
    id: "loc2", name: "Upper Deck", latitude: 47.61, longitude: -122.33,
    elevation_m: 60, horizon_min_deg: null, horizon_points: null,
    created_ts: 0, updated_ts: 0,
  },
];
const CONFIG_AFTER_APPLY = {
  version: 2,
  site: {
    is_default: false, name: "Back Lawn", latitude: 47.6062, longitude: -122.3321,
    elevation_m: 56, horizon_min_deg: 15,
  },
};

const asked: string[] = [];
const bodies: Record<string, unknown> = {};
const ok = (data: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asked.push(`${method} ${u}`);
  if (init?.body) {
    try { bodies[`${method} ${u}`] = JSON.parse(init.body); } catch { bodies[`${method} ${u}`] = init.body; }
  }
  if (/\/api\/locations\/[^/]+\/apply$/.test(u) && method === "POST") return ok({});
  if (u.includes("/api/locations") && method === "GET") return ok(LOCATIONS);
  if (u.includes("/api/locations") && method === "POST") {
    const body = bodies[`${method} ${u}`] as any;
    return ok({ id: "new1", ...body, created_ts: 0, updated_ts: 0 });
  }
  if (/\/api\/locations\/[^/]+$/.test(u) && method === "PUT") {
    const id = u.split("/").filter(Boolean).pop();
    const body = bodies[`${method} ${u}`] as any;
    return ok({ id, ...body, created_ts: 0, updated_ts: 0 });
  }
  if (/\/api\/locations\/[^/]+$/.test(u) && method === "DELETE") return ok({});
  if (u.includes("/api/config") && method === "GET") return ok(CONFIG_AFTER_APPLY);
  if (u.includes("/api/site/mount-gps")) return ok({ available: false, detail: "Mount not connected" });
  if (u.includes("/api/site/sky")) return ok({ place_hint: "N hemisphere · W longitude · ~N. America", sun_alt_deg: 10 });
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { SitesSheet } = await import("../sites");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${JSON.stringify(want)}, got ${JSON.stringify(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const ADMIN = {
  role: "admin", email: "admin@example.test",
  caps: ["view.status", "config.site_optics", "config.safety", "view.site_precise"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status"] };

const seed = (over: Record<string, unknown> = {}) => {
  useStore.setState({
    principal: ADMIN,
    wsPhase: "up",
    equipConnected: true,
    status: { connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [] },
    config: { version: 1, site: { is_default: true, horizon_min_deg: 15 } },
    ...over,
  } as never);
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const q = (sel: string): any => container.querySelector(sel);
const qa = (sel: string): any[] => Array.from(container.querySelectorAll(sel));
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

// ============================================================ ADMIN: render
seed();
await act(async () => { root.render(createElement(SitesSheet, { params: {}, depth: 0 } as any)); });
await settle();

await test("precondition: SITES renders the list from GET /api/locations, with a horizon summary per row", () => {
  assert(/SITES/.test(container.textContent), "the sheet title never rendered");
  assert(asked.some((a) => a === "GET /api/locations"), "the sheet never asked for the saved-location list");
  const rows = qa('[data-testid^="site-row-"]');
  assert(rows.length === 2, `expected 2 rows, found ${rows.length} - the fixture is wrong, not the component`);
  assert(/horizon:/.test(container.textContent), "no row states its horizon summary");
});

await test("picking a site fires exactly one apply request", async () => {
  const before = asked.filter((a) => a === "POST /api/locations/loc1/apply").length;
  eq(before, 0, "precondition: nothing applied yet");
  click(q('[data-testid="site-radio-loc1"]'));
  await settle();
  const after = asked.filter((a) => a === "POST /api/locations/loc1/apply").length;
  eq(after, 1, "selecting a row did not POST .../apply exactly once");
  assert(asked.filter((a) => a === "GET /api/config").length >= 1,
    "the pick did not refresh config afterwards (loadConfig) - other consumers would keep the stale site");
});

await test("…and the radio MARKER follows the newly-applied site once config catches up", () => {
  const radio = q('[data-testid="site-radio-loc1"]');
  eq(radio.getAttribute("aria-checked"), "true",
    "the radio never flipped to checked after the config refresh named this site active");
  const toasts = JSON.stringify(useStore.getState().toasts);
  assert(/Back Lawn selected/.test(toasts), `no confirmation toast named where it applied: ${toasts}`);
});

await act(async () => { root.unmount(); });

// =========================================================== VIEWER: render
seed({ principal: VIEWER });
const vRoot = createRoot(container);
const askedBefore = asked.length;
await act(async () => { vRoot.render(createElement(SitesSheet, { params: {}, depth: 0 } as any)); });
await settle();

await test("a viewer never sees precise coordinates and the editable section names its own gate", () => {
  assert(asked.length === askedBefore,
    "a viewer's render still asked the server for the location library, which 403s for this role");
  assert(!/47\.6062/.test(container.textContent), "a raw coordinate leaked to a role without view.site_precise");
  assert(/needs? .* access/.test(container.textContent),
    `the locked section does not name why: "${container.textContent.slice(0, 200)}"`);
  assert(q('[data-testid="new-site-here"]') == null,
    "+ NEW SITE HERE rendered for a role that cannot even list the locations it would edit");
});

await act(async () => { vRoot.unmount(); });

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`sitesDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
