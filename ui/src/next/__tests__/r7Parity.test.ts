// r7Parity.test.ts - the new UI mounts no legacy presentation component.
//
//   Run directly:  npx tsx src/next/__tests__/r7Parity.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHY THIS TEST EXISTS. Wave 1 shipped the six hubs by MOUNTING about two
// dozen legacy React components inside the new chrome and naming the
// duplication as accepted-for-now. Wave R7 closed that: 77 presentation
// components were rebuilt as new files under `ui/src/next/**` that share the
// legacy LOGIC (slices, pure helpers, validators, geometry, `*Meta`, hooks) and
// re-implement only presentation. The legacy files were NOT deleted - `#/classic`
// still imports every one of them - so nothing stops a later change from
// importing one back into the new UI, and nothing would look wrong when it did:
// the panel renders, the tests pass, and the design language quietly loses a
// screen. That is the failure this file exists to catch, and it can only be
// caught by reading the imports.
//
// The rule, from wave-r7.md section 5 (T-R7-21):
//
//   No file under `ui/src/next/**` may import a React COMPONENT from
//   `components/**` or `views/**`. Values, hooks, constants and types are
//   fine and deliberate - section 2.1 lists 25 logic modules the new UI is
//   SUPPOSED to keep sharing, because re-deriving a validator is how two
//   copies of a rule drift apart.
//
// Three allow-lists encode that, each with the reason beside every entry:
//
//   LOGIC_MODULES  - section 2.1's 25 logic modules plus the pure modules the
//                    tree actually imports; any value or type may come from
//                    these.
//   HELPER_ONLY    - legacy PRESENTATION modules that also export a pure
//                    helper. Section 2.1's "Finding": importing the helper
//                    drags the whole component and its Tailwind tree into the
//                    lazily-split next bundle, so these are pinned to the
//                    exact names, and adding a name is a decision someone has
//                    to make on purpose.
//   KEEP_AS_IS     - section 2.3's 18 widgets classified design-neutral (a
//                    canvas, an image transform, a scientific plot, a reticle),
//                    plus the rulings this wave added. These are the only
//                    legacy COMPONENTS the new UI may mount.
//
// SABOTAGE (run, red, restored - see the task report): re-add
// `import FlowsView from "../../../../components/flows/FlowsView"` to
// `session/flows/FlowsCanvasHost.tsx` and "no legacy presentation component is
// mounted" goes red naming the file, the module and the binding.

/* eslint-disable @typescript-eslint/no-explicit-any */

const { readFileSync, readdirSync, statSync } = await import("node:fs");
const { fileURLToPath } = await import("node:url");
const { dirname: pdirname, resolve: presolve } = await import("node:path");

const NEXT = fileURLToPath(new URL("../", import.meta.url));
const SRC = fileURLToPath(new URL("../../", import.meta.url));
const SEP = NEXT.includes("\\") ? "\\" : "/";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ============================================================ the allow-lists

/** Pure modules: no React component in them, so any binding may be imported.
 *  The `Used by` column of wave-r7.md section 2.1, kept as the reason. */
