// horizonDom.test.tsx - the HORIZON EDITOR sheet, mounted and driven
// (T-SKY-4 plan G). jsdom's `getBoundingClientRect` stub returns an all-zero
// rect, which `horizonStrip.toViewBox`'s width/height fallback (`rect.width
// || STRIP_W`) turns into an IDENTITY map onto the 340x150 viewBox - so a
// pointer event's `clientX`/`clientY` can be dispatched as viewBox units
// directly, exactly like the plan's own "tap the strip at 25% width" test.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/horizonDom.test.tsx
//   Also run by `npm test` and type-checked by `tsc -b`.

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
type Fixture = { id: string; name: string; latitude: number; longitude: number; elevation_m: number; horizon_min_deg: number | null; horizon_points: [number, number][] | null };
const LOCATIONS = new Map<string, Fixture>([
  ["loc1", { id: "loc1", name: "Back Lawn", latitude: 47.6, longitude: -122.3, elevation_m: 50, horizon_min_deg: 15, horizon_points: [[0, 10], [180, 20]] }],
  ["loc2", { id: "loc2", name: "Upper Deck", latitude: 47.6, longitude: -122.3, elevation_m: 60, horizon_min_deg: null, horizon_points: [[0, 5]] }],
  ["loc3", { id: "loc3", name: "Club Field", latitude: 47.6, longitude: -122.3, elevation_m: 70, horizon_min_deg: null, horizon_points: [[10, 5], [50, 25], [90, 15]] }],
]);

const asked: string[] = [];
const putBodies: Record<string, unknown>[] = [];
const ok = (data: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asked.push(`${method} ${u}`);
  if (u.includes("/api/locations") && method === "GET") return ok(Array.from(LOCATIONS.values()));
  const putMatch = /\/api\/locations\/([^/]+)$/.exec(u);
  if (putMatch && method === "PUT") {
    const id = putMatch[1];
    const body = init?.body ? JSON.parse(init.body) : {};
    putBodies.push(body);
    const merged: Fixture = { ...(LOCATIONS.get(id) as Fixture), ...body, id };
    LOCATIONS.set(id, merged);
    return ok(merged);
  }
  if (u.includes("/api/site") && method === "GET") {
    return ok({ site: { name: "Active", is_default: false, horizon_min_deg: 15, horizon_points: [] }, version: 1 });
  }
  if (u.includes("/api/config") && method === "POST") return ok({ version: 2 });
  if (u.includes("/api/config") && method === "GET") return ok({ version: 2, site: { is_default: true, horizon_min_deg: 15 } });
  return ok({});
};

const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { ConfirmHost } = await import("../../../../../components/ConfirmDialog");
const { HorizonSheet } = await import("../horizon");
const { HX, HY } = await import("../horizonStrip");

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
const OPERATOR = {
  role: "operator", email: "op@example.test",
  caps: ["view.status", "control.capture", "control.mount", "control.guide"],
};

const seed = (over: Record<string, unknown> = {}) => {
  useStore.setState({
    principal: ADMIN,
    wsPhase: "up",
    equipConnected: true,
    status: { connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [] },
    config: { version: 1, site: { is_default: true, horizon_min_deg: 15 }, safety: { horizon: [] } },
    ...over,
  } as never);
};

/** jsdom's `getBoundingClientRect` stub is all zeros, and
 *  `horizonStrip.toViewBox` falls back to the strip's own 340x150 grid when
 *  width/height read 0 - so a client coordinate IS a viewBox coordinate here. */
function ptr(type: string, x: number, y: number, opts: Record<string, unknown> = {}): any {
  const ev = new win.Event(type, { bubbles: true, cancelable: true });
  Object.assign(ev, { pointerId: 1, pointerType: "mouse", button: 0, buttons: 1, isPrimary: true, clientX: x, clientY: y, ...opts });
  return ev;
}

// ======================================================= GROUP A: add/delete
seed();
const containerA = win.document.getElementById("root") as any;
const rootA = createRoot(containerA);
await act(async () => {
  rootA.render(createElement(HorizonSheet, { params: { site: "loc1" }, depth: 0 } as any));
});
await settle();

const stripA = () => containerA.querySelector('[data-testid="horizon-strip"]') as any;
const circlesA = () => Array.from(stripA().querySelectorAll("circle")) as any[];

await test("precondition: the strip rendered with the tap/drag/delete hint", () => {
  assert(stripA() != null, "no <svg data-testid=horizon-strip> - the fixture is wrong, not the component");
  assert(/tap to add · drag to move · tap a point to delete/.test(containerA.textContent),
    "the hint chip text is missing or does not match verbatim");
  eq(circlesA().length, 2, "the two seeded points did not render as circles");
});

await test("a tap on empty strip at 25% width adds a point near az 90", async () => {
  const before = putBodies.length;
  act(() => { stripA().dispatchEvent(ptr("pointerdown", 0.25 * 340, 100)); });
  act(() => { stripA().dispatchEvent(ptr("pointerup", 0.25 * 340, 100)); });
  await settle();
  eq(putBodies.length, before + 1, "the tap did not fire exactly one write");
  const body = putBodies[putBodies.length - 1] as any;
  const pts: [number, number][] = body.horizon_points;
  assert(Array.isArray(pts) && pts.length === 3, `the write did not carry the new point: ${JSON.stringify(pts)}`);
  const added = pts.find((p) => p[0] >= 88 && p[0] <= 92);
  assert(added != null, `no point landed near az 90 - the PUT body was ${JSON.stringify(pts)}`);
});

