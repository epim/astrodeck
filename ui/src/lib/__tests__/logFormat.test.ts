// logFormat.test.ts — pure Event Log formatting helpers (R2-LOG-01 partial).
// Inline assert harness like site.test.ts. Run: npx tsx src/lib/__tests__/logFormat.test.ts
import { fmtLogTime, severityWord } from "../logFormat";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

// ---------------------------------------------------------- fmtLogTime
test("fmtLogTime pads HH:MM:SS from an epoch-seconds ts", () => {
  const d = new Date(2026, 6, 16, 3, 5, 9); // local time, single-digit fields
  eq(fmtLogTime(d.getTime() / 1000), "03:05:09", "single-digit fields padded");
});

test("fmtLogTime round-trips a double-digit time unpadded", () => {
  const d = new Date(2026, 6, 16, 23, 59, 1);
  eq(fmtLogTime(d.getTime() / 1000), "23:59:01", "double-digit hour, padded seconds");
});

// ---------------------------------------------------------- severityWord
test("severityWord maps the four known server levels", () => {
  eq(severityWord("error"), "Error");
  eq(severityWord("warning"), "Warning");
  eq(severityWord("info"), "Info");
  eq(severityWord("debug"), "Debug");
});

test("severityWord title-cases an unrecognized level instead of dropping it", () => {
  eq(severityWord("success"), "Success");
});

test("severityWord falls back to Info for an empty level", () => {
  eq(severityWord(""), "Info");
});

// ---------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nlogFormat.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export const result = { passed, failed, total };
