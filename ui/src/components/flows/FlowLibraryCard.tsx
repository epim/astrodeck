// FlowLibraryCard.tsx -- one flow card, plus the `+ NEW FLOW` cell that closes
// the My-flows grid.
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

/** A card's floor height, phone first.
 *
 *  THE 150px FLOOR WAS MEASURED AS THE PROBLEM. At 412x915 the library's
 *  heading, its paragraph and the toolbar spend the first ~350px, so a 150px
 *  card plus the 14px grid gap left room for one and a bit -- and since the
 *  new-flow cell used to lead the grid, the one thing on screen was a control
 *  for making MORE flows, over a folder heading that said there were seven.
 *  112px is the same card with its tagline clamped to two lines (below), which
 *  is what makes two of them plus their heading fit above the fold.
 *
 *  The 150px design height comes back at `lg`, where it was never the problem
 *  and where a three-column grid of short cards looks like a toolbar. */
const CARD_MIN_H = "min-h-[112px] lg:min-h-[150px]";

export interface FlowLibraryCardProps {
  card: FlowCard;
  onOpen: (id: string) => void;
  /** The flow that was just created, so a library that reloaded under the
   *  operator says WHICH row is theirs. A quick flow lands in a folder that may
   *  already hold thirty, sorted by nothing they chose; "it saved" with no
   *  pointer is a claim they have to go and verify. Ringed AND labelled -- the
   *  ring alone would be colour-only. */
  highlight?: boolean;
}

/** `React.memo`'d and store-free: the card takes everything it draws as props,
 *  so re-rendering the library's toolbar (a keystroke in the filter box) does
 *  not re-render every card body. */
export const FlowLibraryCard = memo(function FlowLibraryCard(
  { card, onOpen, highlight = false }: FlowLibraryCardProps,
) {
  const status = cardStatus(card.last_result);
  return (
    <button
      type="button"
      // The harness resolves `("open-flow","example-m16")` through this and
      // requires it VISIBLE — it is the only handle on a card.
      data-flow-id={card.id}
      data-flow-highlight={highlight ? "true" : undefined}
      onClick={() => onOpen(card.id)}
      className={`text-left flex flex-col gap-1.5 lg:gap-2 p-3 lg:p-4 rounded-2xl
                  bg-panel backdrop-blur-[14px] text-ink border
                  cursor-pointer transition-colors ${CARD_MIN_H} ${CARD_SHADOW} ${
                    highlight ? "border-accent" : `border-line ${CARD_HOVER}`}`}
    >
      <span className="font-display font-semibold text-[12.5px] tracking-[0.1em] uppercase">
        {card.name}
      </span>
      {/* The word, not only the ring. A card picked out by border colour alone
          is a card nobody colour-blind can find, and it is the whole answer to
          "did my flow save?". */}
      {highlight && (
        <span className="font-mono text-[10px] text-accent">JUST SAVED</span>
      )}
      {/* CLAMPED TO TWO LINES. The tagline is the one variable-height thing on
          a card, and a generated one runs long ("12 subs each of L, R, G, B, Ha
          on NGC 6946: 60 frames, guided") -- four lines of it on a 372px phone
          made a 200px card and pushed the second flow off the screen. Two lines
          is enough to tell two flows apart, and the whole string is still there
          for anyone who opens it. */}
      <span className="text-[12px] text-dim leading-[1.45] flex-1 line-clamp-2
                       lg:line-clamp-none [text-wrap:pretty]">
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

/** The `+ NEW FLOW` cell that CLOSES the My-flows grid.
 *
 *  THREE THINGS CHANGED HERE AND ALL THREE ARE THE SAME DEFECT (phone review,
 *  2026-09-07, 412x915).
 *
 *  It used to be the FIRST cell. Under a heading reading "MY FLOWS 7", the only
 *  thing a phone could see was a 150px control for making an eighth -- the
 *  library's own contents were entirely below the fold, with nothing on screen
 *  saying so. A creation control belongs after the things it creates.
 *
 *  It used to be DASHED, on a surface with no drag-and-drop anywhere in it
 *  (there is no `onDrop`, `dragover` or file input in this codebase). A dashed
 *  rectangle is the web's drop-zone idiom, so it promised a gesture that does
 *  nothing -- and the promise was loudest on the one screen where the affordance
 *  cost the most room. It is an accent BUTTON now: same action, no invitation
 *  to drop a file on it.
 *
 *  It used to be CARD-SIZED everywhere. Below `lg` it is a 46px button, so it
 *  costs the height of a control rather than the height of a card; from `lg`
 *  it grows back into the grid's rhythm, because a three-column wall of 150px
 *  cards with a 46px stub in the corner reads as a rendering fault. Never
 *  taller than a card either way.
 *
 *  Still only rendered when the filter box is empty -- a cell that is not a
 *  search result must not sit inside a list of search results. */
export function NewFlowCard({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      data-flow-new
      onClick={onClick}
      className="flex flex-row lg:flex-col items-center justify-center gap-2
                 min-h-[46px] lg:min-h-[150px] rounded-[10px] lg:rounded-2xl
                 border border-accent2 bg-accent-fill text-accent
                 cursor-pointer transition-colors hover:border-accent"
    >
      {/* aria-hidden so the accessible name is exactly `NEW FLOW`, which is
          the substring the harness's ("click","NEW FLOW") step matches. */}
      <span className="text-[17px] lg:text-[22px] leading-none" aria-hidden="true">+</span>
      <span className="font-display font-semibold text-[11px] tracking-[0.14em]">
        NEW FLOW
      </span>
    </button>
  );
}

/** The `QUICK FLOW` cell beside it. Same geometry, quieter chrome.
 *
 *  Two creation controls, one accent between them: NEW FLOW opens a canvas and
 *  QUICK FLOW skips it, and painting both in the accent would make the grid's
 *  last row shout twice and say nothing about which is which. */
export function QuickFlowCard({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      data-flow-quick-card
      onClick={onClick}
      className="flex flex-row lg:flex-col items-center justify-center gap-2
                 min-h-[46px] lg:min-h-[150px] rounded-[10px] lg:rounded-2xl
                 border border-line2 bg-transparent text-dim
                 cursor-pointer transition-colors
                 hover:border-accent hover:text-accent"
    >
      <span className="font-display font-semibold text-[11px] tracking-[0.14em]">
        QUICK FLOW
      </span>
      <span className="font-mono text-[10px] text-faint hidden lg:block px-3
                       text-center leading-[1.4]">
        target, subs, filters, go
      </span>
    </button>
  );
}
