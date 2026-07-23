import { computeWizard, WIZARD_STEPS, type WizardSnapshot } from "../firstRunWizard";
let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, f: () => void){ try { f(); passed++; } catch(e){ failed++; failures.push(`✗ ${n}: ${(e as Error).message}`);} }
function eq<T>(a: T, b: T, m=""){ if(a!==b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function assert(c: boolean, m: string){ if(!c) throw new Error(m); }

const BLANK: WizardSnapshot = {
  siteIsDefault: true, equipConnected: false, profileCount: 0,
  targetCount: 0, hasCooler: true, coolerActive: false, frameCount: 0,
};

test("blank slate → location active, nothing done, 6 steps (cooler present)", () => {
  const v = computeWizard(BLANK);
  eq(v.activeId, "location", "active"); eq(v.doneCount, 0, "done"); eq(v.total, 6, "total"); eq(v.complete, false, "complete");
});
test("no cooler drops the cool step: total 5, frame is last", () => {
  const v = computeWizard({ ...BLANK, hasCooler: false });
  eq(v.total, 5, "total"); assert(!v.steps.some(s => s.id === "cool"), "cool absent");
  eq(v.steps[v.steps.length - 1].id, "frame", "frame last");
});
test("set location advances active to connect", () => {
  const v = computeWizard({ ...BLANK, siteIsDefault: false });
  eq(v.activeId, "connect", "active"); eq(v.doneCount, 1, "done");
});
test("order tolerance: connected but no location → location still active", () => {
  const v = computeWizard({ ...BLANK, equipConnected: true });
  eq(v.activeId, "location", "location first-incomplete"); eq(v.doneCount, 1, "connect counted done");
});
test("manual override honored only for applicable steps", () => {
  const v = computeWizard(BLANK, "target");
  eq(v.activeId, "target", "honored");
  const noCool = computeWizard({ ...BLANK, hasCooler: false }, "cool"); // cool not applicable
  eq(noCool.activeId, "location", "falls back to auto when override N/A");
});
test("all applicable done → complete, active is last step", () => {
  const done: WizardSnapshot = { siteIsDefault: false, equipConnected: true, profileCount: 1,
    targetCount: 2, hasCooler: true, coolerActive: true, frameCount: 1 };
  const v = computeWizard(done);
  eq(v.complete, true, "complete"); eq(v.doneCount, 6, "all done"); eq(v.activeId, "frame", "last");
});
test("no-cooler rig can complete without cooling", () => {
  const v = computeWizard({ siteIsDefault: false, equipConnected: true, profileCount: 1,
    targetCount: 1, hasCooler: false, coolerActive: false, frameCount: 1 });
  eq(v.complete, true, "complete on 5 steps"); eq(v.total, 5, "total");
});
test("WIZARD_STEPS never leaks the real backyard label", () => {
  const blob = JSON.stringify(WIZARD_STEPS);
  assert(!blob.includes("Backyard") && !blob.includes("[SITE-LAT]"), "no private site data in copy");
});

const total = passed + failed;
console.log(`\nfirstRunWizard.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));
export const result = { passed, failed, total };
