// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// flowsSlice.ts — the Flows domain's state and the actions that write it.
//
// It lives in its own module rather than inline in store.ts for one practical
// reason: store.ts is 2200 lines and shared by every surface in the app, and a
// 300-line addition to it is 300 lines of merge surface for anyone else working
// in ui/. store.ts gains one field, one action block and a spread.
//
// THE WRITE DISCIPLINE IS LOAD-BEARING. The README requires that a node-status
// tick re-render ONE node, not the canvas. That holds only because every writer
// below replaces exactly one sub-object and leaves its siblings' identities
// alone — a pan must not change `graph`, and a status tick must not change
// `pan`. Zustand compares a selector's RESULT, so a selector reading
// `s.flows.statuses[id]` returns a string and is exact; one reading
// `s.flows.graph` is exact only because nothing but a graph edit rewrites it.
// A convenience writer that rebuilt the whole `flows` object would silently
// re-render every subscriber and nothing would fail.
import { apiErrorPayload } from "../../lib/apiError";
import type { DisarmedSession } from "../../lib/disarmed";
import { flowsApi } from "../../lib/flowsApi";
import type {
  FlowCard, FlowFolder, FlowProgress, FlowRunFlags, FlowRunSession, FlowUnmapped,
} from "../../lib/flowsApi";
import { runIsLive } from "../../lib/lastSessionFrame";
import type { SequenceState, ToastLevel } from "../../types";
import { isRunPhaseLive, knownSessions } from "./flowRunState";
import { NODE_DEFS, createParams } from "./nodeDefs";
import { fitView, type Rect } from "./geometry";
import {
  flowLoopRefusal, laneMismatchRefusal, portKindOf, SELF_WIRE_REFUSAL,
} from "./flowLoop";
import {
  NEXT_PORT, PASS_PORT, isMultiPanel, laneTail, ownerOf, panelLane, withLoop,
} from "./panelLane";
import { acceptCounts } from "./countsNotice";
import { COUNTS_MIGRATION_KEY, FLOW_SETTINGS } from "./flowsTypes";
import type {
  FlowCalHealth, FlowCompileResult, FlowEdgeRec, FlowGraphRec, FlowLogLine,
  FlowNodeRec, FlowNodeType, FlowPhoneTab, FlowReanchored, FlowRecordRec,
  FlowRunState, FlowScreen, FlowSelection, FlowSettingKey, PendingWire,
  TonightTab,
} from "./flowsTypes";

/** Ring size for the run log. README §"State management" says ~120. */
export const LOG_RING = 120;

/** The fewest milliseconds between two LIVE progress re-reads (#214): the ones
 *  a frame landing on the open flow's run starts. Each read compiles the flow
 *  and scans its session on the server, and a run of 10 s subs would otherwise
 *  ask six times a minute to move a card decoration by one. Thirty seconds
 *  keeps the chip within a frame or two of the ledger at any sub length. The
 *  read when the run ENDS is not held to it: it is the one that makes the
 *  final count right. */
export const LIVE_PROGRESS_MIN_MS = 30_000;

/** The toast title when `flowsOpen` refuses to replace the open flow (#450).
 *  The same words the #/next doors (openFlow.ts `FLOW_OPEN_FAILED`) and the
 *  wizard put on a failed open, so the store's toast model coalesces a
 *  caller's own report of the same press onto this one instead of stacking
 *  two cards about one tap. It says what did NOT happen: the dangerous
 *  reading of a silent refusal is that the other flow opened. */
export const FLOW_NOT_OPENED = "That flow did not open";

/** Why `flowsOpen` refused (#450): the flow open in the editor holds edits
 *  that its save, made first, did not keep, and replacing it would drop them.
 *  The wizard said this first, for its own two doors (`openSaved`); since the
 *  rule moved into the store, every door says it. */
export const FLOW_OPEN_OVER_UNSAVED =
  "The flow open in the editor has edits that did not save, and opening this one would drop them. Save or close that flow first.";

/** No session known: one shared empty list, so writing it twice is no
 *  change of identity. */
const NO_SESSIONS: readonly string[] = [];

/** `ids` with `sid` added, or `ids` itself, the same array, when `sid` names
 *  no session or is already there: `sessionIds` keeps its identity unless it
 *  learns something, so a selector over it wakes nobody for nothing. */
function withSession(ids: readonly string[], sid: unknown): readonly string[] {
  return typeof sid !== "string" || sid === "" || ids.includes(sid) ? ids : [...ids, sid];
}

export interface FlowsUiState {
  screen: FlowScreen;
  phoneTab: FlowPhoneTab;
  query: string;
  folderChip: "all" | "mine" | "examples";
  /** Independent booleans, not one enum: Overlay owns its own focus trap and
   *  stacking, so "which overlay is open" is not a single-valued question. */
  tonightOpen: boolean;
  tonightTab: TonightTab;
  wizardOpen: boolean;
  /** The one-screen "pick a target, tick the filters, go" sheet. Independent of
   *  `wizardOpen` for the reason above: two overlays are two questions. */
  quickOpen: boolean;
  /** The flow that was just created, so the library that reloads under the
   *  operator can say which row is theirs. Cleared when they open something --
   *  a highlight that outlives the moment it explains becomes decoration. */
  highlightId: string | null;
  paletteOpen: boolean;
  notesOpen: boolean;
  logOpen: boolean;
}

export interface FlowsState {
  // ── library
  cards: FlowCard[];
  folders: FlowFolder[];
  libraryLoaded: boolean;
  libraryError: string | null;

  // ── the open flow
  record: FlowRecordRec | null;
  graph: FlowGraphRec;
  dirty: boolean;

  // ── selection and editing are SEPARATE, per the README.
  //    On desktop the inspector follows `sel`. On tablet and phone only the
  //    edit glyph sets `editNode`, and only `editNode` opens the sheet. Merging
  //    them would make every tap on the canvas open a sheet over the graph.
  sel: FlowSelection | null;
  editNode: string | null;

  // ── viewport, its own sub-object so a pan re-renders no node
  pan: { x: number; y: number };
  zoom: number;

  // ── wiring in flight
  wire: PendingWire | null;
  tapWire: { from: string; fromPort: string } | null;

  // ── run
  statuses: Record<string, string>;
  run: FlowRunState;
  logs: FlowLogLine[];

  // ── server-derived
  /** The newest compile answer in hand, with the graph it describes
   *  (`FlowCompiled.from`), or null before the first; an open clears it, a
   *  close does not (its graph is then the empty one). It can be
   *  STALE: an edit made since that compile started is not in it, so a
   *  reader that must not draw last round's verdict as this one's asks
   *  `compiledIsCurrent`. Written by `flowsCompile`, which runs when a flow
   *  opens, after the modal's DONE or LOOP PANELS, and after a successful
   *  save (#356). */
  compiled: FlowCompiled | null;
  compiling: boolean;
  tonight: Record<string, unknown> | null;
  tonightLoading: boolean;
  tonightError: string | null;
  calHealth: FlowCalHealth | null;
  /** The OPEN flow's `GET /api/flows/{id}/progress` answer, or null whenever
   *  none is in hand for this record: before the first answer lands, after a
   *  failed read, and from the moment an open, a save or a new run starts a
   *  re-read (`fetchProgress`, private to createFlowsActions). A LIVE re-read,
   *  started by a frame landing on this flow's run (#214), leaves it in place
   *  until its answer lands.
   *  Cards read it only through `progressChip` (flowProgress.ts).
   *
   *  NEVER ASKED WHETHER THE FLOW IS RUNNING (#449). It is a cache of a route
   *  answer that every open, save and RUN blanks for a round trip, and a
   *  failed read leaves blank; whose run the rig is on is a fact about the rig
   *  that holds across all of those. That question is `sessionIds`'. */
  progress: FlowProgress | null;
  /** The session ids known to be the OPEN flow's: the one each progress answer
   *  counted from, and the one `flowsRun`'s answer named. The rig's live run is
   *  this flow's when its session is one of these, which is how the live
   *  refresh (#214), run mode at both Target modal doors, the RUN button and
   *  the run readouts decide it (flowRunState `flowRunLive`, over
   *  `knownSessions`).
   *
   *  A LIST, NOT THE LATEST ONE, because both sources are needed and neither
   *  may overwrite the other. RUN names its session before any answer does
   *  (START OVER, a first night), and a run started elsewhere (ResumeArm on
   *  night two, another browser) is known only by an answer, which a save
   *  clears for the length of one read (#449). A session id is minted once per
   *  session, so every id that was ever this flow's still is.
   *
   *  So nothing within one flow drops an id: not a save, not a failed read,
   *  not a run ending. The list goes when no flow is open (a close, the
   *  sign-out gate's reset of `flows`: a session id is a fact about the rig,
   *  and nothing keeps one behind the login screen) and is replaced, in the
   *  same write as the record, when ANOTHER flow opens: flow A's session
   *  running while flow B is open is not B's run. Readers go through
   *  `knownSessions`, which also answers none while no record is open. */
  sessionIds: readonly string[];
  /** The counts note the server's read carried when the OPEN flow was opened
   *  (`migrated` entry `counts`, spec Revision 2 ruling 2), with the dormant
   *  addendum when the route added it; null when the read carried none.
   *
   *  KEPT, NOT LOGGED (S4 orchestrator ruling 8). It is a standing fact about
   *  the flow, which both editors show as one persistent line
   *  (`countsNotice`), and a log line scrolls away. Read only through
   *  `countsNotice(graph, countsNote)`: the GRAPH decides whether the line
   *  shows, so a save that switches the counts takes it down without a
   *  reopen, and this note adds only the addendum. Replaced on every open,
   *  cleared on close, left alone by a save. */
  countsNote: string | null;

  ui: FlowsUiState;
}

/** A compile answer as the slice keeps it: the server's answer, and `from`,
 *  the graph object the compile request SENT, which is the graph the answer
 *  describes (#356).
 *
 *  THE SENT OBJECT, NOT THE ONE ON SCREEN WHEN THE ANSWER LANDS. An edit
 *  made inside the compile's round trip is not in the answer, and stamping
 *  it with the graph on screen at landing would pass last round's verdict
 *  off as this one's. Identity is enough, for the reason `flowsSave` gives:
 *  every edit replaces the graph object (`touch`), and so does a save that
 *  writes the server's counts switch in.
 *
 *  OPTIONAL only so that a caller that seeds `compiled` by hand (a test's
 *  store, an older fixture) still type-checks; `flowsCompile` always writes
 *  it, and an answer without it is never current. */
