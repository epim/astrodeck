// Logo.tsx — AstroDeck mark as an inline SVG (no external asset). Owned by the
// SHARED lane; feature lanes consume it (the live-preview empty-state fallback,
// header/brand slots). Tasteful for a dark observatory UI: a stylized telescope
// tube aimed up, framed by a reticle ring, with an accent star.
//
// THEMING: the structure (tube, tripod, ring) strokes with `currentColor`, so it
// inherits the surrounding text color and works in both day (cyan ink) and night
// (red) palettes. The star + reticle pip use var(--accent) for a single on-brand
// pop; the faint outer ticks use var(--text-faint) so they read as decoration.
// Pass a color via the parent's text color (e.g. className="text-dim") to tune it.
import type { JSX } from "react";

export default function Logo({ size = 64, className = "" }: {
  size?: number;
  className?: string;
}): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      role="img"
      aria-label="AstroDeck"
      focusable="false"
      className={`shrink-0 ${className}`}
    >
      <title>AstroDeck</title>

      {/* reticle ring — frames the mark, reads as "point at the sky" */}
      <circle cx="32" cy="32" r="27" stroke="currentColor" strokeOpacity={0.55} />
      {/* faint outer alignment ticks (decoration only) */}
      <g stroke="var(--text-faint)" strokeWidth={1.5}>
        <path d="M32 3.5v4M32 56.5v4M3.5 32h4M56.5 32h4" />
      </g>

      {/* telescope tube — aimed up-right toward the star */}
      <g strokeWidth={2.4}>
        {/* tube body */}
        <path d="M20 44 L41 23" />
        <path d="M16 40 L37 19" />
        {/* objective (wide) end */}
        <path d="M37 19 L41 23" />
        {/* eyepiece (narrow) end */}
        <path d="M16 40 L20 44" />
      </g>

      {/* tripod / mount under the tube */}
      <g stroke="currentColor" strokeWidth={2} strokeOpacity={0.85}>
        <path d="M18 42 L14 54M18 42 L24 52" />
        <path d="M12 54h16" />
      </g>

      {/* accent star — the target the scope points at */}
      <g stroke="var(--accent)" strokeWidth={2.2}>
        <path d="M47 17 L47 11M47 17 L47 23M47 17 L41 17M47 17 L53 17" />
      </g>
      <circle cx="47" cy="17" r="1.6" fill="var(--accent)" stroke="none" />

      {/* reticle center pip */}
      <circle cx="32" cy="32" r="1.6" fill="var(--accent)" stroke="none" />
    </svg>
  );
}
