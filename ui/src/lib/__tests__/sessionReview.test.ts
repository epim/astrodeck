// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// sessionReview.test.ts — pure tests for lib/sessionReview.ts (review drawer
// filtering / selection / local override). Inline-assert harness via `npx tsx`.
import {
  effectiveAccepted, filterFrames, groupIdResolver, mosaicGroupsOf, mosaicRollup,
  pruneSelection, targetChoiceOf, toggleSel, verdictOf, withOverride, withTargetChoice,
} from "../sessionReview";
import type { SequencePlan, SessionFrame, Target, TargetGroup } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

let n = 0;
const frame = (over: Partial<SessionFrame> = {}): SessionFrame => ({
  id: `f${n++}`, ts: 0, night: "n1", target_id: "t1", step_id: "s1",
  thumb: null, metrics: { hfr: 2 }, auto_accepted: true, override: null, ...over,
});

test("verdict + effective acceptance", () => {
  assert(verdictOf(frame()) === "accepted", "auto accepted");
  assert(verdictOf(frame({ auto_accepted: false })) === "rejected", "auto rejected");
  assert(verdictOf(frame({ override: "reject" })) === "overridden", "override badge wins");
  assert(effectiveAccepted(frame({ auto_accepted: false, override: "accept" })), "override accept counts");
  assert(!effectiveAccepted(frame({ override: "reject" })), "override reject discounts");
});

test("filterFrames: target / night / verdict semantics", () => {
  const fs = [
    frame({ target_id: "t1", night: "n1" }),                          // accepted
    frame({ target_id: "t2", night: "n2", auto_accepted: false }),    // rejected
    frame({ target_id: "t1", night: "n2", override: "reject" }),      // overridden (eff. rejected)
  ];
  assert(filterFrames(fs, { target_id: "t1" }).length === 2, "target filter");
  assert(filterFrames(fs, { night: "n2" }).length === 2, "night filter");
  assert(filterFrames(fs, { verdict: "accepted" }).length === 1, "accepted = EFFECTIVE");
  assert(filterFrames(fs, { verdict: "rejected" }).length === 2, "rejected = EFFECTIVE (incl. override)");
  assert(filterFrames(fs, { verdict: "overridden" }).length === 1, "overridden = has override");
  assert(filterFrames(fs, {}).length === 3, "no filters = all");
});

test("toggleSel is a pure toggle", () => {
  let sel: string[] = [];
  sel = toggleSel(sel, "a");
  assert(sel.includes("a"), "added");
  const before = sel;
  sel = toggleSel(sel, "a");
  assert(!sel.includes("a") && before.includes("a"), "removed, input untouched");
});

test("pruneSelection keeps only ids in the visible set", () => {
  const fs = [frame(), frame()];
  const both = [fs[0].id, fs[1].id];
  assert(pruneSelection(both, fs).length === 2, "kept when visible");
  const pruned = pruneSelection([fs[0].id, fs[1].id], [fs[0]]);
  assert(pruned.length === 1 && pruned[0] === fs[0].id, "dropped when hidden");
  assert(pruneSelection([], fs).length === 0, "empty selection stays empty");
});

test("withOverride touches only the matching frame", () => {
  const fs = [frame(), frame()];
  const out = withOverride(fs, fs[0].id, "reject");
  assert(out[0].override === "reject", "target frame updated");
  assert(out[1] === fs[1], "other frame reference-equal");
  assert(fs[0].override === null, "input not mutated");
});

