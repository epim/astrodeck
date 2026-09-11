// canvasModel.ts - every pure fact the rebuilt Flows canvas needs, in one
// store-free, React-free, DOM-free module (wave R7, task T-R7-1).
//
// WHY THESE LIVE HERE AND NOT IN `components/flows/*`. The wave's section 2.1
// finding: `wireLane`, `wireAnchors`, `wireStroke`, `wireWidth`, `wireDash`,
// `resolveWireDrop`, `PALETTE_FALLBACK_DROP`, `logTail`, `tapWireHint` and the
// two log constants are PURE, but each of them is declared inside a legacy
// PRESENTATION module (`FlowWireLayer.tsx`, `FlowCanvas.tsx`, `FlowPalette.tsx`,
// `FlowLogStrip.tsx`, `FlowTapWireBar.tsx`). Importing one from `next` would
// pull the whole legacy component - and its transitive Tailwind/`Panel` tree -
// into the lazily split next bundle, which is exactly what D-FU-2 spent a wave
// undoing. So the helpers get a new home here and the legacy files keep their
// own copies for `#/classic`; nothing under `ui/src/components/**` is edited.
//
// WHAT IS *NOT* RE-DERIVED. `geometry.ts` is a LOGIC module and stays shared:
// `portPos`, `edgePath`, `clampZoom`, `nodeW`, `fitView`, `NODE_HEADER_H`,
// `PORT_ROW_H` and the zoom clamps are imported, never restated. Every anchor,
// bezier and card metric on the rebuilt canvas therefore comes from the same
// arithmetic the legacy canvas and the reference captures use - including the
// documented 1 px attachment offset, which is the published contract.
//
// TWO COPY DEFECTS ARE FIXED HERE, both em-dashes the wave's rules forbid:
// `IDLE_LOG_TEXT` (section 6.1 defect 4) and the tap-to-wire hint's dash.

import { NODE_DEFS } from "../../../../../components/flows/nodeDefs";
import { portPos, type FlowTier, type Point, type PortDir } from "../../../../../components/flows/geometry";
import type {
  FlowEdgeRec, FlowLogLine, FlowLogTone, FlowNodeRec, FlowNodeStatus,
  FlowRunPhase, PortKind,
} from "../../../../../components/flows/flowsTypes";
import type { Tone } from "../../../../ui";
// The loss word the pill prints, taken from the one module that owns the
// compile-mark vocabulary (re-exported further down with the rest of it) rather
// than spelled a second time here.
import { MARK_LOST as MARK_LOST_WORD } from "../inspector/issues";

// ------------------------------------------------------------------ wiring

/** Transparent hit band on a wire, in WORLD units. At zoom 0.35 it is ~4.9
 *  screen px and at 1.6 it is ~22 - it scales with the graph, like the wire it
 *  is catching for. A 1.8 px stroke is a ~0.6 px target at the 46% zoom the
 *  reference captures use, so without this band a wire cannot be selected. */
export const HIT_W_CANVAS = 14;
/** The phone stage list never zooms, so 16 is 16. */
export const HIT_W_AUTO = 16;

/** Where a stage lands when the palette has no canvas to measure. The canvas's
 *  own `flowCanvasDropPoint()` answers null in that case and the caller owns the
 *  fallback; this is it. Two stages dropped with no canvas land on top of each
 *  other, exactly as they do in the prototype under the same conditions. */
export const PALETTE_FALLBACK_DROP = { x: 120, y: 120 } as const;

/** Fraction of the lane colour an idle wire is drawn at. */
const IDLE_MIX = "45%";

/** Which lane a wire is in - decided by the SOURCE port's kind, never the
 *  target's. A kind mismatch cannot be wired in the first place, so the two
 *  agree on every edge the editor can produce; a graph that arrived some other
 *  way still gets drawn in the lane it leaves from. Falls back to "flow" for an
 *  unknown node or port, which costs nothing: an edge whose ports cannot be
 *  resolved has no anchors either, so `wireAnchors` drops it first. */
export function wireLane(edge: FlowEdgeRec, nodes: readonly FlowNodeRec[]): PortKind {
  const from = nodes.find((n) => n.id === edge.from);
  const def = from ? NODE_DEFS[from.type] : undefined;
  const port = def?.outs.find((p) => p.id === edge.fromPort);
  return port ? port.kind : "flow";
}