export type FlowCompiled = FlowCompileResult & { from?: FlowGraphRec };

/** True when `compiled` is the answer for the graph on screen now: it was
 *  compiled from this very graph object, and no edit has landed since. A
 *  reader of `flows.compiled` that must not show a stale answer (the loop
 *  chip's count, #356) asks this rather than `dirty`, which says whether
 *  the graph is SAVED, not whether it was COMPILED: DONE's compile is of an
 *  unsaved draft, and an edit then SAVE is saved before its compile lands. */
export function compiledIsCurrent(f: Pick<FlowsState, "compiled" | "graph">): boolean {
  return f.compiled != null && f.compiled.from === f.graph;
}

export const FLOWS_INIT: FlowsState = {
  cards: [], folders: [], libraryLoaded: false, libraryError: null,
  record: null, graph: { nodes: [], edges: [] }, dirty: false,
  sel: null, editNode: null,
  // The prototype opens at this pan/zoom; a fresh canvas that started at 1.0/0,0
  // shows the first node hard against the corner.
  pan: { x: 24, y: 12 }, zoom: 0.92,
  wire: null, tapWire: null,
  statuses: {},
  run: { phase: "idle", startedAt: null, etaS: null, curStage: "—", frames: 0,
         frameGoal: null, acceptedUnmapped: [] },
  logs: [],
  compiled: null, compiling: false,
  tonight: null, tonightLoading: false, tonightError: null,
  calHealth: null,
  progress: null,
  sessionIds: NO_SESSIONS,
  countsNote: null,
  ui: { screen: "library", phoneTab: "flow", query: "", folderChip: "all",
        tonightOpen: false, tonightTab: "timeline", wizardOpen: false,
        quickOpen: false, highlightId: null,
        paletteOpen: false, notesOpen: false, logOpen: false },
};

export interface FlowsActions {
  flowsLoadLibrary: () => Promise<void>;
  /** Opens flow `id` into the editor's state, replacing the open record.
   *
   *  A DIRTY OPEN RECORD OF ANOTHER ID IS SAVED FIRST (#450), and when that
   *  save does not keep its edits the open is REFUSED: nothing is read,
   *  nothing is replaced, and the refusal is said (`FLOW_OPEN_OVER_UNSAVED`,
   *  in a toast and in `libraryError`, where `openFlowById`'s callers read
   *  why an open did not land). A read-only Example is not saved first (the
   *  server refuses it) and is replaced as a close replaces it, and a SAVE
   *  already out carrying exactly the graph on screen is neither sent again
   *  nor waited on (#215's stale completion covers its answer).
   *
   *  Resolves once the record is in, or once the open failed or was refused,
   *  having written `libraryError` either way; it never rejects. */
  flowsOpen: (id: string) => Promise<void>;
  flowsCloseEditor: () => Promise<void>;
  flowsSave: () => Promise<void>;

  /** Adds a node of `type` at `at`, created with `createParams`, and returns
   *  its new id, so a palette drop can select the node it made or open the
   *  TARGET modal on it (spec 2.1). */
  flowsAddNode: (type: FlowNodeType, at: { x: number; y: number }) => string;
  flowsMoveNode: (id: string, x: number, y: number) => void;
  flowsSetParam: (id: string, key: string, raw: string) => void;
  /** The TARGET modal's DONE (spec 2.5): every param in `patch`, coerced as
   *  `flowsSetParam` coerces, and the block's loop wire placed (`true`),
   *  lifted (`false`) or left (`undefined`) by `withLoop`, in ONE graph
   *  write with one dirty flip, then ONE compile. Resolves when that compile
   *  has answered, which is when the block's card may say it is valid; at
   *  once, with nothing written or compiled, when the node is gone or DONE
   *  changed nothing. */
  flowsApplyFraming: (
    id: string, patch: Readonly<Record<string, string | number>>, loop?: boolean,
  ) => Promise<void>;
  /** Writes one flow-level setting (spec 1.6, `FlowGraph.settings`), keeping
   *  every other key. False, and nothing written, for a key or a value
   *  `FLOW_SETTINGS` does not offer. */
  flowsSetSetting: (key: FlowSettingKey, value: string) => boolean;
  flowsDeleteSel: () => void;
  flowsConnect: (from: string, fromPort: string, to: string, toPort: string) => void;
  flowsSetName: (name: string) => void;

  flowsSelect: (sel: FlowSelection | null) => void;
  flowsSetEditNode: (id: string | null) => void;

  flowsSetPan: (pan: { x: number; y: number }) => void;
  flowsSetZoom: (zoom: number, pan?: { x: number; y: number }) => void;
  flowsFit: (rect: Rect) => void;

  flowsBeginWire: (w: PendingWire) => void;
  flowsMoveWire: (to: { x: number; y: number }) => void;
  flowsEndWire: (drop: { nodeId: string; portId: string } | null) => void;
  flowsTapPort: (nodeId: string, portId: string, dir: "in" | "out") => void;

  /** Compiles the graph on screen as a draft into `compiled`, stamped with
   *  that graph (`FlowCompiled.from`). Kept only while newer than the answer
   *  in hand and while its flow is still the one open. Never rejects. */
  flowsCompile: () => Promise<void>;
  flowsFetchTonight: () => Promise<void>;
  flowsFetchCalHealth: () => Promise<void>;
  /** Post the run with `flags` (none by default). Null when the run started,
   *  or when the refusal has already been written to the flow log; otherwise
   *  the server's QUESTION, carrying the flags this request sent so the
   *  answer can be re-posted with `nextRunFlags`. */
  flowsRun: (flags?: FlowRunFlags) => Promise<FlowRunAnswer | null>;

  flowsAppendLog: (msg: string, tone?: FlowLogLine["tone"]) => void;
  flowsSetUi: (patch: Partial<FlowsUiState>) => void;
}

// ─────────────────────────────────────────────── the run route's questions
//
// A 409 from `POST /api/flows/{id}/run` is sometimes a refusal and sometimes a
// QUESTION: the server stating what running would do and waiting for a yes.
// Two kinds of question exist. `unmapped` is about the graph (parts of it the
// engine will not honour). The three below are CONTINUE's (#189 S1, spec 5.9):
// since S1, Run continues the flow's own dormant session, and it asks before
// continuing changes what that session's ledger counts.

/** The three CONTINUE questions, in the server's order (`_continue_flow_session`):
 *
 *  - `adopt`: the session was saved before flows kept their step ids, so no
 *    step id is shared; ADOPT re-keys the frames that match exactly one step.
 *  - `recount`: the compile's count mode differs from the session's, and the
 *    ledger is counted by the plan's mode, so every banked frame recounts.
 *  - `dropped_steps`: steps that hold frames are gone from the flow.
 *
 *  NOT `session_changed`. That 409 says the session moved under the request
 *  (ResumeArm started it) and to press Run again; no flag answers it, so it is
 *  a refusal and goes to the log with the rest. */
export type FlowContinueCode = "adopt" | "dropped_steps" | "recount";
const CONTINUE_CODES: readonly string[] = ["adopt", "dropped_steps", "recount"];

export interface FlowContinueQuestion {
  code: FlowContinueCode;
  /** The server's sentence, VERBATIM. It carries the numbers (how many subs,
   *  both totals) the operator is deciding on, and a paraphrase of it would be
   *  the UI claiming something the server did not compute. */
  detail: string;
  /** The session the question is about, or null if the server named none. */
  sessionId: string | null;
}

/** A question from the run route. `flags` is what the request that drew it
 *  SENT, so an answer re-posts `nextRunFlags(answer.flags, yes)` and nothing
 *  already accepted is lost on the way. */
export type FlowRunAnswer =
  | { kind: "unmapped"; unmapped: FlowUnmapped[]; flags: FlowRunFlags }
  | { kind: "continue"; question: FlowContinueQuestion; flags: FlowRunFlags }
  /** Not a question - the run already started. Returned instead of `null`
   *  only when the response named a non-empty `disarmed` list (#643, W5
   *  integration): `flowsRun` used to discard that list on every successful
   *  start, so `flowRunControls.tsx` had no way to show D-04's warning for
   *  `POST /api/flows/{id}/run`, the one `disarmed`-carrying route
   *  `NowEmpty.tsx`'s WP-65 fix could not reach (it does not own this
   *  file). `runAnsweringQuestions` below returns this straight back out as
   *  `RunOutcome.disarmed` rather than treating it as a question to answer. */
  | { kind: "started"; disarmed: DisarmedSession[] };

/** What the operator said yes to: RUN ANYWAY on the graph question, one of the
 *  CONTINUE questions, or START OVER (`fresh`). */
export type FlowRunAcceptance = "unmapped" | FlowContinueCode | "fresh";

const FLAG_FOR: Record<FlowRunAcceptance, keyof FlowRunFlags> = {
  unmapped: "acceptUnmapped",
  adopt: "adopt",
  dropped_steps: "acceptDropped",
  recount: "acceptRecount",
  fresh: "fresh",
};

/** The next request's flags: everything `asked` already carried, plus `yes`.
 *
 *  THE ACCUMULATION IS THE POINT. The server asks one question per request
 *  and lifts each only with its own flag, so ADOPT followed by a dropped-steps
 *  CONTINUE must send both: a re-post that carried only the latest answer
 *  would be asked ADOPT again, and one that lost `acceptUnmapped` would be
 *  refused on the graph a second time (`run_flow` asks the graph question
 *  before it looks for a session). Both callers build every re-post here. */
export function nextRunFlags(asked: FlowRunFlags, yes: FlowRunAcceptance): FlowRunFlags {
  return { ...asked, [FLAG_FOR[yes]]: true };
}

/** The CONTINUE question in a 409's payload, or null when it is not one.
 *
 *  The sentence is read off the payload rather than the ApiError's message:
 *  the message has been through `forHumans`, and the question promises the
 *  server's words. A payload with no sentence falls back to the message, so a
 *  malformed 409 still asks rather than turning into "could not start". */
