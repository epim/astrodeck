// issues.ts - the compile result's `unmapped[]` split into the statements it
// actually makes, and the one vocabulary every surface that renders one uses.
// Pure.
//
// COPIED from `components/flows/FlowInspector.tsx`'s `RIG_ADVISORY` and its
// three `useMemo` partitions (wave R7 section 2.1's "embedded helpers" rule:
// the helper moves into the rebuilding task's own module, the legacy file is
// neither edited nor imported, and `#/classic` keeps its copy). The node-level
// half (`nodeMarkLevel` / `nodeMarkDetail`) replaces
// `components/flows/flowsTypes`'s `nodeLossLevel` / `nodeLossDetail`, which are
// level-BLIND: they drop `note` entirely, so a note either painted the amber
// mark (while the engine still called these entries `warn`) or vanished from
// the canvas altogether once it stopped. Neither is what a note means.
//
// WHY THE CANVAS IMPORTS THIS FILE AND NOT THE AREA BARREL. `../inspector`
// re-exports `inspector.css`, so a barrel import would drag the inspector's
// stylesheet into the canvas chunk (the same trap `hubs/sky/sheets/quick.tsx`
// documents for `create/quickPayload`). This module imports no css and no
// component, so the deep import costs nothing and the WORDS cannot drift
// between the card badge, the phone stage row and the inspector panel.
//
// THE LEVELS, IN THE ENGINE'S OWN TERMS:
//   danger  a loss that blocks - `to_plan.blocking_reasons` returns it and
//           `/run` refuses until the operator accepts it.
//   warn    a loss - something drawn on the canvas will not happen.
//   note    NOT a quieter warn. The card's numbers are shown for reference and
//           the run takes the real ones from the rig's own settings. A clean
//           flow can carry ten of these, and painting them amber reported a
//           working rig as a failing build (2026-09-11, on the box).

import { NODE_DEFS } from "../../../../../components/flows/nodeDefs";
import type { FlowUnmapped } from "../../../../../lib/flowsApi";
import type { Tone } from "../../../../ui/types";

/** `note`-level entries that are NOT "answered another way".
 *
 *  The note level means one thing to `to_plan.losses` - do not block the run -
 *  and the FROM THE RIG heading reads a second meaning into it: the values on
 *  the card are for reference and the run takes the real ones from the rig.
 *
 *  `cooling.setpoint_c` is not that. It says the run has no target temperature
 *  at all, on a camera that could hold one - the state that put 19 lights on
 *  disk at +23 C against a -10 C library on 2026-08-22. Under FROM THE RIG that
 *  sentence contradicts its own heading (there is no rig value carrying it,
 *  which is the entire news), which is exactly the failure the heading was split
 *  out to fix. So it gets its own list: still non-blocking, still note weight,
 *  but a statement about THIS RIG rather than about the canvas. */
export const RIG_ADVISORY: ReadonlySet<string> = new Set(["cooling.setpoint_c"]);

export type UnmappedLevel = "warn" | "danger" | "note";

export interface UnmappedSplit {
  /** NOT HONOURED BY A RUN - something drawn on the canvas will not happen. */
  losses: FlowUnmapped[];
  /** BEFORE YOU RUN - rig state the canvas cannot show, at note weight. */
  advisories: FlowUnmapped[];
  /** FROM THE RIG - the card's values are for reference; the run uses the rig's. */
  notes: FlowUnmapped[];
}

/** Three lists off one field, because they make three different statements.
 *  A `note` is not a quieter `warn`, and one of the notes is not about the
 *  canvas at all. */
export function splitUnmapped(unmapped: readonly FlowUnmapped[]): UnmappedSplit {
  const losses: FlowUnmapped[] = [];
  const advisories: FlowUnmapped[] = [];
  const notes: FlowUnmapped[] = [];
  for (const u of unmapped) {
    if (u.level !== "note") losses.push(u);
    else if (RIG_ADVISORY.has(u.key)) advisories.push(u);
    else notes.push(u);
  }
  return { losses, advisories, notes };
}

/** The word a compile finding carries beside its tone.
 *
 *  Colour is never the only channel (ARCHITECTURE section 6): under `:root.night`
 *  every token collapses toward one hue, and a red line and an amber line become
 *  the same line. `null` for a note - it is not a finding against the graph. */
export function levelWord(level: string): string | null {
  if (level === "danger") return "DANGER";
  if (level === "warn") return "WARNING";
  return null;
}

// ------------------------------------------------------------- the vocabulary
//
// THREE STATES, THREE WORDS, ONE SOURCE. The badge on a canvas card, the badge
// on a phone stage row and the inspector's panel heading all read from here, so
// a stage that says FROM THE RIG on the canvas cannot say something else in the
// list the operator opens next.
//
// `PARTLY HONOURED` stays the WARN word rather than moving to the note, even
// though the brief offered it: a warn badge already carries it on this branch,
// and one word meaning two different states on the same card is the defect this
// whole change exists to remove.

