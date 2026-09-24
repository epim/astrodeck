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
  /** Present ONLY on a row for a file this build cannot open (#153), and then
   *  it is the whole reason, already worded for display: "saved by a newer
   *  AstroDeck (schema 4); update to open it", or "unreadable: <reason>"
   *  (server `FlowStore._row`). Every other route answers 404 for such an id,
   *  so a row is drawn read-only and never opened or run. Absent from every
   *  card an older server sends. Read it through `unreadableReason`. */
  unreadable?: string;
}

/** What an unreadable row says when the server marked it with an empty
 *  reason. The KEY is the server saying "this is a row, not a record"; an
 *  empty sentence there is a broken reason, never a readable flow. */
export const UNREADABLE_FALLBACK = "this AstroDeck cannot open this flow";

/** Why this card cannot be opened or run, or null for an ordinary card.
 *
 *  The one reading of `unreadable` that every surface shares - both libraries
 *  and SESSION / NOW's runnable list - so no surface decides on its own that an
 *  empty or odd value means "go ahead and open it". */
export function unreadableReason(card: Pick<FlowCard, "unreadable">): string | null {
  const r = card.unreadable as unknown;
  if (r === undefined || r === null) return null;
  return typeof r === "string" && r.trim() !== "" ? r : UNREADABLE_FALLBACK;
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
  // --- the three OPTIONAL fields `to_plan._note` gained so a note can be
  //     rendered as a statement about the rig rather than as a defect report.
  //     Every one is optional and every consumer must degrade to `detail`: an
  //     older engine sends the sentence and nothing else, and this client is
  //     routinely pointed at one (a rig on the previous release, the relay).
  /** The card's own fields the plan DOES carry, already worded for display
   *  ("presence: the night guides", "threshold 3.2"). */
  carried?: string[];
  /** The card's fields the plan does not carry, worded the same way
   *  ("settle 1.5 s", "dither 3 px", "provider PHD2"). NOT a loss on its own:
   *  at `note` level the run takes these from the rig instead. */
  ignored?: string[];
  /** Where the values in `ignored` really live, as a screen path the reader can
   *  follow ("Rig > Guider"). */
  source?: string;
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

/** The quick sheet's payload. `target` carries the three strings the TARGET
 *  node stores -- the name alone is not enough, because `to_plan` reads ra/dec
 *  and never the name, so a flow with a name and no coordinates slews to the
 *  node's shipped default and files the frames under the name that was typed. */
export interface QuickFlowAnswers {
  target: { name: string; ra: string; dec: string };
  subs: number;
  /** Ticked wheel slots, in any order -- the server sorts them into wheel order.
   *  Empty means one channel (colour camera / no wheel). */
  filters: string[];
  /** Seconds per filter. What the sheet displayed, so the operator gets the
   *  numbers they were looking at. In the one-channel case it carries a single
   *  entry whose key is a label, not a slot. */
  exposures?: Record<string, number>;
  guided: boolean;
  run: boolean;
  name?: string;
}

export interface QuickFlowResult {
  flow: { id: string; name: string; [k: string]: unknown };
  started: boolean;
  run?: FlowRunResult;
}

/** What `POST /api/flows/{id}/run` may be told the operator already said yes
 *  to. Every one defaults to false, and each lifts exactly one refusal:
 *
 *  - `acceptUnmapped`: run although parts of the graph are not honoured
 *    (409 `unmapped`). Never the dome refusal.
 *  - `force`: past the horizon check. Never the Sun check.
 *  - `fresh`: START OVER - a new session, the flow's dormant one left on disk.
 *  - `adopt`: re-key a session saved before flows kept their step ids onto
 *    this compile's ids (409 `adopt`).
 *  - `acceptDropped`: continue although steps holding frames are gone from the
 *    flow (409 `dropped_steps`).
 *  - `acceptRecount`: continue under a different count mode, which recounts
 *    every banked frame (409 `recount`).
 *
 *  The last four are CONTINUE's (#189 S1, spec 5.9; server `FlowRunBody`). The
 *  server asks them one at a time, so a re-post must carry every flag already
 *  accepted - `nextRunFlags` in flowsSlice is the one place that builds one. */
export interface FlowRunFlags {
  acceptUnmapped?: boolean;
  force?: boolean;
  fresh?: boolean;
  adopt?: boolean;
  acceptDropped?: boolean;
  acceptRecount?: boolean;
}

/** Which ledger tonight's frames go into (server `run_flow`'s `session`).
 *  `kept`, `new` and `dropped` are STEP counts; `adopted.matched` and each
 *  unmatched entry's `frames` are SUB counts. `id` is null when nothing was
 *  persisted. */
export interface FlowRunSession {
  id: string | null;
  night: number;
  continued: boolean;
  kept: number;
  new: number;
  dropped: number;
  adopted?: {
    matched: number;
    unmatched: { frames?: number; reason?: string; target?: string }[];
  };
}

export interface FlowRunResult {
  started: boolean;
  flow_id: string;
  frames: number;
  unmapped: FlowUnmapped[];
  /** Absent from every answer a server older than S1 sends. */
  session?: FlowRunSession;
}

// ------------------------------------------------ GET /api/flows/{id}/progress
// What a flow has banked and what it still owes, per block, per panel and per
// step (#189 S1 item 9; server `flows/progress.py::flow_progress`, whose
// docstring is the contract these types copy). Every count is a SUB count.
// The numbers come from the session Run would continue: the flow's newest,
// and none when that one was abandoned (server `current_for_flow`, #189
// hardening A2), counted by its frozen count mode, against the STORED graph
// compiled with the flow's id - so they are the saved flow's, never the
// editor's unsaved one.

export interface FlowProgressStep {
  step_id: string;
  filter: string | null;
  frame_type: string;
  exposure_s: number;
  count: number;
  /** Capped at `count`: frames past the quota are real subs, but a step
   *  cannot owe a negative number. */
  banked: number;
  owed: number;
}

/** One place the block images. A TARGET block has one, at row 0 and col 0; a
 *  POOL block has one per member with row and col null. `target_id` is null
 *  for an entry the plan dropped (no coordinates), which owes nothing. */
export interface FlowProgressPanel {
  target_id: string | null;
  name: string;
  row: number | null;
  col: number | null;
  banked: number;
  owed: number;
  total: number;
  steps: FlowProgressStep[];
}

/** One canvas node: `node_id` is the node's id, which is how a card finds its
 *  own block. */
export interface FlowProgressBlock {
  node_id: string;
  name: string;
  kind: "target" | "pool";
  banked: number;
  owed: number;
  total: number;
  panels: FlowProgressPanel[];
}

export interface FlowProgressSession {
  id: string;
  status: "active" | "dormant" | "complete";
  /** How many nights the session has run. */
  nights: number;
  count_mode: "attempts" | "accepted";
}

export interface FlowProgress {
  flow_id: string;
  /** Null when the flow has never run, or its newest session was abandoned
   *  (an older one is not read in its place): then every block's `banked` is
   *  0 because nothing was counted, not because nothing was shot. */
  session: FlowProgressSession | null;
  blocks: FlowProgressBlock[];
  /** Frames in the session whose step the flow no longer has (a changed
   *  recipe is a new step id), and how many step ids they sit on. They fill
   *  no quota, so they are in NO block's `banked`. */
  orphaned: { frames: number; steps: number };
}

// Spelled out, not composed. `${FLOWS_BASE}/folders` reads the same to a human
// and is invisible to a grep — which is exactly what the route-caller detector
// does, and it was right to fail on it: a path you cannot search for is a path
// nobody can trace from the server to the screen.
export const FLOWS_BASE = "/api/flows";
export const FLOWS_FOLDERS = "/api/flows/folders";
export const FLOWS_COMPILE_DRAFT = "/api/flows/compile";
export const FLOWS_WIZARD = "/api/flows/wizard";
export const FLOWS_QUICK = "/api/flows/quick";
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

  /** The quick sheet's four answers -> a generated, SAVED flow, optionally
   *  already running.
   *
   *  Same division of labour as the wizard: the graph shape lives in
   *  server/astrodeck/flows/wizard.py and this sends answers, not a graph. The
   *  route reuses `POST /api/flows/{id}/run`'s own handler for `run`, so a
   *  quick flow cannot start behind a guard that route applies.
   *
   *  `filters: []` is the ONE-CHANNEL rig (a colour camera, or no wheel). It is
   *  an answer, not an omission -- there is nothing to tick -- and the server
   *  builds a capture loop with no filter name rather than inventing a slot. */
  quick: (answers: QuickFlowAnswers) =>
    api.post<QuickFlowResult>(FLOWS_QUICK, answers),
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

  /** Banked and owed subs of the SAVED flow (`CAP_VIEW_STATUS`, so a viewer's
   *  canvas can ask). 404 for an unknown or unreadable id and from any server
   *  older than S1; 422 `invalid_graph` for a saved graph that cannot become a
   *  plan. The TARGET card's chip reads it through `progressChip`. */
  progress: (id: string) => api.get<FlowProgress>(`${one(id)}/progress`),

  /** `acceptUnmapped` is the operator saying "run the rest anyway". It does NOT
   *  clear a dome refusal: everything else on that list costs frames, and a roof
   *  that will not close costs equipment.
   *
   *  All six flags go on EVERY request, false unless set, so a body says in
   *  full what was accepted and a reader of the request never has to know the
   *  server's defaults. A bare boolean is the older `(id, acceptUnmapped,
   *  force)` form, still accepted; the third argument is read only in that
   *  form, and the object form carries its own `force`. */
  run: (id: string, flags: FlowRunFlags | boolean = {}, force = false) => {
    const f: FlowRunFlags = typeof flags === "boolean"
      ? { acceptUnmapped: flags, force }
      : flags;
    return api.post<FlowRunResult>(`${one(id)}/run`, {
      accept_unmapped: f.acceptUnmapped === true,
      force: f.force === true,
      fresh: f.fresh === true,
      adopt: f.adopt === true,
      accept_dropped: f.acceptDropped === true,
      accept_recount: f.acceptRecount === true,
    });
  },

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