const LOGIC_MODULES: Record<string, string> = {
  "components/flows/geometry":
    "2.1 - FlowTier and the lane maths; the canvas's coordinate system, shared so " +
    "a rebuilt wire lands where the legacy one did.",
  "components/flows/flowsTypes":
    "2.1 - FlowGraphRec/FlowNodeRec/FlowEdgeRec plus nodeLossLevel/nodeLossDetail; " +
    "the wire shape of a flow and the compile-loss verdict.",
  "components/flows/nodeDefs":
    "2.1 - NODE_DEFS, PortDef, FieldDef, parseCyclePlan, formatCyclePlan: the stage " +
    "catalogue itself. Re-deriving it is how the canvas and the server disagree.",
  "components/flows/palette":
    "2.1 - node colours and kinds for the palette rail.",
  "components/flows/cyclePlanRows":
    "2.1 - resolveWheel and the cycle-plan row maths, shared with the Sky hub's quick session.",
  "components/flows/autoLayout":
    "2.1 - the layout solver (flowOrder), used by the phone stage list and the Sky lane.",
  "components/flows/flowRunControls":
    "2.1 - useFlowRunControls, runBlockedReason, isRunPhaseLive: one place decides " +
    "whether RUN is allowed and what the refusal says.",
  "components/sequence/sessionDates":
    "2.1 - night-boundary maths, shared by the gallery and the files sheet.",
  "components/sequence/stepDefaults":
    "2.1 - quota defaults, frame-type defaults and the quality-gate readers.",
  "components/settings/backendMeta":
    "2.1 - ROLE_LABEL, ALL_ROLES, backendMeta: the role vocabulary.",
  "components/settings/driversMeta":
    "2.1 - driverTypeChip, offersSummary, validateDriverForm: the driver form's validator.",
  "components/settings/skyAtlasMeta":
    "pure pack-status meta (packStatusLabel, packProgressPct) beside 2.1's other " +
    "*Meta modules; the panel it was named for is rebuilt, the meta is not presentation.",
  "components/capture/captureFilterDial":
    "2.1 - the filter dial's stops.",
  "components/atlas/mosaicNightSummary":
    "2.1 - the mosaic night summary maths.",
  "components/preview/lut":
    "the stretch transfer function (effectiveLevels, transfer) - the same curve the " +
    "stage paints; a second copy would show the operator a histogram of a different image.",
  "components/preview/linearReason":
    "why black/mid/white cannot be re-derived for a frame that arrived stretched - " +
    "a sentence and a state, no JSX.",
  "components/preview/focusAdvice":
    "adviceFor/adviceLabel: the focus verdict wording.",
  "components/preview/clippedFloor":
    "the honest clip floor (full_well-derived, not 65535).",
  "components/preview/usePreviewGestures":
    "2.1 - the pinch/pan gesture hook.",
  "components/preview/useLastSessionFrame":
    "2.1 - the last-frame poll hook.",
};

/** Legacy PRESENTATION modules the new UI still takes a pure helper out of,
 *  pinned to the exact names. Section 2.1's Finding is the reason the names
 *  are pinned rather than the module allowed wholesale. */
const HELPER_ONLY: Record<string, { names: string[]; why: string }> = {
  "components/ConfirmDialog": {
    names: ["confirmDialog", "ConfirmHost"],
    why: "2.1 - the one confirm mechanism, 25 call sites. ConfirmHost is the host " +
      "element NextApp mounts once; both are named here so a future export cannot ride in.",
  },
  "components/PreflightStrip": {
    names: ["usePreflight", "PreflightVerdict"],
    why: "the preflight verdict hook and its type; the STRIP itself is rebuilt as " +
      "session/plan/PlanPreflight.tsx.",
  },
  "components/ViewBoundary": {
    names: ["isChunkLoadError"],
    why: "one predicate the next-side HubBoundary reuses so a chunk-load failure is " +
      "recognised identically in both UIs.",
  },
  "components/ui/CameraPickers": {
    names: ["fmtExposure", "filterFace", "BIN_PRESETS", "EXPOSURE_PRESETS_S", "GAIN_PRESETS"],
    why: "2.1 - the exposure/gain/bin option tables and their formatters; the PICKER " +
      "components in the same file are rebuilt as the capture readouts.",
  },
  "components/preview/SessionStack": {
    names: ["modeLabel", "POLL_MS", "BACKFILL_POLL_MS"],
    why: "WAVE2-RULINGS (b) - constants and one label; the panel is rebuilt as " +
      "session/gallery/frames/SessionStackPanel.tsx.",
  },
  "components/gallery/FrameTile": {
    names: ["useInView"],
    why: "WAVE2-RULINGS (b) - the intersection-observer hook only; the tile is rebuilt " +
      "as session/gallery/frames/FrameTile.tsx.",
  },
  "components/settings/BackendLinkGrid": {
    names: ["linkReason", "linkTriState"],
    why: "2.1 - the link tri-state and its sentence, used by the rebuilt device roster.",
  },
  "components/settings/ProfileList": {
    names: [],
    why: "2.1/WAVE2-RULINGS (b) - waitForProfileActive was re-exported as " +
      "rig/profiles/profileActive.ts, so nothing imports this module any more except " +
      "the drift test that pins the copy against it (see DRIFT_TESTS).",
  },
  "components/flows/FlowLibraryCard": {
    names: ["cardMeta", "cardStatus", "formatLastRun"],
    why: "2.1 - the card's meta line and status word; the CARD is rebuilt as FlowRow.tsx.",
  },
  "components/flows/CalibrationMatrix": {
    names: ["verdictVar", "calHealthNotes", "readCalRow"],
    why: "the verdict-to-token map, the three route-level flags as sentences and the " +
      "row reader; the matrix chrome is rebuilt as tonight/CalibrationMatrixCard.tsx.",
  },
  "components/flows/TonightTimeline": {
    names: ["TL_W", "TL_H", "timelineGeometry", "moonPercent", "TonightTimelineProps",
      "TonightNight", "TonightTarget", "TonightMoon", "TonightFlats"],
    why: "3.A19 - the pure SVG geometry builders and the timeline's coordinate space; " +
      "only the chrome is rebuilt (tonight/TonightTimelineCard.tsx).",
  },
  "components/flows/TonightStory": {
    names: ["TonightStoryRow"],
    why: "3.A20 - the story row type; the list is rebuilt as tonight/TonightStoryList.tsx.",
  },
  "components/flows/TonightCampaign": {
    names: ["memberStatus", "CampaignRead", "CampaignMember"],
    why: "3.A22 - memberStatus and the campaign read types; the panel is rebuilt as " +
      "tonight/TonightCampaignCard.tsx.",
  },
  "views/EquipmentView": {
    names: ["compareRoleIdentity"],
    why: "2.1/2.2 - one comparator out of a 1526-line view the new UI does not mount; " +
      "the role table was rebuilt in wave 1 as rig/devices/roster.ts.",
  },
};