/** The two port anchors a wire runs between, or null when either end cannot be
 *  placed.
 *
 *  Null happens for real: a saved graph can name a node that has been deleted or
 *  a port the vocabulary has since dropped. `portPos` refuses to guess there, so
 *  the caller SKIPS the wire rather than drawing it somewhere plausible - a wire
 *  anchored inside a card header reads as a deliberate connection. */
export function wireAnchors(
  edge: FlowEdgeRec,
  nodes: readonly FlowNodeRec[],
  tier: FlowTier,
): { p1: Point; p2: Point } | null {
  const a = nodes.find((n) => n.id === edge.from);
  const b = nodes.find((n) => n.id === edge.to);
  if (!a || !b) return null;
  const p1 = portPos(a, edge.fromPort, "out", NODE_DEFS, tier);
  const p2 = portPos(b, edge.toPort, "in", NODE_DEFS, tier);
  return p1 && p2 ? { p1, p2 } : null;
}

/** Where a wire's remove control goes: the straight-line midpoint of the two
 *  PORT ANCHORS.
 *
 *  That midpoint is exactly B(0.5) for the canvas bezier, because `edgePath`
 *  puts symmetric +c / -c offsets on the control points and they cancel in
 *  pairs. So "the button always sits on the curve" is an identity, not an
 *  approximation - which is why this lives beside `wireAnchors`: the two must
 *  never drift apart. */
export function wireMidpoint(p1: Point, p2: Point): Point {
  return { x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 };
}

/** A run owns the rig. `holding` and `stopping` count: a cloud hold IS a run
 *  with its wires marching, and a graph that went dead the moment the engine
 *  said "aborting" would claim the rig was free while the mount was still
 *  moving. */
export function isRunning(phase: FlowRunPhase): boolean {
  return phase !== "idle";
}

/** Is this wire carrying the run right now? `ok` counts, not just `busy` - a
 *  finished stage's OUTGOING wires keep marching for the rest of the run, which
 *  is what the whole dusk -> dome -> target chain lit at once shows. */
export function isWireActive(running: boolean, status: string): boolean {
  return running && (status === "busy" || status === "ok");
}

/** Stroke colour. Selection outranks activity, which outranks the idle lane. */
export function wireStroke(kind: PortKind, active: boolean, selected: boolean): string {
  if (selected) return "var(--text)";
  const lane = kind === "flow" ? "--accent" : "--warn";
  return active ? `var(${lane})` : `color-mix(in srgb, var(${lane}) ${IDLE_MIX}, transparent)`;
}

export function wireWidth(active: boolean, selected: boolean): number {
  return active || selected ? 2.5 : 1.8;
}

/** The dash pattern - and the reason a wire's state is never colour alone.
 *
 *  Three states, three silhouettes: a live wire is `7 6` (and marches), an event
 *  wire is `4 5`, a resting flow wire is solid. Night mode collapses the lane
 *  hues toward one another and `prefers-reduced-motion` stops the march, so the
 *  pattern is what still says which lane a wire is in and whether it is carrying
 *  the run. Selection deliberately gets no dash of its own - it changes colour
 *  and width - so the pattern keeps reading the lane while a wire is picked. */
export function wireDash(kind: PortKind, active: boolean): string | undefined {
  if (active) return "7 6";
  return kind === "event" ? "4 5" : undefined;
}

// -------------------------------------------------------------- drop decision

/** What a wire drop resolved to. A refusal carries the sentence to toast, or
 *  `null` when the refusal is SILENT - two of the rules refuse without saying
 *  anything, and that is not the same as "no refusal". */
export type WireDropResult =
  | { ok: true; nodeId: string; portId: string }
  | { ok: false; refusal: string | null };

/** The wire-drop grammar, as one pure function so it can be read in one place
 *  and exercised without a DOM. `portAttr` is the raw `data-port` value of
 *  whatever the pointerup landed on, or null for a miss.
 *
 *  The three fields are pipe-delimited and UNESCAPED, which is why
 *  `flowsSlice.nextNodeId()` mints ids that cannot contain a `|`. */
