import { useProviders, type ResolvedProviderKind } from "../store";

/* ============================================================ UI-PROVIDERBADGE
   The native-parity signal (implementation brief §2). Renders the reference's
   `.prov` chip — "AF · AstroDeck native" / "TPPA · NINA" — bound to the REAL
   per-capability resolution that hub.poll_status attaches as `status.providers`
   (server/astrodeck/providers.py resolve_all → {kind,label,reason}).

     kind "astrodeck" → accent-filled `.prov`     (our native / simulator engine)
     kind "sim"       → accent-filled `.prov`     (the built-in simulator)
     kind "backend"   → muted `.prov-ext`         (the connected backend: NINA/Alpaca)
     kind "astap"     → muted `.prov-ext`         (the local ASTAP binary)
     kind "unavailable" → faint `.prov-na`        (nothing can run it; reason says why)
     no data yet      → faint `.prov-na` "…" slot (stable header until the first poll)

   Shape+text carry the meaning (chip fill is the SECONDARY cue) so it survives
   night mode and color-blindness. `reason` is the hover title. All colors come
   from tokens via the `.prov*` classes — no hardcoded hex here. */

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
  const meta = CAP_META[cap];
  const choice = providers?.[cap];

  // "pending" = pre-first-poll; hold a stable muted slot so the panel header does
  // not reflow when the badge resolves a beat later.
  const kind: ResolvedProviderKind | "pending" = choice?.kind ?? "pending";
  const label = choice?.label ?? "…";

  const variant =
    kind === "astrodeck" || kind === "sim"
      ? ""
      : kind === "backend" || kind === "astap"
        ? " prov-ext"
        : " prov-na";

  const a11y =
    kind === "pending"
      ? `${meta.full} provider resolving`
      : kind === "unavailable"
        ? `${meta.full} provider unavailable`
        : `${meta.full} provider: ${label}${kind === "astrodeck" ? " — AstroDeck native" : ""}`;

  return (
    <span
      className={`prov${variant}${className ? ` ${className}` : ""}`}
      title={choice?.reason}
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
