// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// FlowFieldRow.tsx - one editable parameter of one node, in the design's own
// vocabulary (parity row A12).
//
// THE CONTROL MAP (wave R7's brief for this task):
//   select, <= 4 options  -> `Segmented`   (a real radiogroup: one tab stop)
//   select, > 4 options   -> `Dial`        (ordered stops, 64 px, drag or tap)
//   text with a NUMERIC default -> `NumberField`
//   text with a string default  -> `TextInput`
//   cycleplan             -> `FlowCyclePlanRows`
//
// `FieldDef.control` knows only `select | text | cycleplan`; whether a `text`
// field is a number is decided by the type of the DEFAULT, because that is what
// `flowsSetParam` coerces by (`flowsSlice.ts:264`). Reading it from anywhere
// else would let a select start committing numbers.
//
// WHY `NumberField` AND NOT A RAW BOX. `flowsSetParam` reverts unparseable input
// to the default, so a store-controlled input is unusable: clear it to retype
// and the empty string slams the default back under the cursor; type the "-" of
// "-30" and the same thing happens. `NumberField` holds its own text, parses at
// blur and Enter only, and REJECTS a blank rather than reading it as 0.
//
// LOCKING. An example flow is `readonly` server-side - `flowsSave` refuses it,
// so an edit made here is discarded on close with nothing on screen to say so.
// Every control therefore carries `lockedReason`, never the native `disabled`
// attribute: the value stays readable and selectable and a press says why.
//
// A FIELD WITH `help` GETS AN INFO ICON (#195, owner ruling 7 on #189), in every
// branch: hover on a fine pointer, tap on a coarse one, a 44 px target, carrying
// the field's `help`. This hub turned most of its InfoDots into visible hints,
// and the owner's ruling for this one field is an ICON, so the exception is
// deliberate and is one field wide: no other field carries `help`.
//
// THE ICON IS THIS FILE'S OWN (`InfoButton`), on the new UI's `Popover`, and not
// the legacy `InfoDot`: `r7Parity.test.ts` holds that nothing under `next/`
// mounts a component from `components/` that is not on its keep-as-is list, and
// a tooltip is chrome, not a canvas or a plot. The behaviour is the legacy
// tooltip's where it matters: a mouse hovering opens it and leaving closes it,
// a tap (a click, which a keyboard's Enter and Space also make) pins it open
// and the next one closes it, a press outside or Escape closes it.
//
// The text is also in the document at all times, screen-reader-only
// (`aria-describedby`), because the popover is mounted only while it is open.
// The primitives here (`Segmented`, `Dial`) take no `aria-describedby`, so it
// rides on the `role="group"` element that wraps the control.
//
// A field with no `help` renders exactly as it always did.

import { useCallback, useId, useRef, useState, type CSSProperties, type JSX } from "react";

import { useStore } from "../../../../../store";
import type { FieldDef } from "../../../../../components/flows/nodeDefs";
import type { FlowNodeType } from "../../../../../components/flows/flowsTypes";
import { NxIcon } from "../../../../icons";
import { Dial, Field, Label, NumberField, Popover, Segmented, TextInput } from "../../../../ui";
import { FlowCyclePlanRows } from "./FlowCyclePlanRows";
import {
  SEGMENTED_MAX, fieldAriaLabel, fieldIsNumeric, numericValue, selectOptions, splitUnit,
} from "./fieldModel";

/** The 284 px desktop column vs the sheet body. The sheet is the surface driven
 *  at the scope, so its rows carry the 44 px touch floor. */
export type FlowFieldVariant = "column" | "sheet";

/** The icon's button: a 14 px glyph centred in a 44 px hit area, and a negative
 *  margin of (44 - 14) / 2 so its layout footprint is the glyph's own and no
 *  row reflows. Inline, because `inspector.css` is not this change's to edit.
 *
 *  THE 44 IS THE BOX'S OWN SIZE, WITH NO PADDING. The app's CSS is Tailwind's
 *  preflight, which sets `box-sizing: border-box` on everything, and under it
 *  `width: 14` with `padding: 15` is a 30 px box (a border box cannot be
 *  narrower than its padding), whose -15 margins leave it no footprint at all:
 *  measured in Chromium, 30 x 30 with a 0 px footprint. A width and a height of
 *  44 and no padding are 44 x 44 under either box model. */
const INFO_BUTTON: CSSProperties = {
  display: "inline-flex", alignItems: "center", justifyContent: "center",
  width: 44, height: 44, padding: 0, margin: -15, flex: "none",
  background: "transparent", border: 0, cursor: "help",
  color: "var(--text-dim)",
};

/** The help text in its popover: readable at 12 px in a 284 px column. */
const INFO_BUBBLE: CSSProperties = {
  display: "block", maxWidth: 280, padding: "6px 8px",
  fontSize: 12, lineHeight: 1.45, color: "var(--text)",
};

/** An info icon that shows `text`. Hover (a mouse only: a touch's pointerenter
 *  is its tap, which arrives as the click) opens it and leaving closes it; a
 *  click pins it open and the next click closes it; a press outside or Escape
 *  closes it (`Popover`). While it is open the button is described by it. */
