// flowsApi.ts — the one place the Flows surface talks to the server.
//
// Every path lives here rather than being spelled out at each call site, for the
// reason `tests/test_routes_have_callers.py` exists: a server route with no
// caller is a route nobody can reach, and the detector that catches that scans
// UI string literals. Keeping the paths in one module means the check has one
// honest place to look and the canvas never hand-builds a URL.
//
// Deliberately thin. There is no caching, no retry and no store coupling: the
// editor already owns its graph state, and a second copy of it living behind a
// fetch wrapper is how two truths appear.
import { api } from "../api";

/** The library-card projection. NOT the graph — listing 30 flows at up to 400
 *  nodes each would ship megabytes of wires to draw a card wall. */
export interface FlowCard {
  id: string;
  name: string;
  folder: string;
  tagline: string;
  readonly: boolean;
  stages: number;
  wires: number;
  last_run: number | null;
  last_result: string;
  updated_ts: number;
}

export interface FlowFolder {
  name: string;
  count: number;
  readonly: boolean;
}

/** One thing a graph does that the run will not honour.
 *
 *  `level` shares the doctor's vocabulary on purpose — an operator should not
 *  have to learn that a doctor warning and a compile warning mean different
 *  things. */
export interface FlowUnmapped {
  key: string;
  detail: string;
  /** `note` is not a quieter `warn`. It says the thing drawn on the canvas IS
   *  honoured, by some other part of the engine than the one the wire names —
   *  the cloud hold releases itself, the scheduler advances the pool. `/start`
   *  does not make the operator accept these (server: `to_plan.losses`). */
  level: "warn" | "danger" | "note";
}

export interface FlowIssue {
  text: string;
  level: string;
}

/** Four lists, because four different things can be wrong with a graph:
 *  `structural` is a broken wire, `issues` is the doctor's advice, `unmapped`
 *  is what the engine cannot carry, and `plan` is what it will actually run. */
export interface FlowCompileResult {
  plan: Record<string, unknown>;
  structural: string[];
  issues: FlowIssue[];
  unmapped: FlowUnmapped[];
}

export interface FlowRunResult {
  started: boolean;
  flow_id: string;
  frames: number;
  unmapped: FlowUnmapped[];
}

// Spelled out, not composed. `${FLOWS_BASE}/folders` reads the same to a human
// and is invisible to a grep — which is exactly what the route-caller detector
// does, and it was right to fail on it: a path you cannot search for is a path
// nobody can trace from the server to the screen.
export const FLOWS_BASE = "/api/flows";
export const FLOWS_FOLDERS = "/api/flows/folders";
export const FLOWS_COMPILE_DRAFT = "/api/flows/compile";
export const FLOWS_WIZARD = "/api/flows/wizard";
export const CALIBRATION_HEALTH = "/api/calibration/health";

const one = (id: string) => `${FLOWS_BASE}/${encodeURIComponent(id)}`;

export const flowsApi = {
  list: () => api.get<FlowCard[]>(FLOWS_BASE),
  get: (id: string) => api.get<unknown>(one(id)),

  /** Upsert. The server re-derives `readonly`, `created_ts`, `last_run` and
   *  `last_result` from what it already has, so sending them is harmless and
   *  forging them is not possible. */
  create: (flow: unknown) => api.post<unknown>(FLOWS_BASE, { flow }),

  /** The wizard's three answers -> a generated, SAVED flow. The rules live in
   *  server/astrodeck/flows/wizard.py and are not duplicated here; `kind` and
   *  the `options` labels are that module's own constants, so the sheet sends
   *  the strings it renders. */
  generateFromWizard: (answers: {
    kind: string; options: string[]; target: string;
  }) => api.post<unknown>(FLOWS_WIZARD, answers),
  save: (id: string, flow: unknown) => api.put<unknown>(one(id), { flow }),
  remove: (id: string) => api.del<{ deleted: string }>(one(id)),

  folders: () => api.get<FlowFolder[]>(FLOWS_FOLDERS),
  renameFolder: (name: string, newName: string) =>
    api.post<{ moved: number; folder: string }>(FLOWS_FOLDERS, {
      name,
      new_name: newName,
    }),
  /** Re-parents into My flows; it never deletes a flow. */
  deleteFolder: (name: string) =>
    api.del<{ moved: number; reparented_to: string }>(
      `${FLOWS_FOLDERS}/${encodeURIComponent(name)}`,
    ),

  compile: (id: string) => api.post<FlowCompileResult>(`${one(id)}/compile`),
  /** The live doctor for unsaved work — the editor calls this as you wire. */
  compileDraft: (graph: unknown, name = "") =>
    api.post<FlowCompileResult>(FLOWS_COMPILE_DRAFT, { graph, name }),

  tonight: (id: string) => api.get<Record<string, unknown>>(`${one(id)}/tonight`),

  /** `acceptUnmapped` is the operator saying "run the rest anyway". It does NOT
   *  clear a dome refusal: everything else on that list costs frames, and a roof
   *  that will not close costs equipment. */
  run: (id: string, acceptUnmapped = false, force = false) =>
    api.post<FlowRunResult>(`${one(id)}/run`, {
      accept_unmapped: acceptUnmapped,
      force,
    }),

  /** Without a flow id the answer is an empty matrix and `planned: false`.
   *  An empty matrix MUST NOT be drawn as healthy — it means no lights are
   *  planned yet, which is a different statement from "you have everything". */
  calibrationHealth: (flowId?: string) =>
    api.get<{
      rows: Record<string, unknown>[];
      planned: boolean;
      counts_masters_only: boolean;
      assumed: { offset: number; temp_c: number | null };
    }>(flowId ? `${CALIBRATION_HEALTH}?flow_id=${encodeURIComponent(flowId)}` : CALIBRATION_HEALTH),
};
