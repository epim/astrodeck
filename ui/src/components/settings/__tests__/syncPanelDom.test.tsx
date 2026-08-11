// syncPanelDom.test.tsx — Settings → File sync, MOUNTED.
//
//   Run directly:  npx tsx src/components/settings/__tests__/syncPanelDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The whole point of this panel is that a feature which only ever acts at 02:00
// can be verified at 11:00. So what is under test is not layout — it is whether
// the screen can tell apart the four states that all look identical from the
// outside (off / on-but-nowhere / working / failing), and whether the two
// controls that make it checkable are actually usable when they should be:
//
//   * the enable must be UNUSABLE with an empty path, because the server refuses
//     that write — a toggle that flips and then 422s teaches the operator that
//     the panel lies;
//   * "Push now" must report what the pass DID. A button that says "started" and
//     leaves is the same non-verifiable promise the panel exists to replace.

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "ResizeObserver", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- stub server
// A tiny in-memory rig: the config block it holds and the pass it would run.
// `fetch` is stubbed rather than the api module so the panel's real client code
// (paths, method, body shape) is exercised.
const server = {
  sync_push: { enabled: false, kind: "local_dir", path: "", label: "", limit_per_pass: 0 },
  passes: 0,
  nextPass: { sent: 2, failed: 0, bytes_sent: 2_400_000_000, already_there: 41,
              extra_at_destination: 0, elapsed_s: 12.4, error: "",
              summary: "push: pushed 2 frames, 2.40 GB, 12s" },
  writes: [] as any[],
  status(): any {
    return {
      ...this.sync_push,
      configured: this.sync_push.enabled && !!this.sync_push.path.trim(),
      running: false, debounce_s: 20, sweep_interval_s: 900,
      passes: this.passes, total_sent: this.passes * 2,
      total_bytes: this.passes * 2_400_000_000,
      last_attempt_at: this.passes ? Date.now() / 1000 : null,
      last_ok_at: this.passes ? Date.now() / 1000 : null,
      consecutive_failures: 0, alarm: false,
      last: this.passes ? this.nextPass : null,
    };
  },
};

g.fetch = async (url: string, init?: any) => {
  const path = String(url);
  const json = (body: any, status = 200) => new win.Response(JSON.stringify(body), {
    status, headers: { "content-type": "application/json" },
  });
  if (path.includes("/api/sync/push/now")) {
    server.passes++;
    return json(server.status());
  }
  if (path.includes("/api/sync/push")) return json(server.status());
  if (path.includes("/api/config/sync")) {
    const body = JSON.parse(init.body);
    server.writes.push(body.sync_push);
    if (body.sync_push.enabled && !body.sync_push.path.trim()) {
      return json({ detail: "a sync destination path is required" }, 422);
    }
    server.sync_push = body.sync_push;
    return json({ sync_push: server.sync_push });
  }
  if (path.includes("/api/config")) return json({ sync_push: server.sync_push });
  return json({});
};
// The panel calls store.loadConfig() after a save; give it the same block back.
win.Response = win.Response || g.Response;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const SyncPanel = (await import("../SyncPanel")).default;
const { syncSummary, formatBytes } = await import("../SyncPanel");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// ------------------------------------------------- the four states, as text
test("off reads as off, not as an error", () => {
  const s = syncSummary({ enabled: false } as any);
  assert(/off/i.test(s.text) && s.tone === "dim", `off reads "${s.text}" (${s.tone})`);
});

test("on with no destination is called out, not shown as working", () => {
  const s = syncSummary({ enabled: true, configured: false } as any);
  assert(s.tone === "warn" && /no destination/i.test(s.text),
    `an unconfigured-but-enabled sync reads "${s.text}" (${s.tone})`);
});

test("a run of failures says frames are NOT leaving the rig", () => {
  const s = syncSummary({ enabled: true, configured: true, alarm: true,
                          consecutive_failures: 3, passes: 5 } as any);
  assert(s.tone === "warn", `a failing sync reads as ${s.tone}`);
  assert(/not leaving the rig/i.test(s.text), `failing sync reads "${s.text}"`);
  assert(/3/.test(s.text), "the failure count is not on screen");
});

