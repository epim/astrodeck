// lib/connection.ts — PURE decision logic for the ConnectionBanner (H1 §2e).
// Extracted from ConnectionBanner.tsx so the "which strip to show" rule is
// unit-testable in isolation (idiom: npx tsx src/lib/__tests__/connection.test.ts)
// and the component and the test share ONE source of truth.
//
// The load-bearing distinction (H1): a WS that is DOWN because the upgrade was
// rejected for lack of a session ("sign-in required") must read differently from
// a WS that is DOWN because the server is unreachable ("link down"). A browser
// cannot read the HTTP status of a failed WebSocket handshake, so the caller
// derives `authRequired` from a fetch (/api/me → 401) and passes it in here.

import type { AuthMethods, Principal, WsPhase } from "../types";

export type BannerKind = "hidden" | "signin" | "down" | "stale" | "norig";

export interface BannerInputs {
  phase: WsPhase;
  stale: boolean;
  equipConnected: boolean;
  view: string;
  /** Reachable-but-unauthenticated under an enabled sign-in method: the server
   *  answered /api/me with 401 (or identity is still resolving). Distinguishes
   *  "sign in to reconnect" from a genuine "server unreachable" link drop. */
  authRequired: boolean;
}

/** Pure decision for the ConnectionBanner. Priority order:
 *  sign-in required > link down > telemetry stale > no-rig note > hidden.
 *
 *  `signin` takes precedence over a bare `down`: the socket IS down (the upgrade
 *  was rejected because no session rides it), but the server is reachable and the
 *  honest ask is "sign in", not "the display disconnected". In normal flow the
 *  app routes to the full Login screen before this strip is ever mounted; this
 *  state is the fail-open-after-grace fallback where identity never resolved. */
export function bannerState(i: BannerInputs): BannerKind {
  const down = i.phase !== "up";
  if (down && i.authRequired) return "signin";
  if (down) return "down";
  if (i.stale) return "stale";
  // Quiet no-rig note only when the link is healthy AND we're not already on the
  // Rig page (where connecting is the whole point — a banner there is redundant).
  if (!i.equipConnected && i.view !== "connect") return "norig";
  return "hidden";
}

/** True while the socket is (re)establishing — drives the amber-blink vs
 *  alarm-blink and the CONNECTING… vs DISPLAY DISCONNECTED wording. */
export function isConnecting(phase: WsPhase): boolean {
  return phase === "connecting" || phase === "reconnecting";
}

// ---------------------------------------------------------------- auth-required
// "Reachable but not signed in under an enabled method" — the H1 signal the
// ConnectionBanner uses (as `authRequired`) to say "sign-in required" instead of
// "DISPLAY DISCONNECTED" when the WS upgrade was rejected for lack of a session.
// Lives here (store-free, type-only imports) so it is unit-testable alongside
// bannerState; caps.ts wraps it in the reactive hook. It DIFFERS from
// shouldShowLogin in ONE case: a still-resolving principal (null) counts as
// required here, so the banner reads honestly during the /api/me fetch. A
// genuinely signed-in user (email, an elevated role, or any caps) is NOT
// required — so if THEIR server goes unreachable the banner still reads "link
// down", not "sign-in required".
export function authRequiredForBanner(
  authMethods: AuthMethods | null,
  principal: Principal | null,
): boolean {
  if (!authMethods) return false;
  const methods = authMethods.methods ?? [];
  if (methods.length === 0) return false; // open LAN — never "sign-in required"
  if (authMethods.first_run) return true; // first admin still needs creating
  if (!principal) return true; // identity still resolving → assume gated
  if (principal.email) return false;
  if (principal.role === "admin" || principal.role === "operator") return false;
  if ((principal.caps?.length ?? 0) > 0) return false;
  return true; // anonymous fail-closed viewer sentinel under an enabled method
}