/** Section 2.3's keep-as-is widgets: a canvas, an image transform, a
 *  scientific plot, a reticle - a data surface the design language has no
 *  vocabulary for, rendering no Panel/.btn/.field inside the new UI. These are
 *  the ONLY legacy components the new UI may mount. */
const KEEP_AS_IS: Record<string, { names: string[]; why: string }> = {
  "components/gallery/CaptureGroups": {
    names: ["CaptureGroups"],
    why: "2.3 - a token-driven table of capture-geometry groups with no chrome of its " +
      "own, rendered identically by the legacy GalleryView and the new session files " +
      "sheet. Copying it would leave two groupings of the same frames that must agree " +
      "about target, filter, type, dimensions, binning and exposure, and silently " +
      "disagree the day one is changed.",
  },
  "components/preview/PreviewStage": {
    names: ["PreviewStage", "StageControls"],
    why: "2.3 - the pixel pipeline: double-buffered <img> swap, .preview-transform " +
      "layer, overlay <canvas>. No design analogue; its chrome was rebuilt around it.",
  },
  "components/graphs": {
    names: ["GuideGraph", "GuideScatter", "VCurve", "FocusFit"],
    why: "2.3 - pure SVG plots of guide samples, tokens only, no chrome. VCurve is the " +
      "focus sweep's plot in the same file and the same kind: the design has no plot " +
      "vocabulary and inventing one would be worse than the plot.",
  },
  "components/monitor": {
    names: ["Sparkline", "LiveTrendStrip", "ThermometerBar", "PreviewTile", "HealthStrip",
      "RmsVerdict", "CountdownTile", "HoldButton", "deriveHealthIssues", "useReducedMotion"],
    why: "2.3 - token-driven data surfaces already, no Panel; HoldButton is the confirm " +
      "mechanism shell/ConfirmCard reuses.",
  },
  "components/ui": {
    names: ["HoldButton"],
    why: "2.3 - the same HoldButton, re-exported from the ui barrel for ConfirmCard.",
  },
  "components/weather/RadarMap": {
    names: ["RadarMap"],
    why: "2.3 + WAVE2-RULINGS (d) - tile map and layer maths kept; its own Panel is " +
      "suppressed with the additive chrome=\"bare\" prop at both mount sites.",
  },
  "components/weather/SkyConditionsPanel": {
    names: ["SkyConditionsPanel"],
    why: "2.3 + WAVE2-RULINGS (d) - kept and rewrapped in a Card with chrome=\"bare\".",
  },
  "components/cloudmap/SkyDomePanel": {
    names: ["SkyDomePanel", "DomeOverlayArgs"],
    why: "2.3 + WAVE2-RULINGS (d) - the 3D hemisphere IS the design (README section 7); " +
      "rewrapped with chrome=\"bare\".",
  },
  "components/cloudmap/SkyDome": {
    names: ["DomeGeometry"],
    why: "the dome's geometry type, needed to draw the overlay on top of it.",
  },
  "components/atlas/SkyCanvas": {
    names: ["SkyCanvas"],
    why: "2.3 - the sky renderer itself (WebGL HiPS tiles); no chrome.",
  },
  "components/atlas/CatalogSearch": {
    names: ["CatalogSearch"],
    why: "2.3 - the catalog search widget, already token-only from wave 1.",
  },
  "components/SlewPad": {
    names: ["SlewPad"],
    why: "2.3 - the D-pad IS the design's D-pad.",
  },
  "components/GotoStrip": {
    names: ["GotoStrip"],
    why: "2.3 - the goto strip, token-only.",
  },
  "components/polar": {
    names: ["PolarReticle", "polarTier", "polarInstruction", "knobHint", "KnobDir"],
    why: "2.3 - the polar reticle plus the pure instruction/tier helpers beside it.",
  },
  "components/PolarQuickBar": {
    names: ["PolarQuickBar"],
    why: "2.3 - restyled in wave 1, renders no Panel.",
  },
  "components/PolarSolveRing": {
    names: ["PolarSolveRing"],
    why: "2.3 - a ring gauge, token-only.",
  },
  "components/GuideProviderControl": {
    names: ["GuideProviderControl"],
    why: "2.3 - restyled in wave 1, renders no Panel.",
  },
  "components/GuideQuickBar": {
    names: ["GuideQuickBar"],
    why: "2.3 - restyled in wave 1, renders no Panel.",
  },
  "components/GuideFramePreview": {
    names: ["GuideFramePreview"],
    why: "2.3 - a frame preview surface, no chrome.",
  },
  "components/capture/TargetField": {
    names: ["TargetField"],
    why: "2.3 - the target field widget, token-only.",
  },
  "components/ui/ActivityRing": {
    names: ["ActivityRing"],
    why: "2.3 - a ring gauge, token-only.",
  },
  "components/OverrideNote": {
    names: ["LayerChip"],
    why: "2.3 - the layer chip that names which config layer a value came from.",
  },
  "components/sequence/SessionReviewDrawer": {
    names: ["SessionReviewDrawer"],
    why: "WAVE2-RULINGS (c) - a per-session frame-grading surface that is not in R7's " +
      "inventory at all; mounted as-is by the rebuilt sessions section. A rebuild is a " +
      "named follow-up, not a silent keep.",
  },
  "components/TouchGuard": {
    names: ["TouchGuard"],
    why: "app-level, not a hub panel: the pointer-type guard NextApp wraps the whole " +
      "app in, shared with #/classic so both UIs agree what a touch is.",
  },
  "views/Login": {
    names: ["Login"],
    why: "app-level: there is ONE sign-in form in the product and NextApp mounts it. " +
      "Rebuilding it would mean two login screens that must never disagree.",
  },
};

