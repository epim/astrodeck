import type { JSX, ReactNode } from "react";

/** The notification banner under the header, max two at a time. Green is good
 *  news, cyan is tonight's information, amber is an incident happening while
 *  the user is on another hub. The whole text is the CTA; the x is separate so
 *  a dismiss cannot be mistaken for a tap-through. */
export function BannerCard({ tone, text, cta, onDismiss, className = "", ...rest }: {
  tone: "good" | "info" | "warn";
  text: ReactNode;
  cta?: { label: string; onPress: () => void };
  onDismiss?: () => void;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  return (
    <div className={`nx-banner ${className}`.trim()} data-tone={tone} data-testid={rest["data-testid"]}>
      <span className="nx-banner-dot" aria-hidden="true" />
      {cta ? (
        <button type="button" className="nx-banner-text" onClick={cta.onPress}>
          {text} <span className="nx-banner-cta">{cta.label} &rsaquo;</span>
        </button>
      ) : (
        <span className="nx-banner-text" data-static="true">{text}</span>
      )}
      {onDismiss && (
        <button type="button" className="nx-banner-x" aria-label="Dismiss this notice" onClick={onDismiss}>
          &times;
        </button>
      )}
    </div>
  );
}
