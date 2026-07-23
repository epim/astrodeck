import { parseSeen, serializeSeen, withSeen, spotlightRect } from "../coach";
let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, f: () => void){ try { f(); passed++; } catch(e){ failed++; failures.push(`✗ ${n}: ${(e as Error).message}`);} }
function eq<T>(a: T, b: T, m=""){ if(a!==b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function assert(c: boolean, m: string){ if(!c) throw new Error(m); }

test("parseSeen: null/garbage → {}", () => {
  eq(Object.keys(parseSeen(null)).length, 0, "null");
  eq(Object.keys(parseSeen("not json")).length, 0, "garbage");
  eq(Object.keys(parseSeen("[1,2]")).length, 0, "array");
});
test("parseSeen: keeps only true values", () => {
  const m = parseSeen(JSON.stringify({ a: true, b: false, c: 1 }));
  eq(m.a, true, "a"); assert(!("b" in m), "b dropped"); assert(!("c" in m), "c dropped");
});
test("withSeen: idempotent add", () => {
  const a = withSeen({}, "x"); eq(a.x, true, "added");
  const b = withSeen(a, "x"); eq(b, a, "same ref when already present");
});
test("serializeSeen round-trips through parseSeen", () => {
  const m = withSeen(withSeen({}, "first-run-wizard"), "coach-detect-rig");
  const r = parseSeen(serializeSeen(m));
  eq(r["first-run-wizard"], true, "k1"); eq(r["coach-detect-rig"], true, "k2");
});
test("spotlightRect: pads symmetrically", () => {
  const r = spotlightRect({ left: 100, top: 50, width: 40, height: 20 }, 8);
  eq(r.left, 92, "left"); eq(r.top, 42, "top"); eq(r.width, 56, "w"); eq(r.height, 36, "h");
});

const total = passed + failed;
console.log(`\ncoach.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));
export const result = { passed, failed, total };
