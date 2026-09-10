import { useEffect, useRef, useState, type JSX, type ReactNode } from "react";
import { lockedAttrs, lockedClass } from "./honest";

export interface ActionButtonProps {
  /** `warn` is the amber CTA the design uses for a hold the user can act on
   *  (the CLOUDED card's own button) - it is not a refusal like `danger`, it is
   *  an action taken while something is wrong. */
  kind: "primary" | "secondary" | "ghost" | "danger" | "purple" | "warn";
  /** 44 / 52 / 56 px. `md` is the hit-target floor; `xl` is the lock card's
   *  IMAGE THIS. */
  size?: "md" | "lg" | "xl";
  glyph?: ReactNode;
  children: ReactNode;
  onPress: () => void;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
  /** Two-tap arm. The FIRST press swaps the label for `arm.label` and starts a
   *  window (3000 ms unless told otherwise); a second press inside the window
   *  fires. The window lapsing disarms - never fires by itself. */
  arm?: { label: string; ms?: number };
  /** Visual + `aria-busy` only. It deliberately does NOT block the press: a
   *  control that silently swallows a tap is the defect this library exists to
   *  remove. Pass `lockedReason` when the press must actually be refused. */
  busy?: boolean;
  full?: boolean;
  ariaLabel?: string;
  className?: string;
  "data-testid"?: string;
}

const ARM_MS = 3000;

/** The design's button in six kinds. Primary carries the accent fill and the
 *  `0 0 18px` accent glow; danger is the STOP row; purple is the flows/campaign
 *  family; warn is the amber CLOUDED CTA. */
export function ActionButton({
  kind, size = "md", glyph, children, onPress,
  lockedReason = null, onExplain, arm, busy = false, full = false,
  ariaLabel, className = "", ...rest
}: ActionButtonProps): JSX.Element {
  const [armed, setArmed] = useState(false);
  // `ReturnType<typeof setTimeout>`, not `number`: @types/node rides in on
  // @types/jsdom, so the DOM overload is not the one that wins here.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clear = () => {
    if (timer.current != null) { clearTimeout(timer.current); timer.current = null; }
  };
  // A pending arm window must not outlive the button; without this the timer
  // fires setState on an unmounted tree every time a sheet closes mid-arm.
  useEffect(() => clear, []);

  const press = () => {
    if (lockedReason) { onExplain?.(lockedReason); return; }
    if (arm && !armed) {
      setArmed(true);
      clear();
      timer.current = setTimeout(() => { timer.current = null; setArmed(false); }, arm.ms ?? ARM_MS);
      return;
    }
    clear();
    setArmed(false);
    onPress();
  };

  return (
    <button
      type="button"
      className={lockedClass(lockedReason, `nx-btn ${className}`.trim())}
      data-kind={kind}
      data-size={size}
      data-full={full ? "true" : undefined}
      data-armed={arm ? (armed ? "true" : "false") : undefined}
      data-busy={busy ? "true" : undefined}
      aria-busy={busy || undefined}
      aria-label={ariaLabel}
      onClick={press}
      data-testid={rest["data-testid"]}
      {...lockedAttrs(lockedReason)}
    >
      {busy && <span className="nx-btn-spin" aria-hidden="true" />}
      {glyph != null && <span className="nx-btn-glyph">{glyph}</span>}
      <span className="nx-btn-label">{armed && arm ? arm.label : children}</span>
    </button>
  );
}
