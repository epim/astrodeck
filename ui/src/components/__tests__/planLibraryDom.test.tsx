// planLibraryDom.test.tsx — the Plan panel's Save and export, mounted.
//
//   Run directly:  npx tsx src/components/__tests__/planLibraryDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// TWO CONTROLS THAT REPORTED THEMSELVES INSTEAD OF THE SERVER (2026-08-05).
//
// #51 Save had no in-flight guard. `POST /api/plans` MINTS a plan, so a
//     double-tap on a tablet was not a wasted request: the first press created
//     the row and the second either created a second identical plan or tripped
//     the server's name-collision guard — which then accused the user of
//     duplicating the plan their own first tap had just made.
//
// #53 export fired `showToast("success", "Exported <name>.astroplan.json")`
//     from the anchor CLICK, which knows nothing about what came back. A
//     deleted plan (404) or an expired session (401) produced exactly the same
//     green confirmation as a real download.

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

// ---- the wire -------------------------------------------------------------
type Req = { url: string; method: string; ok: (b: any) => void; fail: (s: number) => void };
const sent: Req[] = [];
const pending: Req[] = [];
function answer(match: string, body: any): void {
  const i = pending.findIndex((p) => p.url.includes(match));
  if (i < 0) throw new Error(`nothing in flight matching "${match}"`);
  const [p] = pending.splice(i, 1);
  p.ok(body);
}
function refuse(match: string, status: number): void {
  const i = pending.findIndex((p) => p.url.includes(match));
  if (i < 0) throw new Error(`nothing in flight matching "${match}"`);
  const [p] = pending.splice(i, 1);
  p.fail(status);
}
const countSent = (url: string, method: string) =>
  sent.filter((r) => r.url.includes(url) && r.method === method).length;

win.fetch = (url: string, init?: any) =>
  new Promise((resolve) => {
    const r: Req = {
      url: String(url),
      method: (init?.method || "GET").toUpperCase(),
      ok: (body: any) => resolve({
        ok: true, status: 200, statusText: "OK",
        json: () => Promise.resolve(body),
        blob: () => Promise.resolve({ size: 12, type: "application/json" }),
      }),
      fail: (status: number) => resolve({
        ok: false, status, statusText: "Not Found",
        json: () => Promise.resolve({ detail: "plan not found" }),
      }),
    };
    sent.push(r);
    pending.push(r);
  });

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "fetch",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
// jsdom implements neither; the export path saves bytes through both.
g.URL.createObjectURL = () => "blob:plan";
g.URL.revokeObjectURL = () => {};
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const PlanLibraryPanel = (await import("../sequence/PlanLibraryPanel")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
async function flush(): Promise<void> {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}
const toasts = () => (useStore.getState() as any).toasts as any[];
const lastToast = () => toasts()[toasts().length - 1];

act(() => {
  useStore.setState({
    principal: { role: "admin", email: null, caps: ["view.status", "control.capture"] },
    plan: { ...(useStore.getState() as any).plan, name: "Veil east" },
    loadedPlanId: null,
    toasts: [],
  } as never);
});

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(PlanLibraryPanel)); });

const byText = (re: RegExp): any =>
  [...container.querySelectorAll("button")].find((b: any) => re.test(b.textContent || ""));
const click = (el: any) => act(() => {
  el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
});

async function scenario(): Promise<void> {
  // the panel's own list load
  answer("/api/plans", []);
  await flush();

  test("the panel mounted with a live Save button", () => {
    const save = byText(/^\s*Save\s*$/);
    assert(save != null, `no Save button: ${container.textContent?.slice(0, 160)}`);
    assert(!save.disabled, "Save is disabled before anything has been pressed");
  });

  // ------------------------------------------------------------------ #51
  const save = byText(/Save/);
  click(save);
  await flush();
  // The second tap of a double-tap, while the first POST is still in the air.
  click(byText(/Saving|Save/));
  await flush();

  test("a double-tap sends ONE save, not two", () => {
    assert(countSent("/api/plans", "POST") === 1,
      `${countSent("/api/plans", "POST")} POSTs went out for one double-tap — each one ` +
      "mints a plan, so the second either duplicates the first or is refused as a " +
      "collision with it");
  });

  test("and the button says it is saving rather than looking untouched", () => {
    const b = byText(/Saving/);
    assert(b != null, `the button still offers "Save" while a save is in flight: ${container.textContent?.slice(0, 160)}`);
    assert(b.disabled, "the in-flight Save button is still enabled");
  });

  answer("/api/plans", { id: "p1", name: "Veil east", targets: 1, frames: 10, integration_s: 3000 });
  await flush();
  if (pending.length) answer("/api/plans", [{ id: "p1", name: "Veil east", targets: 1, frames: 10, integration_s: 3000 }]);
  await flush();

  test("once the server answers, Save is live again", () => {
    const b = byText(/^\s*Save\s*$/);
    assert(b != null && !b.disabled, "Save never came back after a completed save");
    assert(/Saved plan/i.test(lastToast()?.title || ""), `no save confirmation: ${lastToast()?.title}`);
  });

  // ------------------------------------------------------------------ #53
  test("the saved row is listed with an export control", () => {
    assert(byText(/export/) != null,
      `no export button on the saved row: ${container.textContent?.slice(0, 200)}`);
  });

  act(() => { useStore.setState({ toasts: [] } as never); });
  click(byText(/export/));
  await flush();
  const asked = pending.some((p) => p.url.includes("/export"));

  test("export asks the server before it claims anything", () => {
    assert(asked,
      "the export fired an anchor navigation and a green toast in the same breath — " +
      "nothing in that path can see a 404 or an expired session, so both confirm a " +
      "download that never reached the tray");
    assert(lastToast() == null,
      `"${lastToast()?.title}" was announced before the server had answered`);
  });

  if (asked) { refuse("/export", 404); await flush(); }

  test("an export the server refused is NOT reported as a download", () => {
    const t = lastToast();
    assert(t != null, "the failed export said nothing at all");
    assert(t.level === "error",
      `a 404 export produced a "${t.level}" toast reading "${t.title}" — the file is ` +
      "not in the download tray and the screen says it is");
    assert(!/^Exported /.test(t.title), `the toast claims "${t.title}"`);
  });

  act(() => { useStore.setState({ toasts: [] } as never); });
  click(byText(/export/));
  await flush();
  answer("/export", {});
  await flush();

  test("and a real export still confirms, naming the file it wrote", () => {
    const t = lastToast();
    assert(t != null && t.level === "success", `a successful export toasted: ${JSON.stringify(t)}`);
    assert(/Exported .*\.astroplan\.json/.test(t.title), `unhelpful confirmation: "${t?.title}"`);
  });
}

await scenario().catch((e: unknown) => {
  failed++;
  failures.push(`x the scenario could not be driven to the end: ${(e as Error).message}`);
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`planLibraryDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
