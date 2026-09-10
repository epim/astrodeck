// setupSteps.test.ts - the FIRST-TIME SETUP machine (T-SET-1, plan C.2.1).
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/setupSteps.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Pure: no jsdom, no store, no router. `computeSetup` calls the real
// `computeWizard`, so these cases also pin the mapping between the design's
// five steps and the product's six.
//
// What each test guards, and what goes RED if the guard is removed:
//   - "counts", "next names the FIRST incomplete": if `next` were changed to
//     `findLast` (or to the last step), the M31-shaped case below fails on the
//     title, because two steps are incomplete and they are not adjacent.
//   - "a device disappearing re-opens step 2": if the step ever cached its own
//     done flag instead of reading `computeWizard` off the live snapshot, this
//     case ticks and the assertion fails. It is the case the design's own
//     footer sentence promises.
//   - "cooling is named only when the camera can cool": if the `hasCooler`
//     branch were dropped, the uncooled case's sub gains the word "cooling"
//     and the `!/cooling/` assertion fails.
//   - "the target step is tracked but is not a gate": if `light.done` were
//     changed to also require `targetCount > 0`, the assertion that a frame
//     alone finishes step 5 fails.
//   - "GO / REVIEW": if the act word stopped following `done`, both assertions
//     fail at once - which is what tells you it is the mapping and not one row.
//   - route mapping: if a `go` descriptor were retargeted (say OPTICS to
//     /rig/devices), that step's assertion fails by name.

import { computeSetup, nextLine, type SetupSnapshot } from "../general/setupSteps";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

/** A rig with nothing set up at all. Every case below is this, patched. */
const BLANK: SetupSnapshot = {
  // wizard half
  siteIsDefault: true,
  equipConnected: false,
  profileCount: 0,
  targetCount: 0,
  hasCooler: false,
  coolerActive: false,
  frameCount: 0,
  // new half
  linkUp: false,
  identityResolved: false,
  email: null,
  reach: "direct",
  deviceCount: 0,
  profileName: null,
  haveOptics: false,
  fovW: null,
  fovH: null,
  siteName: null,
  horizonSummary: null,
};

const snap = (over: Partial<SetupSnapshot>): SetupSnapshot => ({ ...BLANK, ...over });

const stepOf = (s: SetupSnapshot, id: string) => {
  const st = computeSetup(s).steps.find((x) => x.id === id);
  if (!st) throw new Error(`no step ${id} - the machine dropped a design step`);
  return st;
};

// ------------------------------------------------------------ 1. blank slate
test("a blank rig is 0 of 5 and points at PAIR THE RIG COMPUTER", () => {
  const v = computeSetup(BLANK);
  eq(v.doneCount, 0, "doneCount:");
  eq(v.complete, false, "complete:");
  eq(v.steps.length, 5, "the design has five steps:");
  eq(v.next?.id, "pair", "first incomplete:");
  assert(/pair the rig computer/.test(nextLine(v)), `nextLine was ${nextLine(v)}`);
});

// ---------------------------------------- 2. three done, two not, non-adjacent
const THREE = snap({
  linkUp: true, identityResolved: true, email: "owner@example.test", reach: "relay",
  equipConnected: false, profileCount: 0, deviceCount: 0,          // step 2 NOT done
  haveOptics: true, fovW: 2.54, fovH: 1.7,                          // step 3 done
  siteIsDefault: false, siteName: "Back lawn", horizonSummary: "13 points · up to 38°",
  frameCount: 0,                                                    // step 5 NOT done
});

test("three of five renders 3 and names the FIRST incomplete step", () => {
  const v = computeSetup(THREE);
  eq(v.doneCount, 3, "doneCount:");
  eq(v.next?.id, "devices", "the first incomplete step:");
  assert(
    nextLine(v).startsWith("next: connect the devices"),
    `the card's sub named the wrong step: ${nextLine(v)}`,
  );
});

test("the pair step names the origin it is actually on", () => {
  const st = stepOf(THREE, "pair");
  eq(st.done, true, "pair done:");
  assert(st.sub.includes("the relay"), `relay tab printed: ${st.sub}`);
  assert(st.sub.includes("owner@example.test"), `signed-in identity printed: ${st.sub}`);
  const direct = stepOf(snap({ ...THREE, reach: "direct", email: null }), "pair");
  assert(direct.sub.includes("direct Wi-Fi"), `direct tab printed: ${direct.sub}`);
  assert(direct.sub.includes("this device"), `open-LAN identity printed: ${direct.sub}`);
});

test("a reachable rig with no identity yet is not the same as an unreachable one", () => {
  const noLink = stepOf(snap({ linkUp: false, identityResolved: true }), "pair");
  eq(noLink.sub, "the phone is not reaching the rig", "no link:");
  const noId = stepOf(snap({ linkUp: true, identityResolved: false }), "pair");
  eq(noId.sub, "reaching the rig, but nobody is signed in yet", "no identity:");
});

