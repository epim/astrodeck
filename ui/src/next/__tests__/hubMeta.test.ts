// hubMeta.test.ts - the sub-nav chips are a PURE function of `SubContext`.
//
//   Run directly:  npx tsx src/next/__tests__/hubMeta.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHY THIS IS WORTH A FILE. The chips are the only place several facts appear
// at all: how many alerts did not get out, whether a role the rig was told to
// bring up did not come up, whether a second incident is waiting under the one
// on screen. Each is a number the hub body would otherwise be the only place to
// learn, and each has the same failure mode - a `?? 0` or a `|| ""` somewhere in
// the fold turns "nobody has looked" into a confident zero, and the chip then
// says everything is fine in exactly the case where nothing has been checked.
//
// So every assertion below is about the difference between NULL and ZERO as
// much as about the count itself.
//
// SABOTAGE CHECKS:
//   * `count: ctx.alertsUndelivered ?? undefined` instead of `|| undefined`
//     -> "a zero that is not news is dropped" goes red (0 renders as
//     "ALERTS 0").
//   * drop the `> 1` on the NOW count -> "one incident is the dot, not a count"
//     goes red.
//   * drop `dot` from the monitor LIVE chip -> "LIVE wears the same incident
//     tone as NOW" goes red.
//   * make `subs` read the store instead of the bag -> this file cannot even
//     import, since it never mounts React.

/* eslint-disable @typescript-eslint/no-explicit-any */

// `hubs/index.ts` pulls in every hub component, and those reach `api.ts` ->
// `lib/base.ts`, which reads `window.location.pathname` at module scope, and a
// few `.css` files Node has no idea what to do with. A stub for each is cheaper
// than splitting the registry the architecture names as one module.
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

const { JSDOM } = await import("jsdom");
const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://local/" });
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} send() {} addEventListener() {} removeEventListener() {} };
const g = globalThis as any;
for (const k of ["window", "document", "navigator", "localStorage", "sessionStorage",
  "location", "history", "Element", "Node", "Event", "CustomEvent", "WebSocket",
  "matchMedia", "getComputedStyle", "requestAnimationFrame", "cancelAnimationFrame"]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}

