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
import { flowsApi } from "../../lib/flowsApi";
import type {
  FlowCard, FlowFolder, FlowProgress, FlowRunFlags, FlowRunSession, FlowUnmapped,
} from "../../lib/flowsApi";
import { NODE_DEFS } from "./nodeDefs";
import { fitView, type Rect } from "./geometry";
import { flowLoopRefusal, portKindOf } from "./flowLoop";
import type {
  FlowCalHealth, FlowCompileResult, FlowGraphRec, FlowLogLine, FlowNodeType,
  FlowPhoneTab, FlowRecordRec, FlowRunState, FlowScreen, FlowSelection,
  PendingWire, TonightTab,
} from "./flowsTypes";

/** Ring size for the run log. README §"State management" says ~120. */
export const LOG_RING = 120;

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
  compiled: FlowCompileResult | null;
  compiling: boolean;
  tonight: Record<string, unknown> | null;
  tonightLoading: boolean;
  tonightError: string | null;
  calHealth: FlowCalHealth | null;
  /** The OPEN flow's `GET /api/flows/{id}/progress` answer, or null whenever
   *  none is in hand for this record: before the first answer lands, after a
   *  failed read, and from the moment a re-read starts (`fetchProgress`,
   *  private to createFlowsActions).
   *  Cards read it only through `progressChip` (flowProgress.ts). */
  progress: FlowProgress | null;

  ui: FlowsUiState;
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
  run: { phase: "idle", etaS: null, curStage: "—", frames: 0,
         frameGoal: null, acceptedUnmapped: [] },
  logs: [],
  compiled: null, compiling: false,
  tonight: null, tonightLoading: false, tonightError: null,
  calHealth: null,
  progress: null,
  ui: { screen: "library", phoneTab: "flow", query: "", folderChip: "all",
        tonightOpen: false, tonightTab: "timeline", wizardOpen: false,
        quickOpen: false, highlightId: null,
        paletteOpen: false, notesOpen: false, logOpen: false },
};

export interface FlowsActions {
  flowsLoadLibrary: () => Promise<void>;
  flowsOpen: (id: string) => Promise<void>;
  flowsCloseEditor: () => Promise<void>;
  flowsSave: () => Promise<void>;

  flowsAddNode: (type: FlowNodeType, at: { x: number; y: number }) => void;
  flowsMoveNode: (id: string, x: number, y: number) => void;
  flowsSetParam: (id: string, key: string, raw: string) => void;
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
  | { kind: "continue"; question: FlowContinueQuestion; flags: FlowRunFlags };

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
}

type SetFn = (fn: (s: FlowsHost) => Partial<FlowsHost>) => void;
type GetFn = () => FlowsHost;

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

/** The sentences in a record's `migrated`, in order. An older server sends no
 *  such key, and an entry with no sentence in it is skipped: printed, it would
 *  be a warn line reading "undefined", which looks like the rig saying
 *  something it did not. */
function migrationNotes(rec: FlowRecordRec): string[] {
  const list = rec.migrated as unknown;
  if (!Array.isArray(list)) return [];
  return list
    .map((m) => (m && typeof m === "object" ? (m as { note?: unknown }).note : undefined))
    .filter((n): n is string => typeof n === "string" && n !== "");
}

