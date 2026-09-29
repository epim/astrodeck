// TargetFramingSheet.tsx - the Target modal, "FRAME" (#189 S4 items 1 and 4,
// S5 run mode; spec 2026-09-23 flows mosaic, 2.1-2.7).
//
// ONE SHARED SHEET for both UIs, loaded lazily (index.ts), opened on one
// TARGET node. The classic inspector's FRAME ON SKY row and the #/next
// `flowFrame` sheet both mount this file; neither forks its presentation.
//
// A LOCAL SESSION, NEVER THE GLOBAL ONE. Everything the operator does here
// (drag, zoom, rotate, type, tap) edits state this component owns: the draft
// of the node's params (framingModel `draftFromParams`), and a local
// FramingSession for the sky (centre, zoom, survey). `store.framing` is the
// Atlas's singleton, and a modal that wrote it is how one target's mosaic
// appeared on another target's flow in review #3; nothing below reads or
// writes it. The store is written ONCE, by DONE, through `flowsApplyFraming`
// (every changed param and the loop wire in one write, then one compile),
// preceded by `flowsSetSetting` when the flow-level "while a mosaic waits"
// row changed. CANCEL writes nothing. While DONE's write waits for its
// compile, CANCEL and Escape refuse and say why, the draft takes no edit it
// could not keep, and the sheet calls its host's onClose once at most (#382).
//
// THE SERVER DECIDES WHERE THE PANELS ARE (spec 2.3, 2.5). While the draft
// moves, the sky previews the grid with the client mirror (lib/framing
// `mosaicGrid`). Once it has settled for `framingTiming.settleMs`, the sheet
// asks `POST /api/framing/mosaic` (with the block's stored anchor, so the
// answer carries the re-frame verdict) and redraws from the answer. DONE
// stays locked until the answer for the CURRENT spec is in hand
// (framingModel `doneState`); an answer is recorded only against the request
// it answers, and one that lands after the draft moved on is dropped, so a
// slow answer can never unlock DONE for a layout it was not asked about.
// Offline, or for a viewer (the route needs `view.site_derived`), no request
// is made and DONE opens on the mirror with a chip saying so.
//
// NO ANGLE NOBODY CHOSE (S5 orchestrator ruling 1, #411; spec 1.8). A draft
// at ANY ANGLE that becomes a grid, by a stepper or SUGGEST GRID, stays at ANY
// ANGLE: S4 turned it into ROTATE TO at PA 0, which a DONE would have sent to
// the rotator. DONE is locked until an angle is chosen (framingModel
// `gridAngleLock`), the readout strip says the angle the grid is laid out at,
// and a measured angle in `status.sky_angle` is offered in the strip and in
// ANGLE (`angleOffer`), written only when pressed.
//
// RUN MODE (spec 2.6). While the flow's session runs, both doors open this
// sheet with `viewOnly` (they decide it with flowRunState `flowRunLive`, from
// the sessions the slice knows as the flow's and the rig's, and never from
// the progress answer a save blanks, #449), and it opens read-only as an
// Example does: no DONE, the fieldset disabled, the sky read-only, so it
// writes nothing. What it adds is the run: each panel of the framed grid is
// drawn as the live `state.group` and the progress route say it is
// (framingModel `runPanelsOf`, `panelDrawState`): the panel being shot with
// corner ticks, a panel set aside tonight dotted with "!" and its reason in
// PANELS, and a panel done hatched once the slice's live re-read (#214) banks
// its last sub. The group is this block's only by the progress block's
// `group_id` (flowRunState `groupForBlock`), never by name, and nothing of
// the run's timing is shown: no meridian countdown, no visit clock (5.10).
//
// THE LAYOUT (spec 2.2) IS framing.css's, chosen by container queries on the
// sheet itself and never by a width breakpoint class: phone portrait stacks
// a 48 px header, the sky pinned OUTSIDE the scroller at min(100vw, 52svh)
// (40svh while a text field has focus, `data-typing`), a 32 px mono readout
// strip and the scroller of 44 px rows; phone landscape puts the sky on the
// left at 55%; tablet and desktop put it beside a 360 px control column, in
// Overlay's `full` variant at every width. `lg:` would strand a phone in
// landscape (667-932 px wide) in the portrait stack.