const { HUB_META, HUB_ORDER } = await import("../hubs");
type SubContext = import("../hubs").SubContext;
const { SUBS } = await import("../router");
type HubId = import("../router").HubId;

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = ""): void { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function ok(cond: boolean, m: string): void { if (!cond) throw new Error(m); }

/** Nothing has been read yet. Every optional field is null, which is the state
 *  the app is in for the first second of every launch. */
function coldContext(): SubContext {
  return {
    flowCount: null,
    incidentTone: null,
    incidentCount: 0,
    alertsUndelivered: null,
    rigDeviceCount: null,
    rigLinkTone: null,
    weatherDot: null,
    unseenError: 0,
  };
}

function itemsOf(hub: HubId, ctx: SubContext) {
  return HUB_META[hub].subs(ctx);
}
function item(hub: HubId, id: string, ctx: SubContext) {
  const hit = itemsOf(hub, ctx).find((i) => i.id === id);
  if (!hit) throw new Error(`${hub} has no "${id}" chip`);
  return hit;
}

// ------------------------------------------------------------- the contract

test("every hub's chips are exactly the sections the router recognises", () => {
  for (const hub of HUB_ORDER) {
    const ids = itemsOf(hub, coldContext()).map((i) => i.id).join(",");
    eq(ids, (SUBS[hub] as readonly string[]).join(","), `${hub} chips:`);
  }
});

test("every chip carries a label, and it is not the raw route id", () => {
  for (const hub of HUB_ORDER) {
    for (const it of itemsOf(hub, coldContext())) {
      ok(!!it.label, `${hub}/${it.id} has no label`);
      eq(it.label, it.label.toUpperCase(), `${hub}/${it.id} label case:`);
    }
  }
});

test("nothing read yet means NO count anywhere - null is not zero", () => {
  const cold = coldContext();
  for (const hub of HUB_ORDER) {
    for (const it of itemsOf(hub, cold)) {
      eq(it.count, undefined, `${hub}/${it.id} claims a count before anything was read:`);
      eq(it.dot, undefined, `${hub}/${it.id} claims a state before anything was read:`);
    }
  }
});

test("a zero that is not news is dropped; a zero that IS news is printed", () => {
  const ctx = {
    ...coldContext(), alertsUndelivered: 0, incidentCount: 0,
    rigDeviceCount: 0, flowCount: 0,
  };
  // Nothing undelivered and nothing wrong is the normal state of a good night.
  // A chip that spent it saying "ALERTS 0" would be a badge for the absence of
  // a problem, which is the one thing the row has no room for.
  eq(item("monitor", "alerts", ctx).count, undefined, "ALERTS 0 is not news:");
  eq(item("session", "now", ctx).count, undefined, "NOW 0 is not news:");
  // These two are the opposite case: zero connected devices and zero saved
  // flows are exactly what the operator needs to see on the chip that leads to
  // fixing them, and they are a different claim from "not read yet".
  eq(item("rig", "devices", ctx).count, 0, "DEVICES 0 is the whole point of the chip:");
  eq(item("session", "flows", ctx).count, 0, "an empty library read IS a fact:");
});

// ------------------------------------------------------------------ session

test("the FLOWS chip counts the saved library once it has been read", () => {
  eq(item("session", "flows", { ...coldContext(), flowCount: 9 }).count, 9);
  eq(item("session", "flows", { ...coldContext(), flowCount: null }).count, undefined,
    "an unread library must not show a confident zero");
});

test("one incident is the dot, not a count; a second one is `NOW 2`", () => {
  const one = item("session", "now", { ...coldContext(), incidentCount: 1, incidentTone: "warn" });
  eq(one.dot, "warn");
  eq(one.count, undefined, "NOW 1 beside a dot says the same thing twice:");

  const two = item("session", "now", { ...coldContext(), incidentCount: 2, incidentTone: "bad" });
  eq(two.dot, "bad");
  eq(two.count, 2, "a second card under the first is something the dot cannot say:");
});

// ------------------------------------------------------------------ monitor

test("LIVE wears the same incident tone NOW does", () => {
  const ctx = { ...coldContext(), incidentCount: 1, incidentTone: "bad" as const };
  eq(item("monitor", "live", ctx).dot, "bad");
  eq(item("monitor", "live", ctx).dot, item("session", "now", ctx).dot,
    "two answers to one question is worse than one answer in one place:");
});

test("the ALERTS chip counts what did NOT get out, not what is configured", () => {
  eq(item("monitor", "alerts", { ...coldContext(), alertsUndelivered: 3 }).count, 3);
  eq(item("monitor", "alerts", { ...coldContext(), alertsUndelivered: null }).count, undefined);
});

// ---------------------------------------------------------------------- rig

test("the DEVICES chip counts connected devices and tones the link state", () => {
  const bad = { ...coldContext(), rigDeviceCount: 4, rigLinkTone: "bad" as const };
  eq(item("rig", "devices", bad).count, 4);
  eq(item("rig", "devices", bad).dot, "bad", "a role that never came up is red:");

  const degraded = { ...coldContext(), rigDeviceCount: 5, rigLinkTone: "warn" as const };
  eq(item("rig", "devices", degraded).dot, "warn", "one that came up and dropped is amber:");

  eq(item("rig", "capture", degraded).dot, undefined, "the tone belongs to DEVICES only");
});

// ------------------------------------------------------------------ weather

test("the CONDITIONS chip warns on an un-overridden alert and dims on a stale feed", () => {
  eq(item("weather", "conditions", { ...coldContext(), weatherDot: "warn" }).dot, "warn");
  eq(item("weather", "conditions", { ...coldContext(), weatherDot: "dim" }).dot, "dim",
    "a stale feed is an absence of information, not bad news:");
  eq(item("weather", "radar", { ...coldContext(), weatherDot: "warn" }).dot, undefined,
    "the alert belongs to the screen that explains it");
});

console.log(`hubMeta.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export { passed, failed };
export const total = passed + failed;
