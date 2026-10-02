// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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
// plus `unreadable` on a row for a file this build cannot open (#153,
// `FlowStore._row`), and inferring "this is a calibration flow" from its
// tagline would be a guess printed as a fact. So the verb carries what the rig
// ACTUALLY reports - is this flow the run that is live, does it own a dormant
// session that still owes frames, or neither - and OPEN moves onto the row
// body, where it is the whole name-and-meta block. Nothing is lost: every flow
// keeps both verbs, and neither of them claims to know something the server
// never said.
//
// AN UNREADABLE ROW SAYS WHY ONCE (#153, mosaic S1-10 carry-over 6). Its meta
// line is the reason, and that line is the only place the row says it. The S0
// row also put it in the body's `title`, toasted it on every press, and drew a
// locked RUN carrying it again: four copies of one sentence, and a control that
// existed only to be refused. So the body is `aria-disabled` and described by
// the meta line - a screen reader hears the reason once, on focus, with no
// hover or press needed - a press does nothing, and there is no RUN or RESUME,
// because every route but the listing answers 404 for the id and nothing on
// this screen can run it.

import { useId, type JSX } from "react";

import { unreadableReason, type FlowCard } from "../../../../lib/flowsApi";
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
   *  hand-written. LIVE is a navigation and is never locked. An unreadable
   *  card has no RUN or RESUME to carry it. */
  runReason: string | null;
  /** Why OPEN cannot act, or null. Null at every breakpoint since the cutover:
   *  the phone opens the stage list instead of being told the canvas is
   *  elsewhere. Kept as a prop for a flow the router cannot reach for some
   *  reason the row would not otherwise show. NOT for an unreadable card
   *  (#153): its reason is already the meta line, and the row describes its
   *  body with that line itself, so passing it here too says it twice. */
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
  // Read off the card rather than handed in, so no caller can draw a row for
  // an unreadable file that offers to open or run it. The sentence itself is
  // the caller's `meta`, which is where the screen puts it.
  const unreadable = unreadableReason(card) !== null;
  // `useId`, not `card.id`: a flow id is a file stem, and an IDREF with a space
  // in it would point at two ids that do not exist.
  const metaId = useId();
  // RUN and RESUME start the engine on this flow, which nothing can do with a
  // file this build cannot read. LIVE is a navigation to SESSION / NOW, and the
  // screen never marks an unreadable row live (its own guard), so in practice
  // an unreadable row has no verb at all.
  const showVerb = !unreadable || live;

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
      {/* The row body IS the OPEN control. An `openReason` locks it the honest
          way - `title`, and a toast on press. An unreadable card locks it
          without either: the meta line inside it already says why, so the body
          is described by that line and a press does nothing. It stays a
          focusable button so the reason is still reachable from a keyboard.
          Not dimmed (`nx-locked`) either: half opacity would fall on the one
          line on the row worth reading. */}
      <button
        type="button"
        data-testid={`flow-open-${card.id}`}
        className={lockedClass(openReason)}
        aria-label={openTarget === "stages"
          ? `Open ${card.name}'s stage list`
          : `Open ${card.name} on the flows canvas`}
        aria-disabled={unreadable ? true : undefined}
        aria-describedby={unreadable ? metaId : undefined}
        onClick={() => {
          if (openReason) { onExplain(openReason); return; }
          if (unreadable) return;
          onOpen();
        }}
        style={{
          flex: 1, minWidth: 0, display: "flex", flexDirection: "column",
          gap: 2, alignItems: "flex-start", textAlign: "left",
          background: "none", border: "none", padding: 0,
          cursor: unreadable ? "default" : "pointer",
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
          id={metaId}
          className="nx-mono"
          data-testid={`flow-meta-${card.id}`}
          style={{
            fontSize: 9.5, color: "var(--text-2)",
            // An unreadable card's meta line is its reason, and since S1-10
            // the ONLY copy of it on the row: no title, no toast. Ellipsised, it
            // does not fit a phone. At 390 px this line gets 309 px (the body's
            // two 16 px gutters, the list's 1 px borders, the row's 14 px
            // padding, the 7 px dot and its 12 px gap): 54 characters of IBM
            // Plex Mono at 9.5 px, 600/1000 em = 5.7 px each. "saved by a newer
            // AstroDeck (schema 4); update to open it" is 56 before any folder
            // prefix, and the server's "unreadable: ..." reasons run to 172. So
            // that one line wraps, breaking inside a dotted field path if it
            // must; every other meta line keeps its single ellipsised row.
            whiteSpace: unreadable ? "normal" : "nowrap",
            overflowWrap: unreadable ? "anywhere" : undefined,
            overflow: "hidden", textOverflow: "ellipsis",
            maxWidth: "100%",
          }}
        >
          {meta}
        </span>
      </button>

      {showVerb && (
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
      )}
    </div>
  );
}

export default FlowRow;
