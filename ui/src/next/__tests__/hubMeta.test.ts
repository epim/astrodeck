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
//   * compare sheet ENTRIES by object identity in `composeSheets()` instead of
//     by `.id` -> `hubs/index.ts` throws at module load (Sky and Settings each
//     build their own entry object for `sites`), and every test in this file
//     goes red at the import.
//   * count `rows.length` instead of `buildCards(rows, reports).length` in
//     `galleryCountFrom` -> "a report-only night is a card, and the chip counts
//     it" goes red (2 where the grid draws 3).
//
// THE SHEET REGISTRY IS PART OF THIS FILE'S SUBJECT for the same reason the
// chips are: a sheet name is global, one name is one screen, and the failure
// mode is silent - the same URL opens different things depending on which
// registry composed last. The check runs at module load; these tests prove it
// is checking the right thing and that the shared names really are shared.

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

const { HUB_META, HUB_ORDER, SHEETS, SHEET_ENTRIES, SHEET_REGISTRIES } = await import("../hubs");
type SubContext = import("../hubs").SubContext;
const { SUBS } = await import("../router");
type HubId = import("../router").HubId;
const { buildCards, galleryCountFrom } = await import(
  "../hubs/session/gallery/sessionsIndex");
type SessionRow = import("../../types").SessionRow;
type SessionReportSummary = import("../../types").SessionReportSummary;

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
    galleryCount: null,
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

// ------------------------------------------------------------------ gallery

test("the GALLERY chip carries the shelf's size once something has read it", () => {
  eq(item("session", "gallery", { ...coldContext(), galleryCount: 8 }).count, 8);
});

test("an unread shelf carries NO count - null is not an empty rig", () => {
  const chip = item("session", "gallery", { ...coldContext(), galleryCount: null });
  eq(chip.count, undefined,
    "a chip that says GALLERY 0 before anything was read claims the rig is empty:");
});

test("a shelf read as EMPTY prints the zero, because that zero is the news", () => {
  // The opposite case from ALERTS 0: nothing on the rig is exactly what the
  // operator needs to see on the chip that leads to fixing it, and it is a
  // different claim from "not read yet".
  eq(item("session", "gallery", { ...coldContext(), galleryCount: 0 }).count, 0);
});

// ------------------------------------------------------- the count's derivation

/** Two nights in the ledger; one report that matches neither (an older night,
 *  shot before the session ledger existed). The shelf draws three cards. */
const ROWS: SessionRow[] = [
  {
    id: "s1", name: "M31 LRGB", status: "dormant",
    created_ts: 1_756_900_000, updated_ts: 1_757_000_100,
    nights: 2, accepted: 41, total: 120, auto_resume: false,
  } as SessionRow,
  {
    id: "s2", name: "NGC 7331", status: "dormant",
    created_ts: 1_756_700_000, updated_ts: 1_756_800_000,
    nights: 1, accepted: 10, total: 30, auto_resume: false,
  } as SessionRow,
];

const ORPHAN_REPORT: SessionReportSummary[] = [{
  id: "IC 1396-20260101-201500", plan_name: "IC 1396",
  started_at: 1_700_000_000, ended_at: 1_700_010_000, end_reason: "complete",
  frames_captured: 20, frames_rejected: 2, integration_s: 3600,
} as SessionReportSummary];

test("a report-only night is a card, and the chip counts it", () => {
  eq(buildCards(ROWS, ORPHAN_REPORT).length, 3,
    "the shelf keeps a night that predates the session ledger:");
  eq(galleryCountFrom(ROWS, ORPHAN_REPORT), 3,
    "the chip must count CARDS, not ledger rows - `rows.length` would say 2:");
});

test("the chip's number and the grid's are the same number, not two reads", () => {
  eq(galleryCountFrom(ROWS, ORPHAN_REPORT), buildCards(ROWS, ORPHAN_REPORT).length,
    "chip and grid disagreeing is what makes an operator count tiles:");
});

