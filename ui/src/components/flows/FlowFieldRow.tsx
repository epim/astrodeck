// FlowFieldRow.tsx — one editable parameter of one node. §C.8.
//
// Exactly two controls exist in the design: a `select` over a closed option list
// and a free `text` box with an optional unit suffix. There is no validation, no
// min/max and no disabled state anywhere in the design's inspector (§C.8 spells
// that out twice) — none is added here.
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
import { useState } from "react";
import { useStore } from "../../store";
import type { FieldDef } from "./nodeDefs";

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

  const commit = (raw: string) => {
    setDraft(raw);
    setParam(nodeId, field.key, raw);
  };

  return (
    <label className="flex flex-col gap-1 min-w-0">
      <span className="label">{field.label}</span>
      {field.control === "select" ? (
        <select
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
      )}
    </label>
  );
}