await test("a tap ON a point (no drag) deletes it, with no confirm", async () => {
  const before = putBodies.length;
  const beforeCircles = circlesA().length;
  const x = HX(0);
  const y = HY(10); // the seeded az=0/alt=10 point
  act(() => { stripA().dispatchEvent(ptr("pointerdown", x, y)); });
  act(() => { stripA().dispatchEvent(ptr("pointerup", x, y)); });
  await settle();
  eq(putBodies.length, before + 1, "tapping an existing point did not write exactly once");
  assert(circlesA().length === beforeCircles - 1, "the tapped point is still on the strip");
  const body = putBodies[putBodies.length - 1] as any;
  assert(!(body.horizon_points as [number, number][]).some((p) => p[0] === 0 && p[1] === 10),
    "the deleted point is still in the written body");
});

await act(async () => { rootA.unmount(); });

// ============================================================ GROUP B: clear
seed();
const containerB = win.document.getElementById("root") as any;
const rootB = createRoot(containerB);
await act(async () => {
  rootB.render(createElement(Fragment, null,
    createElement(HorizonSheet, { params: { site: "loc2" }, depth: 0 } as any),
    createElement(ConfirmHost),
  ));
});
await settle();

// The confirm card portals to a body-level host (Overlay.tsx), OUTSIDE
// `containerB` - search the whole document, not the sheet's own container.
const buttonB = (re: RegExp): any =>
  Array.from(win.document.body.querySelectorAll("button")).find((b: any) => re.test(b.textContent || ""));
const clickB = (el: any) => act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });

await test("CLEAR opens a confirm and writes NOTHING until it is confirmed", async () => {
  const before = putBodies.length;
  clickB(buttonB(/CLEAR THE HORIZON/));
  await settle();
  // The confirm card portals to a body-level host - check the whole document.
  assert(/Clear the horizon at/.test(win.document.body.textContent), "the confirm card never appeared");
  eq(putBodies.length, before, "a write fired before the confirm was answered");
  clickB(buttonB(/^\s*CLEAR\s*$/));
  await settle();
  eq(putBodies.length, before + 1, "confirming CLEAR did not write");
  const body = putBodies[putBodies.length - 1] as any;
  eq((body.horizon_points as unknown[]).length, 0, "the confirmed clear did not empty the horizon");
});

await act(async () => { rootB.unmount(); });

// ======================================================= GROUP C: neighbour clamp
seed();
const containerC = win.document.getElementById("root") as any;
const rootC = createRoot(containerC);
await act(async () => {
  rootC.render(createElement(HorizonSheet, { params: { site: "loc3" }, depth: 0 } as any));
});
await settle();
const stripC = () => containerC.querySelector('[data-testid="horizon-strip"]') as any;

await test("dragging a point past its neighbour CLAMPS instead of crossing it", async () => {
  // loc3: az 10, 50, 90. Drag the middle point (az 50) far past the az-90
  // neighbour; horizonModel.movePoint clamps az to (neighbour - 1) = 89.
  const startX = HX(50);
  const startY = HY(25);
  act(() => { stripC().dispatchEvent(ptr("pointerdown", startX, startY)); });
  act(() => { stripC().dispatchEvent(ptr("pointermove", HX(250), startY)); });
  act(() => { stripC().dispatchEvent(ptr("pointerup", HX(250), startY)); });
  await settle();
  const body = putBodies[putBodies.length - 1] as any;
  const pts: [number, number][] = body.horizon_points;
  const moved = pts.find((p) => Math.abs(p[1] - 25) < 3 && p[0] !== 10 && p[0] !== 90);
  if (!moved) throw new Error(`could not find the dragged point in the write: ${JSON.stringify(pts)}`);
  assert(moved[0] <= 89, `the dragged point crossed its neighbour at az 90 (landed at az ${moved[0]}) - the clamp is not enforced`);
  assert(moved[0] > 50, `the point did not move at all (still at az ${moved[0]}) - the drag itself did not register`);
});

await act(async () => { rootC.unmount(); });

// ==================================================== GROUP D: locked render
seed({ principal: OPERATOR });
const containerD = win.document.getElementById("root") as any;
const rootD = createRoot(containerD);
await act(async () => {
  rootD.render(createElement(HorizonSheet, { params: { site: "loc1" }, depth: 0 } as any));
});
await settle();
const stripD = () => containerD.querySelector('[data-testid="horizon-strip"]') as any;
const buttonD = (re: RegExp): any =>
  Array.from(containerD.querySelectorAll("button")).find((b: any) => re.test(b.textContent || ""));

await test("without config.safety the strip still renders and both buttons name the lock", () => {
  assert(stripD() != null, "the strip vanished for a role without config.safety - it must stay drawn, read-only");
  const clearBtn = buttonD(/CLEAR THE HORIZON/);
  const captureBtn = buttonD(/CAPTURE PHOTOSPHERE|RE-CAPTURE/);
  assert(clearBtn != null && captureBtn != null, "the buttons themselves vanished instead of locking");
  eq(clearBtn.getAttribute("aria-disabled"), "true", "CLEAR is not marked locked");
  eq(captureBtn.getAttribute("aria-disabled"), "true", "CAPTURE PHOTOSPHERE is not marked locked");
  assert(/needs .* access/.test(clearBtn.getAttribute("title") || ""), "CLEAR's lock reason is not stated");
});

await test("a tap on the strip writes nothing while locked", async () => {
  const before = putBodies.length;
  act(() => { stripD().dispatchEvent(ptr("pointerdown", HX(200), HY(30))); });
  act(() => { stripD().dispatchEvent(ptr("pointerup", HX(200), HY(30))); });
  await settle();
  eq(putBodies.length, before, "a locked strip still wrote a point");
});

await act(async () => { rootD.unmount(); });

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`horizonDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
