// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// FlowFieldRow.tsx — one editable parameter of one node. §C.8.
//
// THREE controls exist. `select` is a closed option list and `text` is a free
// box with an optional unit suffix; there is no validation, no min/max and no
// disabled state anywhere in the design's inspector (§C.8 spells that out
// twice) and none is added here.
//
// `cycleplan` is the third, from the 2026-08-14 export, and it is delegated
// whole to FlowCyclePlan. It is not a general-purpose control: it renders the
// rig's filter wheel and writes a slot table, which is why it takes the field
// KEY rather than a value-and-onChange pair.
//
// THE `!` PREFIXES ARE MANDATORY, not stylistic. `index.css` is UNLAYERED, so
// `.field { font-size: 13px; width: 100% }` beats any Tailwind utility on the
// same element regardless of specificity or order. That trap is recorded three
// separate times in that file; the last note says four passes edited a `w-24`
// class that never applied and "nothing ever changed on screen".
//
// The caption reuses `.label` rather than the design's Chakra 9.5px. `.label`
// was deliberately bumped 10→11px "for AA legibility" (index.css), and reusing
// it is the house convention; matching the design exactly would need
// `!text-[9.5px] font-display` and would undo that decision. §G-16 covers the
// same class of choice, so this file takes the convention and the milestone
// report records the divergence.
//
// A FIELD WITH `help` GETS AN INFO ICON (#195, owner ruling 7 on #189): the
// `InfoDot` beside the caption, which opens on hover for a fine pointer, on tap
// for a coarse one, with a 44 px target, and carries the field's `help` text.
// A field with no `help` renders exactly as it always did.
//
// WHY THE HELPED ROW IS NOT ONE <label>. A `<label>` that wrapped the icon
// would (1) put the icon's own name ("Explain: ...") into the control's
// accessible name, and (2) forward a press on the icon to the control, which a
// browser does for any click inside a label that is not on native interactive
// content: pressing "i" would open the select. So a helped row is a caption
// ROW (a `<label htmlFor>` and the icon, side by side) over the control, and
// the control names itself from the label and DESCRIBES itself from a
// screen-reader-only copy of the help (`aria-describedby`). That copy is always
// in the document: the tooltip's own bubble is mounted only while it is open
// and its id is private to `Tooltip`, so a control could not point at it.
import { useState } from "react";
import { useStore } from "../../store";
import { InfoDot } from "../ui";
import type { FieldDef } from "./nodeDefs";
import FlowCyclePlan from "./FlowCyclePlan";

/** Desktop's 284px column vs the tablet/phone sheet. §C.8's control table. */
export type FieldVariant = "column" | "sheet";

/** Control geometry per tier, straight from §C.8's table. The sheet carries the
 *  44px touch floor because that surface is the one driven at the scope.
 *
 *  §C.8's table also lists `focus:border-accent-dim` on the text control. It is
 *  not here for two reasons that point the same way: `.field:focus` already sets
 *  `border-color: var(--accent-dim)` in index.css, and the theme block maps that
 *  token to `--color-accent2`, so `border-accent-dim` names no Tailwind colour
 *  and would generate nothing at all. */
const CONTROL: Record<FieldVariant, string> = {
  column: "!text-[12px] !py-[7px] !px-[9px] !rounded-none",
  sheet: "!text-[13px] !p-2.5 min-h-[44px]",
};

const UNIT: Record<FieldVariant, string> = {
  column: "text-[10.5px]",
  sheet: "text-[11px]",
};

/** The options a select should offer for `value`.
 *
 *  models.py validates params PERMISSIVELY on purpose — a vocabulary that gains
 *  a field must not make every saved flow unopenable — so a graph can arrive
 *  carrying a value this option list does not contain. A `<select>` whose value
 *  matches no `<option>` displays the FIRST option instead, which would show the
 *  operator a setting their flow does not hold and quietly write it in on the
 *  next edit. Carrying the stored value as an extra option shows what is
 *  actually saved. This is not validation: nothing is refused, corrected or
 *  disabled. */
export function selectOptions(
  options: readonly string[] | undefined, value: string,
): readonly string[] {
  const list = options ?? [];
  return list.indexOf(value) >= 0 ? list : [value, ...list];
}

