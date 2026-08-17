// planPolicyDefaults.test.ts — the UI's default plan must INHERIT, not decide.
//
// #239 stage A moved twelve settings to the rig's standards, with `null` on a
// plan meaning "inherit". store.ts's defaultPlan() still filled all twelve with
// concrete numbers, mirroring the pydantic defaults it was written against - so
// every plan created in this app was born fully overridden and could never
// inherit anything. The feature was live and inert on the one screen that makes
// plans.
//
// Nothing caught it. The server tests passed (the server was right), the UI
// tests passed (nothing compared these two files), and it took opening the plan
// editor and seeing OVERRIDE on ten of twelve rows.
//
// This parses BOTH sides: the moved-field list out of sequence/policy.py and
// the default plan out of store.ts. Restating either list here would just be a
// third copy to drift.
//
// Run alone:  npx tsx src/__tests__/planPolicyDefaults.test.ts
import { readFileSync } from "node:fs";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}

const policySrc = readFileSync(
  new URL("../../../server/astrodeck/sequence/policy.py", import.meta.url), "utf8");
const storeSrc = readFileSync(new URL("../store.ts", import.meta.url), "utf8");

/** The twelve, read off MOVED_FIELDS in policy.py. */
function movedFields(): string[] {
  const m = /MOVED_FIELDS:[^=]*=\s*\(([\s\S]*?)\)/.exec(policySrc);
  if (!m) throw new Error("MOVED_FIELDS not found in policy.py");
  return [...m[1].matchAll(/"([a-z_]+)"/g)].map((x) => x[1]);
}

/** defaultPlan()'s body, so a stray match elsewhere in store.ts cannot fool us. */
function defaultPlanBody(): string {
  const at = storeSrc.indexOf("function defaultPlan(");
  if (at < 0) throw new Error("defaultPlan() not found in store.ts");
  const rest = storeSrc.slice(at);
  const end = rest.indexOf("\nfunction ", 1);
  return end < 0 ? rest : rest.slice(0, end);
}

const MOVED = movedFields();
const BODY = defaultPlanBody();

test("policy.py parse found the twelve", () => {
  eq(MOVED.length, 12, `parsed ${MOVED.length} moved fields`);
});

test("defaultPlan() leaves every moved field to the rig", () => {
  const decided: string[] = [];
  for (const f of MOVED) {
    const m = new RegExp(`^\s{4}${f}:\s*(.+?),\s*$`, "m").exec(BODY);
    if (!m) continue;                     // absent is inherit, which is fine
    if (m[1].trim() !== "null") decided.push(`${f}=${m[1].trim()}`);
  }
  eq(decided.join(", "), "",
    "these are born as OVERRIDES, so a plan made in this app can never inherit "
    + "the rig's standards and Settings > Imaging standards cannot reach it");
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nplanPolicyDefaults.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
