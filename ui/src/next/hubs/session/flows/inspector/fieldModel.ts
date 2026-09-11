// fieldModel.ts - the pure decisions the rebuilt inspector makes about ONE
// parameter row: which control it gets, what number it is holding, and what
// colour a wheel slot paints in.
//
// No React, no DOM, no store - so the control mapping is testable directly and
// the row component stays a rendering of a decision made here.
//
// THE VOCABULARY IS NOT RE-DERIVED. `NODE_DEFS`, `FieldDef`, `parseCyclePlan`
// and `formatCyclePlan` stay in `components/flows/nodeDefs.ts`, which is the one
// home of the node table (and the one file whose test parses `nodes.py` and
// compares). This module only decides how a field DRAWS.

import { NODE_DEFS, type FieldDef } from "../../../../../components/flows/nodeDefs";
import type { FlowNodeType } from "../../../../../components/flows/flowsTypes";

/** Which control a `select` field gets.
 *
 *  Wave R7's mapping: a closed list of at most four choices is a `Segmented`
 *  radiogroup; a longer ordered list of stops is the design's `Dial`. In this
 *  vocabulary exactly one field crosses the line - CAPTURE LOOP's `Filter`,
 *  seven wheel slots - and a dial is what the design already uses for a wheel.
 */
export const SEGMENTED_MAX = 4;

/** The options a select should offer for `value`.
 *
 *  COPIED, deliberately, from `components/flows/FlowFieldRow.tsx`'s export of
 *  the same name (wave R7 section 2.1's "embedded helpers": re-export from the
 *  new module, never move or edit the legacy file, so `#/classic` keeps its own
 *  copy and the lazily-split next bundle does not pull a legacy component tree
 *  in behind a four-line helper).
 *
 *  `models.py` validates params PERMISSIVELY on purpose - a vocabulary that
 *  gains a field must not make every saved flow unopenable - so a graph can
 *  arrive carrying a value this option list does not contain. A control whose
 *  value matches no option would display the FIRST one instead, showing a
 *  setting the flow does not hold and writing it in on the next edit. Carrying
 *  the stored value as an extra option shows what is actually saved. Nothing is
 *  refused, corrected or disabled. */
export function selectOptions(
  options: readonly string[] | undefined, value: string,
): readonly string[] {
  const list = options ?? [];
  return list.indexOf(value) >= 0 ? list : [value, ...list];
}

/** True when this field's parameter is a NUMBER as far as the store is
 *  concerned.
 *
 *  `flowsSetParam` coerces by the type of the DEFAULT (`flowsSlice.ts:264`):
 *  a numeric default makes the field numeric and reverts unparseable input, a
 *  string default passes text through. That is why CAPTURE LOOP's `bin` is the
 *  string "1" and not the number 1. The control choice has to key off the same
 *  fact or a select would start committing numbers. */
export function fieldIsNumeric(type: FlowNodeType, key: string): boolean {
  return typeof NODE_DEFS[type]?.params?.[key] === "number";
}

/** The node's current value for a numeric field, as a finite number.
 *
 *  A saved graph can carry a string (or nothing at all) in a numeric slot, and
 *  `NumberField` renders `String(value)` - so `NaN` would land in the box and
 *  the first blur would commit the shipped default over it silently. Falling
 *  back to the vocabulary's own default shows the number the run would actually
 *  use. */
export function numericValue(
  type: FlowNodeType, key: string, value: string | number | undefined,
): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const raw = value == null ? "" : String(value).trim();
  const parsed = raw === "" ? NaN : Number(raw);
  if (Number.isFinite(parsed)) return parsed;
  const base = NODE_DEFS[type]?.params?.[key];
  return typeof base === "number" ? base : 0;
}

/** How wide a unit string may be before it stops fitting inside the number box.
 *
 *  `.nx-numfield-unit` sits inside the input's right padding (52 px), so
 *  "frames" fits and "h (0 = none)" does not - and that one really does have to
 *  be legible, because "0 hours" otherwise reads as "shoot nothing" rather than
 *  "no integration goal". A unit too long for the box becomes the field's hint
 *  line instead of being clipped. */
export const UNIT_INLINE_MAX = 6;

/** `[unit for the box, hint under the box]` for one field. */
export function splitUnit(unit: string | undefined): [string | undefined, string | undefined] {
  if (!unit) return [undefined, undefined];
  return unit.length <= UNIT_INLINE_MAX ? [unit, undefined] : [undefined, unit];
}

/** What a screen reader hears for a field whose visible label is an eyebrow
 *  word and whose unit is decorative. */
export function fieldAriaLabel(field: FieldDef): string {
  return field.unit ? `${field.label}, ${field.unit}` : field.label;
}

/** The eight filter colours `next.css` publishes, keyed by the spellings a real
 *  wheel uses.
 *
 *  Matched case-insensitively because the same physical glass is spelled `Ha`,
 *  `HA`, `Oiii`, `OIII`, `S2` and `SII` across wheels - this rig's own wheel
 *  says `Oiii` where the design's token says `OIII`. A slot the table does not
 *  recognise gets NO colour rather than a borrowed one: painting an unknown
 *  filter with L's white would say "luminance" about a slot nobody identified.
 *
 *  Presentation only. Which slots exist, and which are offered, is
 *  `cyclePlanRows.resolveWheel`'s decision and is not re-derived here. */
const FILTER_VAR: Record<string, string> = {
  l: "--nx-filter-L", lum: "--nx-filter-L", luminance: "--nx-filter-L",
  r: "--nx-filter-R", red: "--nx-filter-R",
  g: "--nx-filter-G", green: "--nx-filter-G",
  b: "--nx-filter-B", blue: "--nx-filter-B",
  ha: "--nx-filter-Ha", "h-alpha": "--nx-filter-Ha", halpha: "--nx-filter-Ha",
  oiii: "--nx-filter-OIII", o3: "--nx-filter-OIII",
  sii: "--nx-filter-SII", s2: "--nx-filter-SII",
  osc: "--nx-filter-OSC", rggb: "--nx-filter-OSC",
};

/** `var(--nx-filter-X)` for a wheel slot name, or `null` when the name is not
 *  one of the eight the design has a colour for. */
export function filterInk(name: string): string | null {
  const key = name.trim().toLowerCase();
  const v = FILTER_VAR[key];
  return v ? `var(${v})` : null;
}
