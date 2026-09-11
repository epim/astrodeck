// dewBandDom.test.tsx - the dew LOOP line under the WEATHER conditions band
// (D-RIG-3, task T-U7b-6).
//
//   Run directly:  npx tsx src/next/hubs/weather/__tests__/dewBandDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by
//   `npx tsc --noEmit -p tsconfig.json`.
//
// `ConditionsBand` is mounted DIRECTLY rather than through `ConditionsScreen`
// or `WeatherHub`, for the same reason `conditionsCapsDom.test.tsx` mounts the
// screen on its own: the hub gates its whole body on `view.weather`
// (`WeatherHub.tsx:56`), and half of what this file exists to prove is what a
// principal WITHOUT that capability sees - a shape the hub would never let
// reach the band at all. `weatherDom.test.tsx` already proves the screen mounts
// this band; what is under test here is what the band does with `status.dew`.
//
// FOUR THINGS WORTH A TEST:
//
//   1. THE TILE IS STILL THERE. Every assertion below is about a line UNDER the
//      DEW tile, so the tile being on screen is the precondition: without it,
//      "the line reads X" could be true of a band that rendered nothing.
//   2. THE THREE SHAPES. Following, off and paused are three different
//      sentences, and the server's own `reason` rides through each of them
//      verbatim - it is the one string that says what the loop is DOING, and
//      it is digit-free by construction so it survives redaction.
//   3. A VIEWER'S BAND PRINTS NO MARGIN NUMBER. `api/redact.py:151-173` deletes
//      the three readings and nulls `power_pct` for a principal without
//      `view.weather` while leaving the loop's own state alone. The line must
//      still say the loop is following - and must not print a percentage or a
//      temperature it was never given. A `?? 0` anywhere in `dewModel` prints
//      "0%" and "0.0°C", which are a dead heater and glass at the dew point.
//   4. NO LOOP, NO LINE. An engine that publishes no `dew` node gets no
//      sentence about a mechanism it does not have.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/weather/conditions", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame",
  // `api.ts` and `lib/base.ts` read the BARE `location` at module scope.
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// A recorded fetch, so "the band asked the rig for something" is an assertion
// and not a hope. This band reads the store; it must call nothing.
const asked: string[] = [];
g.fetch = async (url: any) => {
  asked.push(String(url));
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { accessPhrase } = await import("../../../../lib/caps");
const { ConditionsBand } = await import("../conditions/ConditionsBand");
const { dewBandLine, dewView, dewClockAt, dewHiddenNote } =
  await import("../../rig/lib/dewModel");
type DewStatus = import("../../../../types").DewStatus;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

// ------------------------------------------------------------------ fixtures
const NOW = Math.floor(Date.now() / 1000);

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

const NOW_READING = {
  temp_c: 11.2, dewpoint_c: 7.0, humidity_pct: 76,
  wind_kmh: 12, wind_dir_deg: 225, gust_kmh: 19, cloud_base_m: 900,
};

const PORTS = [
  { id: 2, name: "Dew A", follow_dew: true, value: 158 },
  { id: 3, name: "Dew B", follow_dew: true, value: 158 },
];

function dewNode(over: Record<string, unknown> = {}): DewStatus {
  return {
    enabled: true,
    following: true,
    override_until_ts: null,
    margin_c: 3.4,
    temp_c: 11.2,
    dewpoint_c: 7.8,
    power_pct: 62,
    reason: "following the dew margin",
    ports: PORTS,
    ...over,
  } as DewStatus;
}

/** What `_strip_dew` leaves behind: the three readings ABSENT (not null, not
 *  zero) and `power_pct` nulled, everything else untouched. */
function strippedNode(): DewStatus {
  const node = dewNode() as any;
  delete node.margin_c;
  delete node.temp_c;
  delete node.dewpoint_c;
  node.power_pct = null;
  return node as DewStatus;
}

function seed(over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      principal: OPERATOR,
      wsPhase: "up",
      equipConnected: true,
      status: { connected: {}, looping: false, busy_lanes: [], dew: dewNode() } as never,
      config: { dew: { enabled: true, camera_window: true } } as never,
      toasts: [],
      ...over,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(now: unknown = NOW_READING): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => {
    rootRef!.render(createElement(ConditionsBand as any, {
      now, seeing: null, transparency: null, moon: null,
      moonTargetName: null, moonReason: "pick a target",
    }));
  });
}
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const textOf = (id: string) => String(byId(id)?.textContent ?? "");

// ========================================================= 1. the precondition
seed();
mount();

test("precondition: the band and its DEW tile are on screen", () => {
  assert(byId("wx-band") != null, "no wx-band - the fixture is wrong, not the component");
  assert(byId("wx-tile-dew") != null, "no DEW tile - the band lost a slot");
  assert(/4\.2°C/.test(textOf("wx-tile-dew")), `11.2 - 7.0 = 4.2, got "${textOf("wx-tile-dew")}"`);
  eq(asked.length, 0, `the band fetched something: ${asked.join(", ")}`);
});

