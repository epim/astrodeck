// NumberField.tsx — the plan editor's numeric text field.
//
// WHY THIS EXISTS (UX review 2026-07-28, rank 1 of the whole round).
// Every number in the plan editor — exposure, gain, frame count, dither
// cadence, refocus cadence, meridian lead, the quality gates and the sensor
// setpoint — went through one helper in SequenceView:
//
//     const num = (v, fallback) => Number.isFinite(Number(v)) && v !== "" ? n : fallback;
//     <input value={s.exposure_s} onChange={e => patch({ exposure_s: num(e.target.value, s.exposure_s) })} />
//
// A controlled input whose parser returns the PREVIOUS value for an empty
// string can never be emptied: the keystroke that clears it re-renders it
// holding its old contents, with the caret at the end. So every edit
// CONCATENATES onto what was already there. Measured on a phone, typing
// character by character (a single fill() does not show it):
//
//     120 -> Backspace x4 -> "12", "1", "1", "1"  -> type "300" -> 1300
//     cooling setpoint: type "-10" into an empty field -> -1010,
//                       then edit to "-20"            -> -120
//
// -120 C is not a temperature any astro camera can reach, 1300 s is not the
// 300 s sub that was asked for, and neither errors: the run starts, reports
// success, and delivers a night of plausible-looking, unusable data.
//
// THE FIX is the house pattern from CaptureView (the exposure/gain fields
// there hold a STRING and validate at render). The wrinkle here is that the
// plan is not local state — it is the store's persisted SSOT — so the string
// cannot BE the model. Instead the model stays numeric and this field keeps a
// DRAFT of the text while the user is editing it:
//
//   * every keystroke that parses to a finite number commits that number;
//   * a keystroke that does not ("", "-", "1.", "1e") holds the last committed
//     number and leaves the text alone, so the box can be emptied, a minus can
//     be typed before its digits, and a decimal point survives;
//   * blur drops the draft, so the field snaps back to the model's canonical
//     text — which is also how a caller's clamp (count >= 1, cadence >= 0)
//     becomes visible without fighting the user mid-word.
//
// The draft is what makes clamping non-destructive. Committing on each
// keystroke (rather than on blur) is deliberate: the totals, the sub-length
// advisory and the pre-flight strip all read the plan live, and a plan editor
// whose numbers only land on blur reports last-keystroke totals.

import { useState, type JSX } from "react";

/** What one keystroke's worth of text means for the numeric model. */
export type DraftReading =
  | { kind: "hold" }                   // in-progress text — keep the model as it is
  | { kind: "commit"; value: number }  // a real number — write it
  | { kind: "clear" };                 // emptied, on a field where empty IS a value

/** Pure reading of a raw field value. `emptyIsAValue` is true only for fields
 *  where blank means something (the cooling setpoint's "off" => null);
 *  everywhere else blank is a moment mid-edit, not an instruction.
 *
 *  Note the explicit blank test: `Number("")` is 0, not NaN, so a
 *  finite-check alone would silently write a zero exposure the instant the
 *  field is cleared. */
export function readNumberDraft(raw: string, emptyIsAValue: boolean): DraftReading {
  if (raw.trim() === "") return emptyIsAValue ? { kind: "clear" } : { kind: "hold" };
  const n = Number(raw);
  return Number.isFinite(n) ? { kind: "commit", value: n } : { kind: "hold" };
}

export function NumberField({
  value,
  onCommit,
  onEmpty,
  label,
  className = "",
  inputMode = "numeric",
  placeholder,
  title,
  invalid = false,
  readOnly = false,
  ariaDisabled = false,
}: {
  /** The model number. `null`/`undefined` render as an empty box. */
  value: number | null | undefined;
  /** Called with the parsed number for every keystroke that yields one. */
  onCommit: (n: number) => void;
  /** Supplying this makes blank a legal value (and is what gets called for it).
   *  Omit it and the field simply holds its number while the text is blank. */
  onEmpty?: () => void;
  /** Accessible name — every one of these fields is a bare box in a dense row. */
  label: string;
  className?: string;
  inputMode?: "numeric" | "decimal";
  placeholder?: string;
  title?: string;
  invalid?: boolean;
  readOnly?: boolean;
  ariaDisabled?: boolean;
}): JSX.Element {
  const [draft, setDraft] = useState<string | null>(null);
  const committed = value == null ? "" : String(value);
  return (
    <input
      className={["field", invalid && "border-bad", className].filter(Boolean).join(" ")}
      inputMode={inputMode}
      aria-label={label}
      title={title}
      placeholder={placeholder}
      aria-invalid={invalid || undefined}
      readOnly={readOnly}
      aria-readonly={readOnly || undefined}
      aria-disabled={ariaDisabled || undefined}
      value={draft ?? committed}
      onChange={(e) => {
        const raw = e.target.value;
        setDraft(raw);
        const reading = readNumberDraft(raw, !!onEmpty);
        if (reading.kind === "commit") onCommit(reading.value);
        else if (reading.kind === "clear") onEmpty!();
      }}
      onBlur={() => setDraft(null)}
    />
  );
}

export default NumberField;
