// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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

/** `POST /api/flows/wizard`'s body, server `FlowWizardBody` key for key.
 *
 *  The first three are the NEW FLOW sheet's answers, and a body with only
 *  those generates exactly the graph it always did (Revision 2 ruling 4).
 *  Every other key is OPTIONAL and is LEFT OUT when there is nothing to say:
 *  the route reads a missing key as "not given", and each one is inert then.
 *
 *  - the Mosaic kind's: `rows`, `cols`, `overlap_pct` (percent), `angle_mode`
 *    (one of the two mosaic angles), and either `pa_deg` or `use_measured`;
 *  - the door's (#196, S6): `ra` and `dec` as typed, `skip` in the TARGET's
 *    own syntax, `cycle_plan` (a FILTER CYCLE slot table, "L 60, R 60") with
 *    `cycles` (subs of each), and `guiding`;
 *  - the NIGHT and RESUME steps' (#196, backlog WP-100), written to the
 *    lane's one DUSK WINDOW: `stop` ("Dawn" or "Clock time"; never "None")
 *    with `stop_clock`, `start` (the three dusks or "Clock time") with
 *    `start_clock`, each clock strict "HH:MM" and sent only beside its
 *    "Clock time"; `min_alt` (degrees, 0 to 90); and `auto_resume` ("On" or
 *    "Off"). A key the sheet's answer leaves at DUSK WINDOW's own default is
 *    left out.
 *
 *  The camera field, the rotator, the measured angle and the wheel are RIG
 *  FACTS the route injects; there is no key for any of them. The kind and
 *  the chip labels are wizard.py's constants, sent as they render. Built by
 *  `components/flows/wizard/wizardModel.ts::wizardBody` for Send to Flow
 *  Wizard, and graded against the route's recorded answer
 *  (server/tests/fixtures/wizard_mosaic_answer.json). */
