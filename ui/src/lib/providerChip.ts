// providerChip.ts — which visual treatment a resolved provider gets.
//
// Lives in lib/ rather than beside the component for one reason: the component
// imports the store, the store imports the API client, and the API client reads
// `window` at module scope. A test that wants to prove the four kinds map to
// four DISTINCT treatments should not have to boot a browser to do it — and
// that proof is worth having, because these four kinds used to collapse into
// two classes, two of which were byte-identical CSS rules. "TPPA · Simulator"
// and "TPPA · AstroDeck native" were the same chip with a different word in it,
// which is how a profile-pinned simulator drove the polar aligner for twelve
// days on a screen where this chip is the only always-visible signal.
//
// The type import is `import type`, so nothing at runtime is pulled in with it.
import type { ResolvedProviderKind } from "../store";

/**
 * kind → the `.prov*` modifier class.
 *
 * The distinctions are carried by BORDER STYLE and DOT SHAPE (see CSS-PROV in
 * index.css), never by hue: night mode collapses the palette toward coral, and
 * the pair that mattered was the same colour to begin with.
 *
 *   ""          solid border + solid dot     — AstroDeck native
 *   prov-sim    dashed border + hollow dot   — the built-in simulator
 *   prov-ext    solid muted border           — NINA / Alpaca / local ASTAP
 *   prov-na     dotted border + square dot   — unavailable, or not yet resolved
 *
 * `astap` deliberately shares `prov-ext` with `backend`: both are "an external
 * tool is doing this", which is the distinction the chip is making. It is the
 * only intentional merge, and it is asserted as intentional in the tests.
 */
export function provVariant(kind: ResolvedProviderKind | "pending"): string {
  if (kind === "astrodeck") return "";
  if (kind === "sim") return " prov-sim";
  if (kind === "backend" || kind === "astap") return " prov-ext";
  return " prov-na";
}