test("nothing read yet derives no count at all", () => {
  eq(galleryCountFrom(null, null), null);
  eq(galleryCountFrom(ROWS, null), null, "half a read is not a shelf:");
  eq(galleryCountFrom(null, ORPHAN_REPORT), null);
});

// ------------------------------------------------------------ the sheet registry

test("every registered sheet names a module id and a loader", () => {
  const names = Object.keys(SHEET_ENTRIES);
  ok(names.length > 20, `only ${names.length} sheets composed - the registry is not loading`);
  for (const [name, entry] of Object.entries(SHEET_ENTRIES)) {
    ok(typeof entry.id === "string" && entry.id.length > 0, `${name} has no module id`);
    ok(typeof entry.load === "function", `${name} has no loader`);
  }
});

test("no sheet name is registered by two hubs under two different module ids", () => {
  // The composed map cannot show this - it keeps one entry per name - so walk
  // the per-hub registries, which is where the collision would be written.
  const ids = new Map<string, Set<string>>();
  for (const reg of Object.values(SHEET_REGISTRIES)) {
    for (const [name, entry] of Object.entries(reg)) {
      const set = ids.get(name) ?? new Set<string>();
      set.add(entry.id);
      ids.set(name, set);
    }
  }
  for (const [name, set] of ids) {
    eq(set.size, 1, `"${name}" is registered as ${[...set].join(" and ")}:`);
  }
});

test("the RIG hub registers the sheets its own screens navigate to", () => {
  // A sheet name that no registry carries renders "THIS SHEET IS NOT BUILT YET"
  // over a control that looked live - and the ONLY thing standing between a
  // `nav.sheet("x")` and that is a one-line spread in `rig/sheets/index.ts`
  // that no sheet task is allowed to write for itself. `videoLibrary` is where
  // ALL RECORDINGS goes (D-RIG-1); the rest are named so a dropped spread in
  // any fragment is one failing assertion rather than a screen nobody opened.
  const rig = SHEET_REGISTRIES.rig;
  ok(rig != null, "the rig hub registers no sheets at all");
  for (const name of [
    "camera", "power", "mount", "polar", "focuser", "wheel", "guider",
    "rotator", "safety", "videoLibrary", "inspect", "profiles",
  ]) {
    ok(rig[name] != null, `rig does not register "${name}" - its opener is a dead end`);
  }
  eq(rig.videoLibrary.id, "rig/sheets/videoLibrary",
    "videoLibrary is registered under another module id:");
});

test("`sites` and `horizon` are shared by Sky and Settings under one id each", () => {
  const sky = SHEET_REGISTRIES.sky;
  const settings = SHEET_REGISTRIES.settings;
  for (const name of ["sites", "horizon"]) {
    ok(sky[name] != null, `sky does not register ${name}`);
    ok(settings[name] != null, `settings does not register ${name}`);
    eq(settings[name].id, sky[name].id, `${name} must be ONE module in both hubs:`);
    eq(SHEET_ENTRIES[name].id, sky[name].id, `${name} composed under a third id:`);
  }
});

test("`sites` and `horizon` are two different screens, not one lookup twice", () => {
  ok(SHEETS.sites != null && SHEETS.horizon != null, "one of the shared sheets is missing");
  ok(SHEETS.sites !== SHEETS.horizon,
    "two sheet names collapsed onto one component - the hash would open the wrong screen");
});

test("SHEETS answers synchronously for every registered name", () => {
  // This is the property that let the sheets be code-split at all: `SheetHost`
  // decides whether a name EXISTS from this lookup, and a name that answered
  // only after a fetch would render "THIS SHEET IS NOT BUILT YET" over a sheet
  // that was merely still arriving.
  for (const name of Object.keys(SHEET_ENTRIES)) {
    ok(SHEETS[name] != null, `SHEETS["${name}"] is empty at module load`);
  }
});

console.log(`hubMeta.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export { passed, failed };
export const total = passed + failed;