export function resolveWireDrop(
  wire: { from: string; fromPort: string },
  portAttr: string | null,
  kindOf: (nodeId: string, portId: string, dir: PortDir) => PortKind | null,
): WireDropResult {
  // Dropped on empty canvas. Clears the wire, no toast, no edge.
  if (!portAttr) return { ok: false, refusal: null };

  const [nodeId, portId, dir] = portAttr.split("|");
  // Only an INPUT accepts a drop. There is no reverse drag.
  if (dir !== "in" || !nodeId || !portId) return { ok: false, refusal: null };
  // Self-wiring is refused SILENTLY on a drag: a drag that ends where it started
  // is usually a mis-grab, not an attempt. (Tap-to-wire says so out loud, and
  // the asymmetry is deliberate.)
  if (nodeId === wire.from) return { ok: false, refusal: null };

  // The lanes are the grammar. A flow port carries the single run cursor, an
  // event port fires any number of times, and the engine runs them through
  // different machinery - so the mismatch is refused, with a sentence.
  const kOut = kindOf(wire.from, wire.fromPort, "out");
  const kIn = kindOf(nodeId, portId, "in");
  if (kOut && kIn && kOut !== kIn) {
    return {
      ok: false,
      refusal:
        `${kOut === "flow" ? "Flow" : "Event"} output can't feed `
        + `${kIn === "flow" ? "a flow" : "an event"} input`,
    };
  }
  // Single-occupancy inputs and "no toast on success" are the store's:
  // `flowsConnect` filters the incumbent out before it concats.
  return { ok: true, nodeId, portId };
}

/** A port's lane, read off a live node list. Nullable on purpose: a saved graph
 *  can name a port the vocabulary has since dropped, and "unknown" must not be
 *  silently treated as a matching lane. */
export function portKind(
  nodes: readonly FlowNodeRec[],
  nodeId: string,
  portId: string,
  dir: PortDir,
): PortKind | null {
  const n = nodes.find((x) => x.id === nodeId);
  const def = n ? NODE_DEFS[n.type] : undefined;
  if (!def) return null;
  const p = (dir === "in" ? def.ins : def.outs).find((q) => q.id === portId);
  return p ? p.kind : null;
}

/** The `data-port` value a drop is resolved against. One writer, one reader. */
export function portAttr(nodeId: string, portId: string, dir: PortDir): string {
  return `${nodeId}|${portId}|${dir}`;
}

// -------------------------------------------------------------------- status

/** `flows.statuses` is a loose `Record<string, string>` because it is filled
 *  from a WS frame. Anything the vocabulary does not know reads as idle - an
 *  unknown word must not blank the status, which would look like "no stage
 *  here". */
export function asNodeStatus(raw: string | undefined): FlowNodeStatus {
  return raw === "busy" || raw === "ok" || raw === "warn" || raw === "bad" ? raw : "idle";
}

/** The status WORD a stage shows. Never a colour on its own: this is what the
 *  pill and the dot's accessible name both print. */
export const NODE_STATUS_WORD: Record<FlowNodeStatus, string> = {
  idle: "IDLE", busy: "BUSY", ok: "DONE", warn: "WARN", bad: "FAILED",
};

/** Design tone per stage status. `busy` is accent, `ok` good, and idle is dim
 *  rather than absent, so a stage always has a state on the card. */
export const NODE_STATUS_TONE: Record<FlowNodeStatus, Tone> = {
  idle: "dim", busy: "accent", ok: "good", warn: "warn", bad: "bad",
};

// The mark a stage carries when the compile has something to say about it -
// THREE levels, three words, one home.
//
// The words used to live here as `lossLabel`/`lossTone`, over a two-member
// union that could not express a `note` at all. That was fine while every
// `nodes.<type>` entry the engine emitted was a `warn`; the moment the standard
// node-settings entries moved to `note` (server `to_plan`), a canvas that knew
// only "loss or nothing" had to either paint a note amber or drop it. It did
// both, in that order, and a clean flow read as a failing build.
//
// `../inspector/issues` owns them now, because the inspector panel, this card
// and the phone stage row must print the SAME word, and the module is pure with
// no stylesheet of its own - importing it here costs the canvas chunk nothing
// (importing the inspector BARREL would pull `inspector.css` in, which is the
// trap `hubs/sky/sheets/quick.tsx` documents).
export {
  markWord, markTone, isLoss, nodeMarkLevel, nodeMarkDetail, rigValueFor,
  lossCount, worstLoss,
  MARK_LOST, MARK_PARTIAL, MARK_RIG, RIG_VALUE_PREFIX,
  type UnmappedLevel,
} from "../inspector/issues";

// ----------------------------------------------------------------------- log

/** How many of the store's 120-entry ring the expanded panel shows. */
export const LOG_TAIL = 60;

