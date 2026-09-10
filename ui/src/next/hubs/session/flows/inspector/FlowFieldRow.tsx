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

import { useId, type JSX } from "react";

import { useStore } from "../../../../../store";
import type { FieldDef } from "../../../../../components/flows/nodeDefs";
import type { FlowNodeType } from "../../../../../components/flows/flowsTypes";
import { Dial, Field, Label, NumberField, Segmented, TextInput } from "../../../../ui";
import { FlowCyclePlanRows } from "./FlowCyclePlanRows";
import {
  SEGMENTED_MAX, fieldAriaLabel, fieldIsNumeric, numericValue, selectOptions, splitUnit,
} from "./fieldModel";

/** The 284 px desktop column vs the sheet body. The sheet is the surface driven
 *  at the scope, so its rows carry the 44 px touch floor. */
export type FlowFieldVariant = "column" | "sheet";

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

  return (
    <div
      className="nx-flowfield"
      data-variant={variant}
      data-control={field.control}
      data-testid={`flow-field-${field.key}`}
    >
      {control()}
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
        return (
          <Dial<string>
            label={field.label}
            hint={field.unit ?? "drag or tap"}
            options={opts.map((o) => ({ value: o, label: o }))}
            value={stored}
            onChange={write}
            lockedReason={lockedReason}
            onExplain={onExplain}
          />
        );
      }
      return (
        <>
          <Label size={11}>{field.label}</Label>
          {/* A closed list can be wider than a 284 px column. It scrolls rather
              than truncating: a clipped option is a setting nobody can reach. */}
          <div className="nx-flowfield-scroll">
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
      return (
        <NumberField
          label={field.label}
          value={numericValue(nodeType, field.key, value)}
          onCommit={(next) => write(String(next))}
          unit={unit}
          hint={hint}
          ariaLabel={aria}
          lockedReason={lockedReason}
          onExplain={onExplain}
        />
      );
    }

    return (
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
      </Field>
    );
  }
}

export default FlowFieldRow;