function InfoButton({ label, text }: { label: string; text: string }): JSX.Element {
  const [hover, setHover] = useState(false);
  const [pinned, setPinned] = useState(false);
  const anchor = useRef<HTMLButtonElement | null>(null);
  const bubbleId = `${useId()}-bubble`;
  const open = hover || pinned;
  const close = useCallback(() => { setHover(false); setPinned(false); }, []);
  return (
    <>
      <button
        ref={anchor}
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? bubbleId : undefined}
        style={INFO_BUTTON}
        onClick={(e) => { e.stopPropagation(); setPinned((p) => !p); }}
        onPointerEnter={(e) => { if (e.pointerType === "mouse") setHover(true); }}
        onPointerLeave={(e) => { if (e.pointerType === "mouse") setHover(false); }}
      >
        <NxIcon name="info" size={14} />
      </button>
      <Popover open={open} anchorRef={anchor} onClose={close}>
        <span id={bubbleId} role="tooltip" style={INFO_BUBBLE}>{text}</span>
      </Popover>
    </>
  );
}

export interface FlowFieldRowProps {
  nodeId: string;
  nodeType: FlowNodeType;
  field: FieldDef;
  /** The node's CURRENT param, passed down from the one component that
   *  subscribes to the node record - a row must not open its own subscription
   *  per field. */
  value: string | number | undefined;
  variant?: FlowFieldVariant;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
}

export function FlowFieldRow({
  nodeId, nodeType, field, value, variant = "column",
  lockedReason = null, onExplain,
}: FlowFieldRowProps): JSX.Element {
  const setParam = useStore((s) => s.flowsSetParam);
  const id = useId();
  const stored = String(value ?? "");
  const aria = fieldAriaLabel(field);
  const write = (raw: string) => setParam(nodeId, field.key, raw);
  const help = field.help && field.help.trim() ? field.help : null;
  const helpId = `${id}-help`;
  const described = help === null ? undefined : helpId;

  /** The icon, for a field that has help. */
  const infoIcon = (): JSX.Element | null => help === null ? null : (
    <span data-testid={`flow-field-help-${field.key}`} style={{ flex: "none", display: "flex" }}>
      <InfoButton label={`Explain: ${field.label}`} text={help} />
    </span>
  );

  /** A caption with the icon at its right end. The caption wraps (DUSK
   *  WINDOW's Automatic resume label is four lines in the 284 px column) and
   *  `min-width: 0` lets it shrink to the column instead of pushing the icon
   *  out of it. Inline style, not a class: `inspector.css` is not this
   *  change's to edit, and the one row that needs it is this one. */
  const caption = (): JSX.Element => (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 6, minWidth: 0 }}>
      <div style={{ flex: 1, minWidth: 0 }}><Label size={11}>{field.label}</Label></div>
      {infoIcon()}
    </div>
  );

  /** A control that carries its own label (`Dial`, `NumberField`, `Field`) with
   *  the icon beside it, at the top right of the block. */
  const beside = (inner: JSX.Element): JSX.Element => help === null ? inner : (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 6, minWidth: 0 }}>
      <div style={{ flex: 1, minWidth: 0 }} role="group" aria-describedby={described}>{inner}</div>
      {infoIcon()}
    </div>
  );

  return (
    <div
      className="nx-flowfield"
      data-variant={variant}
      data-control={field.control}
      data-testid={`flow-field-${field.key}`}
    >
      {control()}
      {help !== null && <span id={helpId} className="sr-only">{help}</span>}
    </div>
  );

  function control(): JSX.Element {
    if (field.control === "cycleplan") {
      return (
        <>
          <Label size={11}>{field.label}</Label>
          <FlowCyclePlanRows
            nodeId={nodeId}
            fieldKey={field.key}
            value={value}
            lockedReason={lockedReason}
            onExplain={onExplain}
          />
        </>
      );
    }

    if (field.control === "select") {
      const opts = selectOptions(field.options, stored);
      if (opts.length > SEGMENTED_MAX) {
        // The design's dial names its own label in its head, so no separate
        // eyebrow here - two would be the same word twice.
        return beside(
          <Dial<string>
            label={field.label}
            hint={field.unit ?? "drag or tap"}
            options={opts.map((o) => ({ value: o, label: o }))}
            value={stored}
            onChange={write}
            lockedReason={lockedReason}
            onExplain={onExplain}
          />,
        );
      }
      return (
        <>
          {caption()}
          {/* A closed list can be wider than a 284 px column. It scrolls rather
              than truncating: a clipped option is a setting nobody can reach. */}
          <div
            className="nx-flowfield-scroll"
            role={help === null ? undefined : "group"}
            aria-describedby={described}
          >
            <Segmented<string>
              label={aria}
              options={opts.map((o) => ({ value: o, label: o }))}
              value={stored}
              onChange={write}
              lockedReason={lockedReason}
              onExplain={onExplain}
            />
          </div>
        </>
      );
    }

    if (fieldIsNumeric(nodeType, field.key)) {
      const [unit, hint] = splitUnit(field.unit);
      return beside(
        <NumberField
          label={field.label}
          value={numericValue(nodeType, field.key, value)}
          onCommit={(next) => write(String(next))}
          unit={unit}
          hint={hint}
          ariaLabel={aria}
          lockedReason={lockedReason}
          onExplain={onExplain}
        />,
      );
    }

    return beside(
      <Field label={field.label} htmlFor={id}>
        <span className="nx-flowfield-inline">
          <TextInput
            id={id}
            value={stored}
            onChange={write}
            mono
            ariaLabel={aria}
            lockedReason={lockedReason}
          />
          {field.unit != null && (
            <span className="nx-flowfield-unit" aria-hidden="true">{field.unit}</span>
          )}
        </span>
      </Field>,
    );
  }
}

export default FlowFieldRow;
