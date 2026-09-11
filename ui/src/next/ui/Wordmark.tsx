import type { JSX } from "react";

/** ASTRO<accent>DECK</accent> - Chakra Petch 600, 11 px, .22em tracking. */
export function Wordmark({ className = "" }: { className?: string }): JSX.Element {
  return (
    <span className={`nx-wordmark ${className}`.trim()}>
      ASTRO<span className="nx-wordmark-deck">DECK</span>
    </span>
  );
}
