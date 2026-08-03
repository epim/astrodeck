import { useConfig, useProviders, type ResolvedProviderKind } from "../store";
import { entryOf, isProfileOverride, overrideProfileName, providerKey } from "../lib/effective";
import { provVariant } from "../lib/providerChip";

/* ============================================================ UI-PROVIDERBADGE
   The native-parity signal (implementation brief §2). Renders the reference's
   `.prov` chip — "AF · AstroDeck native" / "TPPA · NINA" — bound to the REAL
   per-capability resolution that hub.poll_status attaches as `status.providers`
   (server/astrodeck/providers.py resolve_all → {kind,label,reason}).

     kind "astrodeck" → accent `.prov`, solid border + solid dot  (our engine)
     kind "sim"       → accent `.prov-sim`, DASHED border + HOLLOW dot
     kind "backend"   → muted `.prov-ext`, solid border + solid dot (NINA/Alpaca)
     kind "astap"     → muted `.prov-ext`                          (local ASTAP)
     kind "unavailable" → faint `.prov-na`, DOTTED border + square dot
     no data yet      → faint `.prov-na` "…" slot (stable header until first poll)

   #129: "Simulator" and "AstroDeck native" used to collapse into the SAME class,
   and `.prov-ext`/`.prov-na` were byte-identical rules — so a simulated polar
   aligner looked exactly like a real one, and "Unavailable" looked exactly like
   "NINA". One word was the entire channel. That pairing is what hid a
   profile-pinned simulator for twelve days on a screen where this badge is the
   only always-visible signal (PolarView's phase panel does not exist until you
   press Start). Each kind now has its own BORDER STYLE and DOT SHAPE, which
   survive night mode, a red filter, greyscale and colour-blindness.

   The trailing `*` marks a capability whose provider was chosen by the ACTIVE
   PROFILE rather than by global config or by the resolver — the override that
   previously had no tell anywhere in the product. It is a footnote marker, and
   the footnote is the permanent sentence under the matching Tasks row; the
   accessible name carries the whole thing for anyone who cannot see the chip.
   A word-length marker was measured against the phone layout and rejected: this
   chip shares the Polar panel header with the state readout at 320px. */

type Cap = "autofocus" | "polar_align" | "solve" | "guide";

const CAP_META: Record<Cap, { abbr: string; full: string }> = {
  autofocus: { abbr: "AF", full: "Autofocus" },
  polar_align: { abbr: "TPPA", full: "Polar alignment" },
  solve: { abbr: "SOLVE", full: "Plate solving" },
  guide: { abbr: "GUIDE", full: "Autoguiding" },
};

export function ProviderBadge({ cap, className = "" }: {
  cap: Cap;
  className?: string;
}) {
  const providers = useProviders();
  const config = useConfig();
  const meta = CAP_META[cap];
  const choice = providers?.[cap];

  // "pending" = pre-first-poll; hold a stable muted slot so the panel header does
  // not reflow when the badge resolves a beat later.
  const kind: ResolvedProviderKind | "pending" = choice?.kind ?? "pending";
  const label = choice?.label ?? "…";

  // Which LAYER picked this provider. Read from /api/config's provenance block,
  // not inferred from the resolved kind — a profile can pin the same value
  // global config holds, and the two are indistinguishable by value alone.
  const entry = entryOf(config, providerKey(cap));
  const pinned = isProfileOverride(entry);
  const pinnedBy = overrideProfileName(entry);

  const variant = provVariant(kind);

  const pinNote = pinned
    ? ` — pinned by profile ${pinnedBy ?? "(unnamed)"}, not by the global setting`
    : "";
  const a11y =
    kind === "pending"
      ? `${meta.full} provider resolving`
      : kind === "unavailable"
        ? `${meta.full} provider unavailable${pinNote}`
        : `${meta.full} provider: ${label}${
            kind === "astrodeck" ? " — AstroDeck native" : ""
          }${kind === "sim" ? " — SIMULATED, not a real device" : ""}${pinNote}`;

  // `title` is a bonus on desktop only — this product runs on a tablet where
  // hover does not exist, so it never carries anything the panels do not also
  // say in permanent text.
  const title = [choice?.reason, entry?.reason].filter(Boolean).join("\n");

  return (
    <span
      className={`prov${variant}${pinned ? " prov-pin" : ""}${className ? ` ${className}` : ""}`}
      title={title || undefined}
      role="img"
      aria-label={a11y}
    >
      <span className="dot" aria-hidden />
      <span aria-hidden>
        {meta.abbr} · {label}
      </span>
    </span>
  );
}

export default ProviderBadge;
