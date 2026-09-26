// flowsTypes.ts — the shapes the Flows surface shares with the server.
//
// These mirror server/astrodeck/flows/models.py. Two of them carry a trap that
// has already cost this project a debugging session, so they are spelled out
// here rather than inferred at each call site.
import type { FlowCompileResult, FlowUnmapped } from "../../lib/flowsApi";

export type FlowNodeType =
  | "dusk" | "target" | "safety" | "cloudwatch"
  | "dome" | "flatpanel"
  | "slew" | "autofocus" | "guide" | "capture" | "cycle" | "duskflats" | "calib"
  | "pool" | "condition"
  | "notify" | "refocus" | "holdresume" | "parkclose" | "abort" | "report";

export type PortKind = "flow" | "event";
export type FlowNodeStatus = "idle" | "busy" | "ok" | "warn" | "bad";
export type FlowLogTone = "info" | "good" | "warn" | "bad";

/** Mirrors the server's FlowNode.
 *
 *  `params` is deliberately loose. models.py is strict about EDGES and
 *  permissive about PARAMS, because a node vocabulary that gains a field must
 *  not make every saved flow unopenable. */
export interface FlowNodeRec {
  id: string;
  type: FlowNodeType;
  x: number;
  y: number;
  params: Record<string, string | number>;
}

/** Mirrors the server's FlowEdge.
 *
 *  THE KEY IS `from`, NOT `from_`. FlowEdge sets `alias="from"` and FastAPI
 *  serialises responses by alias — the server's own get_flow docstring warns
 *  that hand-dumping the model would emit `from_` and every wire in the canvas
 *  would vanish with no error anywhere. Anything constructing an edge on this
 *  side has to use the same key. */
export interface FlowEdgeRec {
  id: string;
  from: string;
  fromPort: string;
  to: string;
  toPort: string;
}

/** Mirrors the server's `FlowGraph.settings`: flow-level settings, FLAT
 *  SCALARS keyed as in `FLOW_SETTINGS` (null reads as the default). */
export type FlowSettingsRec = Record<string, string | number | boolean | null>;

export interface FlowGraphRec {
  nodes: FlowNodeRec[];
  edges: FlowEdgeRec[];
  /** OPTIONAL, and read only through `flowSetting`. Every graph saved before
   *  the mosaic slice has none, an older server does not send it, and every
   *  graph built on this side as `{ nodes, edges }` omits it; a missing key
   *  means the setting's default. Carry it through edits by spreading the
   *  graph, or a save drops what the operator chose. */
  settings?: FlowSettingsRec;
}

/** Mirrors models.py `FLOW_SETTINGS`: each flow-level setting's missing-key
 *  default and its choices. `__tests__/flowSettingsParity.test.ts` PARSES
 *  models.py and compares, because a default that drifts here would have the
 *  editor show one behaviour while the engine runs the other.
 *
 *  `whenWaiting` (spec 1.6, Revision 2 ruling 1) is what the run does while
 *  every live panel of a mosaic cannot be shot, for every mosaic in the flow. */
export const FLOW_SETTINGS = {
  whenWaiting: {
    default: "Shoot later targets, then come back",
    options: ["Shoot later targets, then come back", "Wait for the mosaic"],
  },
} as const satisfies Record<string, { default: string; options: readonly string[] }>;

export type FlowSettingKey = keyof typeof FLOW_SETTINGS;

/** A flow's value for one setting. Mirrors models.py `resolve_setting`: the
 *  stored value when it is one of the declared options, otherwise the
 *  default — so a missing key, null, or a value this build does not know all
 *  read as the default, as the engine will run them. */
export function flowSetting(
  settings: FlowSettingsRec | undefined, key: FlowSettingKey,
): string {
  const spec = FLOW_SETTINGS[key];
  const v = settings?.[key];
  return typeof v === "string" && (spec.options as readonly string[]).includes(v)
    ? v : spec.default;
}

/** One thing the server's read changed in a stored flow (server
 *  `MigrationNote`). `key` names what moved (`rotation` for FLOW_SCHEMA 3's
 *  23.4 rewrite, #150); `note` is the sentence to show the operator.
 *
 *  A SAVE'S answer carries one too, keyed `counts` (`COUNTS_MIGRATION_KEY`):
 *  the save switched every TARGET and POOL to counting accepted subs only
 *  (spec Revision 2, ruling 2). The editor says that in its own words
 *  (`flowsSlice` `saveAnswerLines`), because the ruling fixes what the UI says. */
export interface FlowMigrationNote {
  key: string;
  note: string;
}

/** The `migrated` key a save's answer uses for the counts switch. The spec
 *  writes that answer as `migrated: ["counts"]`; the record model carries
 *  `MigrationNote` objects, so the editor reads either spelling. */
export const COUNTS_MIGRATION_KEY = "counts";

/** One block a SAVE re-anchored (server `_persist_flow` through
 *  `framing.reframe_carry`; spec 3.3, Revision 2 ruling 3): its framing moved
 *  too far for its counts to carry, so its step ids, and its counts, start
 *  again. The subs already banked stay on disk under the old ids.
 *
 *  `max_move_deg` and `threshold_deg` are in degrees: the largest distance any
 *  panel corner moved, and the move this grid carries counts under. Either is
 *  null when no move was measured (a grid or angle change re-anchors outright).
 *  `reason` is `reframe_carry`'s own word for why (`move`, `grid`, `angle`,
 *  `identity`) when the server sends it; the spec's list names only the three
 *  fields before it, so it is optional. */
