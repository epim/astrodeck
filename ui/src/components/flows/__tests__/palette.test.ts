// palette.test.ts — the palette must offer every node type exactly once.
//
// The defect this guards against is not cosmetic: a node type the palette omits
// is a stage nobody can create. It still arrives on the canvas from a preset, a
// saved flow or the server, so nothing throws and nothing looks broken — the
// same class as a route with no caller. Only an exhaustiveness check finds it.
//
// The list of node types is read out of `flowsTypes.ts` at run time rather than
// retyped here, because a copy in this file would drift with the union and then
// agree with the palette about a type neither of them has. `palette.ts` carries
// the compile-time half of the same check (`PALETTE_COVERS_EVERY_NODE_TYPE`),
// which `tsc -b` enforces and `tsx` cannot — hence both halves.
//
// Run directly:  npx tsx src/components/flows/__tests__/palette.test.ts
import { readFileSync } from "node:fs";
import {
  PALETTE_GROUPS, PALETTE_ITEM_ORDER_DISPUTED, PALETTE_ITEM_ORDER_SOURCES,
  PALETTE_TYPES,
} from "../palette";
import type { FlowNodeType } from "../flowsTypes";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, f: () => void) { try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = "") { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function eqList(a: readonly string[], b: readonly string[], m = "") {
  if (a.join("|") !== b.join("|")) throw new Error(`${m} expected [${b.join(", ")}], got [${a.join(", ")}]`);
}
function ok(c: boolean, m: string) { if (!c) throw new Error(m); }

/** The 19 members of `FlowNodeType`, parsed from the shared types file.
 *
 *  Throws rather than returning [] on a miss. A regex that quietly matches
 *  nothing would turn every assertion below into a comparison of two empty
 *  sets, i.e. a suite that passes because it checked nothing. */
function nodeTypeUnion(): FlowNodeType[] {
  const src = readFileSync(new URL("../flowsTypes.ts", import.meta.url), "utf8");
  const block = /export type FlowNodeType\s*=([\s\S]*?);/.exec(src);
  if (!block) throw new Error("no `export type FlowNodeType = …;` in flowsTypes.ts — the parser, not the palette, is broken");
  const members = [...block[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  if (members.length === 0) throw new Error("the FlowNodeType union parsed to zero members — the parser, not the palette, is broken");
  return members as FlowNodeType[];
}

const UNION = nodeTypeUnion();

// ------------------------------------------------ the extractor is trustworthy

test("FlowNodeType parses to exactly 20 members", () => {
  // §C.7 and the contract's file plan both say 19; FILTER CYCLE is the 20th and
  // post-dates them. The number is deliberately hard-coded: if it changes, the
  // vocabulary changed, and every group below needs a deliberate re-read rather
  // than a silently widened palette. That is exactly what happened here — the
  // node was added server-side and CI caught the UI still at 19, which is the
  // difference between a capability and a capability the operator can reach.
  eq(UNION.length, 20, "FlowNodeType member count:");
  eq(new Set(UNION).size, 20, "the union itself lists a type twice:");
});

// ------------------------------------------------------------- exhaustiveness

test("every node type appears exactly once across all groups", () => {
  const seen = new Map<string, number>();
  for (const g of PALETTE_GROUPS) for (const t of g.types) seen.set(t, (seen.get(t) ?? 0) + 1);

  const missing = UNION.filter((t) => !seen.has(t));
  ok(missing.length === 0,
     `the palette omits ${missing.join(", ")} — those stages cannot be created from the rail or the add-stage sheet, ` +
     "yet a preset or the server can still put them on the canvas, so nothing errors");

  const twice = [...seen].filter(([, n]) => n > 1).map(([t, n]) => `${t}×${n}`);
  ok(twice.length === 0, `the palette lists ${twice.join(", ")} more than once`);
});

test("the palette offers nothing that is not a node type", () => {
  const union = new Set<string>(UNION);
  const strays = PALETTE_TYPES.filter((t) => !union.has(t));
  ok(strays.length === 0,
     `the palette offers ${strays.join(", ")}, which FlowNodeType does not contain — clicking one would add a node ` +
     "the server's vocabulary cannot compile");
});

test("PALETTE_TYPES is the groups flattened, in rail order", () => {
  eqList(PALETTE_TYPES, PALETTE_GROUPS.flatMap((g) => [...g.types]));
  eq(PALETTE_TYPES.length, UNION.length, "flattened palette length:");
});

// ------------------------------------------------- group names and group order

test("group labels and their order are the ones all three sources agree on", () => {
  // §C.7: "group names and group order are agreed by all three sources".
  eqList(PALETTE_GROUPS.map((g) => g.label),
         ["SOURCES", "EQUIPMENT", "RIG OPS", "LOGIC", "ACTIONS + SINKS"]);
});

test("group labels carry their own uppercase", () => {
  // §C.7's heading classes have no `uppercase` utility — unlike the inspector's
  // `.label`, which uppercases in CSS (§C.8). A lowercase label here would
  // render lowercase on the rail.
  for (const g of PALETTE_GROUPS) {
    eq(g.label, g.label.toUpperCase(), `group label "${g.label}" is not upper-case:`);
  }
});

test("no group label repeats", () => {
  eq(new Set(PALETTE_GROUPS.map((g) => g.label)).size, PALETTE_GROUPS.length,
     "distinct group labels:");
});

// --------------------------------------------- item order: the agreed groups

test("SOURCES, EQUIPMENT and RIG OPS are verbatim from all three sources", () => {
  const by = (label: string) => PALETTE_GROUPS.find((g) => g.label === label)!.types;
  eqList(by("SOURCES"), ["dusk", "target", "safety", "cloudwatch"]);
  eqList(by("EQUIPMENT"), ["dome", "flatpanel"]);
  eqList(by("RIG OPS"), ["slew", "autofocus", "guide", "capture", "duskflats", "calib"]);
});

// ------------------------------------------- item order: the §G-3 dispute

test("the disputed groups are the two §G-3 names, and both still exist", () => {
  eqList(PALETTE_ITEM_ORDER_DISPUTED, ["LOGIC", "ACTIONS + SINKS"]);
  for (const label of PALETTE_ITEM_ORDER_DISPUTED) {
    ok(PALETTE_GROUPS.some((g) => g.label === label),
       `${label} is flagged disputed but no longer exists — the flag would then guard nothing`);
  }
});

test("each disputed group ships an order some source actually states", () => {
  // The whole point of the flag. §G-3 is unresolved, so the shipped order is a
  // reading, not a ruling — but it must remain one of the readings on record.
  // A later edit that reorders these to taste invents a fifth ordering nobody
  // wrote down, and this is the only thing that would notice.
  for (const label of PALETTE_ITEM_ORDER_DISPUTED) {
    const shipped = PALETTE_GROUPS.find((g) => g.label === label)!.types;
    const readings = PALETTE_ITEM_ORDER_SOURCES[label as keyof typeof PALETTE_ITEM_ORDER_SOURCES];
    const named = Object.entries(readings)
      .filter(([, order]) => (order as readonly string[]).join("|") === shipped.join("|"))
      .map(([src]) => src);
    ok(named.length > 0,
       `${label} ships [${shipped.join(", ")}], which no source states — the recorded readings are ` +
       Object.entries(readings).map(([s, o]) => `${s}: [${(o as readonly string[]).join(", ")}]`).join(" · "));
  }
});

test("no source claims a type nodes.py does not, and the older ones may only LAG", () => {
  // The original form of this test demanded every source name the identical
  // SET, for a good reason: two sources disagreeing about membership means
  // picking either one drops a node from the palette, and the exhaustiveness
  // test above would only catch it for whichever reading happens to ship.
  //
  // FILTER CYCLE broke it honestly. The node post-dates the prototype and both
  // README readings, so those three cannot name it without inventing
  // provenance they do not have. What must NOT happen is the dangerous
  // direction — a source naming a type the server does not have, which would
  // put a node in the rail that no run can execute.
  //
  // So: nodes.py is the authority and every other reading must be a SUBSET of
  // it. A source that lags is a source that predates a feature; a source that
  // leads is a bug.
  for (const [label, readings] of Object.entries(PALETTE_ITEM_ORDER_SOURCES)) {
    const server = new Set(readings.server as readonly string[]);
    for (const [src, order] of Object.entries(readings)) {
      for (const t of order as readonly string[]) {
        ok(server.has(t),
           `${label}: source "${src}" names ${t}, which nodes.py does not — ` +
           `that is a palette entry no run could execute`);
      }
    }
  }
});

test("LOGIC and ACTIONS + SINKS are exactly the types no other group claims", () => {
  // Guards the dispute record against drifting away from the shipped palette:
  // these are the two groups whose contents nobody may quietly edit.
  const by = (label: string) => PALETTE_GROUPS.find((g) => g.label === label)!.types;
  eqList([...by("LOGIC")].sort(), ["condition", "cycle", "pool"]);
  eqList([...by("ACTIONS + SINKS")].sort(),
         ["abort", "holdresume", "notify", "refocus", "report"]);
});

console.log(`palette.test.ts: ${passed} passed, ${failed} failed`);
if (failed) { failures.forEach((f) => console.error(f)); (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1); }
