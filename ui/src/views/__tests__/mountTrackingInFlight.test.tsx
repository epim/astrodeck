// mountTrackingInFlight.test.tsx — the tracking switch while the rig is busy.
//
//   Run directly:  npx tsx src/views/__tests__/mountTrackingInFlight.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY A SECOND MOUNT-VIEW DOM FILE. mountViewDom.test.tsx drives this view
// against a rig whose every POST answers inside one microtask, which is the
// right fixture for the lane-driven controls (park/goto/solve answer instantly
// and lie about it). It is the WRONG fixture for tracking, because tracking is
// the opposite kind of route: it is not `_spawn`ed, so its POST resolves only
// when the device has actually answered — and on a serial LX200 mount that is a
// real round trip. A fixture that answers instantly can never show what the
// control looks like DURING it, so the gap audit finding 88 named ("no in-flight
// state") is invisible there by construction. This file holds the round trip
// open and looks.
//
// The second half is about the other way this switch was wrong: it stayed fully
// live while the mount was committed to a park, a home or a goto. Park's whole
// job is to stop tracking; a tracking command sent into one is a command fighting
// the rig, and the switch gave no hint that anything was going on.
//
// WHAT IT CANNOT DO: jsdom lays nothing out. Nothing here is evidence about
// size, contrast or reachability — only about what the control said and whether
// it could be pressed.

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
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};

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

// ------------------------------------------------------- a rig that takes time
// `hold` is the whole point of this fixture: the POST is RECORDED first and only
// then waits, so a test can stand inside the round trip and ask the control what
// it is showing. Without it every request resolves in a microtask and the
// in-flight window does not exist to be measured.
const posts: { path: string }[] = [];
let releaseTracking: (() => void) | null = null;
g.fetch = async (url: string, init?: any) => {
  const path = String(url);
  if ((init?.method ?? "GET") === "POST") {
    posts.push({ path });
    if (path.includes("/api/mount/tracking") && releaseTracking === null) {
      await new Promise<void>((r) => { releaseTracking = r; });
    }
  }
  const json =
    path.includes("/api/catalog") ? []
      : path.includes("/api/sequence/preflight")
        ? { alt: 55, az: 175, verdict: "ok", horizon_min_deg: 10, site_is_default: false }
        : { started: "ok" };
  return { ok: true, status: 200, statusText: "OK", json: async () => json } as any;
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const MountView = (await import("../MountView")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

function seed(lanes: string[] = [], mount: Record<string, unknown> = {}): void {
  useStore.setState({
    status: {
      connected: {}, looping: false, mode: "sim",
      busy_lanes: lanes,
      mount: {
        ra_hours: 5.6, dec_deg: -5.4, ra_str: "05h35m", dec_str: "-05°23'",
        alt: 45, az: 120, tracking: true, parked: false, slewing: false,
        can_find_home: true, can_set_tracking_rate: true, tracking_rate: "sidereal",
        ...mount,
      },
    },
    principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] },
  } as never);
}

seed();
const container = win.document.getElementById("root") as any;
const root = createRoot(container);
await act(async () => { root.render(createElement(MountView)); });