/** Tests that import a legacy module on purpose to pin a COPY against it.
 *  WAVE2-RULINGS (b): these are drift guards, and they are the reason the
 *  copies are safe. */
const DRIFT_TESTS: Record<string, string> = {
  "hubs/rig/profiles/__tests__/rigProfilesDom.test.tsx":
    "pins rig/profiles against components/settings/ProfileList's behaviour.",
  "hubs/session/flows/create/__tests__/createDom.test.tsx":
    "pins create/quickPayload.ts's six copied helpers against components/flows/QuickFlow.",
  "hubs/session/flows/inspector/__tests__/inspectorDom.test.tsx":
    "pins the palette's fallback drop point against components/flows/FlowPalette's.",
  "hubs/sky/__tests__/skyDomeCardDom.test.tsx":
    "mounts the kept SkyDomePanel to prove the card's overlay args reach it.",
  "hubs/session/flows/__tests__/flowsDom.test.tsx":
    "reads runBlockedReason, a logic module, through a dynamic import.",
  "hubs/session/flows/canvas/__tests__/canvasDom.test.tsx":
    "reads runBlockedReason, a logic module, through a dynamic import.",
  "hubs/sky/sheets/__tests__/horizonDom.test.tsx":
    "mounts ConfirmHost so a confirm can be asserted.",
  "__tests__/shellDom.test.tsx":
    "drives confirmDialog through a dynamic import.",
};

