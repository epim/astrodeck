import { guidedModel, type GuidedSnapshot } from "../model";
import type { RigStatus } from "../../types";

let passed = 0, failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`${name}: ${e}`); } }
function assert(value: unknown, message: string) { if (!value) throw new Error(message); }
const snapshot = (over: Partial<GuidedSnapshot> = {}): GuidedSnapshot => ({
  status: { connected: {
    camera: { name: "Test camera", kind: "camera", connected: true },
    telescope: { name: "Test mount", kind: "mount", connected: true },
  }, looping: false } as RigStatus,
  site: { is_default: false, horizon_points: [[0, 15], [180, 20]] },
  fresh: true, sequence: { state: "idle" }, focus: null, polar: { state: "idle" }, ...over,
});
test("missing hardware starts with equipment rather than assuming a complete rig", () => {
  assert(guidedModel(snapshot({ status: null }), "first", "same").recommended.id === "equipment", "wrong first step");
});
test("connected camera and mount advance to a missing site", () => {
  assert(guidedModel(snapshot({ site: { is_default: true } }), "first", "same").recommended.id === "location", "site was skipped");
});
test("a saved skyline is never treated as verified physical alignment", () => {
  const model = guidedModel(snapshot({ focus: { state: "done" }, polar: { state: "done" } }), "returning", "same");
  assert(model.recommended.id === "horizon", "old results wrongly bypassed surroundings review");
  assert(model.steps.find((s) => s.id === "alignment")?.evidence.includes("must be checked"), "old polar completion became ready");
});
test("manual focus is an ordinary alternative without a connected focuser", () => {
  assert(guidedModel(snapshot(), "first", "same").steps.find((s) => s.id === "focus")?.description.includes("by hand"), "manual focus absent");
});
test("a focuser engine link can supply live connection evidence", () => {
  const s = snapshot();
  s.status!.backend_links = [{ role: "focuser", attempted: true, ok: true, error: null, connected: true }];
  assert(guidedModel(s, "first", "same").focuser, "backend-only live role ignored");
});
test("a disconnected engine link overrides an old connected device flag", () => {
  const s=snapshot();s.status!.backend_links=[{role:"telescope",attempted:true,ok:false,error:"Disconnected",connected:false}];
  const m=guidedModel(s,"returning","same");
  assert(!m.mount&&m.recommended.id==="equipment","stale connected flag overrode current engine link");
});
test("stale telemetry cannot report live hardware or fresh focus evidence", () => {
  const m = guidedModel(snapshot({ fresh: false, focus: { state: "done" } }), "first", "same");
  assert(!m.camera && !m.mount && m.recommended.id === "equipment", "stale hardware reported connected");
  assert(m.steps[0].evidence.includes("Waiting"), "missing stale explanation");
});
test("moving the rig asks for location review even with a saved site", () => {
  assert(guidedModel(snapshot(), "returning", "moved").recommended.id === "location", "moved rig trusted old site");
});
test("changed hardware asks for equipment review", () => {
  assert(guidedModel(snapshot(), "returning", "equipment").recommended.id === "equipment", "changed hardware was skipped");
});
for (const state of ["running", "paused", "holding", "aborting", "nina_native"] as const) {
  test(`a ${state} session keeps its monitor within reach`, () => {
    assert(guidedModel(snapshot({ sequence: { state } }), "first", "same").operations[0]?.view === "monitor", "live session hidden");
  });
}
test("concurrent reported operations all retain a destination", () => {
  const m = guidedModel(snapshot({ focus: { state: "running" }, polar: { state: "paused" }, sequence: { state: "holding" } }), "first", "same");
  assert(m.operations.length === 3, "one operation obscured another");
});
test("pausing polar alignment is still an operation, not a stopped telescope", () => {
  assert(guidedModel(snapshot({ polar: { state: "pausing" } }), "first", "same").operations[0]?.view === "polar", "pause request hid the operation");
});
test("reading the model does not mutate any shared state", () => {
  const s = snapshot(); const before = JSON.stringify(s);
  guidedModel(s, "returning", "equipment");
  assert(JSON.stringify(s) === before, "presentation changed the rig");
});
console.log(`guided model: ${passed}/${passed + failed} passed`);
for (const failure of failures) console.log(failure);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
