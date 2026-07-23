import { shareQuery } from "../share";
let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void){try{fn();passed++;}catch(e){failed++;failures.push(`✗ ${n}: ${(e as Error).message}`);}}
function eq<T>(a: T, b: T, m=""){ if(a!==b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }

test("empty when nothing", () => eq(shareQuery(undefined, undefined), ""));
test("target only", () => eq(shareQuery("M42", 1), "?target=M42"));
test("subs only when > 1", () => eq(shareQuery("M42", 30), "?target=M42&subs=30"));
test("drops subs at 1", () => eq(shareQuery("M42", 1), "?target=M42"));
test("url-encodes spaces", () => eq(shareQuery("My Comet", 0), "?target=My+Comet"));

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  // No @types/node in this project (tsc -b gate) — guard process.exit via
  // globalThis so a failure still exits non-zero without a bare `process`
  // reference (connection.test.ts idiom).
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