// ------------------------------------- 3. connect + profile are ONE design step
test("step 2 needs BOTH a connected rig and a saved profile", () => {
  const connectedOnly = stepOf(snap({ equipConnected: true, deviceCount: 7 }), "devices");
  eq(connectedOnly.done, false, "connected but unprofiled:");
  assert(/no profile saved yet/.test(connectedOnly.sub), `sub was ${connectedOnly.sub}`);

  const both = stepOf(
    snap({ equipConnected: true, profileCount: 1, deviceCount: 7, profileName: "Backyard" }),
    "devices",
  );
  eq(both.done, true, "connected and profiled:");
  eq(both.sub, "7 devices in the Backyard profile", "sub:");
});

test("a device disappearing re-opens step 2 even though the profile is still saved", () => {
  // The design's own footer sentence: "Steps re-open if a device disappears".
  // The profile does not go away when the USB cable does.
  const gone = stepOf(
    snap({ equipConnected: false, profileCount: 1, profileName: "Backyard" }),
    "devices",
  );
  eq(gone.done, false, "step 2 after the rig dropped:");
  eq(gone.sub, "no devices connected", "sub:");
  eq(gone.act, "GO ›", "act word:");
});

// -------------------------------------------------- 4. the cool step, and only
test("cooling is named in step 5 only when the camera can cool", () => {
  const uncooled = stepOf(snap({ hasCooler: false }), "light");
  assert(!/cooling/.test(uncooled.sub), `an uncooled rig was told to prove cooling: ${uncooled.sub}`);
  const cooled = stepOf(snap({ hasCooler: true }), "light");
  assert(/cooling/.test(cooled.sub), `a cooled rig's step 5 never mentions it: ${cooled.sub}`);
});

// ---------------------------------------- 5. the target step is NOT a fifth gate
test("the target step is tracked but never gates FIRST LIGHT", () => {
  const s = snap({ frameCount: 1, targetCount: 0 });
  const light = stepOf(s, "light");
  eq(light.done, true, "a frame alone finishes step 5:");
  eq(light.sub, "done - a frame is in the gallery", "sub:");
  eq(computeSetup(s).targetInPlan, false, "targetInPlan is reported, not gated:");
  eq(computeSetup(snap({ targetCount: 2 })).targetInPlan, true, "targetInPlan when a plan has one:");
});

// ---------------------------------------------------- 6. optics and the site
test("optics prints the computed field only when the server computed one", () => {
  eq(stepOf(snap({ haveOptics: false }), "optics").sub,
    "the frame size is unknown", "no optics:");
  eq(stepOf(snap({ haveOptics: true, fovW: 2.54, fovH: 1.7 }), "optics").sub,
    "focal length, aperture, pixel size → 2.54° × 1.70°", "with a field:");
  eq(stepOf(snap({ haveOptics: true }), "optics").sub,
    "focal length and pixel size are set", "optics set but no field on the wire:");
});

test("the site step says the default is not a real sky, and names the line when drawn", () => {
  eq(stepOf(BLANK, "site").sub, "using the default location (0, 0)", "default site:");
  eq(stepOf(snap({ siteIsDefault: false, siteName: "Back lawn" }), "site").sub,
    "Back lawn · no horizon drawn yet", "site with no horizon:");
  eq(stepOf(snap({ siteIsDefault: false, siteName: "Back lawn", horizonSummary: "13 points · up to 38°" }), "site").sub,
    "Back lawn · 13 points · up to 38°", "site with a horizon:");
});

// ------------------------------------------------------- 7. act words + routes
test("GO while unfinished, REVIEW once done", () => {
  eq(stepOf(BLANK, "optics").act, "GO ›", "unfinished:");
  eq(stepOf(snap({ haveOptics: true }), "optics").act, "REVIEW", "finished:");
});

test("each step's GO lands where the plan says", () => {
  const v = computeSetup(BLANK);
  const go = (id: string) => v.steps.find((s) => s.id === id)!.go;
  eq(JSON.stringify(go("pair")), '{"kind":"sheet","name":"connection"}', "step 1:");
  eq(JSON.stringify(go("devices")), '{"kind":"route","path":"/rig/devices"}', "step 2:");
  eq(JSON.stringify(go("optics")), '{"kind":"sheet","name":"optics"}', "step 3:");
  eq(JSON.stringify(go("site")), '{"kind":"sheet","name":"sites"}', "step 4:");
  eq(JSON.stringify(go("light")), '{"kind":"route","path":"/rig/capture"}', "step 5:");
});

// -------------------------------------------------------------- 8. all five
test("five of five is complete, has no next step, and every act is REVIEW", () => {
  const v = computeSetup(snap({
    linkUp: true, identityResolved: true, email: "owner@example.test",
    equipConnected: true, profileCount: 1, deviceCount: 7, profileName: "Backyard",
    haveOptics: true, fovW: 2.54, fovH: 1.7,
    siteIsDefault: false, siteName: "Back lawn", horizonSummary: "13 points · up to 38°",
    hasCooler: true, coolerActive: true, frameCount: 4, targetCount: 1,
  }));
  eq(v.doneCount, 5, "doneCount:");
  eq(v.complete, true, "complete:");
  eq(v.next, null, "next:");
  eq(nextLine(v), "all five done", "card sub at 5/5:");
  assert(v.steps.every((s) => s.act === "REVIEW"), "a finished step still says GO");
});

// --------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`setupSteps.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export default { passed, failed, total };
export { passed, failed, total };
