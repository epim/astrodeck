// FlowLibraryCard.tsx — one flow card, plus the dashed `+ NEW FLOW` card.
//
// A card is built from the FlowCard PROJECTION (`GET /api/flows`), never from a
// graph. flowsApi's own header says why: thirty flows at up to four hundred
// nodes each is megabytes of wires shipped to draw a card wall. Everything on
// the card therefore has to come out of {name, tagline, stages, wires,
// last_run, last_result} — there is no node list here to count.
//
// The three formatters below are exported because they are the only real logic
// on this screen and they are the places a card would otherwise invent a value:
// the meta line's FORMAT is designed but not served (§E.1), and `last_result`
// has two more values than the design has copy (§G-26).
import { memo } from "react";
import type { FlowCard } from "../../lib/flowsApi";
import type { LedState } from "../../types";
import { Led } from "../ui";

/** `last run 2026-08-09 · 02:37` / `never run`.
 *
 *  The server sends a unix float; both strings in the design are formatted, and
 *  §E.1 flags this line only so nobody hard-codes the fixture text. LOCAL time,
 *  deliberately: an observer reads their own night off this, not UTC. */
export function formatLastRun(lastRun: number | null): string {
  if (lastRun == null || lastRun <= 0) return "never run";
  const d = new Date(lastRun * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `last run ${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
       + ` · ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** `14 stages · 14 wires · last run 2026-08-09 · 02:37` */
export function cardMeta(card: FlowCard): string {
  return `${card.stages} stages · ${card.wires} wires · ${formatLastRun(card.last_run)}`;
}

export interface CardStatus {
  led: LedState;
  /** The visible word. Status is never colour alone — the LED is shape-coded
   *  (dash / circle / SQUARE) and this word says the same thing in text. */
  text: string;
  cls: string;
}

/** `last_result` → LED state + word.
 *
 *  ⚠ The design defines exactly TWO of the four server values: `"ok"` is
 *  `COMPLETED CLEAN` and `""` is `NEVER RUN`. `"warn"` and `"bad"` have no
 *  designed string and no designed LED (§G-26, still open). Rather than invent
 *  copy, a warn/bad card shows the server's OWN vocabulary verbatim next to a
 *  truthful LED — a flow that ended badly must not be able to read as one that
 *  completed clean, and it must not read as one that never ran either. */
export function cardStatus(lastResult: string): CardStatus {
  switch (lastResult) {
    case "ok":   return { led: "on",   text: "COMPLETED CLEAN", cls: "text-good" };
    case "warn": return { led: "warn", text: "WARN",            cls: "text-warn" };
    case "bad":  return { led: "bad",  text: "BAD",             cls: "text-bad" };
    default:     return { led: "off",  text: "NEVER RUN",       cls: "text-faint" };
  }
}

// Chrome lifted from the prototype (dc.html:106): 16px pad, 150px floor,
// radius 16, backdrop-blur 14, and an inset 1px white-5% top highlight that has
// no token. Both shadows are Tailwind utilities rather than an inline `style`
// on purpose: an inline box-shadow cannot be beaten by a `hover:` utility, so
// the hover glow would silently never appear.
const CARD_SHADOW = "shadow-[inset_0_1px_1px_rgba(255,255,255,0.05)]";
const CARD_HOVER =
  "hover:border-accent hover:shadow-[0_0_14px_var(--glow),inset_0_1px_1px_rgba(255,255,255,0.05)]";

export interface FlowLibraryCardProps {
  card: FlowCard;
  onOpen: (id: string) => void;
}

/** `React.memo`'d and store-free: the card takes everything it draws as props,
 *  so re-rendering the library's toolbar (a keystroke in the filter box) does
 *  not re-render every card body. */
export const FlowLibraryCard = memo(function FlowLibraryCard(
  { card, onOpen }: FlowLibraryCardProps,
) {
  const status = cardStatus(card.last_result);
  return (
    <button
      type="button"
      // The harness resolves `("open-flow","example-m16")` through this and
      // requires it VISIBLE — it is the only handle on a card.
      data-flow-id={card.id}
      onClick={() => onOpen(card.id)}
      className={`text-left flex flex-col gap-2 p-4 min-h-[150px] rounded-2xl
                  bg-panel border border-line backdrop-blur-[14px] text-ink
                  cursor-pointer transition-colors ${CARD_SHADOW} ${CARD_HOVER}`}
    >
      <span className="font-display font-semibold text-[12.5px] tracking-[0.1em] uppercase">
        {card.name}
      </span>
      <span className="text-[12px] text-dim leading-[1.45] flex-1 [text-wrap:pretty]">
        {card.tagline}
      </span>
      <span className="font-mono text-[10px] text-faint">{cardMeta(card)}</span>
      <span className={`flex items-center gap-[7px] font-mono text-[10px] ${status.cls}`}>
        <Led state={status.led} label={status.text} />
        {status.text}
      </span>
    </button>
  );
});

/** The dashed `+ NEW FLOW` cell. First cell of MY FLOWS, and only when the
 *  filter box is empty — a card that is not a search result must not sit inside
 *  a list of search results. */
export function NewFlowCard({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex flex-col items-center justify-center gap-2 min-h-[150px]
                 bg-transparent border border-dashed border-line2 rounded-2xl
                 text-dim cursor-pointer transition-colors
                 hover:border-accent hover:text-accent"
    >
      {/* aria-hidden so the accessible name is exactly `NEW FLOW`, which is
          the substring the harness's ("click","NEW FLOW") step matches. */}
      <span className="text-[22px] leading-none" aria-hidden="true">+</span>
      <span className="font-display font-semibold text-[11px] tracking-[0.14em]">
        NEW FLOW
      </span>
    </button>
  );
}
