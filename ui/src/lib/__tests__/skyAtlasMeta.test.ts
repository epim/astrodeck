// skyAtlasMeta.test.ts — pure-logic tests for the Settings "Sky Atlas" card
// (offline-pack spec §6). Inline-assert harness (no vitest in this repo); runs
// via `npx tsx`.
import { packProgressPct, packStatusLabel } from "../../components/settings/skyAtlasMeta";
import type { PackStatus } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function ok(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

const status = (over: Partial<PackStatus>): PackStatus => ({
  present: false,
  slug: "dss2-order4",
  survey: "CDS/P/DSS2/color",
  order: null,
  bytes: null,
  tile_count: null,
  fetched_at: null,
  fetching: null,
  ...over,
});

test("packProgressPct: 0/0 => 0", () => {
  eq(packProgressPct({ done: 0, total: 0, failed: 0 }), 0);
});

test("packProgressPct: 2046/4092 => 50", () => {
  eq(packProgressPct({ done: 2046, total: 4092, failed: 0 }), 50);
});

test("packStatusLabel: null => checking…", () => {
  ok(packStatusLabel(null).includes("checking"), "expected 'checking…' in label");
});

test("packStatusLabel: fetching => downloading N/M", () => {
  const s = status({ fetching: { done: 3, total: 12, failed: 0 } });
  ok(packStatusLabel(s).includes("downloading 3/12"), "expected 'downloading 3/12' in label");
});

test("packStatusLabel: not present, not fetching => not downloaded", () => {
  const s = status({});
  ok(packStatusLabel(s).includes("not downloaded"), "expected 'not downloaded' in label");
});

test("packStatusLabel: present => size + order", () => {
  const s = status({
    present: true,
    bytes: 262144000,
    order: 4,
    fetched_at: 1786000000,
  });
  const label = packStatusLabel(s);
  ok(label.includes("250 MB"), `expected '250 MB' in label, got: ${label}`);
  ok(label.includes("order 4"), `expected 'order 4' in label, got: ${label}`);
});

console.log(`skyAtlasMeta.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  // No @types/node in this Vite/browser project; reach process via globalThis so
  // `tsc -b` (browser lib, no Node types) still compiles this file cleanly
  // (same workaround as src/__tests__/ws.test.ts).
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
