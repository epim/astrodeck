// driversAddAll.test.tsx — Settings → Backend Drivers, MOUNTED, across an
// "Add all" that fails part way.
//
//   Run directly:  npx tsx src/components/settings/__tests__/driversAddAll.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// THE DEFECT (audit #66). "Add all" is one POST per discovered device. The
// shared `run()` helper re-listed only on the SUCCESS path, so a batch that
// created two drivers and then failed on the third showed none of the two: the
// scan rows still read "Add", `hwAlreadyConfigured` still said no, and the
// obvious response to a red toast — tap it again — created a second driver row
// for the same physical device. Duplicate rows for one bus is not cosmetic;
// each one is an addressable driver the Equipment dropdowns then offer.
//
// The claim is "what is on screen after a partial failure", so it is asserted
// on the mounted panel after a real failing batch.

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- stub server
// Two serial units on one scan. The SECOND add fails — a real possibility (the
// port went away between scan and add, another client took it, the config
// write lost a race).
const stored: any[] = [];
const mkDriver = (id: string, portPath: string) => ({
  id, type: "zwo-am5", label: `Mount on ${portPath}`, enabled: true, implicit: false,
  host: "", port: 0, transport: "serial", port_path: portPath,
  status: { reachable: true, error: null, detail: null, probed_at: 0 },
  offers: { devices: [], tasks: [] },
});
let addCalls = 0;
g.fetch = async (url: string, init?: any) => {
  const path = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  const ok = (body: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => body } as any);
  if (path.includes("/api/config/drivers") && method === "POST") {
    addCalls++;
    if (addCalls === 2) {
      return {
        ok: false, status: 409, statusText: "Conflict",
        json: async () => ({ detail: "COM7 is already open" }),
      } as any;
    }
    const body = JSON.parse(init.body);
    stored.push(mkDriver(`drv-${stored.length + 1}`, body.port_path));
    return ok({ driver: { id: `drv-${stored.length}` } });
  }
  // COPY, never the live array: handing the component the same object the
  // stub mutates later would let a stale response appear to update itself, and
  // a test for "did the panel re-list" would pass without any re-listing.
  if (path.includes("/api/drivers")) return ok({ roles: ["mount"], drivers: stored.map((d) => ({ ...d })) });
  if (path.includes("/api/backends"))
    return ok([{ name: "zwo-am5", label: "ZWO AM5", hardware: true, discoverable: true, driver_type: "zwo-am5", transport: "serial" }]);
  if (path.includes("/api/discover/"))
    return ok([
      { role: "mount", name: "AM5N on COM6", port_path: "COM6" },
      { role: "mount", name: "AM5N on COM7", port_path: "COM7" },
    ]);
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const DriversPanel = (await import("../DriversPanel")).default;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

useStore.setState({
  status: { connected: {}, looping: false, mode: "none" },
  principal: { role: "admin", email: null, caps: ["view.status", "config.backend"] },
} as never);

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const flush = async () => { for (let i = 0; i < 8; i++) await act(async () => { await Promise.resolve(); }); };
const byText = (re: RegExp): any =>
  [...container.querySelectorAll("button")].find((b: any) => re.test(b.textContent || ""));
const click = async (node: any) => {
  await act(async () => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
  await flush();
};

await act(async () => { root.render(createElement(DriversPanel)); });
await flush();

await testAsync("the scan really found two units and offered to add both", async () => {
  // PRECONDITION. Without two scan rows and a live "Add all", every assertion
  // below about what survives a partial batch is vacuous.
  await click(byText(/scan for usb\/serial hardware/i));
  assert(/COM6/.test(container.textContent || "") && /COM7/.test(container.textContent || ""),
    "the scan did not render both discovered units — the fixture is wrong, not the panel");
  assert(byText(/^add all$/i) != null, "no Add all control offered for the two unconfigured units");
});

await testAsync("a batch that fails part way still shows the drivers it created", async () => {
  await click(byText(/^add all$/i));
  assert(addCalls === 2, `expected the batch to stop at the failing add, saw ${addCalls} POSTs`);
  assert(stored.length === 1, "fixture: exactly one driver should have been created server-side");
  assert(/Mount on COM6/.test(container.textContent || ""),
    "the driver that WAS created is nowhere on screen — the panel re-listed only on " +
    "success, so the user is looking at a list that is missing a real driver");
});

await testAsync("and does not re-offer it, which is how the duplicates were made", async () => {
  const alreadyRows = (container.textContent || "").match(/already configured/g) ?? [];
  assert(alreadyRows.length === 1,
    `the COM6 unit should now read "already configured" (found ${alreadyRows.length} such rows) — ` +
    "while it reads Add, the obvious response to the error toast creates a second " +
    "driver row for the same physical mount");
  // And the remaining offer is the one that genuinely failed.
  const addAll = byText(/^add all$/i);
  assert(addAll != null, "Add all vanished — COM7 is still unconfigured and must stay addable");
  await click(addAll);
  assert(addCalls === 3 && stored.length === 2,
    `the retry should add only the missing unit — saw ${addCalls} total POSTs for ${stored.length} drivers`);
});

act(() => { root.unmount(); });

console.log(`driversAddAll: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