function continueQuestion(
  code: string | undefined, payload: Record<string, unknown> | null, message: string,
): FlowContinueQuestion | null {
  if (!code || !CONTINUE_CODES.includes(code)) return null;
  const detail = typeof payload?.detail === "string" && payload.detail.trim() !== ""
    ? payload.detail : message;
  // `adopt` names its session inside its own block; the other two at the top.
  const adopt = payload?.adopt as { session_id?: unknown } | undefined;
  const sid = payload?.session_id ?? adopt?.session_id;
  return {
    code: code as FlowContinueCode,
    detail,
    sessionId: typeof sid === "string" ? sid : null,
  };
}

const count = (n: number, one: string, many = `${one}s`) =>
  `${n} ${n === 1 ? one : many}`;
const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

/** The success answer's `session` block as one flow-log line, or null when
 *  there is nothing to say (an older server sends no block).
 *
 *  Said because CONTINUE is new and silent otherwise: an operator who pressed
 *  Run expecting a fresh night should read that it went into night 3 of an
 *  existing ledger, and how much of that ledger this compile still counts. */
export function sessionLogLine(raw: unknown): string | null {
  if (!raw || typeof raw !== "object") return null;
  const s = raw as Partial<FlowRunSession>;
  const night = num(s.night);
  const kept = num(s.kept);
  const added = num(s.new);
  const dropped = num(s.dropped);
  if (s.continued === false) {
    return added === null
      ? "started a new session"
      : `started a new session: ${count(added, "step")}`;
  }
  if (s.continued !== true || night === null || kept === null || added === null) {
    return null;
  }
  let line = `continued night ${night}: ${count(kept, "step")} kept, ${added} new`;
  if (dropped) line += `, ${dropped} dropped (their subs stay on disk)`;
  const matched = num(s.adopted?.matched);
  if (matched !== null) {
    line += `; adopted ${count(matched, "sub")} from the earlier session`;
    const rest = Array.isArray(s.adopted?.unmatched)
      ? s.adopted!.unmatched.reduce((n, u) => n + (num(u?.frames) ?? 0), 0)
      : 0;
    if (rest > 0) line += `, ${rest} left as they were`;
  }
  return line;
}

/** What this slice's own `get()` can see.
 *
 *  The actions are part of it because several of them call each other -
 *  flowsOpen compiles, flowsCloseEditor saves then reloads - and going through
 *  `get()` rather than closing over a local reference is what makes those calls
 *  hit the LIVE action, including anything a test has replaced. Kept to these
 *  two members so the slice can be exercised without building the whole
 *  AppState. */
export interface FlowsHost extends FlowsActions {
  flows: FlowsState;
  /** The store's ONE toast model (store.ts `enqueueToast`), which the slice
   *  reaches through `get()` like its own actions. OPTIONAL so a miniature
   *  store built without one still runs every action: it gets the flow-log
   *  line alone. The real store always has it. */
  enqueueToast?: (input: FlowToast) => void;
}

/** The part of store.ts's `EnqueueInput` the slice sends. */
export interface FlowToast {
  level: ToastLevel;
  title: string;
  detail?: string;
  source?: string;
}

type SetFn = (fn: (s: FlowsHost) => Partial<FlowsHost>) => void;
type GetFn = () => FlowsHost;

/** The part of the store the live progress refresh watches (#214): the open
 *  flow, and the run the rig is on. `sequence` is optional so a miniature
 *  store without one still type-checks; it simply never sees a run. */
export interface FlowsWatch {
  flows: FlowsState;
  sequence?: SequenceState;
}

/** The one member of zustand's store api the slice uses: the third argument a
 *  state creator is handed. store.ts passes it; a store that does not (every
 *  miniature store built before #214) gets no live refresh and nothing else
 *  changes. */
export interface FlowsStoreApi {
  subscribe(listener: (s: FlowsWatch, prev: FlowsWatch) => void): () => void;
}

/** The session id of the run `seq` describes while that run is live, or null.
 *  Live is `runIsLive`'s: a cloud hold and an abort's wind-down are still the
 *  run, and taking either for its end would spend the end read early. */
function liveSessionOf(seq: SequenceState | undefined): string | null {
  const id = seq?.session?.id;
  return runIsLive(seq) && typeof id === "string" && id !== "" ? id : null;
}

/** `frames_done` when it is a finite number, else null. */
function framesOf(seq: SequenceState | undefined): number | null {
  const n = seq?.progress?.frames_done;
  return typeof n === "number" && Number.isFinite(n) ? n : null;
}

let edgeSeq = 0;
let nodeSeq = 0;
let logSeq = 0;

/** Ids are minted client-side and are only ever local handles — the server
 *  treats a graph as opaque and re-validates edges by id, so a collision with
 *  a server-minted id would silently rewire the graph. Prefixed to make that
 *  impossible rather than unlikely. */
const nextNodeId = () => `n${++nodeSeq}_${Date.now().toString(36)}`;
const nextEdgeId = () => `e${++edgeSeq}_${Date.now().toString(36)}`;

/** Replace one sub-object of `flows`, leaving every sibling's identity alone. */
function patch(s: FlowsHost, next: Partial<FlowsState>): Partial<FlowsHost> {
  return { flows: { ...s.flows, ...next } };
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** The entries of a record's `migrated` that carry a sentence, as `{key,
 *  note}`, in order. An older server sends no such key, and an entry with no
 *  sentence in it is skipped: printed, it would be a warn line reading
 *  "undefined", which looks like the rig saying something it did not. */
function migrationEntries(rec: FlowRecordRec): { key: unknown; note: string }[] {
  const list = rec.migrated as unknown;
  if (!Array.isArray(list)) return [];
  return list
    .map((m) => (m && typeof m === "object"
      ? { key: (m as { key?: unknown }).key, note: (m as { note?: unknown }).note } : null))
    .filter((m): m is { key: unknown; note: string } =>
      m !== null && typeof m.note === "string" && m.note !== "");
}

/** The sentences a read's `migrated` puts on the flow log, in order: every
 *  one but the counts note, which `countsNoteOf` keeps instead (S4
 *  orchestrator ruling 8). A standing fact logged on every open is noise
 *  beside the persistent line both editors draw for it. */
function migrationNotes(rec: FlowRecordRec): string[] {
  return migrationEntries(rec)
    .filter((m) => m.key !== COUNTS_MIGRATION_KEY)
    .map((m) => m.note);
}

/** The read's counts note (the first, if a server ever sent two), or null. */
function countsNoteOf(rec: FlowRecordRec): string | null {
  return migrationEntries(rec).find((m) => m.key === COUNTS_MIGRATION_KEY)?.note ?? null;
}

/** A param value coerced by the TYPE OF ITS DEFAULT, the rule `flowsSetParam`
 *  and `flowsApplyFraming` share (see flowsSetParam for why). A numeric
 *  default takes a FINITE number, read with `parseFloat` from text, and falls
 *  back to the default on anything else: NaN, and plus or minus Infinity too;
 *  a text default takes text, so a select's value keeps matching its string
 *  options; a key with no default (`angle`, derived, or one this build does
 *  not know) is kept as given.
 *
 *  FINITE, NOT MERELY "NOT NaN" (#358). `parseFloat` reads "Infinity",
 *  "-Infinity" and "1e999" as an infinity, which is not NaN, and
 *  `JSON.stringify` writes an infinity as null: the node showed what was
 *  typed while the save and every compile sent null, which the server reads
 *  as its missing-key default or refuses. */
export function coerceParam(
  base: string | number | undefined, raw: string | number,
): string | number {
  if (typeof base === "number") {
    const v = typeof raw === "number" ? raw : parseFloat(raw);
    return Number.isFinite(v) ? v : base;
  }
  if (typeof base === "string") return String(raw);
  return raw;
}

/** `node`'s params with `patch` coerced into them, or `node.params` itself,
 *  the same object, when no value changes. */
function patchedParams(
  node: FlowNodeRec, patch: Readonly<Record<string, string | number>>,
): FlowNodeRec["params"] {
  const defaults = NODE_DEFS[node.type]?.params ?? {};
  let params = node.params;
  for (const [key, raw] of Object.entries(patch)) {
    const v = coerceParam(defaults[key], raw);
    if (params[key] === v) continue;
    if (params === node.params) params = { ...node.params };
    params[key] = v;
  }
  return params;
}

// ───────────────────────────────────────────────── what a save's answer says
//
// Two things a save can do to the counts that nothing on the canvas shows
// (#189; spec 3.3 and Revision 2, rulings 2 and 3). The server does both in
// `_persist_flow`, so every writer converges, and names them in its answer:
//
//   - `migrated` carries a `counts` entry when the save switched the flow's
//     TARGETs and POOLs to counting accepted subs only. Before the save both
//     editors said it would ("saving this flow switches it"); after it, the
//     log says it did.
//   - `reanchored` lists every block whose framing moved too far for its
//     counts to carry: a raw field edit in the inspector (a nudged RA, a
//     changed field of view) restarts a campaign's counts, and the operator
//     who typed it would otherwise meet that first as CONTINUE's
//     dropped-steps question (5.9), nights later.
//
// One line each, on the flow log both editors draw. Nothing for an answer
// without them: an older server's, and every save that changed neither. A
// re-anchor is also a toast (`reanchorToast`), and the counts switch is also
// written into the graph the editor holds (`acceptCounts`), so the canvas and
// the counts line (`countsNotice`) show what the server now stores.

/** The line for the counts switch. The ruling fixes the UI's words ("now
 *  counts accepted subs only"); the rest says what that changes. */
export const COUNTS_SWITCHED_LINE =
  "this flow now counts accepted subs only; rejected subs no longer count toward any step";

/** Degrees as the arcminutes the modal and the spec speak in: `14.8'`. */
const arcmin = (deg: number): string => `${(deg * 60).toFixed(1)}'`;

/** Why a block re-anchored, in words, from the server's numbers or its reason.
 *  A reason this build does not know gets the one thing every re-anchor has
 *  in common, rather than a guess. */
function reanchorWhy(r: FlowReanchored): string {
  const move = num(r.max_move_deg);
  const limit = num(r.threshold_deg);
  // "Framing", not "panels": a single target re-anchors too (its one panel's
  // corners move when it turns), and "its panels" would misname it.
  if (move !== null && limit === 0) {
    // No camera field recorded means no measure of "a little" (spec 3.3), and
    // "a move under 0.0'" would read as a broken number.
    return `its framing moved ${arcmin(move)}, and with no camera field recorded no move carries counts over`;
  }
  if (move !== null && limit !== null) {
    return `its framing moved ${arcmin(move)}, and its grid carries counts over only for a move under ${arcmin(limit)}`;
  }
  switch (r.reason) {
    case "grid": return "its rows or columns changed";
    case "angle": return "its angle changed between any angle and a set one";
    case "identity": return "it now names a different object";
    default: return "its framing changed";
  }
}