/** Shown when the ring is empty.
 *
 *  It is the literal truth and it must stay the literal truth: there is no
 *  `flow.log` event, so the ring can stay empty through a whole run and "no
 *  events yet" is then the only honest thing on screen.
 *
 *  WAVE R7 DEFECT 4: the legacy constant spells this with an em-dash. The copy
 *  rule is hyphens, never em-dashes. */
export const IDLE_LOG_TEXT = "Idle - no events yet";

/** Tone per log rung. NOT the toast ladder: the log's default rung is dim where
 *  a toast's is accent. Colour is redundant here rather than load-bearing - the
 *  line itself is a sentence, so a reader who cannot see the hue still gets the
 *  meaning. */
export const LOG_TONE: Record<FlowLogTone, Tone> = {
  info: "dim", good: "good", warn: "warn", bad: "bad",
};

/** The newest `LOG_TAIL` lines, NEWEST FIRST.
 *
 *  Newest-first is the whole reason the panel is usable without scrolling: the
 *  line you want mid-run is the one that just landed, and a chronological panel
 *  puts it at the bottom of a box that is already scrolled to the top. */
export function logTail(logs: readonly FlowLogLine[]): FlowLogLine[] {
  return logs.slice(-LOG_TAIL).reverse();
}

/** `HH:MM:SS`. en-GB with `hour12: false` is what gives the 24-hour, zero-padded
 *  form regardless of the viewer's locale - a rig log that reads "2:04:11 pm" on
 *  one machine and "14:04:11" on another cannot be compared to a night log. */
export function logTime(ts: number): string {
  return new Date(ts).toLocaleTimeString("en-GB", { hour12: false });
}

// ------------------------------------------------------------- tap-to-wire

/** The armed tap-to-wire hint, or null when the armed port cannot be named.
 *
 *  A missing node (deleted from under an armed wire) or a missing port returns
 *  null and the bar does not render - a bar reading "WIRING: undefined" is worse
 *  than no bar, because it claims an arm the graph can no longer complete.
 *
 *  The legacy sentence carries an em-dash; the wave's copy rule is a hyphen, so
 *  the dash is the one thing that changes. */
export function tapWireHint(
  label: string | null | undefined,
  portLabel: string | null | undefined,
): string | null {
  if (!label || !portLabel) return null;
  return `WIRING: ${label} · ${portLabel} - tap an input port`;
}

// -------------------------------------------------------------- toolbar copy

/** `m:ss`, or `-` when the rig has not said.
 *
 *  Minutes are unbounded (`73:05`): there is no specified hour form, so the
 *  least-committal rendering is the one that never truncates a real number.
 *  NEVER a client-side countdown from an assumed total - an ETA the client
 *  invented looks identical to one the rig computed. */
