// mountViewDom.test.tsx — the Mount view's controls against a rig that answers.
//
//   Run directly:  npx tsx src/views/__tests__/mountViewDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY A DOM TEST AND NOT A TREE INSPECTION. Every defect here is about STATE
// OVER TIME: a button that must go dead the moment the rig picks the work up and
// come back when it puts it down; a switch that must show your choice before the
// next 2 s frame agrees. A single rendered tree cannot express any of that. So
// the view is mounted into a real document and driven the way the rig drives it
// — by replacing the status frame — with fetch stubbed at the boundary the api
// client uses, so the routes are exercised rather than mocked away.
//
// WHAT IT CANNOT DO: jsdom lays nothing out, so nothing here is evidence about
// widths, overlap or reachability. It answers "what did the control say, and
// could you press it", which is exactly what these findings were about.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Installed BEFORE the store, the api client or the view are imported: the api
// client reads window.location at module scope and the component tree reads
// matchMedia. run-tests.mjs gives this file its own process.
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

// ------------------------------------------------------------- the fake rig
// One catalog row, an "ok" preflight, and {"started": …} for every POST — which
// is the whole point: these routes answer the instant the task EXISTS, so a
// control that believes this promise learns nothing about the mount.
const ROW = {
  id: "M42", name: "Orion Nebula", type: "nebula", ra_hours: 5.59,
  dec_deg: -5.39, mag: 4.0, size_arcmin: 85, alt: 55, az: 175,
};
const posts: { path: string; body: unknown }[] = [];
g.fetch = async (url: string, init?: any) => {
  const path = String(url);
  const method = init?.method ?? "GET";
  if (method === "POST") {
    posts.push({ path, body: init?.body ? JSON.parse(init.body) : undefined });
  }
  const json =
    path.includes("/api/catalog") ? [ROW]
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

/** A status frame from a healthy, unparked, tracking mount. `busy_lanes` is the
 *  field under test: it is the rig saying which long operations are in flight
 *  RIGHT NOW, and replacing this object is exactly what the 2 s poll does. */
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

/** Let the stubbed fetches resolve and React flush what they caused. */
async function settle(ms = 0): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}
/** The 2 s status frame — the only thing that ever tells this view the truth. */
async function frame(lanes: string[], mount: Record<string, unknown> = {}): Promise<void> {
  await act(async () => { seed(lanes, mount); });
}

const buttons = (): any[] => [...container.querySelectorAll("button")];
const byText = (re: RegExp): any =>
  buttons().find((b: any) => re.test((b.textContent || "").trim()));
