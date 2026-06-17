// RoleBadge.tsx — the unobtrusive "current role" indicator (W2.5).
//
// Sits in the header next to the backend chip. It is INTENTIONALLY quiet:
//   - admin under the default `none` provider is the LAN norm, so we render
//     NOTHING for admin (no chip clutters the live tablet — the UI is unchanged).
//   - operator / viewer get a small bordered chip; viewer additionally shows an
//     eye glyph + "VIEW ONLY" so the read-only state reads by SHAPE + TEXT (the
//     night palette collapses color, so we never lean on hue — design-system §8).
//
// A narrow-selector child (reads only the principal role), so mounting it doesn't
// widen the header's store subscription.

import { Icon } from "./icons";
import { usePrincipalRole } from "../lib/caps";

export default function RoleBadge() {
  const role = usePrincipalRole();

  // admin == the default open LAN posture; stay silent so nothing changes there.
  if (role === "admin") return null;

  const viewer = role === "viewer";
  return (
    <span
      className={`hidden sm:inline-flex items-center gap-1 px-2 py-0.5 border text-[9px]
        tracking-[0.18em] uppercase font-display font-medium
        ${viewer ? "border-warn/60 text-warn" : "border-line2 text-dim"}`}
      title={
        viewer
          ? "You are signed in as a viewer — controls are read-only."
          : "You are signed in as an operator."
      }
    >
      <Icon name={viewer ? "eye" : "user"} size={11} />
      {viewer ? "VIEW ONLY" : "OPERATOR"}
    </span>
  );
}
