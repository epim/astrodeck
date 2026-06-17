// lib/caps.ts — capability-gate hooks (W2.5). FAIL-CLOSED: until the principal
// resolves, treat the caller as a viewer (caps = []), so a control surface never
// flashes enabled before /api/me lands. Under the `none` provider the server
// resolves every caller to admin + ALL caps, so the default open LAN UI is
// unchanged. These read the `principal` store slice and never fetch.

import { useShallow } from "zustand/react/shallow";
import { useStore } from "../store";
import type {
  AuthMethods,
  BackendLink,
  Capability,
  DeviceInfo,
  Principal,
  PrincipalRole,
} from "../types";

// ============================================================================
// PURE decision helpers (no React, no store). The hooks below are thin wrappers
// over these, so the gate logic is unit-testable in isolation (spec §T8) and the
// hook and the test share ONE source of truth. FAIL-CLOSED everywhere: a null /
// unresolved principal is treated as a viewer with no caps.
// ============================================================================

/** Does `principal` hold `cap`? null/unresolved → false (deny, viewer default). */
export function capAllowed(principal: Principal | null, cap: Capability): boolean {
  const caps = principal?.caps;
  if (!caps) return false;
  return caps.includes(cap);
}

/** The resolved role, defaulting to "viewer" when unresolved (fail-closed). */
export function roleOf(principal: Principal | null): PrincipalRole {
  return principal?.role ?? "viewer";
}

/** True when the caller is a viewer OR unresolved (drives the read-only surfaces). */
export function isViewerRole(principal: Principal | null): boolean {
  return roleOf(principal) === "viewer";
}

/** Resolve a role's connected/error tri-state from backend_links, falling back to
 *  the legacy per-device flag, then the sticky equipment flag — so the live tablet
 *  (which never populates backend_links) keeps working unchanged. */
export function resolveRoleConnected(
  role: string,
  links: BackendLink[] | undefined,
  connected: Record<string, DeviceInfo> | undefined,
  equipConnected: boolean,
): { connected: boolean; error: string | null } {
  const link = (links ?? []).find((l) => l.role === role);
  if (link) return { connected: link.connected, error: link.error };
  const dev = connected?.[role];
  if (dev) return { connected: dev.connected, error: null };
  return { connected: equipConnected, error: null };
}

// ----------------------------------------------------------------- view→role map
// The route gate (App.tsx GATED replacement, W2.5) treats a role as "connected"
// from backend_links; this is the VIEW → required device-role lookup it pairs
// with. capture/focus → camera, mount/polar → telescope, guide → guider,
// power → switch. Views not listed here are never equipment-gated.
export const VIEW_REQUIRED_ROLE: Partial<Record<string, string>> = {
  capture: "camera",
  focus: "camera",
  mount: "telescope",
  polar: "telescope",
  guide: "guider",
  power: "switch",
};

// ---------------------------------------------------------------- login gate
// Decide whether App should replace the whole shell with the <Login/> screen.
// HARD non-breaking rule: when NO method is enabled (`methods == []`, today's
// open-LAN default) this is ALWAYS false — no login is ever shown and the UI is
// byte-for-byte unchanged. A login is shown ONLY when a method is enabled AND
// either the store still needs a first admin OR the caller is unauthenticated.
//
// "Unauthenticated under an enabled method" = the fail-closed sentinel the store
// pins on a 401: role "viewer" with no email and no caps. A genuinely signed-in
// viewer carries an email (Google) or is a named local account, so they are NOT
// gated — they see the read-only UI. We fail OPEN while the signal is still
// loading (authMethods == null) so a transport blip never traps the live tablet
// behind a login.
export function shouldShowLogin(
  authMethods: AuthMethods | null,
  principal: Principal | null,
): boolean {
  // Signal not loaded yet → never gate (fail open to today's behavior).
  if (!authMethods) return false;
  const methods = authMethods.methods ?? [];
  // No method enabled → open LAN, NEVER a login (the hard guarantee).
  if (methods.length === 0) return false;
  // A first admin still needs creating → show the setup form.
  if (authMethods.first_run) return true;
  // Principal not resolved yet → wait (don't flash a login over an admin).
  if (!principal) return false;
  // Signed in (any account with an identity, or any elevated role) → no login.
  if (principal.email) return false;
  if (principal.role === "admin" || principal.role === "operator") return false;
  if ((principal.caps?.length ?? 0) > 0) return false;
  // Otherwise: the anonymous fail-closed viewer sentinel under an enabled method.
  return true;
}

/** Reactive form of shouldShowLogin — App subscribes to this to gate the shell. */
export function useShouldShowLogin(): boolean {
  return useStore(
    useShallow((s) => shouldShowLogin(s.authMethods, s.principal)),
  );
}

/** Does the resolved principal hold `cap`? Fail-closed: unresolved → false. */
export function useCapability(cap: Capability): boolean {
  return useStore((s) => capAllowed(s.principal, cap));
}

/** Alias matching the prompt's `useCan(cap)` naming. Identical semantics to
 *  useCapability — read a single capability, fail-closed. */
export const useCan = useCapability;

// Convenience hooks for the control surfaces the gating wiring touches.
export const useCanControlMount = () => useCapability("control.mount");
export const useCanControlCapture = () => useCapability("control.capture");
export const useCanControlGuide = () => useCapability("control.guide");
export const useCanControlPower = () => useCapability("control.power");
export const useCanConfigBackend = () => useCapability("config.backend");
/** admin.users — gates the Users panel + the auth-method config panel (W2.6). */
export const useCanAdminUsers = () => useCapability("admin.users");

/** True when the caller is a viewer (or unresolved). Drives the "View-only"
 *  badge + read-only surfaces (controls HIDDEN/disabled, not 403-on-tap). */
export const useIsViewer = (): boolean =>
  useStore((s) => isViewerRole(s.principal));

/** The resolved role, defaulting to "viewer" while unresolved (fail-closed). */
export const usePrincipalRole = (): PrincipalRole =>
  useStore((s) => roleOf(s.principal));

// ----------------------------------------------------------- per-role connected
// A role counts as "connected" if its BackendLink says connected. When
// backend_links is [] (the legacy connect_* path never populates it) fall back to
// status.connected[role].connected, then the sticky equipConnected — so the live
// tablet keeps working unchanged. Returns the role's BackendLink error (if any)
// for inline display. Uses useShallow because the returned object is fresh each
// 2s poll.
export function useRoleConnected(
  role: string,
): { connected: boolean; error: string | null } {
  return useStore(
    useShallow((s) =>
      resolveRoleConnected(role, s.status?.backend_links, s.status?.connected, s.equipConnected),
    ),
  );
}