export function createFlowsActions(set: SetFn, get: GetFn): FlowsActions {
  const touch = (s: FlowsHost, graph: FlowGraphRec) =>
    patch(s, { graph, dirty: true });

  // THE NEWEST PROGRESS READ WINS, not the last one to arrive. Open, save and
  // a started run each start a read, and two for the same flow can land out of
  // order: the read after a save arriving after the read after a RUN would put
  // the pre-run session's counts back on the card. Per store rather than per
  // module, so two stores (the tests' miniature ones) cannot supersede each
  // other's reads.
  let progressTicket = 0;

  /** Re-read the open flow's progress into `flows.progress` (#189 S1 item 9).
   *  Never rejects, and every caller starts it without awaiting it.
   *
   *  PRIVATE, not a store action: only open, save and a started run have a
   *  reason to re-read, and a public action is one more store member that
   *  the sign-out gate (lib/authGate.ts) would have to be told about. */
  const fetchProgress = async (): Promise<void> => {
    const id = get().flows.record?.id;
    const ticket = ++progressTicket;
    // CLEARED FIRST. Every caller starts a read because what the last answer
    // described has just changed - another flow opened, a save moved step
    // ids, a run may have started a new session - so until the new answer
    // lands the old one is a count of something else, and no chip is drawn
    // from it.
    set((s) => patch(s, { progress: null }));
    if (!id) return;
    try {
      const progress = await flowsApi.progress(id);
      // Kept only while it is still the newest read AND its flow is still the
      // one open: the editor can close, or open another flow, while a read is
      // in flight.
      if (ticket !== progressTicket || get().flows.record?.id !== id) return;
      set((s) => patch(s, { progress }));
    } catch {
      // Left null, and SILENT. A server older than S1 answers 404 for every
      // flow, and a saved graph that cannot become a plan answers 422, which
      // the compile's own doctor already reports. A missing chip claims
      // nothing; a log line on every open would be noise about a decoration.
    }
  };

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
        for (const note of migrationNotes(rec)) get().flowsAppendLog(note, "warn");
        // The TARGET cards' chips (#189 S1 item 9). Not awaited here, nor after
        // a save or a RUN: the read compiles the flow and scans the session
        // files on the server, and a card decoration must not hold the editor
        // open, the save before a close, or the RUN press.
        void fetchProgress();
        await get().flowsCompile();
      } catch (e) {
        set((s) => patch(s, { libraryError: errText(e) }));
      }
    },

    flowsSave: async () => {
      const { record, graph, dirty } = get().flows;
      if (!record || record.readonly || !dirty) return;
      try {
        const saved = (await flowsApi.save(record.id,
          { ...record, graph })) as FlowRecordRec;
        set((s) => patch(s, { record: saved, dirty: false }));
        // What the server counts is the STORED graph, and the save just
        // changed it: a new exposure is a new step id with nothing banked.
        void fetchProgress();
      } catch (e) {
        set((s) => patch(s, { libraryError: errText(e) }));
      }
    },

    flowsCloseEditor: async () => {
      await get().flowsSave();
      set((s) => patch(s, {
        record: null, graph: { nodes: [], edges: [] }, dirty: false,
        sel: null, editNode: null, wire: null, tapWire: null,
        // An answer belongs to the open record and goes with it.
        progress: null,
        ui: { ...s.flows.ui, screen: "library", paletteOpen: false },
      }));
      await get().flowsLoadLibrary();
    },

    // ─────────────────────────────────────────────────────────── graph edits
    flowsAddNode: (type, at) => set((s) => {
      const def = NODE_DEFS[type];
      const node = {
        id: nextNodeId(), type, x: at.x, y: at.y,
        params: { ...def.params },
      };
      return touch(s, { ...s.flows.graph,
                        nodes: [...s.flows.graph.nodes, node] });
    }),

    flowsMoveNode: (id, x, y) => set((s) => touch(s, {
      ...s.flows.graph,
      nodes: s.flows.graph.nodes.map((n) => (n.id === id ? { ...n, x, y } : n)),
    })),

    flowsSetParam: (id, key, raw) => set((s) => {
      const node = s.flows.graph.nodes.find((n) => n.id === id);
      if (!node) return {};
      // COERCION KEYS OFF THE TYPE OF THE DEFAULT, exactly as the prototype
      // does. That is why capture's `bin` stays the string "1" - it is a select
      // whose options are strings - while every numeric field reverts to its
      // default on unparseable input rather than becoming NaN. A NaN here
      // reaches the compiler as a step with no exposure.
      const base = NODE_DEFS[node.type].params[key];
      const v = typeof base === "number"
        ? (Number.isNaN(parseFloat(raw)) ? base : parseFloat(raw))
        : raw;
      return touch(s, { ...s.flows.graph,
        nodes: s.flows.graph.nodes.map((n) =>
          n.id === id ? { ...n, params: { ...n.params, [key]: v } } : n) });
    }),

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
        : { nodes: g.nodes.filter((n) => n.id !== sel.id),
            edges: g.edges.filter((e) => e.from !== sel.id && e.to !== sel.id) };
      return { flows: { ...s.flows, graph, dirty: true, sel: null,
                        editNode: s.flows.editNode === sel.id
                          ? null : s.flows.editNode } };
    }),

    flowsConnect: (from, fromPort, to, toPort) => {
      // NO FLOW LOOPS (#149), refused HERE as well as in both drop resolvers,
      // because tap-to-wire (`flowsTapPort`) reaches this action without
      // passing through any resolver. A flow wire that closes a circle makes
      // the compiler drop every stage on it, and none of them shoots a frame.
      // So the graph is left exactly as it was and the server's own sentence
      // goes to the flow log: a tap passes no resolver and so gets no toast,
      // and a refusal that said nothing would read as a tap that missed.
      const { nodes, edges } = get().flows.graph;
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
        return touch(s, {
          ...g,
          edges: g.edges
            .filter((e) => !(flowInput && e.to === to && e.toPort === toPort))
            .concat([{ id: nextEdgeId(), from, fromPort, to, toPort }]),
        });
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
      set((s) => patch(s, { compiling: true }));
      try {
        const compiled = await flowsApi.compileDraft(graph, record?.name ?? "");
        set((s) => patch(s, { compiled, compiling: false }));
      } catch (e) {
        // The compile is advisory - it drives the doctor chip, not the run - so
        // a failure leaves the LAST GOOD result in place rather than blanking
        // the chip. A chip that vanished on a dropped request would read as
        // "no problems found".
        set((s) => patch(s, { compiling: false }));
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
          run: { ...s.flows.run, phase: "running",
                 frames: 0, frameGoal: res.frames,
                 acceptedUnmapped: flags.acceptUnmapped ? (res.unmapped ?? []) : [] },
        }));
        // Which ledger the night went into, in words - "continued night 3: 7
        // steps kept, 0 new". Without it a CONTINUE is indistinguishable from
        // a fresh start on every surface that shows the log.
        const line = sessionLogLine(res.session);
        if (line) get().flowsAppendLog(line, "info");
        // The run may have gone into a NEW session (START OVER, or the first
        // night), whose counts are not the ones the cards are showing.
        void fetchProgress();
        return null;
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