/** A blocking loss: nothing this stage declares reaches the run. */
export const MARK_LOST = "NOT HONOURED";
/** A loss: some of what this stage declares reaches the run and some does not. */
export const MARK_PARTIAL = "PARTLY HONOURED";
/** A note: the card's values are for reference, the run uses the rig's own. */
export const MARK_RIG = "FROM THE RIG";

export function markWord(level: UnmappedLevel): string {
  if (level === "danger") return MARK_LOST;
  return level === "warn" ? MARK_PARTIAL : MARK_RIG;
}

/** The design tone per level. `dim` for a note is the whole point: a note is
 *  secondary information (the design's own weight for it - see `06-flow-card`),
 *  not a finding, and amber on it reports a clean flow as a failing build. */
export function markTone(level: UnmappedLevel): Tone {
  if (level === "danger") return "bad";
  return level === "warn" ? "warn" : "dim";
}

/** Whether this level is a LOSS - the amber/coral half of the vocabulary, the
 *  one that earns the `!` glyph and the card outline. */
export function isLoss(level: UnmappedLevel | null): boolean {
  return level === "warn" || level === "danger";
}

/** How many things drawn on this graph will not happen - `unmapped`'s two loss
 *  levels plus the structural refusals, which the inspector already files under
 *  the same heading.
 *
 *  A NUMBER, so the toolbar can subscribe to it exactly. */
export function lossCount(
  unmapped: readonly FlowUnmapped[] | undefined,
  structural: readonly string[] | undefined,
): number {
  let n = structural ? structural.length : 0;
  for (const u of unmapped ?? []) if (u.level !== "note") n++;
  return n;
}

/** The worst loss level on the graph, or null when there is none. A structural
 *  refusal is a `danger`: a wire that does not resolve is not advice. */
export function worstLoss(
  unmapped: readonly FlowUnmapped[] | undefined,
  structural: readonly string[] | undefined,
): "warn" | "danger" | null {
  if (structural && structural.length) return "danger";
  let worst: "warn" | "danger" | null = null;
  for (const u of unmapped ?? []) {
    if (u.level === "danger") return "danger";
    if (u.level === "warn") worst = "warn";
  }
  return worst;
}

// --------------------------------------------------------------- per NODE
//
// `to_plan` keys a dropped or rig-owned node setting as `nodes.<type>` - one
// entry per node TYPE, not per node id, because the compiler treats a type's
// params wholesale - and `nodes.<type>.<param>` for one setting on a node it
// otherwise reads (`INERT_PARAMS`). Both belong on the same card.

const RANK: Record<UnmappedLevel, number> = { note: 1, warn: 2, danger: 3 };

/** Every entry attached to a node type, in the order the server sent them.
 *
 *  Not exported as the card's subscription: it builds an array, and a selector
 *  that returns a fresh array re-renders every card on every status tick. The
 *  two functions below return primitives for exactly that reason. */
function entriesFor(
  unmapped: readonly FlowUnmapped[] | undefined,
  nodeType: string,
): FlowUnmapped[] {
  if (!unmapped || !unmapped.length) return [];
  const whole = `nodes.${nodeType}`;
  const param = `${whole}.`;
  return unmapped.filter((u) => u.key === whole || u.key.startsWith(param));
}

/** The worst level attached to a node type, or null when nothing is.
 *
 *  A plain function over the array rather than a memoised map, so a caller can
 *  use it inside a zustand selector and get a PRIMITIVE back - which is what
 *  keeps `FlowNodeCard`'s subscription exact under Object.is. Ten entries and
 *  one string compare each; the map would cost more to keep. */
export function nodeMarkLevel(
  unmapped: readonly FlowUnmapped[] | undefined,
  nodeType: string,
): UnmappedLevel | null {
  let worst: UnmappedLevel | null = null;
  for (const u of entriesFor(unmapped, nodeType)) {
    const lvl = u.level as UnmappedLevel;
    if (!RANK[lvl]) continue;
    if (!worst || RANK[lvl] > RANK[worst]) worst = lvl;
  }
  return worst;
}

/** One entry as a single line, for a tooltip or an accessible name.
 *
 *  Prefers the structured fields, because they are the short true sentence -
 *  "carried: threshold 3.2 · from the rig: settle 1.5 s - Rig > Guider" - and
 *  falls back to the server's own `detail` on an engine that sends only that. */
export function markSentence(u: FlowUnmapped): string {
  const parts: string[] = [];
  if (u.carried && u.carried.length) parts.push(`carried: ${u.carried.join(", ")}`);
  if (u.ignored && u.ignored.length) {
    parts.push(`from the rig: ${u.ignored.join(", ")}${u.source ? ` - ${u.source}` : ""}`);
  }
  return parts.length ? parts.join(" · ") : u.detail;
}

