// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// selectTestsRatchet.test.ts - the ratchet behind `run-tests.mjs --changed`
// (#1258): a test file that reads from disk is invisible to the import graph,
// so it must always run, until it declares what it reads.
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/__tests__/selectTestsRatchet.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT FAILS HERE, AND WHAT TO DO.
//
//   "undeclared disk readers that are not on the list": a test file you added
//   (or changed) reads a file by an fs call or by a path built from
//   import.meta.url, __dirname or process.cwd(). Add a line to it naming what
//   it reads, with repo-root-relative paths:
//
//       // disk-reads: ui/src/components/Foo.tsx, server/tests/fixtures/
//
//   (a trailing "/" means every file under that directory). The test then runs
//   when one of those paths changes, and no longer on every change. Do NOT add
//   the file to UNDECLARED below: that list only shrinks.
//
//   "declared, so remove them from the list": a file on UNDECLARED now declares
//   its reads (good: delete its line from the list).
//
//   "declared paths that do not exist": a `disk-reads:` line names a path that
//   is not in the tree; fix the path, because a declaration that names nothing
//   keeps the test from running when the thing it really reads changes.
//
// The list was taken from the tree at WP 7.1: the 130 files that call
// readFileSync or readdirSync, and 15 more that the rule finds because they
// build a path from import.meta.url, import fs, or import a helper that reads
// (imports are followed). Converting one is its own change.
//
// Whether the rule finds a reader at all, and what a declaration does, is
// graded on a made-up tree in selectTests.test.ts; this file grades the real one.
//
// disk-reads: ui/src/, ui/tools/selectTests.mjs

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