export default function FlowFieldRow({ nodeId, field, value, variant = "column" }: {
  nodeId: string;
  field: FieldDef;
  /** The node's CURRENT param, passed down from the one component that
   *  subscribes to the node record — a row must not open its own subscription
   *  per field. */
  value: string | number | undefined;
  variant?: FieldVariant;
}) {
  const setParam = useStore((s) => s.flowsSetParam);
  // The raw text the operator is part-way through typing, or null when the
  // control is showing the stored value.
  //
  // WHY A BUFFER. `flowsSetParam` coerces by the type of the DEFAULT (§B.2): a
  // numeric field reverts unparseable input to its default rather than becoming
  // NaN, because a NaN reaches the compiler as a step with no exposure. Correct
  // — but a purely store-controlled input then makes the field unusable: clear
  // it to retype and the empty string coerces to the default and slams back in
  // under the cursor; type the "-" of "-30" and the same thing happens. The
  // buffer changes nothing about what the store receives (the same raw string,
  // on the same keystroke, coerced the same way) — it only lets the box show
  // what was typed until focus leaves, at which point the stored value, which
  // is the truth, takes the display back.
  const [draft, setDraft] = useState<string | null>(null);

  const stored = String(value ?? "");
  const shown = draft ?? stored;
  const help = field.help && field.help.trim() ? field.help : null;
  // IDS FROM THE NODE AND THE FIELD, NOT `useId`: the same inspector mounted
  // twice must read the same markup (flowInspectorFrame.test.tsx compares two
  // renders of an untouched node's column byte for byte), and a `useId` value
  // is a counter that moves with every mount. A node's field is one row at a
  // time, so the pair is unique where it is used.
  const controlId = `flow-field-${variant}-${nodeId}-${field.key}`;
  const helpId = `${controlId}-help`;

  const commit = (raw: string) => {
    setDraft(raw);
    setParam(nodeId, field.key, raw);
  };

  /** The control, for a plain row (`described` undefined) and a helped one. */
  const control = (described?: { id: string; describedBy: string }) =>
    field.control === "cycleplan" ? (
      <FlowCyclePlan nodeId={nodeId} fieldKey={field.key} value={value} />
    ) : field.control === "select" ? (
      <select
        id={described?.id}
        aria-describedby={described?.describedBy}
        className={`field ${CONTROL[variant]}`}
        value={stored}
        onChange={(e) => setParam(nodeId, field.key, e.target.value)}
      >
        {selectOptions(field.options, stored).map((opt) => (
          <option key={opt} value={opt}>{opt}</option>
        ))}
      </select>
    ) : (
      <span className="flex items-center gap-1.5 min-w-0">
        <input
          id={described?.id}
          aria-describedby={described?.describedBy}
          type="text"
          className={`field min-w-0 ${CONTROL[variant]}`}
          value={shown}
          onChange={(e) => commit(e.target.value)}
          onBlur={() => setDraft(null)}
        />
        {field.unit && (
          <span className={`font-mono ${UNIT[variant]} text-faint flex-none`}>
            {field.unit}
          </span>
        )}
      </span>
    );

  if (help === null) {
    return (
      <label className="flex flex-col gap-1 min-w-0">
        <span className="label">{field.label}</span>
        {control()}
      </label>
    );
  }

  return (
    <div className="flex flex-col gap-1 min-w-0" data-testid={`flow-field-${field.key}`}>
      {/* The caption wraps (a long label such as DUSK WINDOW's Automatic resume
          is four lines in the 284 px column) and the icon stays at the top
          right of it. `min-w-0` lets the label shrink to the column; without
          it a flex child is as wide as its longest line and pushes the icon
          out of the column. */}
      <span className="flex items-start gap-1.5 min-w-0">
        <label htmlFor={controlId} className="label min-w-0 flex-1">{field.label}</label>
        {/* The capture-phase preventDefault is belt and braces: the icon is
            outside the <label> already, so there is nothing to forward a press
            to, and this keeps it that way if the row is ever wrapped again. */}
        <span className="flex-none" onClickCapture={(e) => e.preventDefault()}>
          <InfoDot label={`Explain: ${field.label}`} content={help} />
        </span>
      </span>
      {control({ id: controlId, describedBy: helpId })}
      <span id={helpId} className="sr-only">{help}</span>
    </div>
  );
}
