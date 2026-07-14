// tileCache.test.ts — pure cache/queue bookkeeping (tile-engine spec §4).
import { tilePriority, LruSet, NegativeCache } from "../tileCache";
import { ang2pixNested } from "../healpix";

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

console.log(`tileCache.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