export interface FlowWizardAnswers {
  kind: string;
  options: string[];
  target: string;
  unguided_exposure_s?: number;
  rows?: number;
  cols?: number;
  overlap_pct?: number;
  angle_mode?: string;
  pa_deg?: number;
  use_measured?: boolean;
  ra?: string;
  dec?: string;
  skip?: string;
  cycle_plan?: string;
  cycles?: number;
  guiding?: boolean;
  stop?: string;
  stop_clock?: string;
  start?: string;
  start_clock?: string;
  min_alt?: number;
  auto_resume?: string;
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
 *  - `acceptReopen`: reopen the flow's COMPLETE session because the flow was
 *    edited to owe more (409 `reopen`, #179). An answer, never a command: a
 *    complete session that owes nothing starts fresh whatever this says.
 *
 *  The last five are CONTINUE's (#189 S1, spec 5.9; server `FlowRunBody`). The
 *  server asks them one at a time, so a re-post must carry every flag already
 *  accepted - `nextRunFlags` in flowsSlice is the one place that builds one. */
export interface FlowRunFlags {
  acceptUnmapped?: boolean;
  force?: boolean;
  fresh?: boolean;
  adopt?: boolean;
  acceptDropped?: boolean;
  acceptRecount?: boolean;
  acceptReopen?: boolean;
}

/** Which ledger tonight's frames go into (server `run_flow`'s `session`).
 *  `kept`, `new` and `dropped` are STEP counts; `adopted.matched` and each
 *  unmatched entry's `frames` are SUB counts. `id` is null when nothing was
 *  persisted. */
export interface FlowRunSession {
  id: string | null;
  /** The observing night this run is of its session (server
   *  `Session.night_at`, #430, S7 orchestrator ruling 7): the nights it has
   *  run so far, plus one only when tonight is not already among them, so a
   *  second press in the same evening is the night the log is already
   *  writing. A fresh session's first run is night 1. */
  night: number;
  continued: boolean;
  kept: number;
  new: number;
  dropped: number;
  adopted?: {
    matched: number;
    unmatched: { frames?: number; reason?: string; target?: string }[];
  };
  /** True only when this run reopened a session that was COMPLETE, because
   *  the flow was edited to owe more and the operator said yes (server
   *  `_continue_flow_session`, #179). Absent on every other answer. */
  reopened?: boolean;
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
  /** The angle the session has locked this panel's target to (Revision 2,
   *  owner ruling 9), in the CROTA2 convention, with where it came from in
   *  words (server `progress.LOCK_SOURCE`). Absent when there is no lock;
   *  never the lock's times, which a viewer may not read. */
  locked_angle?: { pa_deg: number; source: string };
  steps: FlowProgressStep[];
}

/** A panel the operator skipped on a mosaic block (#189 S3). It owes nothing
 *  while skipped and is in none of the block's sums, and it is NOT one of the
 *  block's `panels`. `banked` is what the ledger still holds on it, counted by
 *  the session's count mode, so re-enabling the panel brings those subs back.
 *  Row and col are null only for an id that matches no cell of the grid. */
export interface FlowProgressSkipped {
  target_id: string;
  name: string;
  row: number | null;
  col: number | null;
  banked: number;
}

/** One panel a dormant session's standing set-aside record holds back
 *  tonight (#727, WP-141): the panel's target id, its name and its 0-based
 *  grid position. */
export interface FlowProgressSetAside {
  target_id: string;
  name: string;
  row: number | null;
  col: number | null;
  /** A centring set-aside that may still expire tonight. */
  for_now: boolean;
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
  /** A mosaic's panels are the ones the plan SHOOTS, in the plan's order, each
   *  at its own 0-based row and col; a skipped panel is listed in `skipped`
   *  instead (server `progress._mosaic`). */
  panels: FlowProgressPanel[];
  /** Present only on a MOSAIC block, a TARGET with a grid (#189 S3): the grid
   *  as the operator drew it, skipped panels included. Its presence is how a
   *  reader tells a mosaic from a single target, whose block never has it. */
  grid?: { rows: number; cols: number };
  /** Present only on a mosaic block, in grid order. */
  skipped?: FlowProgressSkipped[];
  /** Present only on a mosaic block (#189 S5): the id of the plan group that
   *  shoots its panels, which is the `SequenceGroupState.id` a run publishes
   *  while it shoots them, so the run-mode sheet finds the live group of the
   *  block it draws by this id (`flowRunState.groupForBlock`) and never by
   *  name: two blocks may share one. Null when the plan holds no group for
   *  the block (every panel skipped, or no coordinates), so no run will ever
   *  publish its group. */
  group_id?: string | null;
  /** Present only on a mosaic block of a DORMANT session that holds a
   *  standing set-aside record for tonight (#727, server `progress.
   *  _set_aside_tonight`): the panels a RETRY would take back. Words and
   *  flags only, `row` and `col` 0-based. */
  set_aside?: FlowProgressSetAside[];
  /** A TARGET block's locked angle: the one every panel the plan holds is
   *  locked to (server `progress._block_lock`). Absent when any panel is
   *  unlocked or two panels disagree, and on a POOL block. */
  locked_angle?: { pa_deg: number; source: string };
}

export interface FlowProgressSession {
  id: string;
  status: "active" | "dormant" | "complete";
  /** How many OBSERVING NIGHTS the session has run: one per distinct night
   *  key (local noon to local noon, the key the night log is named by) of
   *  its runs' starts, so a restart in the same night is the same night
   *  (server `Session.observing_nights`; #430, S7 orchestrator ruling 7).
   *  It counted RUNS until S7, one per engine start, so a night that held a
   *  restart counted twice; the key kept its name and changed its meaning.
   *
   *  CONTINUE's `night` (`FlowRunSession.night`) is this plus one on a night
   *  the session has not run, and this on a night it has, so the button
   *  never adds one to it: it prints `continue_night`, below (#511). */
  nights: number;
  count_mode: "attempts" | "accepted";
  /** The night a CONTINUE pressed now would start: the number the run
   *  route then answers as `FlowRunSession.night` (#511, H4). The ROUTE adds
   *  it (server `progress.continue_night`) from the clock it read for the
   *  request, through `Session.night_at`, the rule the run route itself
   *  answers by, so the button and the run's own log line cannot disagree.
   *  Until H4 this answer carried no clock and the button printed `nights`
   *  plus one, one night ahead of the run route on a night the session had
   *  already run (a second CONTINUE in the evening, a CONTINUE after a
   *  crash).
   *
   *  PRESENT ONLY ON A DORMANT SESSION, the one status `run_flow`
   *  continues, the way a panel carries `locked_angle` only where there is a
   *  lock: an active or complete session answers without it, and so does a
   *  server older than H4. `runCopy` reads it through `finite`, so the
   *  button then prints no night rather than guess one. A count keyed by
   *  the server's local noon-to-noon night, never a time and never the
   *  site. */
  continue_night?: number;
  /** Whether auto-resume would start this session at dusk: dormant, with
   *  auto-resume on (server `Session.is_armed`, the rule ResumeArm picks
   *  by). Added by the ROUTE (`progress.replay_facts`; #473, S7 orchestrator
   *  ruling 1), which since S7 always sends it. OPTIONAL because a server
   *  older than S7 sends neither this nor `plan_saved_ts`, and every reader
   *  must survive that answer: `replayNotice` reads a missing value as not
   *  armed. test_types_mirror_status.py holds these two as the optional
   *  members the route always sends; `continue_night`, which it sends only
   *  sometimes, is optional by that test's carried-sometimes rule. */
  armed?: boolean;
  /** The flow record's saved time (`updated_ts`, unix seconds) for the
   *  version this session froze: what an armed auto-resume would replay.
   *  Null for a session older than S7, a shipped Example's (never saved) and
   *  one whose plan a PATCH replaced: the route never guesses a time. Absent
   *  from a server older than S7 (see `armed`). The editor's replay line
   *  compares it with the open record's `updated_ts` (`replayNotice`). */
  plan_saved_ts?: number | null;
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

// ------------------------------------------ GET /api/flows/{id}/tonight: mosaic
// The Tonight answer is `Record<string, unknown>` at the store boundary on
// purpose (flowsSlice: it is large and only the Tonight surfaces read it), so
// these types describe the one key S3 added to a target row rather than the
// whole answer. Server `flows/tonight.py::_mosaic_night`, whose docstring is
// the contract. Every altitude here is f(site), which is why the route is
// `CAP_VIEW_SITE_DERIVED` and why nothing below may reach a surface a viewer
// can open.

/** One panel of a mosaic's band: 1-based "row-col" as the Plan and the log
 *  name it ("2-1"), 0-based row and col, and its PEAK altitude tonight in
 *  degrees (`framing._stamp_transit_alt`, sampled on the panel's centre). */
export interface TonightBandPanel {
  panel: string;
  row: number;
  col: number;
  transit_alt: number;
}

/** The lowest and the highest peak among a mosaic's answered panels, first in
 *  grid order on a tie: the spread the block's one centre curve cannot show. */
export interface TonightMosaicBand {
  worst: TonightBandPanel;
  best: TonightBandPanel;
}

/** A multi-panel TARGET's `mosaic` key on its Tonight row. Absent from a
 *  single target's row and a pool member's. `band` is null when no panel was
 *  answered, and `reason` then says why no panel could be placed at all. */
export interface TonightMosaic {
  rows: number;
  cols: number;
  /** The grid less its skips. */
  live: number;
  /** "r-c" labels of the skipped panels, which are not stamped. */
  skipped: string[];
  panels: {
    panel: string; row: number; col: number;
    transit_alt?: number; transit_alt_error?: string;
  }[];
  band: TonightMosaicBand | null;
  reason?: string;
}

/** A band edge, or null unless every field is what the contract says. */
function bandPanel(v: unknown): TonightBandPanel | null {
  if (v === null || typeof v !== "object" || Array.isArray(v)) return null;
  const p = v as Record<string, unknown>;
  const n = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
  if (typeof p.panel !== "string" || p.panel === "") return null;
  if (!n(p.row) || !n(p.col) || !n(p.transit_alt)) return null;
  return { panel: p.panel, row: p.row, col: p.col, transit_alt: p.transit_alt };
}

/** The band a Tonight target row's `mosaic` value carries, or null.
 *
 *  THE ONE READING BOTH TIMELINES SHARE, for the same reason `unreadableReason`
 *  is one: the classic panel and the #/next sheet each read the Tonight answer
 *  with a reader of their own, and a second reading of the band is how one
 *  timeline comes to draw a spread the other does not.
 *
 *  THE SERVER'S REDUCTION, NOT A SECOND ONE. `tonight.py::_band` already picks
 *  the worst and the best panel the way `mosaicNightSummary` does (the lowest
 *  and highest answered peak), so this reads its answer and re-derives
 *  nothing from `panels`: a copy of the reduction here would be a second rule
 *  for which panel limits the night.
 *
 *  Total: a row with no `mosaic` (a single target, a pool member), a band the
 *  server sent null (no panel answered), or a band with any field missing or
 *  not a finite number is "no band", never a strip drawn at 0 degrees. So is
 *  a band whose worst edge peaks above its best, which no answer of `_band`
 *  can be: a timeline cannot draw an upside-down spread truthfully. */
export function mosaicBand(mosaic: unknown): TonightMosaicBand | null {
  if (mosaic === null || typeof mosaic !== "object" || Array.isArray(mosaic)) return null;
  const band = (mosaic as Record<string, unknown>).band;
  if (band === null || typeof band !== "object" || Array.isArray(band)) return null;
  const worst = bandPanel((band as Record<string, unknown>).worst);
  const best = bandPanel((band as Record<string, unknown>).best);
  if (!worst || !best || worst.transit_alt > best.transit_alt) return null;
  return { worst, best };
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

  /** The wizard's answers -> a generated, SAVED flow, with the generator's
   *  `notes` beside the record. The rules live in
   *  server/astrodeck/flows/wizard.py and are not duplicated here; `kind` and
   *  the `options` labels are that module's own constants, so the sheet sends
   *  the strings it renders. The NEW FLOW sheet sends the three answers;
   *  Send to Flow Wizard sends the door's as well, and the night's it changed
   *  (`FlowWizardAnswers`). */
  generateFromWizard: (answers: FlowWizardAnswers) => api.post<unknown>(FLOWS_WIZARD, answers),

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

  /** RETRY SET-ASIDE PANELS (#600; backlog ruling D-07, owner-approved
   *  2026-09-30): `POST /api/sequence/retry-set-aside`, `control.mount`.
   *  `group` is the live `state.group.id` the progress block names, or null
   *  for every group of the plan. While a run is live the server QUEUES the
   *  panels (`queued`, their `r-c` labels) and the run takes them up at its
   *  next selection, judging each as it judges any panel; `sessionId` is for
   *  a stored session with no run, whose records it clears (`cleared`).
   *  409 "nothing is set aside" and 404 are the server's own words. */
  retrySetAside: (group: string | null, sessionId?: string) =>
    api.post<{ queued?: string[]; cleared?: string[]; live: boolean }>(
      "/api/sequence/retry-set-aside",
      sessionId ? { group, session_id: sessionId } : { group }),

  /** `acceptUnmapped` is the operator saying "run the rest anyway". It does NOT
   *  clear a dome refusal: everything else on that list costs frames, and a roof
   *  that will not close costs equipment.
   *
   *  The first six flags go on EVERY request, false unless set, so a body says
   *  in full what was accepted and a reader of the request never has to know
   *  the server's defaults. `accept_reopen` (#179) goes only when it is true:
   *  a request that never meets that question keeps the body it always had,
   *  byte for byte, which the Send-to-Wizard sheet's test pins. The server
   *  reads its absence as false. A bare boolean is the older `(id,
   *  acceptUnmapped, force)` form, still accepted; the third argument is read
   *  only in that form, and the object form carries its own `force`. */
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
      ...(f.acceptReopen === true ? { accept_reopen: true } : {}),
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
