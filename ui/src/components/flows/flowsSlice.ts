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
import { flowsApi } from "../../lib/flowsApi";
import type { FlowCard, FlowFolder } from "../../lib/flowsApi";
import { NODE_DEFS } from "./nodeDefs";
import { fitView, type Rect } from "./geometry";
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
  ui: { screen: "library", phoneTab: "flow", query: "", folderChip: "all",
        tonightOpen: false, tonightTab: "timeline", wizardOpen: false,
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
  flowsRun: (acceptUnmapped?: boolean) => Promise<FlowCompileResult["unmapped"] | null>;

  flowsAppendLog: (msg: string, tone?: FlowLogLine["tone"]) => void;
  flowsSetUi: (patch: Partial<FlowsUiState>) => void;
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

export function createFlowsActions(set: SetFn, get: GetFn): FlowsActions {
  const touch = (s: FlowsHost, graph: FlowGraphRec) =>
    patch(s, { graph, dirty: true });

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
          ui: { ...s.flows.ui, screen: "editor" },
        }));
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
      } catch (e) {
        set((s) => patch(s, { libraryError: errText(e) }));
      }
    },

    flowsCloseEditor: async () => {
      await get().flowsSave();
      set((s) => patch(s, {
        record: null, graph: { nodes: [], edges: [] }, dirty: false,
        sel: null, editNode: null, wire: null, tapWire: null,
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

    flowsConnect: (from, fromPort, to, toPort) => set((s) => touch(s, {
      ...s.flows.graph,
      // ONE WIRE PER INPUT, enforced by REPLACING on drop. models.py names this
      // as the invariant the server relies on: it refuses a graph with two
      // wires into one input, so an editor that appended would produce a graph
      // that saves and then will not load.
      edges: s.flows.graph.edges
        .filter((e) => !(e.to === to && e.toPort === toPort))
        .concat([{ id: nextEdgeId(), from, fromPort, to, toPort }]),
    })),

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

    flowsRun: async (acceptUnmapped = false) => {
      const id = get().flows.record?.id;
      if (!id) return null;
      try {
        const res = await flowsApi.run(id, acceptUnmapped);
        set((s) => patch(s, {
          run: { ...s.flows.run, phase: "running",
                 frames: 0, frameGoal: res.frames,
                 acceptedUnmapped: acceptUnmapped ? (res.unmapped ?? []) : [] },
        }));
        return null;
      } catch (e) {
        // A 409 carrying `unmapped` is not a failure - it is the server asking
        // whether the operator accepts running a flow that will not honour part
        // of their graph. Handing the list back lets the caller show it and
        // ask; swallowing it would turn a question into an error.
        const err = e as { code?: string; body?: { unmapped?: unknown } };
        const list = (err as { unmapped?: FlowCompileResult["unmapped"] }).unmapped
          ?? (err.body?.unmapped as FlowCompileResult["unmapped"] | undefined);
        if (err.code === "unmapped" && list) return list;
        get().flowsAppendLog(`could not start: ${errText(e)}`, "bad");
        return null;
      }
    },

    // ──────────────────────────────────────────────────────────────── misc
    flowsAppendLog: (msg, tone = "info") => set((s) => patch(s, {
      logs: [...s.flows.logs,
             { id: ++logSeq, ts: Date.now(), msg, tone }].slice(-LOG_RING),
    })),

    flowsSetUi: (p) => set((s) => patch(s, { ui: { ...s.flows.ui, ...p } })),
  };
}