interface Graph {
  tests: string[];
  declared: Map<string, string[]>;
}
interface SelectModule {
  buildGraph(root: string): Graph;
  undeclaredReaders(graph: Graph): string[];
  missingDeclaredPaths(graph: Graph, root: string): string[];
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const { buildGraph, undeclaredReaders, missingDeclaredPaths } =
  (await nodeImport(new URL("../../tools/selectTests.mjs", import.meta.url).href)) as SelectModule;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// Paths below are relative to ui/src. Sorted. Only ever shrinks.
const UNDECLARED: string[] = [
  "__tests__/cspBootstrap.test.ts",
  "__tests__/cssClasses.test.ts",
  "__tests__/deletedDoorStrings.test.ts",
  "__tests__/doorsConverge.test.tsx",
  "__tests__/nav.test.ts",
  "__tests__/noSiteParamsInQueryStrings.test.ts",
  "__tests__/overlay.test.ts",
  "__tests__/planPolicyDefaults.test.ts",
  "__tests__/runTests.test.ts",
  "__tests__/runTestsCss.test.ts",
  "__tests__/sourceEncoding.test.ts",
  "__tests__/w13CleanTallyTimeoutRetry.test.ts",
  "__tests__/w14ChildPhaseMarkers.test.ts",
  "__tests__/w15ChildExitReport.test.ts",
  "__tests__/w4Issue1184StarvedChild.test.ts",
  "__tests__/w5RunTestsTimeoutPhase.test.ts",
  "__tests__/weatherHoldClaim.test.ts",
  "__tests__/weatherHoldClaimPanels.test.ts",
  "components/__tests__/galleryTile.test.tsx",
  "components/__tests__/ledBesideItsWord.test.tsx",
  "components/__tests__/previewTierDom.test.tsx",
  "components/__tests__/w16RotatorCardSequenceLock.test.tsx",
  "components/atlas/__tests__/mosaicNightHeading.test.tsx",
  "components/atlas/__tests__/panelLayer.test.tsx",
  "components/atlas/__tests__/skyCanvasLegend.test.tsx",
  "components/atlas/__tests__/skyCanvasPanels.test.tsx",
  "components/flows/__tests__/classicFrameHost.test.tsx",
  "components/flows/__tests__/countsNotice.test.ts",
  "components/flows/__tests__/flowHeaderText.test.ts",
  "components/flows/__tests__/flowLoopParity.test.ts",
  "components/flows/__tests__/flowRunState.test.ts",
  "components/flows/__tests__/flowSettingsParity.test.ts",
  "components/flows/__tests__/flowsSaveAnswer.test.ts",
  "components/flows/__tests__/flowsSlice.test.ts",
  "components/flows/__tests__/loopArc.test.ts",
  "components/flows/__tests__/loopChipCurrent.test.ts",
  "components/flows/__tests__/nodeDefs.test.ts",
  "components/flows/__tests__/palette.test.ts",
  "components/flows/__tests__/panelLane.test.ts",
  "components/flows/__tests__/replayNotice.test.tsx",
  "components/flows/__tests__/runCopy.test.ts",
  "components/flows/__tests__/runModeAcrossSave.test.tsx",
  "components/flows/__tests__/tonightPanelDom.test.tsx",
  "components/flows/__tests__/w16RunCopyOff.test.ts",
  "components/flows/__tests__/w16RunNoticeSurfaces.test.tsx",
  "components/flows/__tests__/w2_5RunCopyOwesMore.test.ts",
  "components/flows/__tests__/w3_10TimeBound.test.tsx",
  "components/flows/__tests__/w4FlowNodeTickComments.test.ts",
  "components/flows/__tests__/w4LoopWireConsumedCommentMatches.test.ts",
  "components/flows/__tests__/w4_1aQuickChips.test.tsx",
  "components/flows/__tests__/w4_2StageStatusReplay.test.tsx",
  "components/flows/__tests__/w6QuickFlowUnguidedNote.test.tsx",
  "components/flows/__tests__/whenWaitingSelectFits.test.tsx",
  "components/flows/__tests__/wp15uBridgeConstant.test.ts",
  "components/flows/framing/__tests__/framingLayout.test.tsx",
  "components/flows/framing/__tests__/framingModel.test.ts",
  "components/flows/framing/__tests__/framingReadoutsFixture.test.ts",
  "components/flows/framing/__tests__/framingSections.test.tsx",
  "components/flows/framing/__tests__/framingSkyDegraded.test.tsx",
  "components/flows/framing/__tests__/panelOrderFixture.test.ts",
  "components/flows/framing/__tests__/runMode.test.tsx",
  "components/flows/framing/__tests__/targetFramingSheet.test.tsx",
  "components/flows/framing/__tests__/typedCoordinatesFixture.test.ts",
  "components/flows/framing/__tests__/w15RetrySetAside.test.tsx",
  "components/flows/framing/__tests__/w16DormantRetryOnTheSheet.test.tsx",
  "components/flows/framing/__tests__/w5PanelOverridesModel.test.tsx",
  "components/flows/framing/__tests__/w6_5CoverageMap.test.tsx",
  "components/flows/wizard/__tests__/sendToWizardSheet.test.tsx",
  "components/flows/wizard/__tests__/wizardModel.test.ts",
  "components/sequence/__tests__/planLibraryUnreadable.test.tsx",
  "components/sequence/__tests__/sessionsPanelNightsDom.test.tsx",
  "components/sequence/__tests__/w17SessionsPanelArmedChip.test.tsx",
  "components/settings/__tests__/standardsPanel.test.ts",
  "lib/__tests__/atlasFov.test.ts",
  "lib/__tests__/gallery.test.ts",
  "lib/__tests__/healpix.test.ts",
  "lib/__tests__/humanizeRewrites.test.ts",
  "lib/__tests__/laneConflict.test.ts",
  "lib/__tests__/lazyViews.test.ts",
  "lib/__tests__/tileParity.test.ts",
  "lib/__tests__/troubleshoot.test.ts",
  "lib/__tests__/w5NamingCaptureTokens.test.ts",
  "next/__tests__/hubMeta.test.ts",
  "next/__tests__/r7Css.test.ts",
  "next/__tests__/r7Disabled.test.ts",
  "next/__tests__/r7Parity.test.ts",
  "next/__tests__/shellCss.test.ts",
  "next/__tests__/u7bMarkers.test.ts",
  "next/hubs/rig/__tests__/rigFocuserDom.test.tsx",
  "next/hubs/rig/__tests__/rigMountDom.test.tsx",
  "next/hubs/rig/profiles/__tests__/rigProfilesDom.test.tsx",
  "next/hubs/rig/rotator/__tests__/w16RotatorSequenceLock.test.tsx",
  "next/hubs/rig/sheets/__tests__/w14GuiderSettleNative.test.tsx",
  "next/hubs/session/__tests__/crossHub.test.ts",
  "next/hubs/session/__tests__/nowChannelDom.test.tsx",
  "next/hubs/session/__tests__/nowDom.test.tsx",
  "next/hubs/session/__tests__/nowIncidentActions.test.ts",
  "next/hubs/session/__tests__/sessionSheets.test.tsx",
  "next/hubs/session/flows/__tests__/flowsDom.test.tsx",
  "next/hubs/session/flows/canvas/__tests__/canvasDom.test.tsx",
  "next/hubs/session/flows/canvas/__tests__/loopArcNext.test.tsx",
  "next/hubs/session/flows/canvas/__tests__/phoneReadouts.test.tsx",
  "next/hubs/session/flows/create/__tests__/createDom.test.tsx",
  "next/hubs/session/flows/create/__tests__/quickUnguidedLimit.test.tsx",
  "next/hubs/session/flows/framing/__tests__/flowFrameSheet.test.tsx",
  "next/hubs/session/flows/tonight/__tests__/budgetForTheseTargets.test.tsx",
  "next/hubs/session/flows/tonight/__tests__/tonightMosaicBand.test.ts",
  "next/hubs/session/flows/wizard/__tests__/flowWizardSheet.test.tsx",
  "next/hubs/session/now/__tests__/nowUnreadablePlan.test.tsx",
  "next/hubs/session/now/__tests__/sessionColumnFlowRun.test.tsx",
  "next/hubs/session/sheets/__tests__/archiveDom.test.tsx",
  "next/hubs/settings/__tests__/connectionOpticsDom.test.tsx",
  "next/hubs/settings/__tests__/filesSheetsDom.test.tsx",
  "next/hubs/settings/__tests__/settingsDom.test.tsx",
  "next/hubs/sky/__tests__/mosaicCopyPanelFirst.test.ts",
  "next/hubs/sky/sheets/__tests__/horizonEditorFloor.test.ts",
  "next/hubs/sky/sheets/__tests__/photosphereReplay.test.ts",
  "next/hubs/sky/sheets/__tests__/photosphereVertical.test.ts",
  "next/hubs/sky/sheets/__tests__/replaySeam.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/align.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/cameraSource.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/focal.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/liveFrame.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/panoFixtureReplay.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/recorder.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/report.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/rotation.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/scanViewDom.test.tsx",
  "next/hubs/sky/sheets/pano/__tests__/scannerV0.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/scannerV1.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/scannerV1Closure.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/scannerV1Ring.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/synth.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/trackerAlign.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/trackerClosure.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/trackerClosureFallback.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/trackerClosureRepeat.test.ts",
  "next/hubs/sky/sheets/pano/__tests__/wmm.test.ts",
  "next/hubs/sky/sheets/pano/horizon/__tests__/horizonTrace.test.ts",
  "next/hubs/sky/sheets/pano/horizon/__tests__/simplify.test.ts",
  "next/hubs/sky/sheets/pano/horizon/__tests__/tracerVertical.test.ts",
  "next/ui/__tests__/r7Primitives.test.tsx",
  "testing/__tests__/w28DriveTo.test.ts",
  "testing/__tests__/w7NoLocalRafPolyfill.test.ts",
];

const REPO_ROOT = dirname(dirname(dirname(dirname(fileURLToPath(import.meta.url))))).split("\\").join("/");
const graph = buildGraph(REPO_ROOT);
const readers = undeclaredReaders(graph).map((p) => p.replace(/^ui\/src\//, ""));

test("the tree has the test files the ratchet expects to see", () => {
  assert(graph.tests.length > 700, `only ${graph.tests.length} test files found: the walk is broken`);
});

test("no test file reads from disk without declaring it, apart from the ones on the list", () => {
  const onList = new Set(UNDECLARED);
  const added = readers.filter((r) => !onList.has(r));
  assert(added.length === 0,
    `undeclared disk readers that are not on the list: ${added.join(", ")}. `
    + "Add `// disk-reads: <repo-root-relative paths>` to each (see the top of this file).");
});

test("the list holds only files that still read without declaring", () => {
  const live = new Set(readers);
  const stale = UNDECLARED.filter((r) => !live.has(r));
  assert(stale.length === 0,
    `declared, so remove them from the list (or the file is gone): ${stale.join(", ")}`);
});

test("every path a test declares exists", () => {
  const missing = missingDeclaredPaths(graph, REPO_ROOT);
  assert(missing.length === 0, `declared paths that do not exist: ${missing.join("; ")}`);
});

test("at least three test files declare their reads", () => {
  assert(graph.declared.size >= 3, `only ${graph.declared.size} declare their reads`);
});

console.log(`selectTestsRatchet.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