const click = async (node: any): Promise<void> => {
  await act(async () => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
};

// The catalog arrives through a 250ms debounce; without it the GOTO assertions
// below would be asking about a table that has no rows.
await settle(400);

// ------------------------------------------------------------- the fixture
test("the view mounted, with a rig connected and a catalog row", () => {
  // Anti-blank-page guard: several assertions below are of the form "this
  // control is disabled", which a page that rendered nothing satisfies for free.
  assert(byText(/^Solve & Sync$/) != null, "no Solve & Sync button — this is not the mount view");
  assert(byText(/^Park$/) != null, "no Park button");
  assert(byText(/^GOTO$/) != null, "no GOTO row — the catalog fetch never landed");
});

// ------------------------------------------------- SOLVE & SYNC (audit #14)
test("Solve & Sync is live when the rig is not solving", () => {
  // The PRECONDITION for the next test. Without it, "disabled while solving"
  // would pass just as well on a button that is disabled all the time.
  const b = byText(/^Solve & Sync$/);
  assert(b.disabled === false, "Solve & Sync starts out disabled — the fixture is wrong");
});

await frame(["solve"]);
test("a solve in flight takes Solve & Sync out of service and says so", () => {
  assert(byText(/^Solve & Sync$/) == null,
    "the button still offers 'Solve & Sync' while ASTAP is solving — it reports the " +
    "request, not the rig, and invites the press that produces \"'solve' is already running\"");
  const b = byText(/Solving/);
  assert(b != null, "nothing on screen says a solve is running");
  assert(b.disabled === true, "the Solving… button is still pressable");
  assert(b.getAttribute("aria-busy") === "true", "no aria-busy for a screen reader");
});

await frame([]);
test("…and comes back the moment the rig drops the lane", () => {
  const b = byText(/^Solve & Sync$/);
  assert(b != null && b.disabled === false,
    "the solve button never recovered — a control that cannot come back is worse than one that never went away");
});

// --------------------------------------------------------- PARK (audit #36)
test("Park is live before it is pressed", () => {
  const b = byText(/^Park$/);
  assert(b != null && b.disabled === false, "Park starts out unavailable — the fixture is wrong");
});

await click(byText(/^Park$/));
await settle();
test("a park in flight is hard-disabled, not merely relabelled", () => {
  assert(posts.some((p) => p.path.includes("/api/mount/park")), "the press never reached the park route");
  // The other half of the same lie: this mount reports slewing=false for the
  // whole park, so the State stat sat on IDLE while it travelled.
  assert(/PARKING/.test(container.textContent || ""),
    "the State stat still reads IDLE while the mount is parking");
  const b = byText(/Parking/);
  assert(b != null,
    "Park still reads 'Park' during a 30–60 s park: the route _spawns and answers in ~40ms, " +
    "so the POST promise says nothing about the mount");
  // HARD-disabled matters here beyond honesty: the route spawns with
  // replace=True, so a second press CANCELS the park in flight and starts
  // another one.
  assert(b.disabled === true, "the parking button is still pressable — a second press restarts the park");
});

await frame(["goto"]);   // the rig confirms: park runs on the `goto` lane
test("the rig's own lane keeps it disabled once the local latch hands over", () => {
  const b = byText(/Parking/);
  assert(b != null && b.disabled === true, "the button went live again while the mount was still parking");
});

await frame([]);
test("Park returns when the lane retires", () => {
  const b = byText(/^Park$/);
  assert(b != null && b.disabled === false, "Park never came back after the park finished");
});

// --------------------------------------------------------- GOTO (audit #33)
/** The one catalog row, as an element — the marker under test lives on the ROW,
 *  not on the button inside it. */
const catalogRow = (): any =>
  [...container.querySelectorAll("tr")].find((r: any) => /M42/.test(r.textContent || ""));

test("GOTO is live on an idle mount", () => {
  const b = byText(/^GOTO$/);
  assert(b != null && b.disabled === false, "GOTO starts out unavailable — the fixture is wrong");
});

test("PRECONDITION: no row carries the in-flight marker before a slew", () => {
  const row = catalogRow();
  assert(row != null, "no M42 row — the catalog fetch never landed");
  assert(!/border-l-2/.test(row.className || ""),
    "the row is already marked; 'it gets marked' below would prove nothing");
  assert(!/▸/.test(row.textContent || ""), "the row already carries the in-flight glyph");
});

await click(byText(/^GOTO$/));
await settle();
test("GOTO reports the slew it started, and says which target", () => {
  assert(posts.some((p) => p.path.includes("/api/mount/goto")), "the press never reached the goto route");
  const b = byText(/^GOTO$/);
  assert(b.disabled === true,
    "GOTO is still pressable during its own slew — the server spawns `goto` without replace, " +
    "so the second press buys a raw \"'goto' is already running\"");
  assert(b.getAttribute("aria-busy") === "true", "the launching row carries no aria-busy");
  assert(/Slewing to M42/.test(container.textContent || ""),
    "nothing on the page names the target being slewed to");
  const toasts = (useStore.getState() as any).toasts as { title: string }[];
  assert(toasts.some((t) => /Slewing to M42/.test(t.title)),
    "no confirmation that the slew was accepted — success and 'nothing happened' look identical");
});

test("the row being slewed to is marked by SHAPE, not by accent hue alone", () => {
  // Every GOTO in the table is `disabled` while a slew runs, which paints it at
  // 0.35 opacity (index.css:407). The target's own button used to be marked by
  // accent border + accent text and nothing else — two colour channels, both
  // composited toward the near-black panel by that same 0.35, which crushes the
  // border's 3.17:1 step to 1.39:1. Under :root.night the ID cell, the border
  // and the text are all red anyway. So the marker cannot live on the button and
  // cannot be a colour: index.css:154-157 — "any status that must survive night
  // mode needs a non-hue channel".
  const row = catalogRow();
  assert(row != null, "the M42 row vanished");
  assert(/border-l-2/.test(row.className || ""),
    "the slewing row has no bar in the margin — the only marker is on a button painted at 0.35 opacity");
  assert(/▸/.test(row.textContent || ""),
    "the slewing row has no glyph beside its id, so the state has no shape channel at all");
  assert(/bg-accent\/10/.test(row.className || ""),
    "the slewing row has no fill — the one that used to be on the button never rendered, "
    + "because .btn's unlayered `background` always beats an @layer utility");
});

// The rig confirms the lane on its next frame, which is what the local latch
// exists to bridge; from here on the mount's own answer is the only thing
// keeping the row out of service.
await frame(["goto"]);
test("the rig's own lane keeps GOTO out of service once the latch hands over", () => {
  const b = byText(/^GOTO$/);
  assert(b != null && b.disabled === true, "GOTO went live again while the mount was still slewing");
});

await frame([]);
test("GOTO comes back when the mount stops", () => {
  const b = byText(/^GOTO$/);
  assert(b != null && b.disabled === false, "GOTO never recovered after the slew");
  assert(!/Slewing to M42/.test(container.textContent || ""),
    "the view still claims to be slewing after the lane retired");
});

// A slew somebody ELSE started — the sequencer, another tablet — must block a
// GOTO here too, because the server refuses the second one outright.
await frame(["goto"]);
test("a slew started elsewhere blocks GOTO and states why", () => {
  const b = byText(/^GOTO$/);
  assert(b != null && b.disabled === true, "GOTO is offered while the rig is already slewing for someone else");
  assert(/already moving/.test(container.textContent || ""),
    "the row went dead with no reason on screen and no way out stated");
});
await frame([]);

// ----------------------------------------------------- TRACKING (audit #31)
const toggle = (): any => container.querySelector('button[role="switch"][aria-label="Tracking"]');
test("the tracking switch reflects the mount", () => {
  assert(toggle() != null, "no tracking switch on the page");
  assert(toggle().getAttribute("aria-checked") === "true",
    "the switch does not agree with the seeded mount — the fixture is wrong");
});

await click(toggle());
test("the tracking switch shows your choice now, not two seconds from now", () => {
  // PRECONDITION: no status frame has arrived, so the mount still reports
  // tracking ON. Anything the switch shows other than ON came from the press.
  const live = (useStore.getState() as any).status.mount.tracking;
  assert(live === true, "the fixture already changed the mount's own tracking flag — nothing is being proven");
  assert(posts.some((p) => p.path.includes("/api/mount/tracking?on=false")), "the press never reached the route");
  assert(toggle().getAttribute("aria-checked") === "false",
    "the switch sat visibly unmoved after a deliberate press — which is how a control " +
    "teaches you to press it twice");
});

await frame([], { tracking: false });
test("…and hands back to the mount's own telemetry when it agrees", () => {
  assert(toggle().getAttribute("aria-checked") === "false", "the switch lost the state the mount now reports");
});

// -------------------------------- A MOTION THIS TAB DID NOT START (finding #2)
// `motion` — the latch that turns the shared `goto` lane into "Parking…" — is
// per-tab state and dies with a reload. So this is the state of a tablet that
// was not the one that pressed Park: the rig's own lane is the only evidence
// there is, and a native AM5 leaves BOTH `slewing` and `parked` false for the
// whole 15-60 s of a park.
await frame([], { tracking: false, parked: false, slewing: false });
test("PRECONDITION: with the lane idle this tab reads IDLE and offers Home", () => {
  assert(/IDLE/.test(container.textContent || ""),
    "the State stat does not read IDLE on a stopped mount — the fixture is wrong");
  const h = byText(/^Home$/);
  assert(h != null && h.disabled === false,
    "Home is already unavailable, so 'it goes out of service' below would prove nothing");
});

await frame(["goto"], { tracking: false, parked: false, slewing: false });
test("a motion this tab did not start is not reported as an idle mount", () => {
  const t = container.textContent || "";
  assert(!/IDLE/.test(t),
    "the State stat still reads IDLE while the rig's own goto lane is busy: after a "
    + "reload mid-park, or on a second tablet, this is the whole of what the page knows "
    + "and it was saying the mount was stopped");
  assert(/MOVING/.test(t),
    "nothing in the Pointing panel says the mount is moving — the lane cannot say WHICH "
    + "of park/home/goto it is, but 'moving' is certainly true");
});

test("…and Home, which is not an abort, goes out of service and says why", () => {
  const h = byText(/^Home$/);
  assert(h != null && h.disabled === true,
    "Home is offered over a motion already on the lane. /api/mount/home spawns with "
    + "replace=True and find_home() is unpark → park → unpark, so cancelling it mid-sequence "
    + "can leave the mount PARKED — the button looks like it worked and the next slew is refused");
  assert(/already moving/.test(h.getAttribute("title") || ""),
    "the blocked Home says nothing about why or when it comes back");
});

test("…but Park stays pressable, because Park IS the abort", () => {
  const p = byText(/^Park$/);
  assert(p != null && p.disabled === false,
    "Park went dead over a motion it did not start — park is the server's "
    + "motion-committing abort (replace=True cancels the goto and stows the mount), so "
    + "taking it away removes the only way to stop a bad slew");
  assert(/already moving/.test(p.getAttribute("aria-label") || ""),
    "Park offers itself as a fresh, consequence-free action over a motion that a press "
    + "would cancel — and if that motion is itself a park, restart");
});
await frame([]);

// ------------------------------------------------------------------- report
await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`mountViewDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
