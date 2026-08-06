// touchGuardStopDom.test.tsx — the locked screen's EMERGENCY STOP.
//
//   Run directly:  npx tsx src/components/__tests__/touchGuardStopDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS IS ABOUT (UX-2026-08-05 #25). This button is the one control that
// stays live behind the screen lock, on the tablet clipped to the tripod, at
// 3am. Everything it says has to be about the RIG. Before 2026-08-05 it:
//   * buzzed the confirming `stop` pattern before either POST had left, so a
//     stop that 403'd felt exactly like one that zeroed both axes;
//   * swallowed 403 silently on purpose, which is the case where somebody walks
//     up to a moving mount believing it was told to stop;
//   * reported every other failure through `showToast` — and Toasts is a z-40
//     layer while the lock overlay is z-50 with pointerEvents:auto over it, so
//     the message painted UNDERNEATH the lock and could not be read or
//     dismissed. A DOM test cannot see z-index, so the assertion below is the
//     one that survives the layering: the answer must be INSIDE the overlay.

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

// --- haptics recorder --------------------------------------------------------
// `haptics.supported` is computed at module load from navigator.vibrate, so this
// has to exist BEFORE the component (and lib/haptics with it) is imported.
const buzzes: string[] = [];
Object.defineProperty(win.navigator, "vibrate", {
  value: (p: number | number[]) => { buzzes.push(JSON.stringify(p)); return true; },
  writable: true, configurable: true,
});
/** The distinct confirming pattern (PATTERNS.stop in lib/haptics). */
const STOP_PATTERN = JSON.stringify([0, 12, 40, 12]);
const ERROR_PATTERN = JSON.stringify([0, 50, 40, 50]);

// --- controllable wire -------------------------------------------------------
type Pending = { url: string; ok: (body?: any) => void; fail: (status: number) => void };
const pending: Pending[] = [];
function answer(match: string, how: "ok" | number): void {
  const i = pending.findIndex((p) => p.url.includes(match));
  if (i < 0) throw new Error(`nothing in flight matching "${match}"`);
  const [p] = pending.splice(i, 1);
  if (how === "ok") p.ok({});
  else p.fail(how);
}
win.fetch = (url: string) =>
  new Promise((resolve) => {
    pending.push({
      url: String(url),
      ok: (body: any = {}) => resolve({
        ok: true, status: 200, statusText: "OK", json: () => Promise.resolve(body),
      }),
      fail: (status: number) => resolve({
        ok: false, status, statusText: "Forbidden",
        json: () => Promise.resolve({ detail: "capability not held" }),
      }),
    });
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
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const TouchGuard = (await import("../TouchGuard")).default;

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

// The lock is set directly rather than through setLocked(), which fires its own
// /api/mount/stop — this test is about the button, not about engaging the lock.
act(() => {
  useStore.setState({
    locked: true,
    lockAvailable: true,
    principal: { role: "admin", email: null, caps: ["view.status", "control.mount"] },
  } as never);
});

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(TouchGuard)); });

const overlay = () => container.querySelector('[role="dialog"][aria-modal="true"]');
const stopBtn = () =>
  container.querySelector('[aria-label="Emergency stop all motion and abort sequence"]');
/** ONLY text inside the lock overlay counts: anything outside it is painted
 *  under a full-viewport, pointer-swallowing blocker. */
const overlayText = () => (overlay()?.textContent || "");
const press = (el: any) => act(() => {
  el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
});

async function scenario(): Promise<void> {
  test("the lock is up and the emergency stop is on it", () => {
    assert(overlay() != null, "no lock overlay rendered — the fixture is wrong, not the component");
    assert(stopBtn() != null, "no EMERGENCY STOP button behind the lock");
    assert(/SCREEN LOCKED/.test(overlayText()), "this is not the locked screen");
  });

  // ---------------------------------------------------- the buzz waits for the rig
  buzzes.length = 0;
  press(stopBtn());
  await flush();

  test("both authoritative stops are actually sent", () => {
    assert(pending.some((p) => p.url.includes("/api/mount/stop")), "no /api/mount/stop went out");
    assert(pending.some((p) => p.url.includes("/api/sequence/abort")), "no /api/sequence/abort went out");
  });

  test("the confirming buzz has NOT fired while the request is still in flight", () => {
    // A press-time confirm is a claim about the mount made from a button press.
    assert(!buzzes.includes(STOP_PATTERN),
      `the "stopped" pattern buzzed before the rig answered (buzzes: ${buzzes.join(" ")})`);
    assert(buzzes.length > 0, "the press produced no feedback at all — it must acknowledge the tap");
  });

  test("and the button says it is working", () => {
    assert(/STOPPING/.test(overlayText()), `the button still reads: ${overlayText().slice(0, 80)}`);
  });

  // ------------------------------------------------------------- the happy answer
  answer("/api/mount/stop", "ok");
  answer("/api/sequence/abort", "ok");
  await flush();

  test("a stop the rig accepted buzzes the confirm and says so on the lock screen", () => {
    assert(buzzes.includes(STOP_PATTERN),
      `no confirming buzz after both stops landed (buzzes: ${buzzes.join(" ")})`);
    assert(/stopped/i.test(overlayText()),
      `the lock screen never confirms the stop: ${overlayText().slice(0, 200)}`);
  });

  // -------------------------------------------------------------- THE 403 CASE
  buzzes.length = 0;
  press(stopBtn());
  await flush();
  answer("/api/mount/stop", 403);
  answer("/api/sequence/abort", 403);
  await flush();

  test("a refused stop is REPORTED, not swallowed", () => {
    const t = overlayText();
    assert(/DID NOT LAND|not land/i.test(t),
      `a 403 left the lock screen saying: ${t.slice(0, 200)} — the user believes the ` +
      "mount was told to stop and it was not");
    assert(!/Motion stopped/i.test(t),
      "the previous press's success line is still on screen over a refused stop");
  });

  test("the refusal names what is missing, not 'capability not held'", () => {
    const t = overlayText();
    assert(/access/i.test(t) && /watch|view/i.test(t),
      `the refusal reads "${t.slice(0, 220)}" — the server's own wording names nothing ` +
      "the person holding the tablet can act on");
  });

  test("the refusal is INSIDE the lock overlay, where it can be seen", () => {
    // The whole point: a toast would be behind this element.
    assert(/DID NOT LAND/i.test(overlayText()),
      "the failure is not inside the lock overlay's own DOM — anywhere else on this " +
      "screen is underneath a z-50 pointer-blocking layer");
  });

  test("and it buzzes the error pattern, not the confirm", () => {
    assert(buzzes.includes(ERROR_PATTERN), `no error buzz on a refused stop (${buzzes.join(" ")})`);
    assert(!buzzes.includes(STOP_PATTERN), `a refused stop buzzed the CONFIRM (${buzzes.join(" ")})`);
  });

  test("the emergency control is still pressable after a failure", () => {
    assert(!(stopBtn() as any).disabled,
      "the emergency stop disabled itself — on a bad link this is the control that " +
      "must always take another press");
  });
}

await scenario().catch((e: unknown) => {
  failed++;
  failures.push(`x the scenario could not be driven to the end: ${(e as Error).message}`);
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`touchGuardStopDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