// ---------------------------------------------- filter band (UX #37)
test("filterFrames: filters by filter band via the step resolver", () => {
  const fs = [frame({ step_id: "ha" }), frame({ step_id: "oiii" }), frame({ step_id: "ha" })];
  const bandOf = (id: string) => (id === "ha" ? "Ha" : "OIII");
  const out = filterFrames(fs, { filter: "Ha" }, bandOf);
  assert(out.length === 2, `2 Ha frames, got ${out.length}`);
  assert(filterFrames(fs, { filter: "OIII" }, bandOf).length === 1, "1 OIII frame");
  assert(filterFrames(fs, { filter: "SII" }, bandOf).length === 0, "no SII frames");
});
test("filterFrames: band filter combines with the existing keys", () => {
  const fs = [frame({ step_id: "ha", night: "n1", auto_accepted: false }),
              frame({ step_id: "ha", night: "n2" }),
              frame({ step_id: "oiii", night: "n1" })];
  const bandOf = (id: string) => (id === "ha" ? "Ha" : "OIII");
  const out = filterFrames(fs, { filter: "Ha", night: "n1" }, bandOf);
  assert(out.length === 1 && out[0].night === "n1", "band AND night");
  assert(filterFrames(fs, { filter: "Ha", verdict: "accepted" }, bandOf).length === 1,
    "band AND verdict");
});
test("filterFrames: band filter is inert without a resolver (never hides all)", () => {
  const fs = [frame({ step_id: "ha" }), frame({ step_id: "oiii" })];
  assert(filterFrames(fs, { filter: "Ha" }).length === 2, "inert, not empty");
});
test("filterFrames: no band filter → resolver never narrows anything", () => {
  const fs = [frame({ step_id: "ha" }), frame({ step_id: "oiii" })];
  assert(filterFrames(fs, {}, () => "Ha").length === 2, "unfiltered");
});

// ------------------------------------------- mosaic grouping (#188, WP-1)
// A session's plan is a frozen snapshot WITH ids. A mosaic's panels are N
// targets named "<name> r-c" that share a `mosaic_group` and carry their 0-based
// `panel_row`/`panel_col`; the plan's `groups` entry (written by the flow
// compile) names the block. Every name here is made up.
//
// NAMED MUTANTS (WP-108), each applied as a one-line source change from a byte
// backup of lib/sessionReview.ts, run under `npx tsx` on this file and on
// components/__tests__/sessionReviewDom.test.tsx, then restored byte-identical.
// The first failing assertion of each, verbatim:
//   "group by target name" (`const key = t.mosaic_group;` -> `t.name`), 14 of 26
//     x mosaicGroupsOf: a 2x2 plan is ONE group with four panels in grid order:
//       a 2x2 mosaic is one group, got 0
//   "count rejected as accepted" (`effectiveAccepted(f)` -> `f.auto_accepted`
//     in mosaicRollup)
//     x mosaicRollup: counts per panel, per group, from the EFFECTIVE verdict:
//       a regrade moves a frame between the counts, got 1/2
//   "group filter inert" (the group_id test in filterFrames gets `false &&`)
//     x filterFrames: group_id returns every panel's frames and nothing else:
//       four panels' frames, got 6
//   "panels in plan order" (drop `.sort(byGridPlace)`)
//     x mosaicGroupsOf: a 2x2 plan is ONE group with four panels in grid order:
//       labels in grid order, got 2-2,1-1,2-1,1-2
//   "group choice keeps the panel" (withTargetChoice's group branch stops
//     clearing target_id)
//     x targetChoice: a group choice and a target choice are exclusive filter
//       keys: a group choice clears the panel, or the filter reads 'this panel
//       AND this mosaic'
//   "empty group offered" (drop the panel-count filter in mosaicGroupsOf)
//     x mosaicGroupsOf: a group entry with no member targets is not offered:
//       a header with no rows is not a roll-up
//   "legacy single is a mosaic" (a string shared by one target counts)
//     x mosaicGroupsOf: a legacy string on ONE target is a single, a written
//       group of one is not: the Plan itself needs more than one panel before
//       it calls a string a mosaic
const tgt = (id: string, name: string, over: Partial<Target> = {}): Target => ({
  id, name, ra_hours: 1, dec_deg: 2, center: true, autofocus_first: false,
  calibration: false, steps: [], ...over,
});
const grp = (id: string, name: string): TargetGroup => ({
  id, name, kind: "mosaic", mode: "rotate", visit_passes: 1, visit_min_s: 0,
  order: "grid", require_centred: true, max_failed_visits: 3, pa_deg: null,
  rotate: false, angle_tolerance_deg: null, skipped_ids: [], geometry: {},
});
const panel = (id: string, group: string, row: number, col: number, over: Partial<Target> = {}) =>
  tgt(id, `Veil ${row + 1}-${col + 1}`, { mosaic_group: group, panel_row: row, panel_col: col, ...over });
