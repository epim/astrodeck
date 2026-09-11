// rigRotatorDom.test.tsx - the ROTATOR sheet, MOUNTED (plan hub-rig.md D.2 and
// wave-r7.md 3.E / T-R7-8; deviation E28).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/rigRotatorDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by
//   `npx tsc --noEmit -p tsconfig.json`.
//
// WHAT IS WORTH ASSERTING HERE, now that the sheet no longer mounts
// `components/equipment/RotatorCard.tsx` but a rebuilt panel of its own:
//
//   NO NATIVE `disabled`, ANYWHERE. The legacy card carried eleven of them
//   (`RotatorCard.tsx` :165 :167 :169 :173 :185 :199 :206 :225 :250 :259 :272).
//   A `disabled` element leaves the accessibility tree and takes the REASON
//   with it, so a viewer got a grey rectangle and no way to ask why. The
//   attribute count is asserted as a hard zero in the state most likely to
//   produce one - a viewer, with everything locked.
//
//   THE SPLIT GATE SURVIVES. Motion needs `control.capture`; the range of
//   motion needs `config.backend`. An operator holds the first and not the
//   second, and that is the ordinary case, not an edge one: a rotator that went
//   read-only because the user cannot edit its range of motion would be
//   unusable for everyone but an admin. Two locks, two DIFFERENT sentences,
//   asserted as different.
//
//   HALT IS NOT GATED ON BUSY - either kind of busy. Every other motion control
//   locks while a rotator command is in flight AND while the rig reports the
//   `rotator` lane busy. HALT is the escape hatch from exactly those states, so
//   both are driven for real (a held request, then a seeded lane) and both are
//   read.
//
//   A BLANK NUMBER IS NEVER A ZERO. `Number("")` is 0, and 0 is a real
//   mechanical angle. Committing an empty START must write nothing at all.
//
//   THE ANGLE THAT LEAVES IS THE RANGE-MAPPED ONE. Typing a PA the mechanical
//   range cannot reach must send the angle the rig will actually image at, and
//   say so on screen - not send the unreachable number and let the rotator
//   silently land somewhere else.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The rebuilt panel is an AREA ROOT and imports its own `rotator.css` (wave
// R7's rule: `next.css` belongs to one task, every other area ships its own
// stylesheet). Node has no idea what a `.css` file is, so a load hook answers
// with an empty module - the same stub `shellDom.test.tsx` uses for `NextApp`.
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/rotator", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "PointerEvent",
  "Image", "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Asked { method: string; url: string; body: any }
const asked: Asked[] = [];
/** A gate that suspends ONE in-flight request, so a test can observe the busy
 *  face the panel wears between the press and the rig's answer. */
let release: (() => void) | null = null;
let hold = false;

const CFG0 = { range_type: "half", range_start_deg: 20, tolerance_deg: 1.5 };
/** What the server currently stores, so `setRotatorConfig` -> `loadConfig()` is
 *  a real round trip. A stub that answered `{}` to `GET /api/config` would wipe
 *  `config.rotator` after every write and reset the sheet to its defaults,
 *  which is not what the rig does and would hide a rollback bug. */
let cfgOnServer: any = { active_profile_id: null, rotator: { ...CFG0 } };
/** Set to a status code to make the next config write fail. */
let cfgWriteFails = 0;

const ok = (json: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => json });

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const u = String(url);
  let body: any = null;
  try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body; }
  asked.push({ method, url: u, body });
  if (hold && u.includes("/api/rotator/move")) {
    await new Promise<void>((r) => { release = r; });
  }
  if (method === "POST" && u.includes("/api/config/rotator")) {
    if (cfgWriteFails) {
      const status = cfgWriteFails;
      cfgWriteFails = 0;
      return { ok: false, status, statusText: "Unprocessable Entity",
        json: async () => ({ detail: "the rig refused the range" }) };
    }
    cfgOnServer = { ...cfgOnServer, rotator: body };
    return ok(cfgOnServer);
  }
  if (method === "GET" && /\/api\/config$/.test(u)) return ok(cfgOnServer);
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { RotatorSheet } = await import("../sheets/rotator");

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
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

