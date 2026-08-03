// nav.test.ts — the navigation rail's standing rules, enforced against the
// SOURCE so they cannot drift from what ships.
//
//   Run directly:  npx tsx src/__tests__/nav.test.ts
//
// Three rules live here, all of them written down in App.tsx and none of them
// previously checkable:
//
//   1. APPEND-ONLY (Risk-10, "land the order once, append entries thereafter").
//      Monitor, Tonight, Reports, Help and now Gallery were each appended with
//      no reorder and no eviction. A reorder is invisible in review and very
//      visible to a user who has learnt where a tab lives, in the dark, with
//      cold hands. This test pins the landed prefix exactly.
//
//   2. PHONE PARITY. The five primary bottom-nav slots are the setup-critical
//      tabs, so every OTHER rail destination has to be reachable through the
//      More sheet. Reports was invisible on tablet AND desktop for a whole
//      release because it had a route and a sheet entry but no rail entry; the
//      inverse (a rail entry with no sheet entry) makes a destination
//      unreachable on a phone in exactly the same silent way.
//
//   3. THE RAIL HAS TO FIT. At 1440x900 — the shortest viewport this rail
//      renders on — an entry past the fold sits inside a scroll region with no
//      visible affordance, i.e. it is a destination nobody finds. Appending Help
//      hit this once (measured scrollHeight 877 vs clientHeight 818) and it was
//      fixed by dropping the per-entry padding. Appending Gallery hit it again.
//      The arithmetic below is the same arithmetic those two measurements pin,
//      so a 16th entry fails HERE rather than on a tablet at 2am.
//
// Reading the source rather than importing App.tsx is deliberate: App pulls in
// the store, the websocket and every chrome component, and none of that is
// needed to check a table of strings.

// Same dependency-free node:fs access idiom as cssClasses.test.ts — the project
// installs no @types/node and `tsc -b` type-checks everything under src/.
interface NodeFsLike { readFileSync(path: string, encoding: string): string }
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;
const pathOf = (rel: string): string =>
  decodeURIComponent(new URL(rel, import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, "$1");

const appSrc = fs.readFileSync(pathOf("../App.tsx"), "utf8");
const sheetSrc = fs.readFileSync(pathOf("../components/NavMoreSheet.tsx"), "utf8");
const bottomSrc = fs.readFileSync(pathOf("../components/BottomNav.tsx"), "utf8");
const iconsSrc = fs.readFileSync(pathOf("../components/icons.tsx"), "utf8");
const typesSrc = fs.readFileSync(pathOf("../types.ts"), "utf8");

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}

// ---------------------------------------------------------------- parsing
/** Strip `//` line comments. Every one of these tables is annotated at length,
 *  and the prose names the very ids being parsed. */
const decomment = (s: string): string => s.replace(/(^|[^:])\/\/[^\n]*/g, "$1");

/** `[{id, label, icon}]` from a `const NAME … = [ … ];` table. */
function parseTable(src: string, name: string): { id: string; label: string; icon: string }[] {
  const m = src.match(new RegExp(`const ${name}[\\s\\S]*?=\\s*\\[([\\s\\S]*?)\\n\\];`));
  if (!m) throw new Error(`could not locate \`const ${name} = [\``);
  return [...decomment(m[1]).matchAll(
    /\{\s*id:\s*"([a-z0-9_]+)"\s*,\s*label:\s*"([^"]+)"\s*,\s*icon:\s*"([a-z0-9-]+)"\s*\}/gi,
  )].map((x) => ({ id: x[1], label: x[2], icon: x[3] }));
}

/** Keys of a `const NAME: Partial<Record<ViewName, …>> = { … };` map. */
function parseRecordKeys(src: string, name: string): string[] {
  const m = src.match(new RegExp(`const ${name}[\\s\\S]*?=\\s*\\{([\\s\\S]*?)\\n\\};`));
  if (!m) throw new Error(`could not locate \`const ${name} = {\``);
  return [...decomment(m[1]).matchAll(/^\s*([a-z0-9_]+)\s*:/gim)].map((x) => x[1]);
}

const NAV = parseTable(appSrc, "NAV");
const PRIMARY = parseTable(bottomSrc, "PRIMARY");
const OVERFLOW = parseTable(sheetSrc, "OVERFLOW_VIEWS");
const GATED = parseRecordKeys(appSrc, "GATED");
const NAV_IDS = NAV.map((n) => n.id);

/**
 * The order as landed, in full. This literal IS the rule — if a change to
 * App.tsx requires editing anything but the tail of this array, that change is a
 * reorder and Risk-10 forbids it.
 */
const LANDED_ORDER = [
  "connect", "polar", "mount", "focus", "capture", "guide", "atlas",
  "sequence", "power", "monitor", "tonight", "settings", "report", "help",
  "gallery",
];

// ---------------------------------------------------------------- tests

