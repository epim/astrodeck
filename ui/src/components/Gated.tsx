// Gated.tsx — the ONE shared capability-gate wrapper for control surfaces (W2.5).
//
// VIEWER-READ-ONLY model: a control the caller lacks the capability for is
// HIDDEN or DISABLED in the UI — it is NEVER rendered live and then 403'd on tap.
// View surfaces (status, live preview, guiding graph, what-is-imaging) are NOT
// gated and stay fully visible for everyone; only the *write*/*motion*/*power*
// affordances route through here.
//
// Default open behavior is preserved: under the `none` auth provider the server
// resolves every caller to admin + ALL caps (lib/caps.useCan), so on the LAN
// tablet every <Gated> renders its children unchanged — no visible difference.
//
// Two usage shapes, both reading the SAME useCan(cap) hook so the gate decision
// is consistent everywhere:
//
//   1) Wrapper (hide, the default) — drop a whole control/panel behind a cap:
//        <Gated cap="control.mount"><SlewPad /></Gated>
//      Renders nothing when denied (optionally a passive read-only `fallback`).
//
//   2) Wrapper (disable) — keep the control visible but inert (greyed), e.g. when
//      hiding it would leave a confusing hole in a dense grid:
//        <Gated cap="control.capture" mode="disable"><CaptureButtons/></Gated>
//      Denied → children render inside a non-interactive, dimmed, aria-disabled
//      shell (pointer-events:none) so a viewer sees the control but can't fire it.
//
//   For a single button it's usually cleaner to read the hook directly and pass
//   `disabled={!can}` — that path needs no wrapper. Use <Gated> for groups.

import type { ReactNode } from "react";
import type { Capability } from "../types";
import { useCan } from "../lib/caps";

export function Gated({
  cap,
  mode = "hide",
  fallback = null,
  children,
}: {
  cap: Capability;
  /** "hide" (default) renders nothing when denied; "disable" renders the children
   *  inert + dimmed so the affordance is visible but cannot be triggered. */
  mode?: "hide" | "disable";
  /** Optional passive read-only stand-in shown to a denied caller in "hide" mode
   *  (e.g. an "operator can…" note). Ignored in "disable" mode. */
  fallback?: ReactNode;
  children: ReactNode;
}) {
  const allowed = useCan(cap);
  if (allowed) return <>{children}</>;

  if (mode === "disable") {
    // Visible but inert. pointer-events:none blocks taps; aria-disabled +
    // inert-ish opacity communicate the read-only state. Buttons inside also get
    // a real `disabled` where the surface threads `useCan` into them, but this is
    // the belt-and-suspenders shell so a stray live control can't be fired.
    return (
      <div
        className="opacity-40 pointer-events-none select-none"
        aria-disabled
        data-readonly="viewer"
      >
        {children}
      </div>
    );
  }

  return <>{fallback}</>;
}

export default Gated;