/** True when a save's answer says it switched the counts: a `migrated` entry
 *  keyed `counts`, in either spelling (the spec's bare "counts", or the
 *  record model's MigrationNote object). */
function answerSwitchedCounts(saved: FlowRecordRec): boolean {
  const migrated = saved.migrated as unknown;
  return Array.isArray(migrated) && migrated.some((m) => m === COUNTS_MIGRATION_KEY
    || (m && typeof m === "object" && (m as { key?: unknown }).key === COUNTS_MIGRATION_KEY));
}

/** The flow-log lines a save's answer asks for, in order: the counts switch
 *  (once, however many entries say it), then one line per re-anchored block.
 *  `graph` names the blocks: the saved graph, whose node ids the answer
 *  uses. Read defensively, as `migrationNotes` is: the answer is the network's. */
export function saveAnswerLines(
  saved: FlowRecordRec, graph: FlowGraphRec,
): { msg: string; tone: FlowLogLine["tone"] }[] {
  const lines: { msg: string; tone: FlowLogLine["tone"] }[] = [];
  if (answerSwitchedCounts(saved)) {
    lines.push({ msg: COUNTS_SWITCHED_LINE, tone: "info" });
  }
  const reanchored = saved.reanchored as unknown;
  if (Array.isArray(reanchored)) {
    const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
    for (const r of reanchored as FlowReanchored[]) {
      if (!r || typeof r !== "object") continue;
      // WARN: the operator's banked subs stop counting toward this block.
      lines.push({
        msg: `${blockName(nodes, r.node_id)} starts counting from zero: ${reanchorWhy(r)}. The subs it banked stay on disk.`,
        tone: "warn",
      });
    }
  }
  return lines;
}

/** How the save's announcements name a block: by its TARGET name, or as one
 *  with no name. */
function blockName(nodes: readonly FlowNodeRec[], id: unknown): string {
  const node = nodes.find((n) => n.id === id);
  const name = String(node?.params?.name ?? "").trim();
  return name ? `TARGET "${name}"` : "a TARGET with no name";
}

/** The ONE warn toast a save's answer asks for when it re-anchored any block
 *  (S4 orchestrator ruling 8; spec 3.3: "a raw field edit in the inspector
 *  that restarts counts is announced by a toast"), or null when it
 *  re-anchored none: an older server's answer, and every save that moved no
 *  block too far.
 *
 *  A TOAST AS WELL AS THE LOG LINE, because the log is a strip the operator
 *  opens and the restart is the one thing a save does that costs nights: the
 *  RA they nudged in the inspector is why a campaign's counts start again,
 *  and unsaid they meet it as CONTINUE's dropped-steps question. The log line
 *  (`saveAnswerLines`) keeps each block's reason; the toast names the blocks
 *  once each, however many rows name them. */
export function reanchorToast(saved: FlowRecordRec, graph: FlowGraphRec): FlowToast | null {
  const rows = saved.reanchored as unknown;
  if (!Array.isArray(rows)) return null;
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const ids: unknown[] = [];
  for (const r of rows as FlowReanchored[]) {
    if (r && typeof r === "object" && !ids.includes(r.node_id)) ids.push(r.node_id);
  }
  if (ids.length === 0) return null;
  const names = ids.map((id) => blockName(nodes, id));
  const one = names.length === 1;
  const who = one ? names[0]
    : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return {
    level: "warning",
    title: `${who} ${one ? "starts" : "start"} counting from zero`,
    // Not "moved too far": a changed grid, angle or object restarts counts
    // too, and the log line has each block's own reason.
    detail: `The subs ${one ? "it" : "they"} banked stay on disk. The flow log says what changed.`,
    source: "flows",
  };
}

// ───────────────────────────────────────── the panel loop follows the tail
//
// Spec 1.5 item 6 and 1.4 ("when the wire is added"). A mosaic rotates through
// its loop wire, `<tail>.pass -> <target>.next`, and that wire is only the
// loop while it leaves the LAST stage of the block's panel lane: from an
// earlier stage it is M12, a danger, because every stage after it would be
// shot once per panel with nothing to say when. Appending a stage after the
// last one (an Ha pass added to every panel) would therefore leave the loop
// mid-lane without a word, so the connect that appends the stage carries it.
//
// It MOVES a wire the lane already has and never adds one (1.4: the wire "is
// never added as a side effect of connecting something else"). No move for a
// 1x1 block (nothing to rotate between), for a lane with no loop wire (the
// operator chose panel-first), for a wire from anything but the tail, or when
// the new tail is a stage with no `pass` output to give (legacy SLEW, the one
// lane type left without it: the doctor names that lane instead).
//
// THE WIRE IT MOVES IS ANY PASS WIRE INTO THE BLOCK'S `next` FROM A STAGE OF
// ITS LANE, not only the pre-connect tail's (#331). Until AUTOFOCUS and GUIDE
// had a `pass` output, appending one after a looped FILTER CYCLE left the
// loop behind on the cycle, mid-lane (M12), and the next stage appended after
// the AUTOFOCUS did not carry it either, because the tail it was drawn from
// held no loop wire: the mosaic stayed unrunnable until the operator rewired
// it by hand. Flows saved in that state still open with the wire stranded, so
// the next append carries it home. A pass wire from mid-lane has no meaning
// the operator could have wanted: the compile refuses it (M12).

/** `edges` (the graph after the connect) with the lane's pass wires into the
 *  block's `next` carried to the lane's new tail when the wire just drawn out
 *  of `from` appended a stage to a multi-panel block's lane; otherwise `edges`
 *  itself, the same array. `graph` is the graph BEFORE the connect: the tail
 *  is the one the operator drew from, and the lane is the lane it ended. */
function carryLoopWire(
  graph: FlowGraphRec, edges: FlowEdgeRec[], from: string,
): FlowEdgeRec[] {
  const owner = ownerOf(graph, from);
  if (!owner || !isMultiPanel(owner)) return edges;
  if (laneTail(graph, owner.id)?.id !== from) return edges;
  const lane = new Set(panelLane(graph, owner.id).map((n) => n.id));
  const loops = new Set(graph.edges.filter((e) => e.fromPort === PASS_PORT
    && lane.has(e.from) && e.to === owner.id && e.toPort === NEXT_PORT));
  if (loops.size === 0) return edges;
  const tail = laneTail({ nodes: graph.nodes, edges }, owner.id);
  if (!tail || tail.id === from
      || !NODE_DEFS[tail.type]?.outs.some((p) => p.id === PASS_PORT)) return edges;
  // A new tail the operator already looped (its pass wire drawn before it was
  // wired in) keeps its own wire, and the lane's go: two loop wires into one
  // `next` is the doctor's "one is enough", not a better loop.
  const looped = edges.some((e) => e.from === tail.id && e.fromPort === PASS_PORT
    && e.to === owner.id && e.toPort === NEXT_PORT);
  return looped
    ? edges.filter((e) => !loops.has(e))
    // The same wire, re-sourced: its id survives, so a selected loop wire
    // stays selected, and every other edge keeps its identity.
    : edges.map((e) => (loops.has(e) ? { ...e, from: tail.id } : e));
}
// Exported for panelLane.test.ts, which grades it against the fixture's
// `carry_cases`, the graphs test_flows_panel_lane.py holds to compile.py. A
// separate statement, so the declaration above stays the plain `function`
// test_mosaic_spec_claims.py reads.
export { carryLoopWire };

