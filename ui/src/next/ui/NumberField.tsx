import { useId, useRef, useState, type ChangeEvent, type FocusEvent, type JSX, type KeyboardEvent } from "react";
import { Field } from "./Field";
import { lockedAttrs, lockedClass } from "./honest";

/** `Number("")` is 0 - a finite, plausible, WRONG value - so a blank field
 *  would read as a real zero and write it to the rig. Verbatim from
 *  `components/settings/SitePanel.tsx:47` and `components/equipment/
 *  RotatorCard.tsx:36`, which is where this defect was closed the first time. */
const toNum = (raw: string): number => (raw.trim() === "" ? NaN : Number(raw));

/** The one numeric input.
 *
 *  RAW-STRING DRAFT (the RotatorCard / SitePanel pattern). The field holds its
 *  OWN text, not a re-rendered parse of the number: a controlled input driven
 *  from `String(Number(text))` wipes a trailing "." or a lone leading "-" on
 *  every keystroke, because re-parsing "1." to 1 forces the box back to "1"
 *  before the next digit can be typed. Parse and clamp happen at BLUR and
 *  ENTER, nowhere else.
 *
 *  A value that does not parse (blank, "-", "abc") is REJECTED: `onCommit` is
 *  not called and the field restores the last committed number. It is never
 *  read as 0.
 *
 *  A value outside `min`/`max` is clamped, and the clamped number is written
 *  back into the box, so what the user sees is what was committed.
 *
 *  `onCommit` fires only when the committed number DIFFERS from `value`. Twenty
 *  call sites hang a config PATCH off it, and tabbing through a form would
 *  otherwise re-write every field it passed through. */
export function NumberField({
  label, value, onCommit, unit, min, max, step, integer = false, hint, zeroMeans,
  lockedReason = null, onExplain, ariaLabel, className = "", ...rest
}: {
  /** The visible eyebrow on the row. */
  label: string;
  value: number;
  /** Called with the parsed, rounded and clamped number. */
  onCommit: (next: number) => void;
  /** Rendered inside the box, right-aligned ("deg", "min", "px"). Decoration
   *  only - `ariaLabel` has to name the unit itself, or a reader announces a
   *  bare number. */
  unit?: string;
  min?: number;
  max?: number;
  /** How far ArrowUp / ArrowDown move the DRAFT (default 1). Arrows do not
   *  commit; blur and Enter still do, so holding an arrow key cannot fire
   *  twenty writes at the rig. */
  step?: number;
  /** Round to a whole number at commit (frames, minutes, star counts). */
  integer?: boolean;
  hint?: string;
  /** What a zero means in THIS setting ("off", "no limit"). Renders the
   *  design's `0 = off` line, which is the difference between a disabled
   *  feature and one set to nothing. */
  zeroMeans?: string;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
  /** Required: the visible label is an eyebrow word and the unit is decorative,
   *  so the full sentence a reader hears lives here ("Dither size, pixels"). */
  ariaLabel: string;
  className?: string;
  /** Lands on the INPUT, so a test asserts the value the user sees. */
  "data-testid"?: string;
}): JSX.Element {
  const id = useId();
  const [text, setText] = useState<string>(() => String(value));
  // Prop-derived state, synced during render (React's own "adjusting state
  // when a prop changes" idiom) rather than in an effect: an effect would
  // paint one frame of the stale number after a save lands.
  const seen = useRef<number>(value);
  if (seen.current !== value) {
    seen.current = value;
    setText(String(value));
  }

  const commit = (raw: string) => {
    const parsed = toNum(raw);
    if (!Number.isFinite(parsed)) {
      // Rejected. Restore the last committed number - NEVER commit a 0 the
      // user did not type.
      setText(String(value));
      return;
    }
    let next = integer ? Math.round(parsed) : parsed;
    if (min != null) next = Math.max(min, next);
    if (max != null) next = Math.min(max, next);
    setText(String(next));
    if (next !== value) onCommit(next);
  };

  const nudge = (dir: 1 | -1) => {
    const from = Number.isFinite(toNum(text)) ? toNum(text) : value;
    let next = from + dir * (step ?? 1);
    if (min != null) next = Math.max(min, next);
    if (max != null) next = Math.min(max, next);
    // Float steps leave 0.30000000000000004 behind; the draft is text, so trim
    // it here rather than teaching every call site to format.
    setText(String(integer ? Math.round(next) : Number(next.toFixed(6))));
  };

  const rangeHint = min != null && max != null ? `${min} to ${max}`
    : min != null ? `${min} or more`
      : max != null ? `${max} or less`
        : null;
  const hintText = [hint, rangeHint, zeroMeans ? `0 = ${zeroMeans}` : null]
    .filter((s): s is string => !!s).join(" · ");

  return (
    <Field label={label} hint={hintText || undefined} htmlFor={id} className={className}>
      <div className="nx-numfield">
        <input
          id={id}
          type="text"
          inputMode={integer ? "numeric" : "decimal"}
          className={lockedClass(lockedReason, "nx-input")}
          data-mono="true"
          data-unit={unit ? "true" : undefined}
          value={text}
          aria-label={ariaLabel}
          readOnly={!!lockedReason}
          onChange={(e: ChangeEvent<HTMLInputElement>) => { if (!lockedReason) setText(e.target.value); }}
          onBlur={(e: FocusEvent<HTMLInputElement>) => { if (!lockedReason) commit(e.target.value); }}
          onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
            if (lockedReason) return;
            if (e.key === "Enter") { commit((e.target as HTMLInputElement).value); return; }
            if (e.key === "ArrowUp") { e.preventDefault(); nudge(1); return; }
            if (e.key === "ArrowDown") { e.preventDefault(); nudge(-1); }
          }}
          // A locked field is read-only, not `disabled`: the number stays
          // selectable and copyable. Tapping it says why it cannot be typed in,
          // which is the one thing a greyed-out box never does.
          onClick={() => { if (lockedReason) onExplain?.(lockedReason); }}
          data-testid={rest["data-testid"]}
          {...lockedAttrs(lockedReason)}
        />
        {unit != null && <span className="nx-numfield-unit" aria-hidden="true">{unit}</span>}
      </div>
    </Field>
  );
}