export interface FlowReanchored {
  node_id: string;
  max_move_deg: number | null;
  threshold_deg: number | null;
  reason?: string;
}

export interface FlowRecordRec {
  id: string;
  name: string;
  folder: string;
  tagline: string;
  graph: FlowGraphRec;
  created_ts: number;
  updated_ts: number;
  last_run: number | null;
  last_result: "" | "ok" | "warn" | "bad";
  readonly: boolean;
  /** What THIS read rewrote, for `flowsOpen` to put on the flow log. Never
   *  persisted: the server strips it on every write, so it is on each GET until
   *  the file is next written (a save, or a run's `touch_run`, which logs it),
   *  and gone from the written record. Optional because an older server does
   *  not send it. A save's answer uses it for the counts switch only. */
  migrated?: FlowMigrationNote[];
  /** On a SAVE'S answer only: every block that save re-anchored, for
   *  `flowsSave` to put on the flow log. Absent from a read, and from an older
   *  server's answer. */
  reanchored?: FlowReanchored[];
}

/** A wire being dragged.
 *
 *  `to` is in WORLD coordinates, not screen: the pending wire is drawn with the
 *  same bezier as a real edge, and a pending path in screen space would bend
 *  differently from the one it becomes on drop. */
export interface PendingWire {
  from: string;
  fromPort: string;
  kind: PortKind;
  to: { x: number; y: number };
}

export interface FlowLogLine {
  id: number;
  ts: number;
  msg: string;
  tone: FlowLogTone;
}

export type FlowScreen = "library" | "editor";
export type FlowPhoneTab = "flow" | "canvas" | "monitor";
export type TonightTab = "timeline" | "story" | "plan" | "campaign";

/** Drives the `[data-flows-run]` marker the parity harness looks for.
 *
 *  `holding` is a real state, not a label: capture 07 has to be taken INSIDE
 *  the cloud hold, and a harness that cannot see the difference between held
 *  and running would photograph the wrong moment. */
export type FlowRunPhase = "idle" | "running" | "holding" | "stopping";

export interface FlowSelection {
  kind: "node" | "edge";
  id: string;
}

export interface FlowRunState {
  phase: FlowRunPhase;
  /** Seconds remaining, or null when the server has not said.
   *
   *  NEVER a client-side countdown from an assumed total. An ETA the client
   *  invented looks identical to one the rig computed, and the operator cannot
   *  tell which they are being shown. */
  etaS: number | null;
  curStage: string;
  frames: number;
  frameGoal: number | null;
  /** What the operator clicked past on the 409, kept so the run banner can keep
   *  saying which parts of their graph are not being honoured. */
  acceptedUnmapped: FlowUnmapped[];
}

export interface FlowCalHealth {
  rows: Record<string, unknown>[];
  planned: boolean;
  counts_masters_only: boolean;
  assumed: { offset: number; temp_c: number | null };
}

export type { FlowCompileResult, FlowUnmapped };

// ----------------------------------------------------- losses, per NODE
//
// `to_plan.losses()` keys a dropped node setting as `nodes.<type>` — one entry
// per node TYPE, not per node id, because the compiler drops a type's params
// wholesale. The inspector already lists them under NOT HONOURED BY A RUN and
// `flowRunControls` puts them behind a confirm at RUN time, and both of those
// are places you have to already be looking. The canvas is where the operator
// IS looking, and a node whose settings will be ignored should say so there —
// see `FlowNodeCard`, which draws the mark this decides.
//
// Deliberately excludes `note`: a note says the thing DOES happen, by some
// other part of the engine (the cloud hold releases itself, the scheduler
// advances the pool). Marking those would train the mark to mean nothing.

/** The worst loss level attached to a node type, or null when it survives.
 *
 *  A plain function over the array rather than a memoised map, so a caller can
 *  use it inside a zustand selector and get a PRIMITIVE back — which is what
 *  keeps `FlowNodeCard`'s subscription exact under Object.is (see its header:
 *  a selector returning a fresh object re-renders every card on every tick).
 *  Ten entries and one string compare each; the map would cost more to keep.
 */
export function nodeLossLevel(
  unmapped: readonly FlowUnmapped[] | undefined,
  nodeType: string,
): "warn" | "danger" | null {
  if (!unmapped || !unmapped.length) return null;
  let worst: "warn" | "danger" | null = null;
  const whole = `nodes.${nodeType}`;
  const param = `${whole}.`;
  for (const u of unmapped) {
    // `nodes.<type>` is the whole node's params; `nodes.<type>.<param>` is one
    // dropped setting on a node the compiler otherwise reads (to_plan's
    // INERT_PARAMS). Both belong on the same card.
    if (u.key !== whole && !u.key.startsWith(param)) continue;
    if (u.level === "danger") return "danger";
    if (u.level === "warn") worst = "warn";
  }
  return worst;
}

/** Every dropped-setting sentence for a node type, joined — the mark's tooltip.
 *
 *  A bare glyph says "something is wrong here" and leaves the operator to go
 *  find out where; the whole point of `to_plan`'s wording is that it already
 *  says WHICH setting and what happens instead. Returns "" for a node that
 *  survives, so callers get a primitive either way. */
export function nodeLossDetail(
  unmapped: readonly FlowUnmapped[] | undefined,
  nodeType: string,
): string {
  if (!unmapped || !unmapped.length) return "";
  const hits: string[] = [];
  for (const u of unmapped) {
    const k = u.key;
    if ((k === `nodes.${nodeType}` || k.startsWith(`nodes.${nodeType}.`))
        && u.level !== "note") hits.push(u.detail);
  }
  return hits.join(" · ");
}