export function createFlowsActions(
  set: SetFn, get: GetFn, api?: FlowsStoreApi,
): FlowsActions {
  const touch = (s: FlowsHost, graph: FlowGraphRec) =>
    patch(s, { graph, dirty: true });

  // THE NEWEST PROGRESS READ WINS, not the last one to arrive. Open, save, a
  // started run and a frame on a live run each start a read, and two for the
  // same flow can land out of order: the read after a save arriving after the
  // read after a RUN would put the pre-run session's counts back on the card.
  // Per store rather than per module, so two stores (the tests' miniature
  // ones) cannot supersede each other's reads.
  let progressTicket = 0;

  // AN OLDER COMPILE ANSWER NEVER REPLACES A NEWER ONE (#356). Open, DONE and
  // a save each start a compile, and since the save does, DONE followed by
  // SAVE before DONE's answer lands puts two in flight, of two graphs: DONE's
  // answer arriving last would undo the refresh the save asked for. So each
  // compile takes a ticket, and an answer is kept only when it is newer than
  // the one in hand. An older answer that lands FIRST is kept (it is still
  // newer than what it replaces), and `from` says which graph it describes.
  // Per store, like the progress ticket.
  let compileStarted = 0;
  let compileKept = 0;

  /** Is `sid` one of the OPEN flow's sessions (`FlowsState.sessionIds`)? A
   *  live run is this flow's when it is (#214).
   *
   *  THE STATE, NOT A PRIVATE COPY (#449). This closure kept the set to
   *  itself, so the live refresh knew across a save which run was the flow's
   *  while both Target modal doors, reading the progress answer the save had
   *  just blanked, did not. One list in the store, and one reader of it
   *  (`knownSessions`), is what keeps the refresh and the doors agreeing. */
  const openFlowOwns = (sid: string | null): boolean =>
    sid !== null && knownSessions(get().flows).includes(sid);

  /** When the last live read started, for LIVE_PROGRESS_MIN_MS. Reset when a
   *  flow opens, so one flow's reads never hold back another's. */
  let liveReadAt = -Infinity;

  /** The PUT `flowsSave` has out now: the flow, and the graph object and name
   *  it sent. `flowsOpen` reads it (#450): a save already carrying exactly
   *  what is on screen is not sent a second time. Cleared when that PUT
   *  settles, unless a later save has replaced it meanwhile. */
  let saving: { id: string; graph: FlowGraphRec; name: string } | null = null;

  /** `flowsSave`'s OWN promise for the in-flight PUT `saving` describes
   *  (#500 residual): never rejects (the catch below is inside it), so
   *  awaiting it is always safe. `flowsOpen`'s carried-save branch awaits
   *  THIS, instead of calling `flowsSave()` a second time, which would send
   *  a redundant PUT for a save already carrying what is on screen (the
   *  reason the first version of this fix did not wait at all). Cleared in
   *  the same `finally` as `saving`, and only when the two still agree -- a
   *  newer save's promise must never be dropped by an older one settling. */
  let savingPromise: Promise<void> | null = null;

  /** Re-read the open flow's progress into `flows.progress` (#189 S1 item 9).
   *  Never rejects, and every caller starts it without awaiting it.
   *
   *  PRIVATE, not a store action: open, save, a started run and the live run
   *  are the only reasons to re-read, all of them inside this closure, and a
   *  public action is one more store member that the sign-out gate
   *  (lib/authGate.ts) would have to be told about.
   *
   *  `live` is the frame-driven refresh (#214), and the only read that does
   *  not clear first. */
  const fetchProgress = async (live = false): Promise<void> => {
    const id = get().flows.record?.id;
    const ticket = ++progressTicket;
    // CLEARED FIRST, except on a live refresh. Open, save and a new run start
    // a read because what the last answer described has just changed -
    // another flow opened, a save moved step ids, a run may have started a new
    // session - so until the new answer lands the old one is a count of
    // something else, and no chip is drawn from it.
    //
    // A live refresh is the opposite case: same flow, same saved graph, same
    // session, and a count that only grows while the run shoots. The answer
    // in hand is still true of this flow, a frame behind, and blanking it for
    // a round trip on every frame would make the chip blink all night.
    if (!live) set((s) => patch(s, { progress: null }));
    if (!id) return;
    try {
      const progress = await flowsApi.progress(id);
      // Kept only while it is still the newest read AND its flow is still the
      // one open: the editor can close, or open another flow, while a read is
      // in flight.
      if (ticket !== progressTicket || get().flows.record?.id !== id) return;
      // THE ANSWER'S SESSION IS KNOWN FROM THE SAME WRITE (#449): no render
      // sees an answer naming a session the list lacks. It stays known after
      // the next clearing read blanks the answer, which is the point.
      set((s) => patch(s, {
        progress, sessionIds: withSession(s.flows.sessionIds, progress?.session?.id),
      }));
    } catch {
      // Left as it was, and SILENT: null after a clearing read, the previous
      // answer after a live one. A server older than S1 answers 404 for every
      // flow, and a saved graph that cannot become a plan answers 422, which
      // the compile's own doctor already reports. A missing chip claims
      // nothing; a log line on every open would be noise about a decoration.
      // `sessionIds` is left too: a read that failed says nothing about which
      // sessions were this flow's, so a live run stays this flow's (#449).
    }
  };

  // THE LIVE REFRESH (#214). Before it the chip was read on open, save and RUN
  // and never again, so an operator who left the canvas open through the
  // night saw the count the run started with - and the chip carries no time,
  // so a frozen count read as a current one. "212/315 subs" decides whether a
  // target gets another night.
  //
  // Driven by the run's own clock rather than by anything the operator does:
  // every write of `sequence` (store.ts's `sequence` case, and the snapshot a
  // reconnect lands) passes through this one subscription. The rig saves the
  // session ledger BEFORE it publishes the new `frames_done`
  // (engine.py `_record_frame`), so a read started by the publish sees the
  // frame that caused it.
  //
  //   - A frame landing on the open flow's run re-reads, at most once per
  //     LIVE_PROGRESS_MIN_MS. Leading edge only: a trailing timer would be one
  //     more thing to cancel on close and on sign-out, and the next frame
  //     after the window brings the count up to date anyway.
  //   - The run ending re-reads once more, outside the window, because the
  //     frames that landed inside the last window are otherwise never counted.
  //   - Nothing for another flow's run: its frames are not on this ledger.
  //   - Nothing while the graph is dirty: the chip is hidden over an unsaved
  //     edit (progressChip), and the save that ends the edit re-reads.
  const onSequence = (prev: SequenceState | undefined,
                      next: SequenceState | undefined): void => {
    const was = liveSessionOf(prev);
    const now = liveSessionOf(next);
    const ended = was !== null && was !== now && openFlowOwns(was);
    // #647: THE OPTIMISTIC `phase` IS CLEARED HERE, NOT LEFT TO EXPIRE.
    // `flowsRun` sets `flows.run.phase` to a live value the instant its POST
    // returns, before the engine's first publish - a guess
    // `flowRunControls.tsx` bridges for only `RUN_PHASE_BRIDGE_MS`, but
    // nothing ever wrote it back once that window had nothing to confirm or
    // deny it, and besides `useFlowRunControls` three OTHER files read
    // `flows.run.phase` directly (`FlowEditor.tsx`, `FlowWireLayer.tsx`,
    // `FlowCanvasToolbar.tsx`/`FlowWires.tsx`'s own selectors) with no bridge
    // of their own at all. Cleared unconditionally the moment the server
    // reports the OPEN flow's run has ended, regardless of `dirty` or
    // whether a progress re-read below is warranted: the stale STOP this
    // fixes (label, action and confirm disagreeing on a flow's run that is
    // actually over) is not a progress-chip concern, and must not wait on
    // one. Guarded on `isRunPhaseLive` so an already-idle `phase` (the common
    // case: this subscription fires on every sequence write, not just this
    // flow's) is not rewritten on every unrelated tick.
    if (ended && isRunPhaseLive(get().flows.run.phase)) {
      set((s) => patch(s, { run: { ...s.flows.run, phase: "idle", startedAt: null } }));
    }
    const wasFrames = framesOf(prev);
    const nowFrames = framesOf(next);
    const advanced = now !== null && now === was && openFlowOwns(now)
      && wasFrames !== null && nowFrames !== null && nowFrames > wasFrames;
    if (!ended && !advanced) return;
    const f = get().flows;
    if (!f.record || f.dirty) return;
    const t = Date.now();
    if (!ended && t - liveReadAt < LIVE_PROGRESS_MIN_MS) return;
    liveReadAt = t;
    void fetchProgress(true);
  };

  // Called on EVERY store write in the app (status ticks every 2 s, log
  // lines, previews), so it does one comparison before anything else. It
  // used to drop the private session set when no flow was open; the list is
  // state now, dropped by the writes that close the record (#449).
  api?.subscribe((s, prev) => {
    if (s.sequence !== prev.sequence) onSequence(prev.sequence, s.sequence);
  });

  return {
    // ────────────────────────────────────────────────────────────── library
    flowsLoadLibrary: async () => {
      try {
        const [cards, folders] = await Promise.all([
          flowsApi.list(), flowsApi.folders(),
        ]);
        set((s) => patch(s, {
          cards, folders, libraryLoaded: true, libraryError: null,
        }));
      } catch (e) {
        // libraryLoaded stays FALSE on an error. A failed load that flipped it
        // true would render "no flows yet" over a library the server has and
        // the client could not reach - which reads as data loss.
        set((s) => patch(s, { libraryError: errText(e) }));
      }
    },

    flowsOpen: async (id) => {
      // EVERY WAY OUT SAVES FIRST, AND AN OPEN IS A WAY OUT (#450). The rule
      // lived on the exits that are components (the canvas host's BACK and
      // its stranded-route effect, the Flows screen's, the stage sheet's,
      // each through openFlow.ts `leaveFlowEditor`), and #/next renders only
      // the active hub: a hub switch unmounts all of them at once, leaving
      // the edited graph in the store with `dirty` set, and the next open
      // from another hub (the Sky's quick flow, its flow card's deep link,
      // Tonight, RUN on a Now row) replaced it without a word. So the save
      // belongs to the store, where every caller, present and future, passes
      // through it. The wizard's own copy of this rule (`openSaved`) is now
      // this.
      //
      // A read-only Example is left to be replaced, as a close replaces it:
      // the server refuses its save, and refusing the open over it would
      // trap the operator in an Example they cannot keep. A clean record is
      // asked too, and `flowsSave` sends nothing for it; `dirty` after the
      // save is the one test, so there is no second one before it to drift.
      //
      // A SAVE ALREADY CARRYING WHAT IS ON SCREEN is not sent a SECOND time
      // (#500 residual, W2 integration): the operator pressed SAVE, its PUT
      // holds this very graph and name, and sending another would hold the
      // open for a round trip that stores nothing new. Instead this AWAITS
      // that same PUT's own promise (`savingPromise`), and #215's stale
      // completion (that PUT answering after this open, writing nothing onto
      // the flow now open) is still the race the slice already handles --
      // `flowsSave`'s own stale-completion check (`cur.id !== record.id`)
      // runs whether anyone is awaiting its promise or not. If the carried
      // PUT fails, this folds the PUT's OWN error text into the refusal
      // (the generic FLOW_OPEN_OVER_UNSAVED alone said nothing a corrupted-
      // store or validation failure actually gave) -- ONLY for the carried
      // branch: the non-carried refusal below is `hubSwitchSavesFlow.test
      // .tsx`'s own pin (its mutant "a refusal leaves libraryError" grades
      // the bare sentence exactly), and this fix's own files do not include
      // that test.
      const leaving = get().flows.record;
      const carried = saving !== null && leaving !== null && saving.id === leaving.id
        && saving.graph === get().flows.graph && saving.name === leaving.name;
      if (leaving && leaving.id !== id && !leaving.readonly) {
        const was = leaving.id;
        if (carried) {
          if (savingPromise) await savingPromise;
        } else {
          await get().flowsSave();
        }
        const now = get().flows;
        // STILL DIRTY IS REFUSED, WHATEVER THE REASON: the PUT failed (its
        // catch wrote `libraryError`), or an edit landed inside its round
        // trip (flowsSave keeps `dirty` for it, #215). Either way the graph
        // on screen is not the one stored, and replacing it is the loss this
        // exists to prevent. A record that is no longer `was` means another
        // open landed meanwhile and made its own save; this one goes on.
        if (now.record?.id === was && now.dirty) {
          // THE PUT'S OWN REASON, read before it is overwritten below, and
          // folded in ONLY for the carried-save case (see the comment above
          // this branch): `flowsSave`'s catch already wrote it to
          // `libraryError` when the PUT itself failed; an edit that merely
          // raced the round trip (#215) leaves it null, and the generic
          // sentence alone is the whole story then, carried or not.
          const own = now.libraryError;
          const detail = carried && own && own !== FLOW_OPEN_OVER_UNSAVED
            ? `${FLOW_OPEN_OVER_UNSAVED} (${own})` : FLOW_OPEN_OVER_UNSAVED;
          // In `libraryError` because that is where `openFlowById`'s callers
          // (openFlow.ts `flowOpenFailure`) and the wizard read why an open
          // did not land; in a toast because the callers that open from an
          // effect (the Sky's flow card, Tonight, the canvas host) report
          // nothing of their own, and a refusal nobody sees reads as a tap
          // that missed.
          set((s) => patch(s, { libraryError: detail }));
          get().enqueueToast?.({
            level: "error", title: FLOW_NOT_OPENED, detail, source: "flows",
          });
          return;
        }
      }
      // CLEARED HERE, not before the refusal check above (#555). That check
      // either returns early having written ITS OWN `libraryError`
      // (FLOW_OPEN_OVER_UNSAVED) or falls through here, meaning this open is
      // really going to try a read. Clearing right before the read - rather
      // than trusting `flowOpenFailure`'s old text comparison against a value
      // captured before this call - is what lets a caught failure below be
      // told apart from a stale leftover: whatever `libraryError` holds once
      // this function returns, it is this attempt's, never an earlier one's,
      // even when the server gives the identical reason twice in a row (the
      // repeated "no flow named <id>" case #555 found unproven).
      set((s) => patch(s, { libraryError: null }));
      try {
        const rec = (await flowsApi.get(id)) as FlowRecordRec;
        set((s) => patch(s, {
          record: rec,
          graph: rec.graph ?? { nodes: [], edges: [] },
          dirty: false, sel: null, editNode: null,
          compiled: null, tonight: null, calHealth: null,
          // IN THE SAME WRITE AS THE RECORD, not left to the read below: node
          // ids are not unique across flows (the wizard mints n1, n2, ... in
          // every flow it generates), so for as long as the previous flow's
          // answer sat beside this record its counts would land on this
          // flow's cards.
          progress: null,
          // So are the sessions known to be the open flow's (#449), for a
          // stronger reason: another flow's session running while this one is
          // open would read as THIS flow's run, and freeze its Target modal
          // and turn its RUN into a STOP over someone else's night. The same
          // flow opened again keeps its own.
          sessionIds: s.flows.record?.id === rec.id ? s.flows.sessionIds : NO_SESSIONS,
          // Kept for the counts line both editors draw, in the same write as
          // the record it is about (see FlowsState.countsNote).
          countsNote: countsNoteOf(rec),
          // The highlight has done its job the moment a flow is opened, and it
          // would otherwise still be ringing a card on the next visit.
          ui: { ...s.flows.ui, screen: "editor", highlightId: null },
        }));
        // WHAT THE SERVER'S READ CHANGED, said on the log both editors draw
        // (#150). FLOW_SCHEMA 3 turns a stored rotation of 23.4 - the old
        // palette default, a real PA to the compiler - into "any angle", which
        // changes what this saved flow does; a change nobody is told about is
        // the "semantics flip needs a migration" defect again. Warn tone, once
        // per open: the file keeps its 23.4 until it is next saved, so every
        // GET carries the note again and every open says it exactly once. A
        // RUN does not write it away - `touch_run` edits last_run in the raw
        // file and leaves the version alone - which is why `run_flow` puts the
        // same sentence on the server log on every run until a save.
        //
        // Every note but the counts note, which is kept above instead (S4
        // orchestrator ruling 8): that one is a standing fact, shown as a
        // persistent line for as long as it is true, not once per open.
        for (const note of migrationNotes(rec)) get().flowsAppendLog(note, "warn");
        // The TARGET cards' chips (#189 S1 item 9). Not awaited here, nor after
        // a save or a RUN: the read compiles the flow and scans the session
        // files on the server, and a card decoration must not hold the editor
        // open, the save before a close, or the RUN press.
        liveReadAt = -Infinity;
        void fetchProgress();
        await get().flowsCompile();
      } catch (e) {
        set((s) => patch(s, { libraryError: errText(e) }));
      }
    },

    flowsSave: async () => {
      const { record, graph, dirty } = get().flows;
      if (!record || record.readonly || !dirty) return;
      const sent = { id: record.id, graph, name: record.name };
      saving = sent;
      // #500 residual: this call's own completion, exposed as `savingPromise`
      // so `flowsOpen`'s carried-save branch can await THIS exact PUT rather
      // than firing a second one. Wrapped rather than just awaiting `flowsSave()`
      // itself recursively, because the try/catch/finally below must still run
      // (and still clear `saving`) whether a caller is awaiting the promise or
      // not -- the IIFE is the PUT's lifetime, `savingPromise` a handle onto it.
      const run = (async () => {
      try {
        const saved = (await flowsApi.save(record.id,
          { ...record, graph })) as FlowRecordRec;
        // WHAT WAS SENT IS WHAT WAS SAVED, and nothing after it (#215). This
        // wrote `dirty: false` unconditionally, so an edit made inside the
        // PUT's round trip - a param, a drag, a wire, a rename - was marked
        // saved while the canvas still showed it. The next SAVE then sent
        // nothing, `flowsCloseEditor`'s save declined on `!dirty`, and every
        // way out of the editor dropped the edit without a word; meanwhile the
        // TARGET chip, hidden only while `dirty`, drew the saved flow's count
        // beside an unsaved recipe.
        //
        // So `dirty` stays true when the canvas is no longer what was sent.
        // Identity is enough for the graph: every edit replaces the graph
        // object (`touch`), and a false positive costs one redundant PUT while
        // a false negative loses an edit. The name lives on the record, which
        // the server's answer replaces, so an in-flight rename is carried onto
        // that answer or the next PUT would send the old name back.
        const cur = get().flows.record;
        // A STALE COMPLETION: another flow was opened while this PUT was in
        // flight. Its answer belongs to a record no longer open, and writing
        // it would put this flow's id under the other flow's graph - which
        // the check below would then call an unsaved edit, so the next close
        // would PUT that graph into this flow's file. The other flow's open
        // has already read its own progress.
        if (!cur || cur.id !== record.id) return;
        const renamed = cur.name !== record.name;
        const edited = get().flows.graph !== graph;
        // THE NOTES ARE THE ANSWER'S, NOT THE FLOW'S. They are said once below,
        // and the record kept here is what the next SAVE sends back. The
        // server's model takes `migrated` as MigrationNote objects and refuses
        // the bare "counts" the spec writes, so a note kept here would make
        // every later save a 422, and a close, which does not wait for its
        // save to succeed, would then drop the edit.
        const kept: FlowRecordRec = { ...saved };
        delete kept.migrated;
        delete kept.reanchored;
        const switched = answerSwitchedCounts(saved);
        set((s) => patch(s, {
          record: renamed ? { ...kept, name: cur.name } : kept,
          dirty: edited || renamed,
          // THE SWITCH REACHES THE CANVAS (ruling 2). The server stored every
          // TARGET and POOL counting accepted subs; the graph here still says
          // what was sent. Written in, as the server wrote it, into whatever
          // graph is on screen now: the counts line (`countsNotice`) reads
          // this graph and so comes down, the draft compile counts what the
          // stored flow counts, and the next save does not switch it again.
          // Not an edit: `dirty` was decided above, from what was sent.
          ...(switched ? { graph: acceptCounts(s.flows.graph) } : {}),
        }));
        // What the save did to the counts, said once (`saveAnswerLines`).
        // AFTER the stale check above: an answer for a flow no longer open
        // would land on another flow's log, and CONTINUE's dropped-steps
        // question still guards that flow's ledger when it next runs.
        for (const line of saveAnswerLines(saved, saved.graph ?? graph)) {
          get().flowsAppendLog(line.msg, line.tone);
        }
        // A restart is also said where the operator is looking (S4
        // orchestrator ruling 8): one toast through the store's one toast
        // model, naming every block the save re-anchored.
        const toast = reanchorToast(saved, saved.graph ?? graph);
        if (toast) get().enqueueToast?.(toast);
        // What the server counts is the STORED graph, and the save just
        // changed it: a new exposure is a new step id with nothing banked.
        void fetchProgress();
        // AND THE COMPILE ANSWER IS REFRESHED (#356). It was asked for only
        // on open and on DONE, so after any other edit and its save it
        // described the graph as opened, and every reader of it went stale:
        // the loss marks, the checks pill, the PLAN tab, and the loop chip,
        // whose one unguarded case was exactly this one (a skip-list edit,
        // then SAVE, which clears `dirty` and leaves the grid alone).
        //
        // OF THE GRAPH THE STORE HOLDS NOW, after the write above: the graph
        // this save stored, with the server's counts switch written in as
        // the server wrote it, so the answer is current the moment it lands.
        // Were an edit made inside the PUT's round trip, it is on screen and
        // `dirty` says so, and the answer describes what is on screen, which
        // is what every reader of `compiled` draws it beside.
        //
        // Only here: a refused PUT stored nothing new (the catch below), and
        // a save with nothing to send, or one answered after another flow
        // opened, returned above. NOT AWAITED, for the reason the progress
        // read is not: a close saves first, and a compile must not hold the
        // editor open for its round trip. `flowsCompile` keeps the answer
        // only while its flow is still the one open.
        void get().flowsCompile();
      } catch (e) {
        set((s) => patch(s, { libraryError: errText(e) }));
      } finally {
        if (saving === sent) { saving = null; savingPromise = null; }
      }
      })();
      savingPromise = run;
      await run;
    },

    flowsCloseEditor: async () => {
      await get().flowsSave();
      // A SAVE THAT FAILED MUST NOT BE CLEARED AWAY (#500). `dirty` once the
      // save above has settled is the same test `flowsOpen`'s own refusal
      // reads (#450): the PUT failed (its catch wrote `libraryError`), or an
      // edit landed inside its round trip (`flowsSave` keeps `dirty` for that,
      // #215). Either way the graph on screen is not the one the server
      // holds, and clearing it here would be worse than `flowsOpen`'s
      // refusal: this close then reloads the library below, and a successful
      // `flowsLoadLibrary` clears `libraryError` too (#555), so the save's own
      // failure text would be gone along with the edit - the operator finding
      // out only on the next reopen, or when a night runs the stale graph.
      //
      // `!record?.readonly` is the same carve-out `flowsOpen` makes for an
      // Example (#450): the server refuses to save one at all, so
      // `flowsSave` returns at once and `dirty` never clears on its own -
      // refusing the close for it would trap the operator in an Example they
      // cannot keep, for good.
      const now = get().flows;
      if (now.dirty && !now.record?.readonly) {
        // In `libraryError` and a toast for the same reason `flowsOpen`'s
        // refusal uses both: callers that read the door's outcome (a future
        // `leaveFlowEditor`) read `libraryError`, and the callers that close
        // from an effect, with nothing of their own to show, need the toast.
        set((s) => patch(s, { libraryError: FLOW_OPEN_OVER_UNSAVED }));
        get().enqueueToast?.({
          level: "error", title: FLOW_NOT_OPENED, detail: FLOW_OPEN_OVER_UNSAVED, source: "flows",
        });
        return;
      }
      set((s) => patch(s, {
        record: null, graph: { nodes: [], edges: [] }, dirty: false,
        sel: null, editNode: null, wire: null, tapWire: null,
        // An answer belongs to the open record and goes with it, and so do
        // the note its read carried and the sessions known to be its (#449).
        progress: null,
        sessionIds: NO_SESSIONS,
        countsNote: null,
        ui: { ...s.flows.ui, screen: "library", paletteOpen: false },
      }));
      await get().flowsLoadLibrary();
    },

    // ─────────────────────────────────────────────────────────── graph edits
    flowsAddNode: (type, at) => {
      // THE ID IS MINTED HERE, OUTSIDE THE WRITE, so it can be returned (spec
      // 2.1): a TARGET dropped from the palette opens its modal at once, and
      // a caller that had to guess the id of the node it just made would
      // guess from the graph, where a second drop in the same frame, or a
      // duplicated id in a hand-built file, hands it the wrong node.
      const id = nextNodeId();
      set((s) => {
        // A DROP IS A CREATION, so the node is written with `createParams`:
        // the missing-key defaults overlaid with the type's "Created as"
        // column (spec 3.1). The defaults alone are what a SAVED node lacking
        // a key is read as, and they keep each key's old meaning: a TARGET
        // built from them counted rejected subs and carried M31's name and
        // coordinates, so one renamed M16 and run slewed to Andromeda, #190's
        // defect through the palette's door. Both editors' palettes drop
        // through here.
        const node = {
          id, type, x: at.x, y: at.y,
          params: createParams(type),
        };
        return touch(s, { ...s.flows.graph,
                          nodes: [...s.flows.graph.nodes, node] });
      });
      return id;
    },

    flowsMoveNode: (id, x, y) => set((s) => touch(s, {
      ...s.flows.graph,
      nodes: s.flows.graph.nodes.map((n) => (n.id === id ? { ...n, x, y } : n)),
    })),

    flowsSetParam: (id, key, raw) => set((s) => {
      const node = s.flows.graph.nodes.find((n) => n.id === id);
      if (!node) return {};
      // COERCION KEYS OFF THE TYPE OF THE DEFAULT, exactly as the prototype
      // does. That is why capture's `bin` stays the string "1" - it is a select
      // whose options are strings - while every numeric field keeps only a
      // FINITE number and reverts to its default on anything else, rather
      // than becoming NaN or Infinity. A NaN here reaches the compiler as a
      // step with no exposure, and an Infinity reaches it as null (#358). One
      // rule for this action and the modal's DONE (`coerceParam`), so the two
      // cannot drift.
      const v = coerceParam(NODE_DEFS[node.type].params[key], raw);
      return touch(s, { ...s.flows.graph,
        nodes: s.flows.graph.nodes.map((n) =>
          n.id === id ? { ...n, params: { ...n.params, [key]: v } } : n) });
    }),

    flowsApplyFraming: (id, framing, loop) => {
      // ONE WRITE, ONE COMPILE (spec 2.5). The modal changes a handful of
      // params at once (name, coordinates, grid, overlap, angle, field) and
      // perhaps the loop wire. Through `flowsSetParam` that is one graph
      // write per key, and every write between the first and the last is a
      // block nobody framed: a 3x2 grid on the old coordinates, a new field
      // of view on the old grid. The compile, the doctor chip and the draft
      // progress read whichever of those they catch, and a compile started
      // per key has N answers racing to be the chip. So every param and the
      // wire land in one write, and one compile follows it.
      let wrote = false;
      set((s) => {
        const g = s.flows.graph;
        const node = g.nodes.find((n) => n.id === id);
        if (!node) return {};
        const params = patchedParams(node, framing);
        const nodes = params === node.params
          ? g.nodes : g.nodes.map((n) => (n === node ? { ...n, params } : n));
        // THE LOOP IS JUDGED ON THE FRAMED BLOCK, not the one the modal
        // opened on: DONE that turns a 1x1 into a 3x2 and asks for the loop
        // gets it, and one that turns a 3x2 into a 1x1 gets no NEW wire
        // (spec 1.4: "when the block becomes multi-panel and owns a stage").
        // The loop wire leaving the TAIL is lifted only by `false`: `true`
        // keeps it, and since #410 lifts only a pass wire stranded mid-lane
        // (M12), moving the first to the tail when the tail has none (#429
        // corrected this comment, which said `true` never lifts). So a DONE
        // that makes a looped mosaic a single target must send `false`: left
        // in place, the wire is a pass wire into a 1x1 block, which the
        // compile CONSUMES as a note from the doctor (M4) rather than
        // refusing or rotating anything on it -- no rule, no loss, and Run
        // asks nothing about it (S4 orchestrator ruling 3, spec 1.4 "As
        // built", #349; #397 item 4 corrected this comment, which had left
        // that consequence unsaid).
        const edges = withLoop({ ...g, nodes }, id, loop, nextEdgeId);
        if (nodes === g.nodes && edges === g.edges) return {};
        wrote = true;
        return touch(s, { ...g, nodes, edges });
      });
      // Nothing written, nothing to check: the compile in hand is still the
      // answer for this graph. Otherwise the promise is the compile's, so the
      // modal marks the card valid only once the compiler has answered.
      return wrote ? get().flowsCompile() : Promise.resolve();
    },

    flowsSetSetting: (key, value) => {
      // REFUSED, NOT COERCED, when FLOW_SETTINGS does not offer it. The
      // engine reads a value it does not know as the default
      // (`resolve_setting`), so a stored typo would show one behaviour in the
      // editor and run another. The editors draw their choices from the same
      // table, so only a caller's mistake reaches this, and it says so with
      // `false` rather than on the flow log, which is the rig's voice.
      //
      // OWN KEYS ONLY. A plain index would find `toString` or `constructor`
      // on the table's prototype, and the `options` read on it threw where
      // the contract says `false`.
      const spec = Object.hasOwn(FLOW_SETTINGS, key)
        ? (FLOW_SETTINGS as Record<string, { options: readonly string[] }>)[key] : undefined;
      if (!spec || !spec.options.includes(value)) return false;
      set((s) => {
        const g = s.flows.graph;
        if (g.settings?.[key] === value) return {};
        // EVERY OTHER KEY IS KEPT: a flow saved by a newer build can carry a
        // setting this one does not know, and a save that dropped it would
        // change what that build runs.
        return touch(s, { ...g, settings: { ...g.settings, [key]: value } });
      });
      return true;
    },

    flowsDeleteSel: () => set((s) => {
      const sel = s.flows.sel;
      if (!sel) return {};
      const g = s.flows.graph;
      const graph = sel.kind === "edge"
        ? { ...g, edges: g.edges.filter((e) => e.id !== sel.id) }
        // Deleting a node takes its wires with it. An edge left pointing at a
        // node that is gone is exactly what FlowGraph.validation_errors()
        // refuses, so the graph would stop compiling and the operator would be
        // told their graph is broken by an action they took on purpose.
        // `...g` as the edge arm has it: the graph also carries the flow's
        // own settings (spec 1.6), which a rebuilt `{ nodes, edges }` dropped.
        : { ...g, nodes: g.nodes.filter((n) => n.id !== sel.id),
            edges: g.edges.filter((e) => e.from !== sel.id && e.to !== sel.id) };
      return { flows: { ...s.flows, graph, dirty: true, sel: null,
                        editNode: s.flows.editNode === sel.id
                          ? null : s.flows.editNode } };
    }),

    flowsConnect: (from, fromPort, to, toPort) => {
      // THE SELF-WIRE AND LANE CHECKS RUN HERE TOO (#197), not only in the two
      // drop resolvers, for the same reason the loop check below does:
      // tap-to-wire (`flowsTapPort`) reaches this action without passing
      // through any resolver, so a check that lived only in `resolveWireDrop`
      // (classic `FlowCanvas`) and its canvasModel.ts copy (#/next) would
      // never see a tap. A drag never trips either branch below — both
      // resolvers refuse the same two cases before they ever call
      // `flowsConnect` — so in practice these are tap's own gates.
      //
      // SELF-WIRE gets a TOAST, not a log line, and that is the one place tap
      // and drag still disagree on purpose: a drag that ends where it started
      // is usually a mis-grab (refused silently), while tapping a second port
      // on the stage just armed is a choice an operator made and gets told
      // about (`flowLoop.SELF_WIRE_REFUSAL`).
      if (from === to) {
        get().enqueueToast?.({ level: "warning", title: SELF_WIRE_REFUSAL, source: "flows" });
        return;
      }
      const { nodes, edges } = get().flows.graph;
      // LANE MISMATCH, the same sentence `resolveWireDrop` toasts on a drag
      // (`flowLoop.laneMismatchRefusal`): into the flow log here, as the loop
      // refusal below already is, since tap has no toast wired for either.
      const lane = laneMismatchRefusal(
        portKindOf(nodes, from, fromPort, "out"), portKindOf(nodes, to, toPort, "in"),
      );
      if (lane) {
        get().flowsAppendLog(lane, "warn");
        return;
      }
      // NO FLOW LOOPS (#149). A flow wire that closes a circle makes the
      // compiler drop every stage on it, and none of them shoots a frame. So
      // the graph is left exactly as it was and the server's own sentence
      // goes to the flow log: a refusal that said nothing would read as a tap
      // or a drop that missed.
      const loop = flowLoopRefusal(nodes, edges, { from, fromPort, to, toPort });
      if (loop) {
        get().flowsAppendLog(loop, "warn");
        return;
      }
      set((s) => {
        const g = s.flows.graph;
        // ONE WIRE PER FLOW INPUT, enforced by REPLACING on drop. models.py
        // names this as the invariant the server relies on: it refuses a graph
        // with two wires into one flow input, so an editor that appended there
        // would produce a graph that saves and then will not load.
        //
        // AN EVENT INPUT FANS IN, and replacing there is data loss (#152): the
        // server allows many wires into one event input (models.py:149-167) and
        // the campaign example needs it - CLOUD WATCH "clouds in" AND PARK +
        // CLOSE "closed" both feed CALIBRATION QUEUE "do". Filtering on every
        // input silently deleted the first feed when the second was drawn, and
        // nothing said so. An input whose lane cannot be resolved is not a flow
        // input, so it is never cleared either.
        const flowInput = portKindOf(g.nodes, to, toPort, "in") === "flow";
        const edges = g.edges
          .filter((e) => !(flowInput && e.to === to && e.toPort === toPort))
          .concat([{ id: nextEdgeId(), from, fromPort, to, toPort }]);
        // IN THE SAME WRITE as the wire that caused it: one graph edit, one
        // dirty/compile cycle, and no moment at which the compile sees a
        // mosaic whose loop leaves a stage in the middle of its lane.
        return touch(s, { ...g, edges: carryLoopWire(g, edges, from) });
      });
    },

    flowsSetName: (name) => set((s) => (s.flows.record
      ? patch(s, { record: { ...s.flows.record, name }, dirty: true })
      : {})),

    // ──────────────────────────────────────────────── selection and editing
    flowsSelect: (sel) => set((s) => patch(s, { sel })),
    flowsSetEditNode: (id) => set((s) => patch(s, { editNode: id })),

    // ───────────────────────────────────────────────────────────── viewport
    flowsSetPan: (pan) => set((s) => patch(s, { pan })),
    flowsSetZoom: (zoom, pan) => set((s) =>
      patch(s, pan ? { zoom, pan } : { zoom })),
    flowsFit: (rect) => set((s) => {
      // null on an empty graph. Leaving the viewport alone is the right answer
      // there - "fit nothing" has no meaningful pan, and defaulting to 0,0/1.0
      // would yank the canvas out from under an operator who pressed FIT before
      // adding a node.
      const fit = fitView(s.flows.graph.nodes, rect, NODE_DEFS, "desktop");
      return fit ? patch(s, { pan: fit.pan, zoom: fit.zoom }) : {};
    }),

    // ────────────────────────────────────────────────────────────── wiring
    flowsBeginWire: (w) => set((s) => patch(s, { wire: w })),
    flowsMoveWire: (to) => set((s) => (s.flows.wire
      ? patch(s, { wire: { ...s.flows.wire, to } }) : {})),
    flowsEndWire: (drop) => {
      const w = get().flows.wire;
      set((s) => patch(s, { wire: null }));
      if (w && drop) get().flowsConnect(w.from, w.fromPort, drop.nodeId, drop.portId);
    },

    flowsTapPort: (nodeId, portId, dir) => {
      const armed = get().flows.tapWire;
      if (!armed) {
        // Only an OUTPUT arms. Tapping an input first would leave the operator
        // holding a wire with no source and no way to say what it carries.
        if (dir === "out") {
          set((s) => patch(s, { tapWire: { from: nodeId, fromPort: portId } }));
        }
        return;
      }
      set((s) => patch(s, { tapWire: null }));
      if (dir === "in") get().flowsConnect(armed.from, armed.fromPort, nodeId, portId);
    },

    // ────────────────────────────────────────────────────────────── server
    flowsCompile: async () => {
      const { graph, record } = get().flows;
      const ticket = ++compileStarted;
      set((s) => patch(s, { compiling: true }));
      // Still the flow this compile was asked for. A close saves first, so a
      // save's compile can outlive its editor, and the sign-out gate
      // (lib/authGate.ts) resets `flows` without waiting for anything in
      // flight: an answer written after it would put the rig's plan, its
      // targets and filters, behind the login screen.
      const ours = () => get().flows.record?.id === record?.id;
      // Only the newest compile STARTED settles the checking flag: while a
      // newer one is out, the flow is still being checked.
      const newest = () => ticket === compileStarted;
      try {
        const answer = await flowsApi.compileDraft(graph, record?.name ?? "");
        const keep = ticket > compileKept && ours();
        if (keep) compileKept = ticket;
        if (!keep && !newest()) return;
        set((s) => patch(s, {
          // `from` is the graph this request SENT, captured before the
          // await, never the graph on screen now (see FlowCompiled).
          ...(keep ? { compiled: { ...answer, from: graph } } : {}),
          ...(newest() ? { compiling: false } : {}),
        }));
      } catch (e) {
        // The compile is advisory - it drives the doctor chip, not the run - so
        // a failure leaves the LAST GOOD result in place rather than blanking
        // the chip. A chip that vanished on a dropped request would read as
        // "no problems found".
        if (newest()) set((s) => patch(s, { compiling: false }));
        // Said only while its flow is open: the log is one strip for
        // whichever flow is, and "could not check this flow" on it would be
        // about another one.
        if (!ours()) return;
        get().flowsAppendLog(`could not check this flow: ${errText(e)}`, "warn");
      }
    },

    flowsFetchTonight: async () => {
      const id = get().flows.record?.id;
      if (!id) return;
      set((s) => patch(s, { tonightLoading: true, tonightError: null }));
      try {
        const tonight = await flowsApi.tonight(id);
        set((s) => patch(s, { tonight, tonightLoading: false }));
      } catch (e) {
        set((s) => patch(s, { tonightLoading: false, tonightError: errText(e) }));
      }
    },

    flowsFetchCalHealth: async () => {
      const id = get().flows.record?.id;
      try {
        const calHealth = (await flowsApi.calibrationHealth(id)) as FlowCalHealth;
        set((s) => patch(s, { calHealth }));
      } catch {
        // Left null, which the matrix renders as "could not read the library" -
        // NOT as an empty matrix. An empty matrix means "nothing is planned",
        // and the two must never look alike.
        set((s) => patch(s, { calHealth: null }));
      }
    },

    flowsRun: async (flags = {}) => {
      const id = get().flows.record?.id;
      if (!id) return null;
      try {
        const res = await flowsApi.run(id, flags);
        set((s) => patch(s, {
          // `startedAt` stamps THIS optimistic belief (#647): read by
          // `flowRunControls.tsx`'s `RUN_PHASE_BRIDGE_MS` window, so a label
          // that leans on this guess stops doing so once the bridge has had
          // long enough to hear from the engine either way.
          run: { ...s.flows.run, phase: "running", startedAt: Date.now(),
                 frames: 0, frameGoal: res.frames,
                 acceptedUnmapped: flags.acceptUnmapped ? (res.unmapped ?? []) : [] },
          // The session this run went into is this flow's, known from this
          // moment: the live refresh (#214) recognizes the run's frames by
          // it, and run mode, the RUN button and the readouts the run itself
          // (#449), before any progress answer has named it, and through the
          // clearing read started below. Only while the flow that was run is
          // still the one open: another flow's list is not this one's.
          ...(s.flows.record?.id === id
            ? { sessionIds: withSession(s.flows.sessionIds, res.session?.id) } : {}),
        }));
        // Which ledger the night went into, in words - "continued night 3: 7
        // steps kept, 0 new". Without it a CONTINUE is indistinguishable from
        // a fresh start on every surface that shows the log.
        const line = sessionLogLine(res.session);
        if (line) get().flowsAppendLog(line, "info");
        // The run may have gone into a NEW session (START OVER, or the first
        // night), whose counts are not the ones the cards are showing.
        void fetchProgress();
        // #643 (W5 integration): `disarmed`, present only when non-empty -
        // the codebase's own convention (`below_horizon`) - names every
        // session this start's SINGLETON (`SequenceEngine.start`) turned
        // auto-resume off for (#595, D-04). `FlowRunResult` does not declare
        // the field (it belongs to a route `NowEmpty.tsx`'s WP-65 fix does
        // not call), so it is read off the parsed response exactly as that
        // file's own two call sites do, not through a widened type.
        const disarmed = (res as unknown as { disarmed?: DisarmedSession[] }).disarmed;
        return disarmed && disarmed.length > 0 ? { kind: "started", disarmed } : null;
      } catch (e) {
        // A 409 carrying `unmapped` is not a failure - it is the server asking
        // whether the operator accepts running a flow that will not honour part
        // of their graph. Handing the list back lets the caller show it and
        // ask; swallowing it would turn a question into an error.
        //
        // THIS READ WAS DEAD FOR AS LONG AS IT EXISTED. It looked for
        // `err.unmapped` and `err.body?.unmapped`; ApiError carried NEITHER
        // (it kept message/status/code/id and dropped the decoded body), and
        // the server nests the payload under `detail` anyway. So the guard
        // could never be true, every flow with a loss fell through to the log
        // line below, and RUN silently did nothing — on a rig where almost
        // every flow has a loss. `apiErrorPayload` is the one place that knows
        // which of the two shapes FastAPI used.
        const err = e as { code?: string; body?: unknown };
        const payload = apiErrorPayload(err.body);
        const list = payload?.unmapped as FlowCompileResult["unmapped"] | undefined;
        if (err.code === "unmapped" && list) return { kind: "unmapped", unmapped: list, flags };
        // CONTINUE's questions, the same kind of 409 (#189 S1): the server
        // saying what continuing the flow's session would do to its ledger,
        // and waiting for a yes. Before this branch they fell to the line
        // below, so Run on any flow with a pre-S1 session printed "could not
        // start" and offered no way forward - the dead guard above, again.
        const question = continueQuestion(err.code, payload, errText(e));
        if (question) return { kind: "continue", question, flags };
        get().flowsAppendLog(`could not start: ${errText(e)}`, "bad");
        return null;
      }
    },

    // ──────────────────────────────────────────────────────────────── misc
    flowsAppendLog: (msg, tone = "info") => set((s) => patch(s, {
      logs: [...s.flows.logs,
             { id: ++logSeq, ts: Date.now(), msg, tone }].slice(-LOG_RING),
    })),

    flowsSetUi: (p) => set((s) => patch(s, {
      ui: { ...s.flows.ui, ...p },
      // Leaving the phone FLOW tab CANCELS a half-made wire (§C.15: "switching
      // tabs clears tapWire"). An arm is a half-finished sentence only that tab
      // can finish; carried across to MONITOR it means the next port touched,
      // minutes later, silently completes a wire the operator forgot starting.
      // Done here rather than in the tab bar so the CANVAS tab's own drag
      // wiring and any future caller inherit it.
      ...(p.phoneTab !== undefined && p.phoneTab !== s.flows.ui.phoneTab
        ? { tapWire: null }
        : {}),
    })),
  };
}
