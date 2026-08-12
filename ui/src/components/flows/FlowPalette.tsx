// FlowPalette.tsx — the add-stage list. ONE component, two shapes.
//
// §C.7 and the file plan both say it this way: "Rendered as the 192px rail AND
// as the sheet body — one component, a `variant` prop." Two components would be
// two copies of the group walk, and the thing that would drift between them is
// the ⚠ §G-3 item order — the one part of this surface that is still disputed
// and will be edited again the moment the user rules on it. It is read from
// `palette.ts`; nothing here re-states a group name or an item order.
//
// WHY THE ITEMS CARRY `data-palette-type` AND NOT `data-node-type` (§C.7's own
// ⚠ note): the parity harness selects the calibration node with
// `[data-node-type='calib']`. At 1440px the rail is on screen at the same time
// as the canvas, so a rail item wearing `data-node-type` would win that query —
// `("select-node-type","calib")` would click the PALETTE and drop a twentieth
// node, and the capture would be of a graph the fixture never contained.
//
// Sources: MILESTONE2-CONTRACT.md §C.7 (every class string below is quoted from
// it), "AstroDeck Flows.dc.html" lines 131-144 (the rail) and 501-513 (the sheet
// list), README.md §3.

import type { JSX } from "react";
import { useStore } from "../../store";
import { NODE_DEFS } from "./nodeDefs";
import { PALETTE_GROUPS } from "./palette";
import type { FlowNodeType } from "./flowsTypes";

export type FlowPaletteVariant = "rail" | "sheet";

/**
 * Where a stage lands when nobody told the palette where the canvas is.
 *
 * NOT a design decision — it is the prototype's own no-canvas-ref fallback
 * (`addNode()`, "AstroDeck Flows.dc.html" line 1035: `let x = 120, y = 120`).
 * The real rule (§D.2) is `toCanvas(rect.centre-ish)`, which needs the canvas
 * element's bounding rect and the live pan/zoom — neither of which this
 * component can see. So the editor, which owns both, passes `onPick` and does
 * the placement itself; this constant is only what happens when it does not.
 * Two stages dropped without an `onPick` land on top of each other, exactly as
 * they do in the prototype under the same conditions.
 */
export const PALETTE_FALLBACK_DROP = { x: 120, y: 120 } as const;

export interface FlowPaletteProps {
  /** `"rail"` = the 192px docked column (desktop only). `"sheet"` = the body of
   *  `<FlowPaletteSheet>`. Defaults to the rail, which is the only variant that
   *  can stand on its own — the sheet body needs the Overlay around it. */
  variant?: FlowPaletteVariant;
  /**
   * Called instead of the built-in add.
   *
   * The editor should always pass this: it is the only thing that knows the
   * canvas rect, so it is the only thing that can honour §D.2's drop position.
   * Left off, the palette still works (see `PALETTE_FALLBACK_DROP`) rather than
   * rendering nineteen buttons that do nothing.
   */
  onPick?: (type: FlowNodeType) => void;
}

// ─────────────────────────────────────────────────────────────── class strings
// Quoted from §C.7. The rail's translucent ground is `--bg-raise` at 60%, not a
// hex: the prototype's literal `rgba(9,10,16,0.6)` has no token and would go
// wrong in both the light ground and the night LUT.
const RAIL_ROOT =
  "w-[192px] flex-none border-r border-line overflow-y-auto px-2.5 py-3 " +
  "flex flex-col gap-3.5 bg-[color-mix(in_srgb,var(--bg-raise)_60%,transparent)]";

// The sheet body's own padding lives on the Overlay's `bodyClassName`; the
// bottom 20px lives here (see the safe-area note in FlowPaletteSheet.tsx).
const SHEET_ROOT = "flex flex-col gap-3.5 pb-5";

// Uppercase comes from `palette.ts`'s DATA, and §C.7's header classes carry no
// `uppercase` utility — deliberately, because the inspector's captions are the
// opposite case (uppercased by `.label`'s text-transform). Re-casing here in
// either direction would break one of the two.
const GROUP_HEAD =
  "font-display font-semibold text-[9.5px] tracking-[0.2em] text-faint";

