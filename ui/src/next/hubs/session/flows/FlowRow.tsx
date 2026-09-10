// FlowRow.tsx - one row of MY FLOWS: dot, name, meta line, verb
// (plan section D.1; proto `29-my-flows.html`, screenshot 29).
//
// Store-free and fetch-free, like a primitive: everything it draws arrives as a
// prop. That is what lets the screen decide ONCE whether a run is live, which
// flow is the campaign and what the operator is allowed to do, instead of every
// row asking the store the same three questions.
//
// TWO CONTROLS, NOT ONE - AND WHY. The design gives each row a single button
// whose word depends on the flow's KIND (quick / pool / campaign / calibration /
// template). The server does not serve a kind: `FlowRecord.card()`
// (`server/astrodeck/flows/models.py:213-219`) is {id, name, folder, tagline,
// readonly, stages, wires, last_run, last_result, updated_ts} and nothing else,
// and inferring "this is a calibration flow" from its tagline would be a guess
// printed as a fact. So the verb carries what the rig ACTUALLY reports - is this
// flow the run that is live, does it own a dormant session that still owes
// frames, or neither - and OPEN moves onto the row body, where it is the whole
// name-and-meta block. Nothing is lost: every flow keeps both verbs, and neither
// of them claims to know something the server never said.

import type { JSX } from "react";

import type { FlowCard } from "../../../../lib/flowsApi";
import { ActionButton, Pill } from "../../../ui";
import { lockedAttrs, lockedClass } from "../../../ui/honest";

export type FlowVerb = "run" | "resume" | "live";

/** Where the row body goes. The canvas is the tablet and desktop workspace; the
 *  stage list is the phone's whole way inside a flow (wave R7 section 4). The
 *  row needs to know which, because "Open M31 on the flows canvas" read out by a
 *  screen reader on a phone would name a screen that phone will never show. */
export type FlowOpenTarget = "canvas" | "stages";

/** The word the library puts on a flow that was saved a moment ago. A word and
 *  not only the highlight ring: a ring is a colour, and a colour is not a
 *  readable claim (README, and `FlowLibrary`'s own `highlightId` rule). */
export const JUST_SAVED = "JUST SAVED";

/** The word AND the glyph, so the state is never carried by colour. */
const VERB_LABEL: Record<FlowVerb, string> = {
  run: "RUN",
  resume: "RESUME",
  live: "LIVE",
};

export interface FlowRowProps {
  card: FlowCard;
  /** The meta line under the name - the card's own `{stages} stages · {wires}
   *  wires · {last run}` plus its status word, or the campaign's live ledger
   *  line when this flow is the campaign. */
  meta: string;
  dotColor: string;
  verb: FlowVerb;
  busy?: boolean;
  /** True for the flow the library was just told about (`flows.ui.highlightId`),
   *  which is how a wizard or quick-flow save says which row is the new one. */
  highlight?: boolean;
  /** What the row body opens. Drives the accessible name, so the promise the
   *  row makes is the screen the press actually produces. */
  openTarget?: FlowOpenTarget;
  /** Why RUN / RESUME cannot act, or null. From `runBlockedReason`, never
   *  hand-written. LIVE is a navigation and is never locked. */
  runReason: string | null;
  /** Why OPEN cannot act, or null. Null at every breakpoint since the cutover:
   *  the phone opens the stage list instead of being told the canvas is
   *  elsewhere. Kept as a prop because a flow whose id the router cannot reach
   *  still needs a sentence rather than a dead press. */
  openReason: string | null;
  onRun: () => void;
  onResume: () => void;
  onLive: () => void;
  onOpen: () => void;
  onExplain: (reason: string) => void;
}

export function FlowRow({
  card, meta, dotColor, verb, busy = false, highlight = false,
  openTarget = "canvas", runReason, openReason,
  onRun, onResume, onLive, onOpen, onExplain,
}: FlowRowProps): JSX.Element {
  const live = verb === "live";
  const act = live ? onLive : verb === "resume" ? onResume : onRun;
  // LIVE goes to the run, not at it: there is nothing to start, and the only
  // useful thing the row can do is show what is already happening.
  const reason = live ? null : runReason;

  return (
    <div
      data-testid={`flow-row-${card.id}`}
      style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "10px 14px", minHeight: 60,
        borderBottom: "1px solid rgba(120,140,200,.1)",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 7, height: 7, borderRadius: 2, background: dotColor,
          boxShadow: `0 0 8px ${dotColor}`, flexShrink: 0,
        }}
      />
      {/* The row body IS the OPEN control. On a phone it explains instead of
          acting; the reason lands in `title` and in the screen's own line, so
          it is readable without a hover a touch screen cannot perform. */}
      <button
        type="button"
        data-testid={`flow-open-${card.id}`}
        className={lockedClass(openReason)}
        aria-label={openTarget === "stages"
          ? `Open ${card.name}'s stage list`
          : `Open ${card.name} on the flows canvas`}
        onClick={() => { if (openReason) { onExplain(openReason); return; } onOpen(); }}
        style={{
          flex: 1, minWidth: 0, display: "flex", flexDirection: "column",
          gap: 2, alignItems: "flex-start", textAlign: "left",
          background: "none", border: "none", padding: 0, cursor: "pointer",
          minHeight: 44,
          justifyContent: "center",
        }}
        {...lockedAttrs(openReason)}
      >
        <span
          style={{
            display: "flex", alignItems: "center", gap: 8,
            minWidth: 0, maxWidth: "100%",
          }}
        >
          <span
            className="nx-display"
            style={{
              fontSize: 11.5, letterSpacing: ".1em",
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
              minWidth: 0,
            }}
          >
            {card.name}
          </span>
          {/* A `Pill` with no `onClick` renders a span, so this is legal inside
              the row-body button and is not a second tab stop. */}
          {highlight && (
            <span data-testid={`flow-new-${card.id}`} style={{ flex: "none" }}>
              <Pill tone="good">{JUST_SAVED}</Pill>
            </span>
          )}
        </span>
        <span
          className="nx-mono"
          data-testid={`flow-meta-${card.id}`}
          style={{
            fontSize: 9.5, color: "var(--text-2)",
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            maxWidth: "100%",
          }}
        >
          {meta}
        </span>
      </button>

      <ActionButton
        kind={live ? "purple" : "secondary"}
        size="md"
        glyph={<span aria-hidden="true">{live ? "●" : "▶"}</span>}
        busy={busy}
        lockedReason={reason}
        onExplain={onExplain}
        onPress={act}
        ariaLabel={`${VERB_LABEL[verb]} ${card.name}`}
        data-testid={`flow-verb-${card.id}`}
      >
        {VERB_LABEL[verb]}
      </ActionButton>
    </div>
  );
}

export default FlowRow;