/** The surfaces wave R7 REBUILT. Nothing under next/** may import these at
 *  all - not a component, not a helper. Anything they exported that the new UI
 *  still needs was re-exported from a next-side module (section 2.1's Finding). */
const REBUILT_SURFACES = [
  "views/SequenceView", "views/ReportView", "views/HelpView",
  "components/equipment/RotatorCard", "components/flows/FlowsView",
  "components/flows/FlowEditor", "components/flows/FlowLibrary", "components/flows/FlowHeader",
  "components/flows/FlowInspector", "components/flows/FlowNodeCard", "components/flows/FlowCanvas",
  "components/flows/TonightPanel", "components/flows/FlowWizard",
  "components/settings/AlertsPanel", "components/settings/UsersPanel",
  "components/settings/AuthMethodPanel", "components/settings/AccountPanel",
  "components/settings/UpdatePanel", "components/settings/FactoryResetPanel",
  "components/settings/CreditsPanel", "components/settings/RestrictedAssetsPanel",
  "components/settings/SyncPanel", "components/settings/NamingPanel",
  "components/settings/WcsStampPanel", "components/settings/StandardsPanel",
  "components/settings/CalibrationLibraryPanel", "components/settings/CalibrationTolerancesPanel",
  "components/settings/SkyAtlasPanel", "components/settings/WeatherPanel",
  "components/settings/CloudmapPanel", "components/settings/SafetyPanel",
  "components/settings/SafetyLimitsPanel", "components/settings/EscalationPanel",
  "components/gallery/FrameViewer", "components/gallery/TrashPanel",
  "components/preview/PreviewToolbar", "components/preview/StretchHistogram",
  "components/preview/PreviewMeta", "components/preview/FrameStats",
  "components/preview/FrameFilmstrip", "components/preview/LiveStackReadout",
  "components/preview/FocusVerdict",
  "components/sequence/SchedulePanel", "components/sequence/SessionsPanel",
  "components/sequence/PlanLibraryPanel", "components/sequence/InstructionsPanel",
];

// ------------------------------------------------------------- the scan

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = `${dir}${SEP}${name}`;
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

