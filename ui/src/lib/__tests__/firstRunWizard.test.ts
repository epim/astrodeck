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

// --- copy contract for the DOCKED BAR (phone feedback) -------------------
// The bar shows one step at a time, so each step's own text has to carry the
// whole answer to "what now?": WHERE to go and WHY Next is still grey. These
// assert the two properties a reviewer cannot eyeball across six strings.

test("every step names the view it sends you to, and where on it", () => {
  // The view name a novice reads on the nav, per step id.
  const WHERE: Record<string, string> = {
    location: "Settings", connect: "Equipment", profile: "Equipment",
    target: "Atlas", cool: "Capture", frame: "Capture",
  };
  for (const s of WIZARD_STEPS) {
    assert(s.body.includes(WHERE[s.id]), `${s.id} body must name ${WHERE[s.id]}`);
  }
  // The step that produced the complaint ("I need to scroll to the bottom of
  // the page, which is not easy for a user to know about unless we tell them")
  // must say so out loud.
  const cool = WIZARD_STEPS.find((s) => s.id === "cool")!;
  assert(/scroll/i.test(cool.body) && /bottom/i.test(cool.body),
    "cool step must tell the user to scroll to the BOTTOM of Capture");
});

test("every step states what Next is waiting for, and only 'location' mentions the default site", () => {
  for (const s of WIZARD_STEPS) {
    assert(s.need.trim().length > 0, `${s.id} needs a stated reason for a locked Next`);
    // Rendered as "Next unlocks once <need>." — so it must not start a sentence.
    assert(s.need[0] === s.need[0].toLowerCase(), `${s.id} need must be a lowercase clause`);
    if (s.id !== "location") {
      assert(!/\(0,\s*0\)|My Observatory/.test(s.body + s.need),
        `${s.id} must not reference the default site`);
    }
  }
  const loc = WIZARD_STEPS.find((s) => s.id === "location")!;
  assert(/\(0,\s*0\)/.test(loc.body), "location step should still name the default (0, 0) site");
});

test("Next's gate is the step's own `done` flag, not a second source of truth", () => {
  // The bar wires Next to view.steps[activeIndex].done. Flipping ONLY the
  // signal for the active step must be what ungreys it.
  const v0 = computeWizard({ ...BLANK, targetCount: 0 }, "target");
  assert(!v0.steps[v0.activeIndex].done, "target not done with an empty plan");
  const v1 = computeWizard({ ...BLANK, targetCount: 1 }, "target");
  assert(v1.steps[v1.activeIndex].done, "target done the moment a target lands in the plan");
});

const total = passed + failed;
console.log(`\nfirstRunWizard.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));
export const result = { passed, failed, total };