test("sanity: all four nav tables parsed", () => {
  assert(NAV.length >= 14, `parsed only ${NAV.length} NAV entries`);
  assert(PRIMARY.length === 5, `bottom nav should have 5 primary slots, parsed ${PRIMARY.length}`);
  assert(OVERFLOW.length >= 9, `parsed only ${OVERFLOW.length} More-sheet entries`);
  assert(GATED.length >= 6, `parsed only ${GATED.length} GATED entries`);
});

test("APPEND-ONLY: the landed rail order is byte-for-byte unchanged", () => {
  eq(NAV_IDS.join(","), LANDED_ORDER.join(","),
    "the rail order changed. Risk-10 permits APPENDING a destination and nothing else — " +
    "a user who has learnt where a tab lives finds it moved, in the dark, mid-session. " +
    "If this is a deliberate append, add the new id to the END of LANDED_ORDER here too");
});

test("APPEND-ONLY: Gallery is the newest entry and sits last", () => {
  eq(NAV_IDS[NAV_IDS.length - 1], "gallery");
});

test("every rail entry is a real ViewName", () => {
  const at = typesSrc.search(/export type ViewName\s*=/);
  const tail = decomment(typesSrc.slice(at));
  const union = tail.slice(0, tail.indexOf(";"));
  const names = new Set([...union.matchAll(/"([a-z0-9_]+)"/gi)].map((x) => x[1]));
  const strays = NAV_IDS.filter((id) => !names.has(id));
  eq(strays.length, 0, `rail entries that are not ViewName members: ${strays.join(", ")}`);
});

test("every rail entry has a glyph that actually exists", () => {
  // Tailwind-style silence: an unknown icon name is a TypeScript error today,
  // but the PATHS table is a separate literal — a name in the union with no path
  // renders an empty <svg>, which on a 72px rail reads as a missing tab.
  for (const n of NAV) {
    assert(new RegExp(`^\\s*"?${n.icon}"?:`, "m").test(iconsSrc),
      `nav entry "${n.id}" uses icon "${n.icon}", which has no entry in icons.tsx PATHS`);
  }
});

test("PHONE PARITY: every non-primary rail destination is in the More sheet", () => {
  const primary = new Set(PRIMARY.map((p) => p.id));
  const sheet = new Set(OVERFLOW.map((o) => o.id));
  const unreachable = NAV_IDS.filter((id) => !primary.has(id) && !sheet.has(id));
  eq(unreachable.length, 0,
    `these rail destinations cannot be reached on a phone at all: ${unreachable.join(", ")} — ` +
    "the More sheet is the phone's copy of this rail, not an optional extra");
});

test("PHONE PARITY: the More sheet offers nothing the rail does not", () => {
  const rail = new Set(NAV_IDS);
  const orphans = OVERFLOW.map((o) => o.id).filter((id) => !rail.has(id));
  eq(orphans.length, 0, `More-sheet entries with no rail entry: ${orphans.join(", ")}`);
});

test("Gallery is NOT equipment-gated", () => {
  assert(!GATED.includes("gallery"),
    "browsing frames the rig already wrote needs no equipment — sending someone to the " +
    "not-connected interstitial to look at last night's files is a lie about what is required");
});

// ------------------------------------------------- the rail actually fits
// Derived from the two measurements recorded in App.tsx, not re-guessed:
//   13 entries at py-3 -> scrollHeight 815;  14 entries at py-3 -> 877.
// So one entry costs (877-815) = 62px at py-3, of which 24px is the padding,
// leaving 38px of icon (20) + gap (4) + label line (~14). Changing the padding
// class moves the whole rail by 2px per entry per 0.5 unit.
const RAIL_CLIENT_H = 818;   // 900 - 48 header - 32 (my-4) - 2 border, measured
const RAIL_OWN_PAD = 16;     // the <nav>'s own py-2
const ENTRY_BODY = 38;       // icon + gap + label line, measured (62 - 2*12)
const TOUCH_FLOOR = 44;      // touch spec R14

test("the rail's per-entry padding is the one the arithmetic below assumes", () => {
  const m = appSrc.match(/flex flex-col items-center gap-1 py-([\d.]+) transition-colors/);
  assert(!!m, "could not find the nav button's padding class in App.tsx");
  const pad = Number(m![1]) * 4;   // Tailwind spacing unit = 4px
  const entry = ENTRY_BODY + 2 * pad;
  const railHeight = NAV.length * entry + RAIL_OWN_PAD;
  assert(entry >= TOUCH_FLOOR,
    `a nav entry is ${entry}px tall, below the ${TOUCH_FLOOR}px touch floor (touch spec R14)`);
  assert(railHeight <= RAIL_CLIENT_H,
    `${NAV.length} entries at py-${m![1]} come to ${railHeight}px against a ${RAIL_CLIENT_H}px rail at ` +
    "1440x900. The last entry would sit below the fold of a scroll region with no visible " +
    "affordance — a destination nobody finds. Reduce the padding (and re-measure) before appending");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nnav.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