/** Every sentence for a node type, joined - the mark's tooltip.
 *
 *  A bare glyph says "something is here" and leaves the operator to go find out
 *  what; the wording already says which settings and where the run gets them.
 *  Returns "" for a stage with nothing attached, so callers get a primitive
 *  either way. */
export function nodeMarkDetail(
  unmapped: readonly FlowUnmapped[] | undefined,
  nodeType: string,
): string {
  return entriesFor(unmapped, nodeType).map(markSentence).join(" · ");
}

// ------------------------------------------------------------ the note panel

/** One row of the FROM THE RIG panel. */
export interface NoteRow {
  key: string;
  /** The stage this is about, in the vocabulary's own label, or null when the
   *  entry names a rule rather than a stage (`instructions[...]`). */
  name: string | null;
  carried: string[];
  ignored: string[];
  source: string | null;
  /** The server's own sentence. Rendered only when there is no structured
   *  content - an older engine sends nothing else, and an empty row would be
   *  worse than a long one. */
  detail: string;
  /** False on an older engine, and the flag the row's shape turns on. */
  structured: boolean;
}

/** The stage an `unmapped` key is about, in the label the canvas prints.
 *
 *  An unknown type still gets its own word rather than nothing: the client's
 *  vocabulary and the server's can drift, and a row headed by a blank is a row
 *  the reader cannot connect to anything on the canvas. */
export function stageNameFor(key: string): string | null {
  const m = /^nodes\.([^.]+)/.exec(key);
  if (!m) return null;
  // Indexed as a plain string on purpose: the key comes off the wire, so the
  // type it names is whatever the SERVER's vocabulary holds, not whatever this
  // build's union does.
  const defs = NODE_DEFS as unknown as Record<string, { label: string } | undefined>;
  return defs[m[1]]?.label ?? m[1].toUpperCase();
}

/** The panel's rows, one per note, in the order the server sent them. */
export function noteRows(notes: readonly FlowUnmapped[]): NoteRow[] {
  return notes.map((u) => {
    const carried = u.carried ?? [];
    const ignored = u.ignored ?? [];
    return {
      key: u.key,
      name: stageNameFor(u.key),
      carried,
      ignored,
      source: u.source ?? null,
      detail: u.detail,
      structured: carried.length > 0 || ignored.length > 0,
    };
  });
}

/** What the panel is, in one line. It states the RULE (these are for reference,
 *  the rig owns the real values), which is the one thing neither the rows nor
 *  the badges can say. */
export const NOTES_LEAD =
  "These card values are shown for reference - the run takes them from the "
  + "rig's own settings.";

/** The two tags a structured row prints. Exported so a test names the string
 *  rather than a regex nobody can trace back to a source. */
export const CARRIED_TAG = "carried:";
export const FROM_RIG_TAG = "from the rig:";

// ------------------------------------------------- what the rig actually uses
//
// The card's summary line is drawn from the NODE's stored params, and two of
// those params name a PROVIDER the rig overrides: the GUIDE node ships
// `provider: "PHD2"` and the SLEW node ships `solver: "ASTAP"`, so a rig that
// guides natively has read PHD2 off its own canvas since the day the vocabulary
// was written. The note rows say the run takes these from the rig; these two
// functions say WHAT the rig's answer is, so the reader does not have to go
// find it.

/** Which resolved capability answers for a stage type. Only the two the card
 *  actually prints a provider for - inventing a third would put an unrelated
 *  fact under a summary that never claimed it. */
const RIG_CAP_BY_NODE: Record<string, "guide" | "solve"> = {
  guide: "guide",
  slew: "solve",
};

/** The label prefix, so the reader knows which value is which. */
export const RIG_VALUE_PREFIX = "rig: ";

/** What the rig will really use for this stage, or null when it has not said.
 *
 *  Reads `status.providers[cap].label` - the server's own per-capability
 *  resolution (`providers.py resolve_all`), which is the only place the WINNING
 *  answer exists: `config.providers.guide` is routinely the literal "auto", and
 *  an active profile beats it anyway (`lib/effective.ts`'s whole reason to
 *  exist). Returns a STRING so a card can subscribe to it exactly.
 *
 *  Null is the honest answer for `unavailable` and for a rig that has not
 *  polled yet: the card's own value stays, and nothing is invented. */
export function rigValueFor(nodeType: string, status: unknown): string | null {
  const cap = RIG_CAP_BY_NODE[nodeType];
  if (!cap) return null;
  const providers = (status as { providers?: Record<string, unknown> } | null)?.providers;
  const choice = providers?.[cap] as { kind?: string; label?: string } | undefined;
  if (!choice || choice.kind === "unavailable") return null;
  const label = typeof choice.label === "string" ? choice.label.trim() : "";
  return label ? label : null;
}
