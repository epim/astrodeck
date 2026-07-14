// tileCache.test.ts — pure cache/queue bookkeeping (tile-engine spec §4).
import { tilePriority, planFetches, LruSet, NegativeCache } from "../tileCache";
import { ang2pixNested, parentOf } from "../healpix";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

test("tilePriority: center tile ranks below (nearer than) an edge tile", () => {
  const order = 6, ra = 45, dec = 20;
  const center = ang2pixNested(order, ra, dec);
  const edge = ang2pixNested(order, ra + 0.8, dec + 0.8);
  assert(tilePriority(center, order, ra, dec) < tilePriority(edge, order, ra, dec),
    "center priority < edge priority");
});

test("LruSet evicts oldest, get promotes recency, onEvict fires", () => {
  const evicted: string[] = [];
  const lru = new LruSet<string>(2, (v) => evicted.push(v));
  lru.set("a", "A");
  lru.set("b", "B");
  assert(lru.get("a") === "A", "a present");   // promotes a
  lru.set("c", "C");                            // evicts b (oldest)
  assert(!lru.has("b"), "b evicted");
  assert(evicted.length === 1 && evicted[0] === "B", "onEvict got B");
  assert(lru.has("a") && lru.has("c"), "a + c remain");
  assert(lru.size === 2, "size capped");
});

test("NegativeCache blocks within ttl and clears after", () => {
  const nc = new NegativeCache(45_000);
  nc.mark("k", 1000);
  assert(nc.blocked("k", 1000) === true, "blocked at t0");
  assert(nc.blocked("k", 40_000) === true, "blocked before ttl");
  assert(nc.blocked("k", 46_001) === false, "cleared after ttl");
  assert(nc.blocked("k", 46_050) === false, "stays cleared (entry dropped)");
});

// --- planFetches: a lone missing tile queues itself then its whole parent
//     chain (finest->coarsest) down to order 0, once resident() is always false.
test("planFetches: missing tile -> tile + full ancestor chain, clamped at 0", () => {
  const order = 6;
  const ra = 45, dec = 20;
  const npix = ang2pixNested(order, ra, dec);
  const plan = planFetches([npix], order, ra, dec, () => false, 5);
  assert(plan.length === 6, `tile + 5 ancestors, got ${plan.length}`);
  assert(plan[0].order === order && plan[0].npix === npix, "native tile first");
  // Orders descend 6,5,4,3,2,1 and each npix is the parentOf the previous.
  for (let i = 1; i < plan.length; i++) {
    assert(plan[i].order === order - i, `level ${i} order`);
    assert(plan[i].npix === parentOf(plan[i - 1].npix), `level ${i} is parentOf prev`);
  }
});

// --- planFetches: the walk STOPS at the first resident ancestor (a coarser
//     fill already exists) — nothing at/above it is queued.
test("planFetches: stops the ancestor walk at the first resident ancestor", () => {
  const order = 6;
  const ra = 45, dec = 20;
  const npix = ang2pixNested(order, ra, dec);
  const grandparent = parentOf(parentOf(npix)); // order 4
  const plan = planFetches(
    [npix], order, ra, dec,
    (o, n) => o === order - 2 && n === grandparent, 5,
  );
  // Queues native (o6) + parent (o5); stops AT the resident grandparent (o4).
  assert(plan.length === 2, `tile + 1 ancestor, got ${plan.length}`);
  assert(plan[1].order === order - 1, "only the immediate parent queued");
  assert(!plan.some((j) => j.order === order - 2), "resident grandparent not queued");
});

// --- planFetches: a resident native tile contributes NOTHING (no self, no
//     ancestors); shared ancestors are deduped across sibling tiles.
test("planFetches: skips resident tiles and dedups shared ancestors", () => {
  const order = 6;
  // Two siblings sharing one parent (npix 4k and 4k+1 -> parentOf both = k).
  const a = ang2pixNested(order, 45, 20);
  const b = a ^ 1; // same parentOf(a) since parentOf = npix >> 2
  assert(parentOf(a) === parentOf(b), "chose sibling tiles sharing a parent");
  const plan = planFetches([a, b], order, 45, 20, () => false, 5);
  const parentJobs = plan.filter((j) => j.order === order - 1);
  assert(parentJobs.length === 1, `shared parent queued once, got ${parentJobs.length}`);

  const resident = planFetches([a, b], order, 45, 20, (o) => o === order, 5);
  assert(resident.length === 0, "both native tiles resident -> empty plan");
});

// --- planFetches: nearest tile (and hence its ancestor column) is ordered
//     before a far tile — one round-trip fill hinges on this.
test("planFetches: nearest tile ordered before a far tile", () => {
  const order = 6, ra = 45, dec = 20;
  const near = ang2pixNested(order, ra, dec);
  const far = ang2pixNested(order, ra + 0.6, dec + 0.6);
  const plan = planFetches([far, near], order, ra, dec, () => false, 5);
  const iNear = plan.findIndex((j) => j.order === order && j.npix === near);
  const iFar = plan.findIndex((j) => j.order === order && j.npix === far);
  assert(iNear >= 0 && iFar >= 0, "both native tiles present");
  assert(iNear < iFar, "nearer native tile queued before the far one");
});

console.log(`tileCache.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