const planOf = (targets: Target[], groups?: TargetGroup[]): SequencePlan =>
  ({ name: "p", targets, groups } as unknown as SequencePlan);

// Listed OUT of grid order on purpose: plan order is the order the flow wrote
// the targets in (a snake, a setting-first order), not the order a person reads
// a 2x2 in.
const MOSAIC_2X2 = [
  panel("p22", "g1", 1, 1), panel("p11", "g1", 0, 0),
  panel("p21", "g1", 1, 0), panel("p12", "g1", 0, 1),
];

test("mosaicGroupsOf: a 2x2 plan is ONE group with four panels in grid order", () => {
  const groups = mosaicGroupsOf(planOf(MOSAIC_2X2, [grp("g1", "Veil")]));
  assert(groups.length === 1, `a 2x2 mosaic is one group, got ${groups.length}`);
  assert(groups[0].id === "g1" && groups[0].name === "Veil", "group id and name come from plan.groups");
  assert(groups[0].panels.map((p) => p.label).join(",") === "1-1,1-2,2-1,2-2",
    `labels in grid order, got ${groups[0].panels.map((p) => p.label)}`);
  assert(groups[0].panels.map((p) => p.target_id).join(",") === "p11,p12,p21,p22",
    "each panel row names its own target id");
  assert(groups[0].panels[1].row === 0 && groups[0].panels[1].col === 1, "row and col carried");
});

test("mosaicGroupsOf: singles and plans with no groups produce no group", () => {
  assert(mosaicGroupsOf(planOf([tgt("a", "M31"), tgt("b", "M42")])).length === 0, "two singles");
  assert(mosaicGroupsOf(planOf([])).length === 0, "empty plan");
  assert(mosaicGroupsOf(planOf([tgt("a", "M31", { mosaic_group: "" })])).length === 0,
    "an empty mosaic_group string is no group");
});

test("mosaicGroupsOf: a single target beside a mosaic stays out of the group", () => {
  const groups = mosaicGroupsOf(planOf([...MOSAIC_2X2, tgt("solo", "M31")], [grp("g1", "Veil")]));
  assert(groups.length === 1 && groups[0].panels.length === 4, "solo is not a panel");
  assert(!groups[0].panels.some((p) => p.target_id === "solo"), "solo absent from the panels");
});

test("mosaicGroupsOf: two mosaics are two groups, each with its own panels", () => {
  const second = [panel("q11", "g2", 0, 0), panel("q12", "g2", 0, 1)];
  const groups = mosaicGroupsOf(planOf([...MOSAIC_2X2, ...second],
    [grp("g1", "Veil"), grp("g2", "Cygnus")]));
  assert(groups.length === 2, `two groups, got ${groups.length}`);
  assert(groups[0].panels.length === 4 && groups[1].panels.length === 2, "panels split by group id");
});

test("mosaicGroupsOf: a legacy Plan-UI mosaic (no groups entry) groups by its string", () => {
  const plan = planOf([
    tgt("a", "M31 NW", { mosaic_group: "M31" }), tgt("b", "M31 SE", { mosaic_group: "M31" }),
    tgt("c", "M42"),
  ]);
  const groups = mosaicGroupsOf(plan);
  assert(groups.length === 1, `one legacy group, got ${groups.length}`);
  assert(groups[0].id === "M31" && groups[0].name === "M31", "id and name are the string");
  assert(groups[0].panels.map((p) => p.label).join("|") === "M31 NW|M31 SE",
    "a legacy panel is labelled with the target's own name");
  assert(groups[0].panels[0].row === null && groups[0].panels[0].col === null, "no grid place");
});

