// ReadOnlyBadge.tsx — a small "VIEW ONLY" pill surfaced next to a control panel's
// title when the viewer lacks the capability to operate it (W2.5).
//
// The point: a viewer's controls are HIDDEN or DISABLED (never 403-on-tap), but a
// silently-missing button is confusing. This pill explains WHY — "you're a viewer,
// these are read-only" — so the read-only state is legible, by glyph + text (the
// night palette collapses color, so we don't rely on hue). Render it in a Panel's
// `right` slot, gated on `!can`:
//
//   <Panel title="Power Outputs" right={!canControl && <ReadOnlyBadge />}>
//
// `reason` overrides the default tooltip (e.g. "operator role required").

import { Icon } from "./icons";

export default function ReadOnlyBadge({
  reason = "Read-only — your role can view this but not control it.",
  label = "View only",
}: {
  reason?: string;
  label?: string;
}) {
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[9px] tracking-[0.18em]
        uppercase border border-warn/60 text-warn font-display font-medium"
      title={reason}
      role="note"
    >
      <Icon name="eye" size={11} />
      {label}
    </span>
  );
}