// ============================================================ 2. three shapes
test("FOLLOWING: the loop's own sentence, the level, and the surfaces it drives", () => {
  const t = textOf("wx-dew-loop");
  assert(t.length > 0, "no dew-loop line under the band");
  assert(t.includes("following the dew margin"),
    `the server's own reason is not printed verbatim: "${t}"`);
  assert(/62%/.test(t), `the heater level is missing: "${t}"`);
  assert(/the camera window and 2 ports/.test(t),
    `the line does not say what the loop is driving: "${t}"`);
});

test("OFF: the reason verbatim, and no level invented for a loop that commands nothing", () => {
  seed({
    status: { connected: {}, looping: false, busy_lanes: [], dew: dewNode({
      enabled: false, following: false, margin_c: null, temp_c: null,
      dewpoint_c: null, power_pct: null, reason: "dew following is off", ports: [],
    }) } as never,
  });
  mount();
  const t = textOf("wx-dew-loop");
  eq(t, "heaters · dew following is off", `the off line: "${t}"`);
});

test("PAUSED: a hand-set level, with the wall clock the loop takes them back at", () => {
  const until = NOW + 7020;
  const reason = "following is paused for another 117 min - a level was set by hand";
  seed({
    status: { connected: {}, looping: false, busy_lanes: [], dew: dewNode({
      following: false, override_until_ts: until, reason,
    }) } as never,
  });
  mount();
  const t = textOf("wx-dew-loop");
  assert(t.includes(reason), `the pause reason is not verbatim: "${t}"`);
  const clock = new Date(until * 1000)
    .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  assert(t.includes(`back at ${clock}`), `no wall clock on the pause line: "${t}"`);
});

// ================================= 3. a viewer's band prints no margin number
test("a stripped node still says the loop is following, with no number behind it", () => {
  seed({
    principal: VIEWER,
    status: { connected: {}, looping: false, busy_lanes: [], dew: strippedNode() } as never,
  });
  // A viewer gets no weather payload at all, so the tile has no reading either.
  mount(null);

  const line = textOf("wx-dew-loop");
  assert(line.includes("following the dew margin"),
    `a viewer must still learn the loop is following: "${line}"`);
  assert(line.includes(dewHiddenNote()),
    `the line does not say WHY there is no number: "${line}"`);
  assert(!/\d+%/.test(line), `a percentage was printed from a null power_pct: "${line}"`);
  assert(!/\d+(\.\d+)?°C/.test(line), `a temperature was printed from an absent reading: "${line}"`);

  const tile = textOf("wx-tile-dew");
  // "hidden", not "not reported": `api/redact.py:151-173` DELETED the ambient
  // and the dew point on the way out, so the feed sent them and the role is
  // what is missing. The two look identical from here - both fields are null
  // either way - which is exactly why the tile reads the capability.
  assert(/hidden/.test(tile), `the DEW tile: "${tile}"`);
  assert(!/not reported/.test(tile),
    `the tile blames the feed for a field the redaction removed: "${tile}"`);
  assert(tile.includes(`the dew margin needs ${accessPhrase("view.weather")}`),
    `the tile does not name what is missing: "${tile}"`);
  assert(!/\d+(\.\d+)?°C/.test(tile), `the tile printed a margin nobody sent it: "${tile}"`);
  eq(asked.length, 0, `a viewer's band asked the rig for something: ${asked.join(", ")}`);
});

// ========================================================= 4. no loop, no line
test("an engine with no dew loop gets no sentence about one", () => {
  seed({ status: { connected: {}, looping: false, busy_lanes: [] } as never });
  mount();
  assert(byId("wx-tile-dew") != null, "precondition: the band did not render at all");
  assert(byId("wx-dew-loop") == null,
    "a line about the dew loop was drawn for an engine that publishes no dew node");
});

test("a loop that exists but has not ticked says so rather than nothing", () => {
  seed({ status: { connected: {}, looping: false, busy_lanes: [], dew: null } as never });
  mount();
  const t = textOf("wx-dew-loop");
  assert(/has not ticked yet/.test(t), `the un-ticked loop line: "${t}"`);
});

act(() => { rootRef?.unmount(); });

// ============================================== the pure model, on its own
test("dewBandLine: a loop following nothing says so instead of naming a level", () => {
  const view = dewView(dewNode({ ports: [] }), true, Date.now());
  eq(dewBandLine(view, false), "following the dew margin · nothing is set to follow it",
    "a loop with the window off and no ports:");
  // And with the config not read at all, the window is unknown, so silence
  // about it is the only honest answer.
  eq(dewBandLine(view, undefined), "following the dew margin · 62%",
    "an unread config claimed the loop drives nothing:");
});

test("dewClockAt renders the override's own timestamp, not now", () => {
  const ts = NOW + 3600;
  eq(dewClockAt(ts),
    new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    "the clock helper:");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`dewBandDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