test("mosaicGroupsOf: the group id, not the name, is the key (a rename changes nothing)", () => {
  const before = mosaicGroupsOf(planOf(MOSAIC_2X2, [grp("g1", "Veil")]));
  const after = mosaicGroupsOf(planOf(MOSAIC_2X2, [grp("g1", "Veil Nebula")]));
  assert(before[0].id === after[0].id, "same id across the rename");
  assert(after[0].name === "Veil Nebula", "the name follows the plan");
  assert(after[0].panels.length === 4, "members found by the id");
});

test("mosaicGroupsOf: an empty group name falls back to its id", () => {
  const groups = mosaicGroupsOf(planOf(MOSAIC_2X2, [grp("g1", "  ")]));
  assert(groups[0].name === "g1", `fell back to the id, got "${groups[0].name}"`);
});

test("mosaicGroupsOf: a legacy string on ONE target is a single, a written group of one is not", () => {
  assert(mosaicGroupsOf(planOf([tgt("a", "M31", { mosaic_group: "M31" }), tgt("b", "M42")])).length === 0,
    "the Plan itself needs more than one panel before it calls a string a mosaic");
  const one = mosaicGroupsOf(planOf([panel("p11", "g1", 0, 0)], [grp("g1", "Veil")]));
  assert(one.length === 1 && one[0].panels.length === 1,
    "a group the compile wrote is a mosaic whatever its size (the others may be skipped panels)");
});

test("mosaicGroupsOf: a target with no id cannot own a frame and is not a panel", () => {
  const noId = { ...panel("x", "g1", 0, 0), id: undefined } as unknown as Target;
  const groups = mosaicGroupsOf(planOf([noId, panel("p12", "g1", 0, 1)], [grp("g1", "Veil")]));
  assert(groups[0].panels.length === 1 && groups[0].panels[0].target_id === "p12", "id-less skipped");
});

test("mosaicGroupsOf: a panel without a grid place sorts last and is named by its target", () => {
  const plan = planOf([
    tgt("x", "Odd panel", { mosaic_group: "g1" }), panel("p12", "g1", 0, 1), panel("p11", "g1", 0, 0),
  ], [grp("g1", "Veil")]);
  const labels = mosaicGroupsOf(plan)[0].panels.map((p) => p.label);
  assert(labels.join("|") === "1-1|1-2|Odd panel", `got ${labels.join("|")}`);
});

test("mosaicGroupsOf: a group entry with no member targets is not offered", () => {
  const groups = mosaicGroupsOf(planOf([tgt("a", "M31")], [grp("ghost", "Gone")]));
  assert(groups.length === 0, "a header with no rows is not a roll-up");
});

test("filterFrames: group_id returns every panel's frames and nothing else", () => {
  const plan = planOf([...MOSAIC_2X2, tgt("solo", "M31")], [grp("g1", "Veil")]);
  const groupOf = groupIdResolver(mosaicGroupsOf(plan));
  const fs = [
    frame({ target_id: "p11" }), frame({ target_id: "p12" }),
    frame({ target_id: "p21" }), frame({ target_id: "p22" }),
    frame({ target_id: "solo" }), frame({ target_id: "gone" }),
  ];
  const out = filterFrames(fs, { group_id: "g1" }, undefined, groupOf);
  assert(out.length === 4, `four panels' frames, got ${out.length}`);
  assert(out.every((f) => f.target_id.startsWith("p")), "no solo or unknown-target frame");
});