const stripComments = (s: string): string =>
  s.replace(/(^|[^:])\/\/[^\n]*/g, "$1").replace(/\/\*[\s\S]*?\*\//g, "");

// Issue #39: both scans below used to match double-quoted specifiers only, so
// `import Foo from '../../../../components/Foo'` (single quotes) contributed
// no record at all - invisible to "no legacy presentation component is
// mounted" and every other rule keyed off RECORDS, silently, in both
// directions. `QUOTED` accepts a double-quoted, single-quoted, or
// substitution-free template-literal specifier - see r7Css.test.ts, which has
// the identical fragment and the same reasoning.
const QUOTED = `"([^"]+)"|'([^']+)'|\`((?:(?!\\\$\\{)[^\`])+)\``;
const pick = (m: RegExpMatchArray): string => (m[1] ?? m[2] ?? m[3]) as string;

/** `import <clause> from <spec>` - group 1 is the clause, groups 2-4 are the
 *  three quote-style alternatives of `QUOTED` (see `pick`, applied at
 *  offset 1 by the caller). Hoisted so the vacuity probe below exercises the
 *  exact pattern the scan uses, not a hand-copied stand-in. */
const IMPORT_FROM_RE = new RegExp(`import\\s+([\\s\\S]*?)\\s+from\\s+(?:${QUOTED})`, "g");
/** `import(<spec>)` - a dynamic import. */
const IMPORT_DYN_RE = new RegExp(`import\\(\\s*(?:${QUOTED})\\s*\\)`, "g");

const FILES = walk(NEXT.slice(0, -1)).filter((p) => /\.tsx?$/.test(p) && !p.endsWith(".d.ts"));

interface Record_ { file: string; module: string; name: string; typeOnly: boolean; dynamic: boolean }

/** Normalise a relative specifier to a path under `src/`, so
 *  `../../../../components/flows/nodeDefs` becomes `components/flows/nodeDefs`. */
function moduleOf(file: string, spec: string): string | null {
  if (!spec.startsWith(".")) return null;
  // node:path, not hand-rolled splitting. Splitting an ABSOLUTE POSIX path on
  // "/" yields a leading "" that encodes the root, and skipping empty parts
  // threw it away, so `abs` came back relative while SRC (from fileURLToPath)
  // kept its leading slash. startsWith could then never match, moduleOf
  // returned null for every specifier, and RECORDS was EMPTY on Linux -- this
  // whole scan inert, on CI only, because on Windows the first component is
  // the drive letter and is not empty. The vacuity guard below is what caught
  // it. Same defect as the CSS scanner's resolveSpec (#116).
  const abs = presolve(pdirname(file), spec);
  if (!abs.startsWith(SRC)) return null;
  return abs.slice(SRC.length).replace(/\\/g, "/").replace(/\.(tsx?|jsx?)$/, "");
}

/** The bindings an import clause introduces, each flagged type-only. Handles
 *  `import X from`, `import { a, type B }`, `import type { C }` and
 *  `import X, { y }`. */
function bindingsOf(clause: string): { name: string; typeOnly: boolean }[] {
  let text = clause.trim();
  let all = false;
  if (text.startsWith("type ")) { all = true; text = text.slice(5).trim(); }
  const out: { name: string; typeOnly: boolean }[] = [];
  const braces = /\{([\s\S]*)\}/.exec(text);
  if (braces) {
    for (const raw of braces[1].split(",")) {
      const part = raw.trim();
      if (!part) continue;
      const typeOnly = all || part.startsWith("type ");
      const name = part.replace(/^type\s+/, "").split(/\s+as\s+/)[0].trim();
      if (name) out.push({ name, typeOnly });
    }
  }
  const head = text.split(/[,{]/)[0].trim();
  if (head && !head.startsWith("*")) out.push({ name: head, typeOnly: all });
  return out;
}

const RECORDS: Record_[] = [];
for (const file of FILES) {
  const text = stripComments(readFileSync(file, "utf8"));
  const rel = file.slice(NEXT.length).replace(/\\/g, "/");
  for (const m of text.matchAll(IMPORT_FROM_RE)) {
    const mod = moduleOf(file, (m[2] ?? m[3] ?? m[4]) as string);
    if (mod == null || !/^(components|views)\//.test(mod)) continue;
    for (const b of bindingsOf(m[1])) {
      RECORDS.push({ file: rel, module: mod, name: b.name, typeOnly: b.typeOnly, dynamic: false });
    }
  }
  for (const m of text.matchAll(IMPORT_DYN_RE)) {
    const mod = moduleOf(file, pick(m));
    if (mod == null || !/^(components|views)\//.test(mod)) continue;
    RECORDS.push({ file: rel, module: mod, name: "*", typeOnly: false, dynamic: true });
  }
}

/** A PascalCase binding is component-shaped. SCREAMING_CASE (NODE_DEFS, TL_W,
 *  POLL_MS) is a constant and camelCase is a value or a hook; neither can be a
 *  React component, because JSX only calls capitalised identifiers and nobody
 *  writes `<NODE_DEFS />`. */
const componentShaped = (name: string): boolean =>
  /^[A-Z]/.test(name) && !/^[A-Z0-9_]+$/.test(name);

// ================================================================ 1. vacuity
// Every other test here is "no record violates the rule", which a scan that
// found nothing passes perfectly.

test("the scan found the legacy imports it is supposed to police", () => {
  assert(FILES.length >= 200, `only ${FILES.length} modules under next/`);
  assert(RECORDS.length >= 120, `only ${RECORDS.length} legacy import bindings found`);
  const mods = new Set(RECORDS.map((r) => r.module));
  assert(mods.size >= 40, `only ${mods.size} legacy modules imported - is the scan working?`);
  assert(RECORDS.some((r) => r.module === "components/flows/nodeDefs" && r.name === "NODE_DEFS"),
    "the canvas's NODE_DEFS import did not parse");
  assert(RECORDS.some((r) => r.module === "components/preview/PreviewStage" && !r.typeOnly
    && r.name === "PreviewStage"), "the kept PreviewStage import did not parse");
  assert(componentShaped("FlowsView") && !componentShaped("NODE_DEFS")
    && !componentShaped("cardMeta"), "the component-shape rule is not discriminating");
  // Issue #39's corpus assertion: the edge count for a KNOWN real file, so a
  // scanner that silently starts finding nothing fails loudly instead of
  // quietly starving every rule below of records. Computed by hand against
  // `tonight/CalibrationMatrixCard.tsx` as it stands: three named imports
  // (calHealthNotes, readCalRow, verdictVar) from the one legacy module it
  // still uses, all double-quoted today. If this file's imports change, this
  // count changes with it.
  const cardRecords = RECORDS.filter((r) => r.file === "hubs/session/flows/tonight/CalibrationMatrixCard.tsx");
  assert(cardRecords.length === 3,
    `CalibrationMatrixCard.tsx: expected 3 legacy import bindings, found ${cardRecords.length} - ` +
    "either its imports changed (update this number) or the scanner is missing some");
});

// =================================================== 1b. every quote style
// Issue #39: the scan used to match double-quoted specifiers only, so
// `import Foo from '../../components/Foo'` (single quotes) or a
// substitution-free template-literal specifier contributed no record at all -
// invisible to every rule below, silently, in both directions.
//
// Mutation: restore the double-quote-only pattern (reproduced inline below,
// unchanged from before this fix) and this test goes red on its own probes.

// Builds a probe string at RUNTIME rather than as one literal in the source
// text of this file. This file is itself one of `FILES` (r7Parity does not
// exclude __tests__ from its scan - see DRIFT_TESTS above), so a probe typed
// as a literal contiguous `import Foo from "..."` would be picked up by the
// live scan reading THIS file's own source text and misreported as a real
// legacy import, failing tests 2 and 3 for a reason that has nothing to do
// with the rule. Splitting the two keywords the regex looks for keeps this
// file's raw text free of anything that shape-matches an import statement,
// while still producing the exact runtime string the probe below needs.
const probeImport = (open: string, spec: string, close: string): string =>
  ["im" + "port", "Foo", "fr" + "om", `${open}${spec}${close};`].join(" ");

test("the import scan sees a single-quoted, double-quoted and template-literal specifier", () => {
  const probes = [
    probeImport('"', "../../components/Foo", '"'),
    probeImport("'", "../../components/Foo", "'"),
    probeImport("`", "../../components/Foo", "`"),
  ];
  for (const src of probes) {
    const hit = [...src.matchAll(IMPORT_FROM_RE)][0];
    assert(hit != null, `the scan missed ${JSON.stringify(src)}`);
    assert((hit[2] ?? hit[3] ?? hit[4]) === "../../components/Foo",
      `the scan resolved ${JSON.stringify(src)} to the wrong specifier`);
  }

  // The bug this issue fixes, reproduced exactly (this is what the scan used
  // before this task): a single-quoted or template-literal legacy import was
  // invisible, which is precisely how a re-imported legacy component could
  // have gone unseen by "no legacy presentation component is mounted".
  const doubleQuoteOnlyRe = new RegExp("im" + "port" + "\\s+([\\s\\S]*?)\\s+" + "fr" + "om" + "\\s+\"([^\"]+)\"", "g");
  assert([...probes[1].matchAll(doubleQuoteOnlyRe)].length === 0,
    "the pre-fix scanner unexpectedly saw a single-quoted specifier - this probe no longer pins the regression");
  assert([...probes[2].matchAll(doubleQuoteOnlyRe)].length === 0,
    "the pre-fix scanner unexpectedly saw a template-literal specifier - this probe no longer pins the regression");

  // Negative control: a template literal WITH a substitution is a computed
  // specifier, not a static one - it must not be reported as a match.
  const substituted = probeImport("`", "../../${area}/Foo", "`");
  assert([...substituted.matchAll(IMPORT_FROM_RE)].length === 0,
    "the scan treated a substituted template literal as if it were static");
});

// ================================== 2. no legacy presentation component is mounted
// The rule itself. A component-shaped binding from `components/**` or
// `views/**` has to be in KEEP_AS_IS, with its reason.

test("no legacy presentation component is mounted by the new UI", () => {
  const problems: string[] = [];
  for (const r of RECORDS) {
    if (r.typeOnly || r.dynamic || !componentShaped(r.name)) continue;
    const keep = KEEP_AS_IS[r.module];
    if (keep && keep.names.includes(r.name)) continue;
    const helper = HELPER_ONLY[r.module];
    if (helper && helper.names.includes(r.name)) continue;
    problems.push(`${r.file} imports ${r.name} from ${r.module}`);
  }
  assert(problems.length === 0,
    `${problems.length} legacy component import(s) with no keep-as-is entry: ${problems.join("; ")}`);
});

// ============================================= 3. and no legacy helper drifts in
// The other half: a value or type import is fine from a LOGIC module, and from
// a presentation module only for the exact names section 2.1 named.

test("every legacy value or type import comes from an allow-listed module", () => {
  const problems: string[] = [];
  for (const r of RECORDS) {
    if (r.dynamic) continue;
    if (LOGIC_MODULES[r.module]) continue;
    const helper = HELPER_ONLY[r.module];
    if (helper && helper.names.includes(r.name)) continue;
    const keep = KEEP_AS_IS[r.module];
    if (keep && keep.names.includes(r.name)) continue;
    problems.push(`${r.file} imports ${r.typeOnly ? "type " : ""}${r.name} from ${r.module}`);
  }
  assert(problems.length === 0,
    `${problems.length} legacy import(s) outside the allow-lists: ${problems.join("; ")}`);
});

// ==================================== 4. the rebuilt surfaces are gone entirely
// Not "no component from them" - NOTHING from them. Each of these is a screen
// wave R7 rebuilt in the design's own vocabulary, and each still exists for
// `#/classic`, which is exactly what makes an accidental re-import invisible.

test("nothing under next/ imports a surface wave R7 rebuilt", () => {
  const problems: string[] = [];
  for (const r of RECORDS) {
    if (!REBUILT_SURFACES.includes(r.module)) continue;
    problems.push(`${r.file} imports ${r.dynamic ? "(dynamic)" : r.name} from ${r.module}`);
  }
  assert(problems.length === 0, problems.join("; "));
});

// ============================== 5. a dynamic import is a drift test or a keep
// `await import("../../components/flows/QuickFlow")` bypasses every static
// check above, and three tests do it on purpose to pin a copied helper against
// the original. Any OTHER dynamic legacy import is the same defect as a static
// one, wearing a hood.

test("a dynamic legacy import is a named drift test or an allow-listed module", () => {
  const problems: string[] = [];
  for (const r of RECORDS) {
    if (!r.dynamic) continue;
    if (DRIFT_TESTS[r.file]) continue;
    if (LOGIC_MODULES[r.module] || HELPER_ONLY[r.module] || KEEP_AS_IS[r.module]) continue;
    problems.push(`${r.file} dynamically imports ${r.module}`);
  }
  assert(problems.length === 0, problems.join("; "));
});

// ================================================ 6. the allow-lists carry reasons
// A rule nobody can read is a rule nobody keeps. Every entry states why the
// import survives, and an entry with no live importer is dead weight that
// would quietly re-open the door.

test("every allow-list entry carries a reason and is still used", () => {
  const problems: string[] = [];
  const used = new Set(RECORDS.map((r) => r.module));
  for (const [mod, why] of Object.entries(LOGIC_MODULES)) {
    if (why.length < 30) problems.push(`LOGIC_MODULES["${mod}"] has no real reason`);
    if (!used.has(mod)) problems.push(`LOGIC_MODULES["${mod}"] is imported by nothing - drop it`);
  }
  for (const [mod, e] of Object.entries(HELPER_ONLY)) {
    if (e.why.length < 30) problems.push(`HELPER_ONLY["${mod}"] has no real reason`);
  }
  for (const [mod, e] of Object.entries(KEEP_AS_IS)) {
    if (e.why.length < 30) problems.push(`KEEP_AS_IS["${mod}"] has no real reason`);
    if (!used.has(mod)) problems.push(`KEEP_AS_IS["${mod}"] is mounted by nothing - drop it`);
  }
  for (const [file, why] of Object.entries(DRIFT_TESTS)) {
    if (why.length < 20) problems.push(`DRIFT_TESTS["${file}"] has no real reason`);
    if (!RECORDS.some((r) => r.file === file)) {
      problems.push(`DRIFT_TESTS["${file}"] imports no legacy module any more - drop it`);
    }
  }
  assert(problems.length === 0, problems.join("; "));
});

// ---------------------------------------------------------------- report
const total = passed + failed;
console.log(`r7Parity.test: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
if (failed > 0) process.exitCode = 1;

export { passed, failed, total };