test("armed but never run is distinguishable from working", () => {
  const s = syncSummary({ enabled: true, configured: true, passes: 0 } as any);
  assert(/no pass has run/i.test(s.text), `reads "${s.text}"`);
});

test("a working sync states volume, not just a count", () => {
  const s = syncSummary({ enabled: true, configured: true, passes: 3,
                          total_sent: 170, total_bytes: 8_600_000_000,
                          last_ok_at: Date.now() / 1000 - 90 } as any);
  assert(/170 frames/.test(s.text), `reads "${s.text}"`);
  assert(/8\.60 GB/.test(s.text), `volume missing from "${s.text}"`);
});

test("bytes are formatted at the scale a night is measured in", () => {
  assert(formatBytes(0) === "0 B", formatBytes(0));
  assert(formatBytes(2_400_000_000) === "2.40 GB", formatBytes(2_400_000_000));
  assert(formatBytes(52_000_000) === "52 MB", formatBytes(52_000_000));
});

// ------------------------------------------------------------ mounted panel
useStore.setState({
  config: { sync_push: server.sync_push },
  principal: { role: "admin", email: null,
               caps: ["view.status", "view.media", "config.site_optics"] },
  showToast: () => {},
} as never);

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const flush = async () => { for (let i = 0; i < 20; i++) await act(async () => { await Promise.resolve(); }); };

await act(async () => { root.render(createElement(SyncPanel)); });
await flush();

const pathInput = (): any => container.querySelector('input[aria-label="Destination folder"]');
const toggle = (): any => container.querySelector('[aria-label="Push frames to a folder"]')
  || [...container.querySelectorAll("button,input")].find(
    (n: any) => /push frames to a folder/i.test(n.getAttribute("aria-label") || ""));
const buttonNamed = (rx: RegExp): any =>
  [...container.querySelectorAll("button")].find((b: any) => rx.test(b.textContent || ""));
const type = (node: any, value: string) => act(() => {
  const setter = Object.getOwnPropertyDescriptor(
    win.HTMLInputElement.prototype, "value")!.set!;
  setter.call(node, value);
  node.dispatchEvent(new win.Event("input", { bubbles: true }));
});
const click = (node: any) => act(() => {
  node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
});

test("the destination field is on screen", () => {
  assert(!!pathInput(), "no destination input rendered");
});

test("the enable is unusable while no destination is typed", () => {
  const t = toggle();
  assert(!!t, "no enable control rendered");
  assert(t.disabled === true || t.getAttribute("aria-disabled") === "true",
    "the enable is live with an empty path — the server would 422 that write");
});

test("'Push now' is unusable before a destination is configured", () => {
  const b = buttonNamed(/push now/i);
  assert(!!b, "no 'Push now' button rendered");
  assert(b.disabled === true, "'Push now' offers to push to nowhere");
});

await testAsync("typing a path offers a save, and the save reaches the server", async () => {
  type(pathInput(), "\\\\nas\\astro");
  await flush();
  const save = buttonNamed(/save destination/i);
  assert(!!save, "no way to save the typed destination");
  click(save);
  await flush();
  assert(server.writes.length > 0, "nothing was written to the server");
  assert(server.writes[server.writes.length - 1].path === "\\\\nas\\astro",
    `server got path "${server.writes[server.writes.length - 1].path}"`);
});

await testAsync("'Push now' reports what the pass DID, not that it started", async () => {
  // The rig now has a destination and the feature on.
  server.sync_push = { ...server.sync_push, enabled: true, path: "\\\\nas\\astro" };
  useStore.setState({ config: { sync_push: server.sync_push } } as never);
  await act(async () => { root.render(createElement(SyncPanel)); });
  await flush();

  const b = buttonNamed(/push now/i);
  assert(!!b && !b.disabled, "'Push now' is still disabled with a live destination");
  const before = server.passes;
  click(b);
  await flush();
  assert(server.passes === before + 1, "the button did not run a pass");
  const text = container.textContent || "";
  assert(/pushed 2 frames/.test(text),
    `the panel does not state the pass result; it shows: ${text.slice(0, 300)}`);
  assert(/41 already there/.test(text),
    "the panel hides how much was already at the destination");
});

act(() => { root.unmount(); });

console.log(`syncPanelDom.test: ${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