// ------------------------------------------------------------------ fixtures
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};
const ADMIN = {
  role: "admin", email: "admin@rig",
  caps: ["view.status", "view.preview", "view.media", "view.site_precise",
    "view.site_derived", "view.weather", "control.capture", "control.mount",
    "control.guide", "control.power", "config.safety", "config.solar_override",
    "config.backend", "config.site_optics", "config.alerts", "admin.users",
    "system.update"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function rotator(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    name: "ZWO CAA", sky_deg: 42.5, mech_deg: 100.5, moving: false,
    synced: true, can_reverse: true, reverse: false,
    ...over,
  };
}

function seed(over: Record<string, unknown> = {}, statusOver: Record<string, unknown> = {}): void {
  cfgOnServer = { active_profile_id: null, rotator: { ...CFG0 } };
  act(() => {
    useStore.setState({
      principal: OPERATOR,
      wsPhase: "up",
      equipConnected: true,
      toasts: [],
      config: {
        active_profile_id: null,
        rotator: { ...CFG0 },
      },
      status: {
        connected: { rotator: { name: "ZWO CAA", connected: true } },
        backend_links: [{ role: "rotator", connected: true, error: null }],
        busy_lanes: [],
        rotator: rotator(),
        ...statusOver,
      },
      ...over,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(RotatorSheet as any, { params: {}, depth: 0 })); });
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
/** Type into a React-controlled input: the native value setter, then the
 *  `input` event React's onChange is delegated from. */
function type(node: any, value: string): void {
  act(() => {
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!
      .call(node, value);
    node.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
}
/** Commit a NumberField / TextInput draft. Enter, not blur: React 18 delegates
 *  `onBlur` from `focusout`, which jsdom will not raise for an element that was
 *  never actually focused. */
function enter(node: any): void {
  act(() => {
    node.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  });
}
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => [...container.querySelectorAll(sel)] as any[];
const text = () => (container.textContent || "") as string;
const id = (marker: string) => q(`[data-testid="${marker}"]`);
const locked = (node: any): boolean => node?.getAttribute("aria-disabled") === "true";
const commands = () => asked.filter((a) => a.method !== "GET");
const toasts = () => (useStore.getState().toasts ?? []) as any[];

// ============================================================ precondition
seed();
mount();

test("the sheet mounted the rebuilt panel, not an empty wrapper", () => {
  // The anti-empty-wrapper guard: every one of these is a control the gap
  // analysis asked for, and the sheet renders none of them itself.
  assert(id("rig-rotator") != null, "no sheet marker - the sheet did not render at all");
  eq(qa('[data-testid="rotator-tiles"] .nx-readout').length, 4,
    "the readout grid is not showing the four tiles");
  for (const t of ["tile-sky-pa", "tile-mechanical", "tile-range", "tile-tolerance"]) {
    assert(id(t) != null, `no ${t} tile`);
  }
  assert(id("rotator-arc") != null, "no range-of-motion arc");
  assert(id("rotator-dial") != null, "no dial - the selected tile has nothing editing it");
  assert(q('[aria-label="Target position angle, degrees"]') != null, "no MOVE TO input");
  for (const b of ["rotator-move", "rotator-nudge-minus", "rotator-nudge-plus",
    "rotator-halt", "rotator-solve", "rotator-sync", "rotator-reverse"]) {
    assert(id(b) != null, `no ${b} control`);
  }
  assert(q('[role="radiogroup"][aria-label="Mechanical range"]') != null,
    "no range-of-motion radiogroup");
  assert(q('[aria-label="Range start, mechanical degrees"]') != null, "no range START input");
  assert(q('[aria-label="Rotate tolerance, degrees"]') != null, "no TOLERANCE input");
  assert(id("rotator-start-current") != null, "no SET TO CURRENT POSITION");
});

test("the live line and the tiles read the derived mechanical offset, not a wire field", () => {
  // offset = mechanical - sky (lib/rotation.ts:108). It is NOT on the wire; a
  // sheet that printed a wire field here would print nothing at all.
  const live = q(".nx-sheet-live");
  assert(/sky PA 42\.5°/.test(live.textContent || ""),
    `the live line does not carry the sky PA: ${JSON.stringify(live.textContent)}`);
  assert(/mech 100\.5°/.test(live.textContent || ""), "the live line does not carry the mechanical angle");
  assert(/synced/.test(live.textContent || ""), "the live line does not say whether the two are related");
  assert(/offset 58\.0°/.test(id("tile-mechanical").textContent || ""),
    `the MECHANICAL tile's offset is wrong: ${JSON.stringify(id("tile-mechanical").textContent)}`);
  assert(/HALF/.test(id("tile-range").textContent || ""),
    "the RANGE tile is not showing the configured range type");
  assert(/start 20°/.test(id("tile-range").textContent || ""),
    "the RANGE tile does not say where the sweep starts");
  assert(/1\.5°/.test(id("tile-tolerance").textContent || ""),
    "the TOLERANCE tile is not showing the configured tolerance");
  // The legacy card carried the device name in its Panel title; nothing else in
  // the new chrome says WHICH rotator this is.
  assert(/ZWO CAA/.test(q(".nx-sheet-head").textContent || ""),
    "the sheet does not name the device it is driving");
});

test("the arc says where in the sweep the rotator is, in words as well as pixels", () => {
  const arc = id("rotator-arc");
  const label = arc.getAttribute("aria-label") || "";
  assert(/100\.5 degrees mechanical/.test(label),
    `the arc's accessible name does not carry the position: ${JSON.stringify(label)}`);
  assert(/180 degree range/.test(label),
    "the arc's accessible name does not carry the allowed sweep, which is the only thing it draws");
  assert(id("rotator-arc-start") != null,
    "a limited range draws no start marker, so the arc says nothing about where it begins");
});

// ==================================================================== motion
await testAsync("pressing +1 degree commands an absolute move", async () => {
  seed();
  mount();
  asked.length = 0;
  click(id("rotator-nudge-plus"));
  await settle();
  const moves = asked.filter((a) => a.url.includes("/api/rotator/move"));
  eq(moves.length, 1, "the +1 degree nudge did not POST /api/rotator/move");
  eq(moves[0].method, "POST", "the nudge used the wrong method");
  // 42.5 + 1, wrapped by mod360 - a nudge is an ABSOLUTE position command, which
  // is why it is computed from the live sky angle rather than sent as a delta.
  eq(moves[0].body.position_deg, 43.5, "the nudge did not send sky + 1 degree");
});

await testAsync("GO sends the angle the rig will actually image at, and says when it moved it", async () => {
  seed();
  mount();
  // sky 42.5 / mech 100.5 -> offset 58; a HALF range starting at 20 mechanical
  // cannot reach PA 200, and `adjustedPa` maps it to PA 20.
  type(q('[aria-label="Target position angle, degrees"]'), "200");
  await settle();
  const warn = id("rotator-outofrange");
  assert(warn != null,
    "no out-of-range warning - the rotator would silently image 180 degrees from the requested angle");
  assert(/PA 200° is outside the range of motion - it will image as 20°\./
    .test(warn.textContent || ""),
  `the warning does not say the angle that will be used: ${JSON.stringify(warn.textContent)}`);
  assert(!/—/.test(warn.textContent || ""),
    "the warning still carries the legacy em-dash");
  asked.length = 0;
  click(id("rotator-move"));
  await settle();
  const moves = asked.filter((a) => a.url.includes("/api/rotator/move"));
  eq(moves.length, 1, "GO did not POST /api/rotator/move");
  eq(moves[0].body.position_deg, 20,
    "GO sent the unreachable angle instead of the range-mapped one");
});

await testAsync("GO with nothing typed refuses with a reason instead of a dead button", async () => {
  seed();
  mount();
  await settle();
  const go = id("rotator-move");
  assert(locked(go), "GO is live with an empty MOVE TO field - it has nothing to send");
  assert(/type a position angle/.test(go.getAttribute("title") || ""),
    `GO gives no reason for being locked: ${JSON.stringify(go.getAttribute("title"))}`);
  asked.length = 0;
  act(() => { useStore.setState({ toasts: [] } as never); });
  click(go);
  await settle();
  eq(commands().length, 0, "an empty GO reached the rig");
  assert(toasts().length > 0, "a locked GO was silent - the reason never reached the user");
});

await testAsync("SYNC TO SKY establishes the offset and moves nothing", async () => {
  seed();
  mount();
  asked.length = 0;
  click(id("rotator-sync"));
  await settle();
  const sync = asked.filter((a) => a.url.includes("/api/rotator/sync-to-sky"));
  eq(sync.length, 1, "SYNC TO SKY did not POST /api/rotator/sync-to-sky");
  eq(sync[0].method, "POST", "SYNC TO SKY used the wrong method");
  eq(asked.filter((a) => a.url.includes("/api/rotator/move")).length, 0,
    "SYNC TO SKY turned the camera - it is the half of the solve that must not");
  eq(asked.filter((a) => a.url.includes("/api/rotator/rotate-to-pa")).length, 0,
    "SYNC TO SKY fell through to the rotating route");
});

await testAsync("ROTATE TO PA closes the loop on the typed angle", async () => {
  seed();
  mount();
  type(q('[aria-label="Target position angle, degrees"]'), "77");
  asked.length = 0;
  click(id("rotator-solve"));
  await settle();
  const solves = asked.filter((a) => a.url.includes("/api/rotator/rotate-to-pa"));
  eq(solves.length, 1, "ROTATE TO PA did not POST /api/rotator/rotate-to-pa");
  eq(solves[0].body.target_pa_deg, 77, "ROTATE TO PA did not send the typed position angle");
});

// ================================================================ halt is live
await testAsync("HALT stays live while a rotator command is in flight", async () => {
  // The never-blocked rule (hub-rig.md 0.5). HALT exists to end the state every
  // other motion control is dimmed by, so gating it on that state is the bug.
  seed();
  mount();
  hold = true;
  click(id("rotator-nudge-plus"));
  await settle();
  assert(locked(id("rotator-nudge-minus")),
    "precondition failed: the panel is not in its busy state, so HALT being live proves nothing");
  assert(!locked(id("rotator-halt")),
    "HALT went dim while a move was in flight - it is the way out of that state");
  act(() => { release?.(); });
  await settle();
  hold = false;
  release = null;
});

await testAsync("HALT stays live while the rig reports the rotator lane busy", async () => {
  seed({}, { busy_lanes: ["rotator", "rotate_to_pa"] });
  mount();
  await settle();
  const nudge = id("rotator-nudge-plus");
  assert(locked(nudge),
    "precondition failed: a busy rotator lane did not lock the nudge, so HALT proves nothing");
  assert(/already turning/.test(nudge.getAttribute("title") || ""),
    `the busy lane gave no sentence: ${JSON.stringify(nudge.getAttribute("title"))}`);
  assert(!locked(id("rotator-halt")), "HALT went dim because the rotator was turning");
  asked.length = 0;
  click(id("rotator-halt"));
  await settle();
  eq(asked.filter((a) => a.url.includes("/api/rotator/halt")).length, 1,
    "HALT did not reach POST /api/rotator/halt while the lane was busy");
});

// ================================================================ split gate
await testAsync("an operator keeps the rotator and loses only its range of motion", async () => {
  seed();
  mount();
  await settle();
  assert(!locked(id("rotator-nudge-plus")),
    "an operator cannot move the rotator - motion is control.capture");
  assert(!locked(id("rotator-halt")), "an operator cannot halt the rotator");
  const radios = qa('[data-testid="rotator-range"] [role="radio"]');
  eq(radios.length, 3, "the range-of-motion radiogroup is not offering three ranges");
  assert(locked(id("rotator-range")),
    "an operator can edit the range of motion - that write is config.backend");
  assert(id("rotator-start").readOnly === true, "the range START is writable for an operator");
  assert(id("rotator-tolerance").readOnly === true, "the TOLERANCE is writable for an operator");

  const cfgNote = id("rotator-lock-config");
  assert(cfgNote != null, "the range-of-motion lock has no note");
  assert(/changing the range of motion needs admin access/.test(cfgNote.textContent || ""),
    `the config note names the wrong role: ${JSON.stringify(cfgNote.textContent)}`);
  assert(id("rotator-lock-motion") == null,
    "the sheet claims motion is read-only for an operator, which it is not");

  asked.length = 0;
  click(radios[2]);
  await settle();
  eq(commands().length, 0, "an operator's range-of-motion press reached the rig");
});

await testAsync("an admin holds both halves of the split gate", async () => {
  seed({ principal: ADMIN });
  mount();
  await settle();
  assert(!locked(id("rotator-range")), "an admin cannot edit the range of motion");
  assert(id("rotator-lock-config") == null,
    "an admin is shown a lock note for a control they hold");
  assert(id("rotator-lock-motion") == null,
    "an admin is shown a motion lock note for a control they hold");
});

// ==================================================================== viewer
await testAsync("a viewer sees the whole sheet, is told why twice, and reaches the rig with nothing", async () => {
  seed({ principal: VIEWER });
  mount();
  await settle();
  asked.length = 0;
  assert(id("rotator-tiles") != null, "the readouts are HIDDEN from a viewer");
  assert(q('[aria-label="Target position angle, degrees"]') != null,
    "the MOVE TO input is HIDDEN from a viewer - nothing may be hidden");

  const motionNote = id("rotator-lock-motion");
  const cfgNote = id("rotator-lock-config");
  assert(motionNote != null, "a viewer is given no reason for the dead motion controls");
  assert(cfgNote != null, "a viewer is given no reason for the dead range-of-motion controls");
  const m = (motionNote.textContent || "").trim();
  const c = (cfgNote.textContent || "").trim();
  assert(/moving the rotator needs operator or admin access/.test(m),
    `the motion note is wrong: ${JSON.stringify(m)}`);
  assert(/changing the range of motion needs admin access/.test(c),
    `the config note is wrong: ${JSON.stringify(c)}`);
  assert(m !== c, "the two locks were merged into one sentence - the server enforces them separately");

  assert(locked(id("rotator-move")), "GO is live for a viewer");
  assert(locked(id("rotator-halt")),
    "HALT is live for a viewer - it is capability-gated even though it is urgent");
  assert(locked(id("rotator-reverse")), "REVERSE is live for a viewer");
  for (const b of ["rotator-nudge-plus", "rotator-nudge-minus", "rotator-solve",
    "rotator-sync", "rotator-halt", "rotator-start-current"]) {
    click(id(b));
  }
  click(qa('[data-testid="rotator-range"] [role="radio"]')[0]);
  await settle();
  eq(commands().length, 0,
    `a viewer's presses reached the rig: ${JSON.stringify(commands().map((a) => `${a.method} ${a.url}`))}`);
});

test("nothing on this sheet uses the native disabled attribute", () => {
  // The viewer is the state most likely to produce one, and it is already
  // mounted. `disabled` strips the element - and its reason - from the
  // accessibility tree; the whole sheet is honest-disabled instead.
  eq(qa("[disabled]").length, 0,
    `a control is natively disabled: ${qa("[disabled]").map((n: any) => n.outerHTML.slice(0, 90)).join(" | ")}`);
  assert(qa('[aria-disabled="true"]').length > 0,
    "nothing is locked for a viewer at all, so the zero above proves nothing");
});

// ======================================================= range-of-motion writes
await testAsync("picking a range writes the WHOLE config block and re-reads it", async () => {
  seed({ principal: ADMIN });
  mount();
  await settle();
  asked.length = 0;
  click(qa('[data-testid="rotator-range"] [role="radio"]')[2]);   // QUARTER
  await settle();
  const writes = asked.filter((a) => a.url.includes("/api/config/rotator"));
  eq(writes.length, 1, "the range pick did not POST /api/config/rotator");
  eq(writes[0].body.range_type, "quarter", "the range pick sent the wrong range");
  // Wholesale replace (hub-rig.md 0.4): the other two fields ride along, or the
  // server's defaults would overwrite them.
  eq(writes[0].body.range_start_deg, 20, "the range pick dropped the start angle");
  eq(writes[0].body.tolerance_deg, 1.5, "the range pick dropped the tolerance");
  assert(asked.some((a) => a.method === "GET" && /\/api\/config$/.test(a.url)),
    "the write was never followed by a re-read, so the sheet is showing its own guess");
  assert(/QUARTER/.test(id("tile-range").textContent || ""),
    "the RANGE tile did not follow the write");
});

await testAsync("SET TO CURRENT POSITION sends the mechanical angle, wrapped", async () => {
  seed({ principal: ADMIN });
  mount();
  await settle();
  asked.length = 0;
  click(id("rotator-start-current"));
  await settle();
  const writes = asked.filter((a) => a.url.includes("/api/config/rotator"));
  eq(writes.length, 1, "SET TO CURRENT POSITION did not write the config");
  eq(writes[0].body.range_start_deg, 100.5,
    "SET TO CURRENT POSITION did not send the live mechanical angle");
});

await testAsync("a blank START is refused, never read as a real 0 degrees", async () => {
  seed({ principal: ADMIN });
  mount();
  await settle();
  const start = id("rotator-start");
  asked.length = 0;
  type(start, "");
  enter(start);
  await settle();
  eq(asked.filter((a) => a.url.includes("/api/config/rotator")).length, 0,
    "a blank START was committed - Number(\"\") is 0, and 0 is a real mechanical angle");
  eq(start.value, "20", "the rejected field did not restore the last committed value");
});

await testAsync("START is locked, with its own reason, while the sweep is the whole circle", async () => {
  seed({ principal: ADMIN });
  mount();
  await settle();
  click(qa('[data-testid="rotator-range"] [role="radio"]')[0]);   // FULL
  await settle();
  const start = id("rotator-start");
  assert(locked(start), "START is editable on a full 360 degree range, which has no start angle");
  assert(/full 360 degree sweep has no start angle/.test(start.getAttribute("title") || ""),
    `START's reason reads as a permission problem: ${JSON.stringify(start.getAttribute("title"))}`);
  assert(id("rotator-lock-config") == null,
    "a range-shaped reason was reported as a missing capability");
});

await testAsync("a refused config write rolls the sheet back to what the rig has", async () => {
  seed({ principal: ADMIN });
  mount();
  await settle();
  cfgWriteFails = 422;
  click(qa('[data-testid="rotator-range"] [role="radio"]')[2]);   // QUARTER
  await settle();
  assert(/HALF/.test(id("tile-range").textContent || ""),
    "the sheet kept showing a range the rig refused to store");
  const err = id("rotator-error");
  assert(err != null, "the refusal was swallowed - nothing on screen says the write failed");
  assert(/refused the range/.test(err.textContent || ""),
    `the error line does not carry the rig's own message: ${JSON.stringify(err.textContent)}`);
});

// ================================================================ the one dial
await testAsync("the selected tile is what the one dial edits", async () => {
  seed({ principal: ADMIN });
  mount();
  await settle();
  assert(/MOVE TO/.test(id("rotator-dial").textContent || ""),
    "the dial does not start on the SKY PA tile's setting");
  click(id("tile-tolerance"));
  await settle();
  const tolDial = id("rotator-dial");
  assert(tolDial != null,
    "selecting the TOLERANCE tile left the sheet with no dial at all");
  assert(/TOLERANCE/.test(tolDial.textContent || ""),
    `selecting the TOLERANCE tile did not point the one dial at it: ${JSON.stringify((tolDial.textContent || "").slice(0, 40))}`);
  eq(qa('[data-testid="rotator-dial"]').length, 1,
    "two dials are mounted at once - the sheet has exactly one");
});

// ============================================================== framing + empty
test("the framing hand-off is stated here, and warns when the offset is unknown", () => {
  seed({ principal: ADMIN }, { rotator: rotator({ synced: false }) });
  mount();
  assert(id("rotator-framing-row") != null,
    "no framing hand-off row - GAP-2 asks for the PA to reach the rotator from framing");
  assert(id("rotator-unsynced-warning") != null,
    "an unsynced rotator gets no warning - the angle framing sends would be off by the mechanical offset");
  assert(/not synced/.test(id("tile-sky-pa").textContent || ""),
    "the SKY PA tile does not say the angle is unsynced");
});

test("no rotator is an EmptyCard with a way forward, never a blank route", () => {
  seed({ principal: ADMIN }, { rotator: undefined, connected: {}, backend_links: [] });
  mount();
  assert(id("rotator-empty") != null,
    "a route with no rotator renders nothing at all - a dead end");
  assert(/ADD A DEVICE/.test(text()), "the empty state offers no way to connect one");
  assert(id("rotator-tiles") == null, "tiles are drawn from a device that is not there");
  assert(id("rotator-motion") == null, "motion controls are drawn for a device that is not there");
});

test("a rotator that cannot reverse is not offered a reverse switch", () => {
  seed({ principal: ADMIN }, { rotator: rotator({ can_reverse: false }) });
  mount();
  assert(id("rotator-reverse") == null,
    "a REVERSE switch is offered for a device that cannot reverse - a control with a promise nothing keeps");
  assert(id("rotator-halt") != null, "precondition failed: the panel did not render at all");
});

act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`rigRotator.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