import "./framing.css";
import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";
import { Overlay } from "../../Overlay";
import { HonestButton } from "../../ui";
import { useStore } from "../../../store";
import { accessPhrase, useCapability } from "../../../lib/caps";
import { effectiveOptics, opticsOverrideProfile } from "../../../lib/effective";
import { fovFromOptics, mosaicGrid, mosaicTotalFov } from "../../../lib/framing";
import type { FlowCompileResult } from "../../../lib/flowsApi";
import type { CatalogEntry, FramingSession, MosaicPanel } from "../../../types";
import { groupForBlock, type PanelRunState } from "../flowRunState";
import { MosaicNightCard } from "../../../next/hubs/sky/frame/MosaicNightCard";
import type { SkyPanel } from "../../atlas/PanelLayer";
import { raHms, decDms } from "../QuickFlow";
import { NODE_DEFS } from "../nodeDefs";
import { flowSetting, type FlowNodeRec } from "../flowsTypes";
import { isMultiPanel, laneBranched, laneTail, loopSource, loopWires, panelLane } from "../panelLane";
import { targetLoops } from "../targetSummary";
import { countsAttempts, countsNotice } from "../countsNotice";
import {
  angleLocks, angleOf, angleOffer, cameraFieldLine, currentAnswer, doneState, draftCentre,
  draftFromParams, driftBanner, framingPatch, gridAngleLock, gridLock, gridOf, layoutOf,
  loadViewPrefs, matchCamera, matchCameraLock, moveLine, mosaicRequest, panelDrawState, parseSkip,
  readoutStrip, reframeDecision, requestKey, runLines, runPanelsOf, saveViewPrefs, setAngleMode,
  stripAngle, suggestGrid, takeOffer, toggleSkip, toleranceLine, useMeasured, useMeasuredLine,
  ZOOM_MAX_DEG, ZOOM_MIN_DEG,
  type FramingDraft, type PanelAnswer, type ServerView, type TargetAngle,
} from "./framingModel";
import {
  campaignLine, compiledReadouts, compiledRig, framedGraph, framingApi, framingTiming, liveRig,
} from "./framingApi";
import { FramingSky, type SkyPoint } from "./FramingSky";
import { WhereSection, NO_OBJECT, NO_SIZE } from "./sections/WhereSection";
import { GridSection } from "./sections/GridSection";
import { AngleSection } from "./sections/AngleSection";
import { PanelsSection, panelRows, runLine } from "./sections/PanelsSection";
import { RunSection } from "./sections/RunSection";
import { CentringSection } from "./sections/CentringSection";

// ------------------------------------------------------------------ words

/** Why a read-only Example opens in view mode (spec 2.1). An Example is
 *  `readonly` on the server, so `flowsSave` refuses it and an edit made here
 *  could only be thrown away on close. */
export const EXAMPLE_VIEW_ONLY =
  "Example flow: its framing opens to view, not to edit. Duplicate the flow to frame your own.";
/** Why run mode opens the sheet read-only (spec 2.6). Said ahead of
 *  EXAMPLE_VIEW_ONLY when an Example is the flow that runs: the run is the
 *  reason the sheet is showing live panels, and "duplicate the flow" is still
 *  true once it ends. */
export const RUNNING_VIEW_ONLY = "This flow's session is running: its framing opens to view, not to edit.";
export const CHECKING = "writing the framing and waiting for the compiler";
export const ONE_PANEL_NO_LOOP = "one panel, nothing to rotate between";
export const WAITING_FOR_COMPILE = "RUN numbers follow the framing: waiting for the compile of this layout";
export const NO_COMPILE = "no compile of this flow is in hand yet";
export const NO_READOUT = "the compile gives no RUN numbers for this block; the flow's checks say why";
/** Why an edited layout has no RUN numbers for a principal the draft compile
 *  refuses (`POST /api/flows/compile` is `control.capture`). */
export const COMPILE_NEEDS_ACCESS =
  `the RUN numbers for an edited layout come from the compile, which needs ${accessPhrase("control.capture")}`;
export const ANY_ANGLE_HOLDS_NONE =
  "ANY ANGLE holds no angle: choose ROTATE TO or CAMERA FIXED AT to set one";
export const GONE = "That stage is no longer in this flow.";

// ------------------------------------------------------------------ props

export interface TargetFramingSheetProps {
  /** The TARGET node to frame. */
  nodeId: string;
  onClose: () => void;
  /** Run mode (spec 2.6): the same sheet, read-only, drawing the live run's
   *  panels. Each door passes it while flowRunState `flowRunLive` says the
   *  open flow's session is running. A read-only Example opens read-only on
   *  its own, and draws no run unless this is set too. */
  viewOnly?: boolean;
}

/** What the sheet heard from `POST /api/framing/mosaic`: framingModel's
 *  `PanelAnswer` plus the panels, which the model never reads. */
interface SheetAnswer extends PanelAnswer {
  panels: MosaicPanel[] | null;
}

/** The sky's local session: the part of the Atlas's FramingSession this
 *  modal needs, held in component state (see the header). */
type LocalSky = Pick<FramingSession, "survey" | "stretch" | "fovZoomDeg"> & {
  /** The sky's centre while MOVE GRID holds it still. */
  viewCentre: SkyPoint | null;
  moveGrid: boolean;
  /** The catalogue object last picked, for FIT OBJECT, SUGGEST GRID and the
   *  size ellipse. */
  target?: CatalogEntry;
};