const RAIL_ITEM =
  "flex items-center gap-2 w-full text-left px-2 py-[7px] rounded-lg " +
  "border border-transparent bg-transparent text-ink " +
  "hover:border-line2 hover:bg-raise";

// `tap` is the app's touch floor (44px, and 48px under a coarse pointer), which
// is what the 44px house rule means in this codebase — `min-h-[44px]` alone
// would be a number that never grows for a gloved thumb at the scope.
const SHEET_ITEM =
  "tap min-h-[44px] flex items-center gap-2.5 w-full text-left px-2.5 py-[11px] " +
  "rounded-[10px] border border-line text-ink " +
  "bg-[color-mix(in_srgb,var(--bg)_60%,transparent)]";

function PaletteItem({ type, sheet, onPick }: {
  type: FlowNodeType;
  sheet: boolean;
  onPick: (type: FlowNodeType) => void;
}): JSX.Element {
  const def = NODE_DEFS[type];
  // 7×7 / `0 0 5px` on the rail, 8×8 / `0 0 6px` in the sheet (§C.7; prototype
  // 136 and 506). The colour is `var(--…)` off the node's own token, never the
  // prototype's hex — that is what makes the dot follow the night LUT.
  const px = sheet ? 8 : 7;
  const tint = `var(${def.colorVar})`;
  return (
    <button
      type="button"
      data-palette-type={type}
      // The rail shows only the label, so `desc` reaches a mouse here and only
      // here. The sheet prints it instead — a tablet has no hover.
      title={def.desc}
      onClick={() => onPick(type)}
      className={sheet ? SHEET_ITEM : RAIL_ITEM}
    >
      <span
        aria-hidden
        className="flex-none rounded-[2px]"
        style={{ width: px, height: px, background: tint,
                 boxShadow: `0 0 ${sheet ? 6 : 5}px ${tint}` }}
      />
      {sheet ? (
        <span className="flex flex-col gap-0.5 min-w-0">
          <span className="font-mono text-[11.5px]">{def.label}</span>
          {/* One line, clipped — the prototype's `text-overflow:ellipsis`. A
              wrapping description would push the list past a thumb's reach. */}
          <span className="text-[10.5px] text-faint truncate">{def.desc}</span>
        </span>
      ) : (
        <span className="font-mono text-[10.5px] tracking-[0.03em]">{def.label}</span>
      )}
    </button>
  );
}

export function FlowPalette({ variant = "rail", onPick }: FlowPaletteProps): JSX.Element {
  // Narrow selectors, and both of these are stable identities zustand hands out
  // once — so this component never re-renders for a pan, a status tick or a
  // graph edit. It has nothing on screen that any of those change.
  const addNode = useStore((s) => s.flowsAddNode);
  const setUi = useStore((s) => s.flowsSetUi);

  const pick = (type: FlowNodeType): void => {
    if (onPick) {
      onPick(type);
      return;
    }
    addNode(type, PALETTE_FALLBACK_DROP);
    // The prototype closes the palette on every add (line 1040) — one tap, one
    // stage, back to the graph. It also SELECTS the new node, which cannot be
    // done from here: `flowsAddNode` mints the id internally and returns
    // nothing. Noted rather than faked with a guess at the id.
    setUi({ paletteOpen: false });
  };

  const sheet = variant === "sheet";
  return (
    <div className={sheet ? SHEET_ROOT : RAIL_ROOT}>
      {PALETTE_GROUPS.map((g) => (
        <div key={g.label} className="flex flex-col gap-1">
          <div className={sheet ? GROUP_HEAD : `${GROUP_HEAD} px-1 pb-0.5`}>
            {g.label}
          </div>
          {g.types.map((t) => (
            <PaletteItem key={t} type={t} sheet={sheet} onPick={pick} />
          ))}
        </div>
      ))}
      {/* Rail only — §C.7 files the hint under the rail, and the prototype's
          sheet (line 492-517) has no footer. It says what a click DOES, which
          is the one thing nineteen labelled buttons do not say. */}
      {!sheet && (
        <p className="text-[10.5px] text-faint leading-[1.5] px-1 pt-2.5 pb-0.5 border-t border-line">
          Click to drop a stage on the canvas, then drag from a port to wire it.
        </p>
      )}
    </div>
  );
}

export default FlowPalette;