export function formatEta(secs: number | null | undefined): string {
  if (secs == null || !Number.isFinite(secs) || secs < 0) return "-";
  const s = Math.round(secs);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** The validation pill's word once the checker has answered.
 *
 *  TWO different facts, in priority order, because they are not the same news:
 *  `losses` is a promise the graph makes that the run will not keep, `openChecks`
 *  is the doctor's advice about a good night. A loss outranks advice, so the
 *  count the pill prints is the one an operator would act on first.
 *
 *  WHY THE LOSS COUNT IS HERE AT ALL. The pill graded `compiled.issues` and
 *  nothing else, so a flow whose compile said "nothing will bind the dome or
 *  close it on an unsafe reading during this run" - a DANGER row, two inches
 *  below on the same screen - still read GRAPH VALID in green. Green here has
 *  always meant "safe to press RUN". Seen on the probe, 2026-09-11.
 *
 *  NOTES DO NOT COUNT, and that is the other half: `lossCount` skips them, so
 *  a clean flow with ten "the run takes this from the rig" notes stays green. */
export function checksLabel(openChecks: number, losses = 0): string {
  if (losses > 0) return losses === 1 ? `1 ${MARK_LOST_WORD}` : `${losses} ${MARK_LOST_WORD}`;
  if (openChecks <= 0) return "GRAPH VALID";
  return openChecks === 1 ? "1 OPEN CHECK" : `${openChecks} OPEN CHECKS`;
}

/** What the pill says when the graph it graded is not the graph RUN would
 *  execute.
 *
 *  `flowsCompile` posts the DRAFT (`flowsApi.compileDraft(graph, name)`), while
 *  `POST /api/flows/{id}/run` compiles the STORED record. With unsaved edits
 *  those are two different graphs, so a bare GRAPH VALID would be a claim about
 *  a flow nobody can start. The prefix says which one was graded; RUN is locked
 *  meanwhile (see {@link unsavedRunReason}), so the two controls agree. */
export const CHECKS_DRAFT_PREFIX = "DRAFT: ";

/** The pill's whole text: unknown, the draft's verdict, or the stored flow's. */
export function checksWord(
  checked: boolean, openChecks: number, dirty: boolean, losses = 0,
): string {
  if (!checked) return CHECKS_UNKNOWN;
  const word = checksLabel(openChecks, losses);
  return dirty ? `${CHECKS_DRAFT_PREFIX}${word}` : word;
}

/** The pill's tone. GOOD is reserved for "the flow RUN would start is clean":
 *  with unsaved edits a clean draft is dim, not green, because green here has
 *  always meant "safe to press RUN". */
export function checksTone(
  checked: boolean, openChecks: number, dirty: boolean,
  worst: "warn" | "danger" | null = null,
): Tone {
  if (!checked) return "dim";
  // A blocking loss is coral: `/run` refuses it outright, which is a different
  // state from "this will not do everything you drew".
  if (worst === "danger") return "bad";
  if (worst === "warn" || openChecks > 0) return "warn";
  return dirty ? "dim" : "good";
}

/** The popover's sentence while the checks describe the draft. */
export const CHECKS_DRAFT_WHY =
  "These checks are on the graph as drawn. RUN starts the SAVED flow, so save first "
  + "and they describe what the rig will do.";

// --------------------------------------------------------- saving and running

/** The state word beside SAVE. Three states, three WORDS: an absence cannot say
 *  "your edits are stored", and a colour cannot say it either. */
export const SAVE_STATE_DIRTY = "UNSAVED EDITS";
export const SAVE_STATE_CLEAN = "SAVED";
export const SAVE_STATE_READONLY = "READ ONLY";

export function saveStateWord(dirty: boolean, readonly: boolean): string {
  if (readonly) return SAVE_STATE_READONLY;
  return dirty ? SAVE_STATE_DIRTY : SAVE_STATE_CLEAN;
}

export function saveStateTone(dirty: boolean, readonly: boolean): Tone {
  return !readonly && dirty ? "warn" : "dim";
}

/** Why SAVE cannot act on an example flow. `flowsSave` declines a `readonly`
 *  record before it reaches the network, and the server refuses it too, so the
 *  control has to say that rather than look pressable and do nothing. */
export const SAVE_READONLY_REASON =
  "This is an example flow - the rig will not store changes to it, so there is nothing to save.";

/** Why SAVE cannot act with nothing changed. Not hidden: a SAVE that came and
 *  went would leave the operator unsure whether the last press landed. */
export const SAVE_CLEAN_REASON = "Nothing has changed since this flow was last saved.";

export function saveLockReason(dirty: boolean, readonly: boolean): string | null {
  if (readonly) return SAVE_READONLY_REASON;
  return dirty ? null : SAVE_CLEAN_REASON;
}

/** RUN executes the STORED flow, so unsaved edits are a refusal, not a silent
 *  save.
 *
 *  Two answers were available - save then run, or refuse - and this is the
 *  refusal, for two reasons. A save can fail (an example flow refuses one
 *  outright, and a PUT can 409), and a save-then-run that swallowed that would
 *  start the OLD graph while the operator watched their edit on screen and
 *  believed it went with it. And with the refusal in place the validation pill
 *  can go on describing exactly the graph RUN will execute, which is the second
 *  half of the same finding. */
export const RUN_UNSAVED_REASON = "This flow has unsaved changes - save it first.";

/** The example-flow case, which has no SAVE to send the operator to. It names
 *  the blocker AND the way out; a lock with neither is a dead end. */
export const RUN_UNSAVED_EXAMPLE_REASON =
  "This example flow cannot be saved, so RUN would start the stored version, not the one "
  + "drawn here. Leave the flow and open it again to drop these edits.";

export function unsavedRunReason(dirty: boolean, readonly: boolean): string | null {
  if (!dirty) return null;
  return readonly ? RUN_UNSAVED_EXAMPLE_REASON : RUN_UNSAVED_REASON;
}

// ------------------------------------------------------------- wire, in words

/** A wire as two phrases, for a surface that cannot draw one.
 *
 *  Null on the same condition `wireAnchors` returns null - a saved graph can
 *  name a node or a port the vocabulary has since dropped - because a row
 *  offering to remove "undefined -> undefined" claims a wire nobody can see. */
export function wireRowLabel(
  edge: FlowEdgeRec,
  nodes: readonly FlowNodeRec[],
): { out: string; into: string } | null {
  const a = nodes.find((n) => n.id === edge.from);
  const b = nodes.find((n) => n.id === edge.to);
  if (!a || !b) return null;
  const da = NODE_DEFS[a.type];
  const db = NODE_DEFS[b.type];
  if (!da || !db) return null;
  const pa = da.outs.find((p) => p.id === edge.fromPort);
  const pb = db.ins.find((p) => p.id === edge.toPort);
  if (!pa || !pb) return null;
  return { out: pa.label, into: `${db.label} · ${pb.label}` };
}

/** The remove control's accessible name. Says which wire, because a list of
 *  four buttons all called "remove" is four guesses. */
export function wireRemoveLabel(row: { out: string; into: string }): string {
  return `Remove the wire from ${row.out} to ${row.into}`;
}

/** What the phone's wire list says when a stage feeds nothing. */
export const NO_WIRES_TEXT = "feeds nothing yet - tap an output port, then an input port on another stage";

/** The label on every control that adds a stage.
 *
 *  It lives in this pure module because two components in this area render it -
 *  the canvas's floating control and the phone stage list's - and the phone
 *  sheet is its own lazily-loaded chunk: importing the constant from the surface
 *  would drag the canvas, its gestures and its wires into a bundle that must
 *  never draw one. The label carries its own `+`, so no plus glyph is set beside
 *  it (a glyph and the same character would read "+ + ADD STAGE"). */
export const ADD_STAGE_LABEL = "+ ADD STAGE";

/** The third state, and the reason it exists: the compiler has not answered for
 *  this flow yet (a fresh open, or every compile so far has failed - the slice
 *  keeps the LAST GOOD result, so a dropped request leaves this null rather than
 *  blanking the pill). "GRAPH VALID" would be a claim nobody made and
 *  "0 OPEN CHECKS" says the same thing in different words. */
export const CHECKS_UNKNOWN = "NOT CHECKED";

export const CHECKS_UNKNOWN_WHY = "The graph checker has not answered for this flow yet.";
export const CHECKS_CLEAN_WHY = "The graph checker found nothing to flag.";

/** The popover's line for the losses the pill is counting. It names where the
 *  sentences are, which is the one thing a count cannot say. */
export function lossesWhy(losses: number): string {
  const n = losses === 1
    ? "One setting drawn on this graph does"
    : `${losses} settings drawn on this graph do`;
  return `${n} not reach the run. The FLOW column lists them under NOT HONOURED BY A RUN.`;
}

/** `run.etaS` has no publisher, so a null is the rig's silence and the tooltip
 *  says so rather than letting a bare `-` read as zero. */
export const ETA_UNREPORTED = "The rig has not reported a time remaining for this run.";

/** PLAN's tooltip: what the plan editor is still FOR, now that Flows expresses
 *  most of a night. */
export const PLAN_TITLE =
  "Open the plan editor - guiding, count mode and per-target flip are not "
  + "expressible in a flow yet";

/** TONIGHT's lock sentence, verbatim. `/api/flows/{id}/tonight` sits behind the
 *  DERIVED-site capability because an audit recovered this observatory to 2.9 km
 *  from three viewer-legal requests. */
export function tonightLockReason(phrase: string): string {
  return `Tonight is worked out from the observatory site, so it needs ${phrase}.`;
}

/** `flows.run.curStage` is initialised to a bare em-dash placeholder by the
 *  slice and written by nothing server-side. Rendered through here so the phone
 *  monitor prints the house dash for "the rig has not said" instead of a
 *  character the copy rules forbid. */
export function stageWord(raw: string | null | undefined): string {
  const s = (raw ?? "").trim();
  if (!s || s === "—" || s === "–") return "-";
  return s;
}

/** `frames` of `frameGoal`. A goal the server has not stated is left off
 *  entirely rather than printed as "/ 0", which reads as a finished run of
 *  nothing. */
export function framesWord(frames: number, frameGoal: number | null): string {
  return frameGoal == null ? String(frames) : `${frames} / ${frameGoal}`;
}
