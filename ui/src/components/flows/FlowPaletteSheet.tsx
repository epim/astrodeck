// FlowPaletteSheet.tsx — the add-stage palette as a bottom sheet (§C.10).
//
// The whole component is `<Overlay variant="sheet">` around `<FlowPalette
// variant="sheet">`. Nothing about the sheet's chrome is re-implemented here:
// Escape-to-close, the focus trap, the scrim, the body portal and the opaque
// surface all come from the primitive, which is what satisfies README §3's
// "Esc should close overlays (add in production)". A `fixed inset-0` inside the
// view tree — which is what the prototype does at line 493 — is the exact bug
// Overlay.tsx's header documents four separate measurements of.
//
// Sources: MILESTONE2-CONTRACT.md §C.7 (sheet variant), §C.10 (both sheets go
// through Overlay, size rides the CSS vars), §F.1 (`data-flows-palette`),
// "AstroDeck Flows.dc.html" lines 492-517.

import { type CSSProperties, type JSX } from "react";
import { useStore } from "../../store";
import { Overlay } from "../Overlay";
import { Icon } from "../icons";
import { FlowPalette } from "./FlowPalette";
import type { FlowNodeType } from "./flowsTypes";

/**
 * The visible label on every control that opens this sheet.
 *
 * Authored once because it is an ACCESSIBLE-NAME contract, not decoration:
 * the parity harness resolves the opener with a role+name lookup, and
 * `get_by_role(name=…)` matches on a substring — so "ADD STAGE" has to survive
 * inside whatever the button reads. The prototype spells the control
 * `+ ADD STAGE` at all three of its placements (lines 210, 338, 418) and the
 * sheet's own heading `ADD STAGE` (line 497): four occurrences, one wording.
 *
 * ⚠ §G-28 is still OPEN and this constant does not close it: the harness step
 * as written is `("click","ADD NODE")` at DESKTOP, where the design has no
 * opener at all — the desktop editor opens the palette from the rail, which is
 * a list. Do not add `aria-label="ADD NODE"` to make that step pass; §F.4
 * records that it would break WCAG 2.5.3 (visible label not in the accessible
 * name), and §F.4's other two options are the user's to choose.
 */
export const ADD_STAGE_LABEL = "+ ADD STAGE";

/** The sheet's own heading, and the accessible name of the dialog. */
export const ADD_STAGE_TITLE = "ADD STAGE";

/**
 * The one action that opens this sheet.
 *
 * The opener CONTROL is not in this file: §C's editor skeleton renders
 * `<AddStageFloat/>` from `FlowEditor.tsx` at tablet (`right:12 bottom:44 z-5`)
 * and `FlowPhoneGraph` pins a second, differently-styled one at the graph's end
 * — placement and treatment both belong to those files. This hook is the seam,
 * so no caller has to know that "open the palette" is spelled
 * `flowsSetUi({ paletteOpen: true })`.
 */
export function useOpenFlowPalette(): () => void {
  const setUi = useStore((s) => s.flowsSetUi);
  return () => setUi({ paletteOpen: true });
}

export interface FlowPaletteSheetProps {
  /** Forwarded to `<FlowPalette>`; the sheet closes itself afterwards. Omit it
   *  and the palette falls back to its own add (see `PALETTE_FALLBACK_DROP`),
   *  which also closes the sheet by clearing `paletteOpen`. */
  onPick?: (type: FlowNodeType) => void;
}

export function FlowPaletteSheet({ onPick }: FlowPaletteSheetProps = {}): JSX.Element {
  // Narrow selectors: one boolean and one stable action. A pan, a status tick
  // or a graph edit must not re-render an overlay that shows none of them.
  const open = useStore((s) => s.flows.ui.paletteOpen);
  const setUi = useStore((s) => s.flowsSetUi);
  const close = (): void => setUi({ paletteOpen: false });

  // Only wrap when there is something to wrap. Passing an always-defined
  // handler would shadow FlowPalette's fallback add and leave the sheet's
  // buttons inert for any caller that did not pass `onPick`.
  const pick = onPick
    ? (type: FlowNodeType): void => { onPick(type); close(); }
    : undefined;

  return (
    <Overlay
      open={open}
      variant="sheet"
      label="Add stage"
      onClose={close}
      // 72dvh, not Overlay's 85dvh default and not the edit sheet's 76dvh — the
      // prototype's palette sheet is `max-height:72dvh` (line 494). It goes
      // through the CSS var because `.overlay-surface`'s authored max-height is
      // UNLAYERED and beats any Tailwind class on the same element; Overlay.tsx
      // records the 788px-on-an-820px-tablet measurement that proved it.
      surfaceStyle={{ "--ov-max-h": "72dvh" } as CSSProperties}
      // Left/right/top only. `padding-bottom` is contested: `.overlay-safe-b`
      // is unlayered authored CSS and would beat a `pb-[…]` utility here, so
      // the safe-area inset comes from the primitive and the design's 20px
      // rides on FlowPalette's sheet root instead, where nothing fights it.
      bodyClassName="px-4 pt-3"
      head={
        // §C.10's ruling for the sibling sheet, applied verbatim: the marker
        // must sit on a VISIBLE element inside the portal, and `head` is the
        // one element that always renders and always has a box. The Overlay's
        // own surface is not reachable from out here without editing the
        // primitive.
        <header
          data-flows-palette
          className="flex items-center justify-between px-4 py-[13px]"
        >
          <span className="font-display font-semibold text-[11px] tracking-[0.2em]">
            {ADD_STAGE_TITLE}
          </span>
          <button
            type="button"
            onClick={close}
            aria-label="Close add stage"
            className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center
                       rounded-lg border border-line2 text-dim"
          >
            {/* §G-9: there is no ✕-in-a-circle in the 42-name icon set; `x` is
                the bare cross the set does have, and it is what every other
                sheet in the app closes with. */}
            <Icon name="x" size={16} />
          </button>
        </header>
      }
    >
      <FlowPalette variant="sheet" onPick={pick} />
    </Overlay>
  );
}

export default FlowPaletteSheet;