const clampZoom = (v: number) => Math.min(ZOOM_MAX_DEG, Math.max(ZOOM_MIN_DEG, v));
const round1 = (v: number) => Math.round(v * 10) / 10;
const wrap360 = (v: number) => ((v % 360) + 360) % 360;
const todayIso = () => {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

/** The sky's width that shows the whole grid with a margin: 1.6 x its larger
 *  extent, the Atlas's FIT OBJECT factor. 2 deg with no field to size by. */
function fitZoom(d: FramingDraft): number {
  const l = layoutOf(d);
  if (!(l.fov_x > 0 && l.fov_y > 0)) return 2;
  const t = mosaicTotalFov(l.cols, l.rows, l.overlap, l.fov_x, l.fov_y);
  return clampZoom(1.6 * Math.max(t.total_fov_x_deg, t.total_fov_y_deg));
}

/** A field the operator types into, which shrinks the sky (spec 2.2). */
function isTextField(el: EventTarget | null): boolean {
  if (typeof HTMLInputElement !== "undefined" && el instanceof HTMLInputElement) {
    return ["text", "search", "number", ""].includes(el.type);
  }
  return typeof HTMLTextAreaElement !== "undefined" && el instanceof HTMLTextAreaElement;
}

// ------------------------------------------------------------------ shell

export default function TargetFramingSheet(p: TargetFramingSheetProps): JSX.Element {
  const node = useStore((s) => s.flows.graph.nodes.find((n) => n.id === p.nodeId) ?? null);
  const example = useStore((s) => s.flows.record?.readonly ?? false);
  if (!node || node.type !== "target") {
    return (
      <Overlay open label="Frame" onClose={p.onClose} variant="full" bodyClassName="tfs-host">
        <div className="tfs" data-testid="target-framing-sheet">
          <header className="tfs-head">
            <button type="button" className="tfs-btn" onClick={p.onClose}>CLOSE</button>
          </header>
          <p className="tfs-status" role="status">{GONE}</p>
        </div>
      </Overlay>
    );
  }
  const runMode = p.viewOnly === true;
  const why = runMode ? RUNNING_VIEW_ONLY : example ? EXAMPLE_VIEW_ONLY : null;
  // Keyed by node: a sheet re-pointed at another block starts from that
  // block's params, never from the last one's draft.
  return <FramingSheetBody key={node.id} node={node} onClose={p.onClose} viewWhy={why} runMode={runMode} />;
}

// ------------------------------------------------------------------- body

function FramingSheetBody({ node, onClose, viewWhy, runMode }: {
  node: FlowNodeRec; onClose: () => void; viewWhy: string | null; runMode: boolean;
}): JSX.Element {
  const nodeId = node.id;
  const stored = node.params;
  const readOnly = viewWhy !== null;

  // ---- the store, read-only until DONE
  const graph = useStore((s) => s.flows.graph);
  const recordId = useStore((s) => s.flows.record?.id ?? null);
  const recordName = useStore((s) => s.flows.record?.name ?? "");
  const compiled = useStore((s) => s.flows.compiled);
  const progress = useStore((s) => s.flows.progress);
  const tonight = useStore((s) => s.flows.tonight);
  const tonightLoading = useStore((s) => s.flows.tonightLoading);
  const countsNote = useStore((s) => s.flows.countsNote);
  const skyAngle = useStore((s) => s.status?.sky_angle ?? null);
  const statusOptics = useStore((s) => s.status?.optics ?? null);
  const config = useStore((s) => s.config);
  const night = useStore((s) => s.night);
  const site = useStore((s) => s.site);
  const wsConnected = useStore((s) => s.wsConnected);
  const canSite = useCapability("view.site_derived");
  const canCompile = useCapability("control.capture");
  const applyFraming = useStore((s) => s.flowsApplyFraming);
  const setSetting = useStore((s) => s.flowsSetSetting);
  const compileFlow = useStore((s) => s.flowsCompile);
  const fetchTonight = useStore((s) => s.flowsFetchTonight);

  // ---- the draft (the node's params as the modal holds them)
  const [draft, setDraft] = useState<FramingDraft>(() => draftFromParams(stored));
  const patchDraft = useCallback((over: Partial<FramingDraft>) => setDraft((d) => ({ ...d, ...over })), []);

  // ---- the sky's local session
  const [sky, setSky] = useState<LocalSky>(() => {
    const prefs = loadViewPrefs(recordId, nodeId);
    return {
      survey: prefs.survey, stretch: "linear",
      fovZoomDeg: prefs.zoomDeg ?? fitZoom(draftFromParams(stored)),
      viewCentre: null, moveGrid: false,
    };
  });

  // ---- what the block was when the sheet opened, for the loop rule
  //
  // RUN OPENS ON WHAT THE RUN DOES (#429): `targetLoops`, the one reader of
  // "does this block rotate" the card's footer, the loop chip, the phone
  // rail and LOOP PANELS all ask. It opened on `loopWires` alone, which
  // finds the tail's wire standing beside a stale pass wire from mid-lane
  // (M12): the toggle read ON over a lane the card said runs one panel at a
  // time and `/run` refuses, and a DONE that left it alone sent no `loop`,
  // which repairs nothing. Now it opens OFF there, and switching it on sends
  // `true`, whose `withLoop` moves the lane to one loop from its tail.
  //
  // `tailWire` is the other fact DONE needs, and a different one: a wire
  // leaving the tail, which a DONE that makes the mosaic one panel lifts
  // (`loopArg` below). A lane that does not rotate can still carry one.
  const [opened] = useState(() => ({
    multi: isMultiPanel(node),
    loop: targetLoops(node, graph),
    tailWire: loopWires(graph, nodeId).length > 0,
  }));
  const [loopTouched, setLoopTouched] = useState<boolean | null>(null);
  const storedWaiting = flowSetting(graph.settings, "whenWaiting");
  const [waiting, setWaiting] = useState(storedWaiting);
  const settingChanged = waiting !== storedWaiting;

  const [typing, setTyping] = useState(false);
  const [explained, setExplained] = useState<string | null>(null);
  const [question, setQuestion] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Whether this sheet is still mounted, for DONE's close after its await.
  // Set in the effect as well as cleared in its cleanup, so a development
  // StrictMode remount (effects run, clean up, run again) leaves it true.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  // ---- the rig as MATCH CAMERA and the locks read it
  const optics = useMemo(
    () => effectiveOptics(config, config?.optics ?? null, statusOptics ?? config?.optics_computed ?? null),
    [config, statusOptics]);
  const rig = useMemo(() => {
    const f = optics ? fovFromOptics(optics) : null;
    return liveRig(compiledRig(compiled), f && f.fov_x_deg > 0 ? f : null, opticsOverrideProfile(config), todayIso());
  }, [optics, compiled, config]);

  const layout = layoutOf(draft);
  const { rows, cols } = gridOf(draft);
  const centre = draftCentre(draft);
  // The sky stays on the last centre the draft could be read at while an RA
  // or Dec is half typed ("00h"), rather than unmounting the canvas, and its
  // tile engine, on every keystroke; the grid itself is drawn only from a
  // centre that parses.
  const lastCentre = useRef(centre);
  if (centre) lastCentre.current = centre;
  const skyCentre = centre ?? lastCentre.current;
  const mode = angleOf(draft);

  // ---- the route: one request per settled spec
  const anchor = stored.frameAnchor;
  const request = useMemo(() => mosaicRequest(draft, anchor), [draft, anchor]);
  const reqKey = request ? requestKey(request) : null;
  const offline = !wsConnected;
  const reachable = !offline && canSite;
  const [answer, setAnswer] = useState<SheetAnswer | null>(null);
  const latestKey = useRef<string | null>(reqKey);
  latestKey.current = reqKey;
  const requestRef = useRef(request);
  requestRef.current = request;
  useEffect(() => {
    const req = requestRef.current;
    if (!req || !reachable) return;
    const sent = requestKey(req);
    const t = setTimeout(() => {
      // AN ANSWER IS RECORDED AGAINST THE REQUEST IT ANSWERS, and only while
      // that is still the request the draft makes: one that lands after a
      // drag moved on says where the panels WERE.
      framingApi.mosaic(req, canSite).then(
        (res) => {
          if (latestKey.current !== sent) return;
          setAnswer({ key: sent, ok: true, reframe: res?.reframe ?? null,
            panels: Array.isArray(res?.panels) ? res.panels : null });
        },
        () => {
          if (latestKey.current !== sent) return;
          setAnswer({ key: sent, ok: false, reframe: null, panels: null });
        });
    }, framingTiming.settleMs);
    return () => clearTimeout(t);
  }, [reqKey, reachable, canSite]);

  const view: ServerView = { request, answer, offline, canViewSiteDerived: canSite };
  const done = doneState(view);
  const current = currentAnswer(view) as SheetAnswer | null;
  const serverPanels = current && current.ok ? current.panels : null;

  // ---- progress (the progress route, for the SAVED flow)
  const progressBlock = useMemo(
    () => progress?.blocks.find((b) => b.node_id === nodeId) ?? null, [progress, nodeId]);

  // ---- the live run (run mode, spec 2.6)
  // The group is this block's by the progress block's `group_id`, the id the
  // engine publishes, and never by name. The selector answers a STRING,
  // because the store's `sequence` is a new object on every publish (every
  // frame, every detail line), and the sheet, with the sky under it, should
  // redraw when a panel changes state, not when a frame counter ticks. Outside
  // run mode no group id is asked for, so nothing of a run is ever drawn on a
  // sheet that can still edit.
  const runGroupId = runMode ? progressBlock?.group_id ?? null : null;
  const runGrid = progressBlock?.grid ?? null;
  const runKey = useStore((s) =>
    JSON.stringify(runPanelsOf(groupForBlock(s.sequence, runGroupId), rows, cols, runGrid, s.sequence)));
  const run = useMemo(() => JSON.parse(runKey) as Record<string, PanelRunState>, [runKey]);

  // ---- the panels, in run order, and as the sky draws them
  const rowsModel = useMemo(() => panelRows({
    rows, cols, skip: parseSkip(draft.skip, rows, cols).skip, progress: progressBlock,
    answerPanels: serverPanels, order: String(draft.order), run,
  }), [rows, cols, draft.skip, draft.order, progressBlock, serverPanels, run]);
  const mirror = useMemo(() => (request ? mosaicGrid({
    ra_hours: request.ra_hours, dec_deg: request.dec_deg, rows: request.rows, cols: request.cols,
    overlap: request.overlap, rotation_deg: request.rotation_deg,
    fov_x_deg: request.fov_x_deg, fov_y_deg: request.fov_y_deg,
  }) : []), [request]);
  const skyPanels: SkyPanel[] = useMemo(() => {
    const byLabel = new Map(rowsModel.map((r) => [`${r.row - 1},${r.col - 1}`, r]));
    return (serverPanels ?? mirror).map((pp) => {
      const r = byLabel.get(`${pp.row},${pp.col}`);
      const state: SkyPanel["state"] = r ? panelDrawState(r, r.run) : "pending";
      return {
        row: pp.row, col: pp.col, ra_hours: pp.ra_hours, dec_deg: pp.dec_deg,
        rotation_deg: pp.rotation_deg, order: r?.order ?? undefined, state,
        // The current panel of a held run is drawn as shot (panelDrawState)
        // and told to a screen reader in PANELS' words, never "shooting
        // now" (#451).
        words: r?.run?.kind === "current" ? runLine(r.run) ?? undefined : undefined,
      };
    });
  }, [serverPanels, mirror, rowsModel]);

  // ---- the loop wire (spec 1.4, 2.5)
  const draftMulti = rows * cols > 1;
  const loopLock = useMemo(() => {
    if (!draftMulti) return ONE_PANEL_NO_LOOP;
    const g = {
      ...graph,
      nodes: graph.nodes.map((n) => (n.id === nodeId ? { ...n, params: { ...n.params, rows, cols } } : n)),
    };
    if (loopSource(g, nodeId)) return null;
    if (laneBranched(g, nodeId)) return "this block's stages branch, so no one stage is the last to loop from";
    const tail = laneTail(g, nodeId);
    return tail ? `the last stage, ${NODE_DEFS[tail.type]?.label ?? tail.type}, has no 'pass done' output to loop from`
      : "this block owns no stage to loop";
  }, [draftMulti, graph, nodeId, rows, cols]);
  const wanted = loopTouched ?? (opened.multi ? opened.loop : true);
  const loopOn = draftMulti && loopLock === null && wanted;
  // What DONE tells `withLoop`: `undefined` leaves the wires alone, so a DONE
  // that never touched RUN on a block that was already a mosaic moves no
  // wire; a block that BECOMES a mosaic gets the loop (spec 1.4 "when the
  // wire is added"); a looped mosaic that becomes one panel has its wire
  // lifted, which would otherwise be a pass wire into a 1x1 block.
  const loopArg: boolean | undefined = !draftMulti
    ? (opened.multi && opened.tailWire ? false : undefined)
    : loopTouched !== null ? loopTouched
      : !opened.multi ? true : undefined;

  // ---- RUN: shown only when the block owns a stage
  const ownsStage = useMemo(() => panelLane(graph, nodeId).length > 0, [graph, nodeId]);
  const patch = useMemo(() => framingPatch(stored, draft), [stored, draft]);
  const framedKey = JSON.stringify([patch, loopArg ?? null, settingChanged ? waiting : null]);
  const framed = Object.keys(patch).length > 0 || loopArg !== undefined || settingChanged;
  const needsLocal = ownsStage && framed;
  // The draft compile is `control.capture` on the server; a principal it
  // would refuse is not sent to be refused, and is told why instead.
  const mayCompile = needsLocal && canCompile;
  const [local, setLocal] = useState<{ key: string; compiled: FlowCompileResult | null; error: string | null } | null>(null);
  const framedKeyRef = useRef(framedKey);
  framedKeyRef.current = framedKey;
  const framedInputs = useRef({ graph, patch, loopArg, waiting, settingChanged, recordName });
  framedInputs.current = { graph, patch, loopArg, waiting, settingChanged, recordName };
  useEffect(() => {
    if (!mayCompile) return;
    const key = framedKey;
    const t = setTimeout(() => {
      const f = framedInputs.current;
      const g = framedGraph(f.graph, nodeId, f.patch, f.loopArg, f.settingChanged ? { whenWaiting: f.waiting } : null);
      framingApi.compileDraft(g, f.recordName).then(
        (c) => { if (framedKeyRef.current === key) setLocal({ key, compiled: c, error: null }); },
        (e) => {
          if (framedKeyRef.current === key) {
            setLocal({ key, compiled: null, error: `the compile of this layout failed: ${(e as Error)?.message ?? e}` });
          }
        });
    }, framingTiming.settleMs);
    return () => clearTimeout(t);
  }, [mayCompile, framedKey, nodeId]);
  const localHere = local !== null && local.key === framedKey;
  const runCompiled = needsLocal ? (localHere ? local.compiled : null) : compiled;
  const read = compiledReadouts(runCompiled, nodeId);
  const readouts = read && read.ok ? read.value : null;
  const runRig = compiledRig(runCompiled) ?? compiledRig(compiled);
  const lines = readouts ? runLines(readouts, runRig) : null;
  const linesNote = readouts ? null
    : needsLocal && !canCompile ? COMPILE_NEEDS_ACCESS
      : needsLocal && !localHere ? WAITING_FOR_COMPILE
        : needsLocal && local?.error ? local.error
          : runCompiled === null ? NO_COMPILE
            : read && !read.ok ? `the compile's RUN numbers could not be read: ${read.why}`
              : NO_READOUT;
  const counts = countsAttempts(node) ? countsNotice({ nodes: [node] }, countsNote) : null;
  const campaign = campaignLine(readouts, tonight, canSite);

  // The Tonight answer, for the campaign line: site-derived, so fetched only
  // for a holder of the site view, once, when the sheet opens on a saved
  // flow whose block has stages to run.
  const tonightAsked = useRef(false);
  useEffect(() => {
    if (tonightAsked.current || !canSite || !ownsStage || !recordId || tonight !== null || tonightLoading) return;
    tonightAsked.current = true;
    void fetchTonight();
  }, [canSite, ownsStage, recordId, tonight, tonightLoading, fetchTonight]);

  // ---- the re-frame question (spec 2.5, Revision 2 ruling 3)
  const decision = reframeDecision({ stored, draft, progress: progressBlock, view });

  // ---- the angle a grid is laid out at (S5 orchestrator ruling 1, #411)
  const angleLock = gridAngleLock(draft);
  const offer = angleOffer(draft, skyAngle, rig);
  const measuredLine = useMeasuredLine(skyAngle, Date.now() / 1000);
  // The offer is taken against the draft as it is at the press, never the
  // one it was drawn for: pressed twice, or after a drag, it writes the
  // measurement or nothing.
  const onTakeOffer = () => setDraft((d) => {
    const o = angleOffer(d, skyAngle, rig);
    return o ? takeOffer(d, o) : d;
  });

  // ---- DONE
  const commit = async () => {
    setQuestion(null);
    setBusy(true);
    try {
      // The flow setting first: it writes the graph but compiles nothing, so
      // the one compile the framing write starts covers it too.
      if (settingChanged) setSetting("whenWaiting", waiting);
      const before = useStore.getState().flows.graph;
      const applied = applyFraming(nodeId, patch, loopArg);
      const wrote = useStore.getState().flows.graph !== before;
      await applied;
      // A DONE that changed only the flow setting leaves `flowsApplyFraming`
      // nothing to write and so nothing to compile; the setting reaches every
      // block's `mosaic.when_waiting` only through a compile.
      if (settingChanged && !wrote) await compileFlow();
    } finally {
      setBusy(false);
    }
    // ONE CLOSE PER SHEET (#382). CANCEL and Escape refuse while this write
    // is in flight (`requestClose`), but the host can still take the sheet
    // down meanwhile, as a browser Back does in #/next. That close has
    // happened; a second onClose would pop whatever sits under the modal.
    if (alive.current) onClose();
  };
  const onDone = () => {
    if (busy) return;
    if (decision.ask) { setQuestion(decision.question); return; }
    void commit();
  };
  // A grid with no angle says so first: it is the operator's to fix, and
  // waiting for the server's answer does not fix it.
  const doneReason = angleLock ?? (done.locked ? done.reason : busy ? CHECKING : null);
  // CANCEL, Escape and the scrim, while DONE's write waits for its compile.
  // By then the write has happened, so CANCEL can no longer mean "write
  // nothing"; and a close taken now was followed by DONE's own when the
  // compile answered, running the host's onClose twice (#382). So they refuse
  // and say why. The wait is bounded: the compile times out with the api's
  // 15 s, and flowsCompile settles either way, so this never traps anyone.
  const cancelReason = busy ? CHECKING : null;
  // The draft is FROZEN over the same wait, for the same reason: the write
  // took the draft as it stood when DONE was pressed, and the sheet closes
  // when the compile answers, so an edit made now (a name typed, a column
  // added, a panel tapped) was drawn, accepted and then dropped with the
  // sheet, stored nowhere. Every control and every sky gesture that edits the
  // draft goes quiet, as in view mode, until the sheet closes.
  const frozen = readOnly || busy;
  const requestClose = useCallback(() => {
    if (cancelReason) { setExplained(cancelReason); return; }
    onClose();
  }, [cancelReason, onClose]);
  // A reason explained for a press says why THAT press did nothing; once the
  // draft moves or DONE's lock changes it may no longer be true, so it goes.
  // So does the re-frame question, for a stronger reason: its RE-FRAME
  // commits whatever the draft is when it is pressed. Left standing while
  // the operator keeps editing, it would commit a layout the question never
  // described, and one DONE would refuse while its server answer is still
  // out (#377). Pressing DONE again asks about the layout on screen.
  useEffect(() => { setExplained(null); setQuestion(null); }, [draft, done.locked]);

  // ---- sky gestures, all into the local draft and session
  const setCentre = useCallback((ra: number, dec: number) =>
    patchDraft({ ra: raHms(ra), dec: decDms(dec) }), [patchDraft]);
  const onSkyRotate = useCallback((deg: number) => setDraft((d) => {
    const m = angleOf(d);
    const nd = m === "Any angle"
      ? setAngleMode(d, rig?.has_rotator === false ? "Camera fixed at PA" : "Rotate to PA") : d;
    return { ...nd, rotation: wrap360(round1(deg)) };
  }), [rig]);
  const onZoom = useCallback((deg: number) => {
    const z = clampZoom(deg);
    setSky((s) => ({ ...s, fovZoomDeg: z }));
    saveViewPrefs(recordId, nodeId, { zoomDeg: z });
  }, [recordId, nodeId]);
  const onPanelTap = useCallback((row0: number, col0: number) => setDraft((d) => {
    const g = gridOf(d);
    return { ...d, skip: toggleSkip(d.skip, g.rows, g.cols, row0 + 1, col0 + 1) };
  }), []);
  const onMoveGrid = useCallback((on: boolean) => setSky((s) => ({
    ...s, moveGrid: on, viewCentre: on ? (s.viewCentre ?? draftCentre(draft)) : null,
  })), [draft]);

  // ---- section handlers
  // A grid change touches the grid and nothing else. A draft at ANY ANGLE
  // that becomes a grid stays at ANY ANGLE and owes an angle, which DONE's
  // lock asks for and the offer can pay; S4 set it to ROTATE TO here, whose
  // "none" is 0, an angle nobody chose (S5 orchestrator ruling 1, #411).
  const setGrid = (over: Partial<FramingDraft>) => setDraft((d) => ({ ...d, ...over }));
  const suggestion = sky.target ? suggestGrid(sky.target.size_arcmin ?? 0, draft) : null;
  const suggestLock = !sky.target ? NO_OBJECT
    : !(sky.target.size_arcmin && sky.target.size_arcmin > 0) ? NO_SIZE
      : suggestion === null ? gridLock(draft, rig) ?? "no camera field to size the grid by"
        : gridLock(draft, rig);
  const fitLock = sky.target || (layout.fov_x > 0 && layout.fov_y > 0) ? null : NO_OBJECT;
  const onFit = () => {
    const size = (sky.target?.size_arcmin ?? 0) / 60;
    onZoom(size > 0 ? 1.6 * size : fitZoom(draft));
  };
  const rotationNum = Number.isFinite(layout.rotation_deg) ? (layout.rotation_deg as number) : 0;

  const stripText = readoutStrip(draft);
  const move = moveLine(view);
  const nightCard = canSite && centre && draftMulti && layout.fov_x > 0 && layout.fov_y > 0 ? (
    <MosaicNightCard
      raHours={centre.ra_hours} decDeg={centre.dec_deg} rows={rows} cols={cols}
      overlap={layout.overlap} rotationDeg={rotationNum}
      fovXDeg={layout.fov_x} fovYDeg={layout.fov_y}
      altLimitDeg={site?.horizon_min_deg ?? 0}
    />
  ) : null;
  const name = String(draft.name ?? "").trim();

  return (
    <Overlay open label={`Frame ${name || "target"}`} onClose={requestClose} variant="full" bodyClassName="tfs-host">
      <div className="tfs" data-testid="target-framing-sheet"
        data-typing={typing ? "true" : undefined} data-view={readOnly ? "true" : undefined}>
        <header className="tfs-head" data-testid="framing-header">
          <HonestButton className="tfs-btn" reason={cancelReason} onClick={onClose} onExplain={setExplained}>
            {readOnly ? "CLOSE" : "CANCEL"}
          </HonestButton>
          <h2 className="tfs-title" title={name}>{`FRAME ${name || "TARGET"}`}</h2>
          {!readOnly && (
            <HonestButton className="tfs-btn tfs-done" reason={doneReason} onClick={onDone} onExplain={setExplained}>
              <span data-testid="framing-done">DONE</span>
            </HonestButton>
          )}
        </header>
        {viewWhy && <div className="tfs-status" role="status" data-testid="framing-view-why">{viewWhy}</div>}
        {!readOnly && done.chip && <div className="tfs-status tfs-chip-line" role="status" data-testid="framing-chip">{done.chip}</div>}
        {!readOnly && explained && (
          <div className="tfs-status" role="status" data-testid="framing-explain">{explained}</div>
        )}
        {question && (
          <div className="tfs-question" role="alertdialog" aria-label="Re-frame restarts the counts" data-testid="framing-question">
            <p>{question}</p>
            <div className="tfs-chips">
              <button type="button" className="tfs-btn tfs-on" onClick={() => void commit()}>RE-FRAME</button>
              <button type="button" className="tfs-btn" onClick={() => setQuestion(null)}>KEEP EDITING</button>
            </div>
          </div>
        )}
        <div className="tfs-body">
          <div className="tfs-sky" data-testid="framing-sky">
            <FramingSky
              frameCentre={skyCentre}
              viewCentre={sky.viewCentre}
              moveGrid={sky.moveGrid}
              panels={skyPanels}
              panelFov={layout.fov_x > 0 && layout.fov_y > 0 ? { fov_x_deg: layout.fov_x, fov_y_deg: layout.fov_y } : null}
              rotationDeg={rotationNum}
              zoomDeg={sky.fovZoomDeg}
              survey={sky.survey}
              optics={optics}
              mosaic={{ rows, cols, overlap: layout.overlap }}
              catalogTarget={sky.target}
              night={night}
              onlineFetch={config?.survey?.online_fetch ?? false}
              readOnly={frozen}
              onMoveGrid={onMoveGrid}
              onFrameCentre={setCentre}
              onViewCentre={(ra, dec) => setSky((s) => ({ ...s, viewCentre: { ra_hours: ra, dec_deg: dec } }))}
              onRotate={onSkyRotate}
              onZoom={onZoom}
              onPanelTap={onPanelTap}
            />
          </div>
          <div className="tfs-controls">
            <div className="tfs-strip tfs-mono" data-testid="framing-strip">
              <div>{stripText}</div>
              {/* The angle the grid is laid out at, and for a grid that owes
                  one, the measured angle offered in one press (#411). The
                  strip is outside the fieldset, so the offer is left out
                  while the draft is frozen rather than disabled by it. */}
              <div>
                <span data-testid="framing-strip-angle">{stripAngle(draft)}</span>
                {offer && !frozen && (
                  <>
                    {" "}
                    <button type="button" className="tfs-btn tfs-on" data-testid="framing-strip-offer"
                      title={measuredLine ?? undefined} onClick={onTakeOffer}>
                      {offer.label}
                    </button>
                  </>
                )}
              </div>
              {move && <div data-testid="framing-move">{move}</div>}
            </div>
            <div
              className="tfs-scroller"
              data-testid="framing-scroller"
              onFocus={(e) => setTyping(isTextField(e.target))}
              onBlur={(e) => setTyping(isTextField(e.relatedTarget))}
            >
              <fieldset className="tfs-fieldset" disabled={frozen}>
                <legend className="sr-only">{`Framing of ${name || "this target"}`}</legend>
                <WhereSection
                  name={String(draft.name)} ra={String(draft.ra)} dec={String(draft.dec)}
                  suggestion={suggestion} suggestLock={suggestLock} fitLock={fitLock}
                  onName={(t) => patchDraft({ name: t })}
                  onRa={(t) => patchDraft({ ra: t })}
                  onDec={(t) => patchDraft({ dec: t })}
                  onPick={(e) => {
                    setSky((s) => ({ ...s, target: e }));
                    patchDraft({ name: e.name || e.id, ra: raHms(e.ra_hours), dec: decDms(e.dec_deg) });
                  }}
                  onFit={onFit}
                  onSuggest={() => { if (suggestion) setGrid({ cols: suggestion.cols, rows: suggestion.rows }); }}
                  explain={setExplained}
                />
                <GridSection
                  cols={cols} rows={rows} overlap={Math.round(layout.overlap * 100)}
                  lock={gridLock(draft, rig)}
                  cameraLine={cameraFieldLine(draft)}
                  matchLock={matchCameraLock(rig)}
                  drift={driftBanner(draft, rig)}
                  onCols={(n) => setGrid({ cols: n })}
                  onRows={(n) => setGrid({ rows: n })}
                  onOverlap={(pct) => patchDraft({ overlap: pct })}
                  onMatchCamera={() => setDraft((d) => matchCamera(d, rig))}
                  explain={setExplained}
                />
                <AngleSection
                  mode={mode}
                  rotation={mode === "Any angle" ? "" : String(draft.rotation)}
                  locks={angleLocks(draft, rig)}
                  measured={measuredLine}
                  offer={offer?.label ?? null}
                  tolerance={toleranceLine(readouts?.angle_tolerance_deg ?? null, draft)}
                  degreeLock={mode === "Any angle" ? ANY_ANGLE_HOLDS_NONE : null}
                  onMode={(m: TargetAngle) => setDraft((d) => setAngleMode(d, m))}
                  onRotation={(t) => patchDraft({ rotation: t })}
                  onNudge={(delta) => setDraft((d) => ({ ...d, rotation: wrap360(round1((layoutOf(d).rotation_deg ?? 0) + delta)) }))}
                  onUseMeasured={() => setDraft((d) => useMeasured(d, skyAngle))}
                  onOffer={onTakeOffer}
                  explain={setExplained}
                />
                <PanelsSection
                  rows={rowsModel}
                  order={String(draft.order)}
                  showAltitude={canSite}
                  nightCard={nightCard}
                  onOrder={(v) => patchDraft({ order: v })}
                  onToggle={(r, c) => onPanelTap(r - 1, c - 1)}
                />
                {ownsStage && (
                  <RunSection
                    onePanel={!draftMulti}
                    noMosaic={!draftMulti && !graph.nodes.some((n) => n.id !== nodeId && isMultiPanel(n))}
                    loop={loopOn}
                    loopLock={loopLock}
                    passes={layoutNum(draft.passes, 1)}
                    minVisit={layoutNum(draft.minVisit, 0)}
                    whenWaiting={waiting}
                    lines={lines}
                    linesNote={linesNote}
                    countsNotice={counts}
                    campaign={campaign}
                    onLoop={(on) => setLoopTouched(on)}
                    onPasses={(n) => patchDraft({ passes: n })}
                    onMinVisit={(m) => patchDraft({ minVisit: m })}
                    onWhenWaiting={setWaiting}
                    explain={setExplained}
                  />
                )}
                <CentringSection
                  tolerance={layoutNum(draft.centerTol, 1.2)}
                  tries={layoutNum(draft.centerTries, 3)}
                  ifNotCentred={String(draft.ifNotCentred)}
                  onTolerance={(v) => patchDraft({ centerTol: v })}
                  onTries={(n) => patchDraft({ centerTries: n })}
                  onIfNotCentred={(v) => patchDraft({ ifNotCentred: v })}
                  explain={setExplained}
                />
              </fieldset>
            </div>
          </div>
        </div>
      </div>
    </Overlay>
  );
}

/** A draft number as a stepper shows it: the value, or `fallback` when the
 *  text does not parse. */
function layoutNum(v: string | number, fallback: number): number {
  const n = typeof v === "number" ? v : parseFloat(v);
  return Number.isFinite(n) ? n : fallback;
}