test("filterFrames: group_id combines with the other keys and is inert without a resolver", () => {
  const groupOf = groupIdResolver(mosaicGroupsOf(planOf(MOSAIC_2X2, [grp("g1", "Veil")])));
  const fs = [
    frame({ target_id: "p11", night: "n1" }), frame({ target_id: "p12", night: "n2" }),
    frame({ target_id: "solo", night: "n1" }),
  ];
  const out = filterFrames(fs, { group_id: "g1", night: "n1" }, undefined, groupOf);
  assert(out.length === 1 && out[0].target_id === "p11", "group AND night");
  assert(filterFrames(fs, { group_id: "g1" }).length === 3, "inert, not empty, without a resolver");
});

test("groupIdResolver: a target in no group answers undefined", () => {
  const groupOf = groupIdResolver(mosaicGroupsOf(planOf(MOSAIC_2X2, [grp("g1", "Veil")])));
  assert(groupOf("p11") === "g1", "a panel answers its group id");
  assert(groupOf("solo") === undefined, "a single answers undefined");
});

test("targetChoice: a group choice and a target choice are exclusive filter keys", () => {
  const withGroup = withTargetChoice({ night: "n1" }, "group:g1");
  assert(withGroup.group_id === "g1" && withGroup.target_id === undefined, "group choice");
  assert(withGroup.night === "n1", "the other keys survive");
  const withPanel = withTargetChoice(withGroup, "p11");
  assert(withPanel.target_id === "p11" && withPanel.group_id === undefined,
    "a panel choice clears the group");
  const regrouped = withTargetChoice(withPanel, "group:g1");
  assert(regrouped.group_id === "g1" && regrouped.target_id === undefined,
    "a group choice clears the panel, or the filter reads 'this panel AND this mosaic'");
  const cleared = withTargetChoice(withPanel, "");
  assert(cleared.target_id === undefined && cleared.group_id === undefined, "all targets clears both");
  assert(targetChoiceOf(withGroup) === "group:g1", "choice value of a group filter");
  assert(targetChoiceOf(withPanel) === "p11", "choice value of a panel filter");
  assert(targetChoiceOf({}) === "", "choice value of no filter");
});

test("mosaicRollup: counts per panel, per group, from the EFFECTIVE verdict", () => {
  const groups = mosaicGroupsOf(planOf(MOSAIC_2X2, [grp("g1", "Veil")]));
  const fs = [
    frame({ target_id: "p11" }),                                      // accepted
    frame({ target_id: "p11", auto_accepted: false }),                // rejected
    frame({ target_id: "p12", override: "reject" }),                  // auto-accepted, regraded OUT
    frame({ target_id: "p12", auto_accepted: false, override: "accept" }), // auto-rejected, regraded IN
    frame({ target_id: "p12", auto_accepted: false, override: "accept" }),
    frame({ target_id: "solo" }),                                     // not in the mosaic
  ];
  const [g] = mosaicRollup(groups, fs);
  const by = (id: string) => g.panels.find((p) => p.target_id === id)!;
  assert(by("p11").accepted === 1 && by("p11").rejected === 1, "p11 counts");
  assert(by("p12").accepted === 2 && by("p12").rejected === 1,
    `a regrade moves a frame between the counts, got ${by("p12").accepted}/${by("p12").rejected}`);
  assert(g.accepted === 3 && g.rejected === 2, `group sums, got ${g.accepted}/${g.rejected}`);
});

test("mosaicRollup: a panel with no frames still has a row, at zero", () => {
  const groups = mosaicGroupsOf(planOf(MOSAIC_2X2, [grp("g1", "Veil")]));
  const [g] = mosaicRollup(groups, [frame({ target_id: "p11" })]);
  assert(g.panels.length === 4, "all four panels present");
  const idle = g.panels.find((p) => p.target_id === "p22")!;
  assert(idle.accepted === 0 && idle.rejected === 0, "set-aside panel reads 0 and 0");
});

console.log(`sessionReview.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
