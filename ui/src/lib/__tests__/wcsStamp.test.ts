// wcsStamp.test.ts — the only non-render logic behind the per-frame-WCS Settings
// panel (spec §7 item 7). Inline-assert harness (no vitest); runs via `npx tsx`.
import {
  DEFAULT_WCS_STAMP, downsampleLabel, wcsStampAdvisory, wcsStampOrDefault,
  wcsStampSummary,
} from "../wcsStamp";

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
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ------------------------------------------------------------------ advisory
// `unavailable` is the server's own "no ASTAP and a real mount is connected"
// verdict — the one case where flipping the toggle on tags nothing.
test("advisory warns when the solve capability is unavailable", () => {
  const msg = wcsStampAdvisory({ kind: "unavailable" });
  assert(msg !== null && msg.includes("ASTAP"), `expected an ASTAP notice, got ${msg}`);
});

test("advisory is silent when a solver is available", () => {
  eq(wcsStampAdvisory({ kind: "astap" }), null, "astap:");
  eq(wcsStampAdvisory({ kind: "sim" }), null, "sim (built-in solver is fine):");
  eq(wcsStampAdvisory(null), null, "not yet known:");
  eq(wcsStampAdvisory(undefined), null, "no status yet:");
});

// ------------------------------------------------------------------- summary
test("summary reflects off", () => {
  const s = wcsStampSummary(false, { solver: "astap", downsample: 2, min_stars: 9, queue_max: 4 });
  assert(s.startsWith("Off"), `expected an Off summary, got ${s}`);
});

test("summary reflects on, naming only the non-default knobs", () => {
  const plain = wcsStampSummary(true, DEFAULT_WCS_STAMP);
  assert(plain.startsWith("On"), `expected an On summary, got ${plain}`);
  assert(plain.includes("Auto solver"), plain);
  assert(!plain.includes("downsample"), `defaults must not be listed: ${plain}`);
  assert(!plain.includes("min "), `defaults must not be listed: ${plain}`);

  const tuned = wcsStampSummary(true, { solver: "astap", downsample: 2, min_stars: 25, queue_max: 4 });
  assert(tuned.includes("ASTAP"), tuned);
  assert(tuned.includes("downsample 2x"), tuned);
  assert(tuned.includes("min 25 stars"), tuned);
});

// ------------------------------------------------------------ defaults/labels
test("an old config without the block falls back to server defaults", () => {
  const c = wcsStampOrDefault(undefined);
  eq(c.solver, "auto");
  eq(c.downsample, 0);
  eq(c.min_stars, 0);
  eq(c.queue_max, 4);
  // a partially-populated block keeps its own values
  eq(wcsStampOrDefault({ ...DEFAULT_WCS_STAMP, min_stars: 12 }).min_stars, 12);
});

test("downsample 0 reads as Auto, not 0x", () => {
  eq(downsampleLabel(0), "Auto");
  eq(downsampleLabel(2), "2x");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nwcsStamp.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