async function settle(ms = 0): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}
/** The 2 s status frame — the only thing that ever tells this view the truth. */
async function frame(lanes: string[], mount: Record<string, unknown> = {}): Promise<void> {
  await act(async () => { seed(lanes, mount); });
}
const toggle = (): any => container.querySelector('button[role="switch"][aria-label="Tracking"]');
/** The row the switch lives in — where a reason, if there is one, has to appear. */
const trackingRow = (): any => toggle()?.closest("div");
const click = async (node: any): Promise<void> => {
  await act(async () => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
};
const trackingPosts = (): number => posts.filter((p) => p.path.includes("/api/mount/tracking?")).length;

await settle(400); // the catalog debounce, so the view is fully settled

// ------------------------------------------------------------- the fixture
test("the view mounted with a tracking switch on a live mount", () => {
  // Anti-blank-page guard: most assertions below are "this control is disabled"
  // or "this text is present", both of which an empty page satisfies for free.
  assert(toggle() != null, "no tracking switch on the page — this is not the mount view");
  assert(toggle().disabled === false, "the switch starts out disabled; nothing below would prove anything");
  assert(toggle().getAttribute("aria-checked") === "true",
    "the switch disagrees with the seeded mount — the fixture is wrong");
});

// ------------------------------------------ IN FLIGHT (audit finding 88)
await click(toggle());
await settle();
test("the press reaches the mount and the switch shows the choice at once", () => {
  assert(trackingPosts() === 1, `the press never reached the route (${trackingPosts()} posts)`);
  // PRECONDITION for everything below: the round trip is genuinely still open,
  // so this is the in-flight window and not the state after it.
  assert(releaseTracking !== null, "the fixture never held the request — there is no in-flight window to measure");
  const live = (useStore.getState() as any).status.mount.tracking;
  assert(live === true, "the fixture already flipped the mount's own flag — nothing is being proven");
  assert(toggle().getAttribute("aria-checked") === "false",
    "the switch sat unmoved after a deliberate press");
});

test("…and says the command is in flight, rather than looking finished", () => {
  // A switch that has already moved to the new position and gone dim reads as
  // DONE. The mount has not answered yet; on a serial LX200 that is a real
  // round trip, and the thing the user needs to know is which of the two it is.
  const row = (trackingRow()?.textContent || "");
  assert(/stopping|sending|in flight|…/i.test(row),
    `the row reads ${JSON.stringify(row.trim())} while the command is still on the wire — ` +
    "nothing on screen distinguishes 'sent' from 'done'");
});

test("a repeat tap during the round trip cannot reach the rig", () => {
  assert(toggle().disabled === true,
    "the switch is pressable while its own command is still in flight — the second press is " +
    "dropped by act()'s guard with nothing on screen to explain why");
});

await click(toggle());
await settle();
test("…and pressing it anyway changes neither the rig nor the switch", () => {
  assert(trackingPosts() === 1, `a second command went to the mount (${trackingPosts()} posts)`);
  assert(toggle().getAttribute("aria-checked") === "false",
    "the switch moved on a press that was never sent — it now shows a value no hardware agreed to");
});

test("the mount answering releases the switch", () => {
  assert(releaseTracking !== null, "precondition: nothing to release");
  releaseTracking!();
});
await settle();
await frame([], { tracking: false });
test("…and the switch hands back to the mount's own telemetry", () => {
  assert(toggle().disabled === false, "the switch never came back after the mount answered");
  assert(toggle().getAttribute("aria-checked") === "false", "the switch lost the state the mount now reports");
});

// ------------------------------- COMMITTED MOTION (the other half of 88)
await frame(["goto"], { tracking: false });
test("a mount committed to a park/home/goto takes the tracking switch out of service", () => {
  // The `goto` lane carries all three. Park's whole purpose is to stop tracking
  // and it spawns with replace=True; a tracking command sent into one is a
  // command fighting the rig, from a control that looked completely idle.
  assert(toggle().disabled === true,
    "tracking is still offered while the mount is on the goto lane — the press goes out " +
    "underneath a park that is about to turn tracking off anyway");
});

test("…and says why, instead of going quietly dead", () => {
  const row = (trackingRow()?.textContent || "");
  assert(/moving|slewing|parking/i.test(row),
    `the switch went dead with no reason in its row: ${JSON.stringify(row.trim())}`);
});

await frame([], { tracking: false });
test("the switch comes back when the lane retires", () => {
  assert(toggle().disabled === false,
    "the tracking switch never recovered — a control that cannot come back is worse than one " +
    "that never went away");
});

// ------------------------------------------------------------------- report
await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`mountTrackingInFlight.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
