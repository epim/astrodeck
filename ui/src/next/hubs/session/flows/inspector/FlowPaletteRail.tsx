// FlowPaletteRail.tsx - the add-stage palette (parity row A14). ONE component,
// two shapes: the 192 px rail beside the canvas at desktop, and the body of the
// `flowPalette` sheet everywhere else.
//
// Two components would be two copies of the group walk, and the thing that would
// drift between them is the item order - which is disputed, lives in
// `components/flows/palette.ts`, and is not re-stated here. Group names, group
// order and item order all come from `PALETTE_GROUPS`; the labels and the
// descriptions come from `NODE_DEFS`.
//
// WHERE A STAGE LANDS is `paletteDrop.ts`'s decision: the canvas's centre-ish
// point in world coordinates when a canvas is mounted, and the prototype's
// (120, 120) when none is - the phone, a palette sheet opened over a list, a
// test. Adding a stage is ungated client-side (the server gates the save), so
// the only lock this surface carries is an example flow's `readonly`.

import type { CSSProperties, JSX } from "react";

import { useStore } from "../../../../../store";
import { NODE_DEFS } from "../../../../../components/flows/nodeDefs";
import { PALETTE_GROUPS } from "../../../../../components/flows/palette";
import type { FlowNodeType } from "../../../../../components/flows/flowsTypes";
import { useBreakpoint } from "../../../../breakpoint";
import { nav } from "../../../../router";
import { Chip, Label, ListRow } from "../../../../ui";
import { explainLock } from "../../../../shell/explain";
import { paletteDropPoint } from "./paletteDrop";
import { FLOW_READONLY_REASON } from "./FlowInspectorColumn";
import "./inspector.css";

/** The visible label on every control that opens the palette.
 *
 *  Authored once because it is an accessible-NAME contract, not decoration: the
 *  probe resolves the opener by role and name, and a name match is a substring
 *  match - so "ADD STAGE" has to survive inside whatever the button reads. */
export const ADD_STAGE_LABEL = "+ ADD STAGE";

/** The sheet's own heading, and the accessible name of the sheet. */
export const ADD_STAGE_TITLE = "ADD STAGE";

/** What a tap on a stage actually does. The nineteen labelled chips say what
 *  each stage IS; this says where it goes and what to do next. */
export const PALETTE_FOOTER =
  "A stage drops into the middle of the canvas. Drag from one of its ports onto "
  + "another stage's port to wire it in.";

/** The one action that opens the palette.
 *
 *  The opener CONTROL is not in this file - the canvas owns its placement - so
 *  this hook is the seam, and no caller has to know that "open the palette" is
 *  now a route (`flowPalette`) rather than a boolean in `flows.ui`. */
export function useOpenFlowPalette(): () => void {
  return () => nav.sheet("flowPalette");
}

export interface FlowPaletteRailProps {
  /** `"rail"` = the 192 px docked column beside the canvas. `"sheet"` = the body
   *  of the `flowPalette` sheet, which also prints each stage's one-line
   *  description because a tablet has no hover. */
  variant?: "rail" | "sheet";
  /** Called after a stage has been added, so the sheet can close itself. */
  onPicked?: (type: FlowNodeType) => void;
}

export function FlowPaletteRail({
  variant = "rail", onPicked,
}: FlowPaletteRailProps): JSX.Element {
  // Narrow selectors, and both are stable identities zustand hands out once - so
  // this component never re-renders for a pan, a status tick or a graph edit. It
  // has nothing on screen that any of those change.
  const addNode = useStore((s) => s.flowsAddNode);
  const readonly = useStore((s) => s.flows.record?.readonly ?? false);
  const tier = useBreakpoint();
  const sheet = variant === "sheet";
  const lockedReason = readonly ? FLOW_READONLY_REASON : null;

  const pick = (type: FlowNodeType) => {
    addNode(type, paletteDropPoint(tier));
    // The legacy palette also SELECTS the new node, which cannot be done from
    // here: `flowsAddNode` mints the id internally and returns nothing. Noted
    // rather than faked with a guess at the id.
    onPicked?.(type);
  };

  return (
    <div className="nx-flowpal" data-variant={variant} data-testid="flow-palette">
      {PALETTE_GROUPS.map((g) => (
        <div className="nx-flowpal-group" key={g.label}>
          <Label size={10}>{g.label}</Label>
          {/* The rail shows only the label, so `desc` reaches a mouse through
              `title` and only there. The sheet prints it instead - a tablet has
              no hover, and nineteen unexplained words are nineteen guesses. */}
          {sheet ? (
            g.types.map((t) => {
              const def = NODE_DEFS[t];
              const ink = { "--nx-node-ink": `var(${def.colorVar})` } as CSSProperties;
              return (
                <ListRow
                  key={t}
                  icon={<span className="nx-flowpal-dot" style={ink} aria-hidden="true" />}
                  title={def.label}
                  sub={def.desc}
                  onPress={() => pick(t)}
                  lockedReason={lockedReason}
                  onExplain={explainLock}
                  data-testid={`palette-type-${t}`}
                />
              );
            })
          ) : (
            <div className="nx-flowpal-grid">
              {g.types.map((t) => {
                const def = NODE_DEFS[t];
                const ink = { "--nx-node-ink": `var(${def.colorVar})` } as CSSProperties;
                return (
                  <Chip
                    key={t}
                    onClick={() => pick(t)}
                    lockedReason={lockedReason}
                    onExplain={explainLock}
                    data-testid={`palette-type-${t}`}
                  >
                    <span className="nx-flowpal-item" style={ink} title={def.desc}>
                      <span className="nx-flowpal-dot" aria-hidden="true" />
                      {def.label}
                    </span>
                  </Chip>
                );
              })}
            </div>
          )}
        </div>
      ))}
      <p className="nx-flowpal-foot">{PALETTE_FOOTER}</p>
    </div>
  );
}

export default FlowPaletteRail;
