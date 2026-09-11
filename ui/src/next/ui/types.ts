// next/ui/types.ts - the shared vocabulary of the primitives library.
//
// Deliberately structural, not imported from `next/lib/*`: the primitives may
// not depend on the pure libs (T0.3 owns those), and a value produced by
// `next/lib/incidents.ts` must still assign to `IncidentCard`'s prop. Keeping
// the fields as widened `string`s here makes that direction of assignment work
// without either module importing the other.

/** ARCHITECTURE.md section 6. `accent2` is the purple (`--accent-dim`). */
export type Tone = "accent" | "accent2" | "good" | "warn" | "bad" | "info" | "dim";

/** Alias for call sites that already import a different `Tone` (`src/types.ts`
 *  has one with three members). */
export type NxTone = Tone;

export interface IncidentAction {
  id: string;
  label: string;
  primary?: boolean;
  /** `Capability` in `lib/caps.ts`; widened so no import is needed here. */
  cap?: string;
}

/** Mirror of `next/lib/incidents.ts`'s `Incident`. Fields widened so that the
 *  lib's narrower unions (`IncidentKind`, the pill union) assign to it. */
export interface Incident {
  kind: string;
  pill: string;
  /** A colour VALUE, not a token name - the engine picks warn/bad/dim per kind
   *  and the card paints its border, glow and primary action with it. */
  color: string;
  title: string;
  sinceMs: number | null;
  resolvesItself: boolean;
  engine: string;
  next: string;
  actions: IncidentAction[];
}
