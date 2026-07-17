// Pure-logic tests for lib/connection.ts (H1). Run:
//   npx tsx ui/src/lib/__tests__/connection.test.ts
// No test runner, no @types/node: a tiny inline assert + a guarded process.exit
// so a failure is a non-zero exit (mirrors the repo's pure-logic test idiom).

import {
  authRequiredForBanner,
  bannerState,
  isConnecting,
  type BannerKind,
} from "../connection";
import type { AuthMethods, Principal } from "../../types";

let failures = 0;
function check(name: string, cond: boolean): void {
  if (!cond) {
    failures++;
    // eslint-disable-next-line no-console
    console.error(`FAIL: ${name}`);
  }
}
function eq<T>(name: string, got: T, want: T): void {
  check(`${name} (got ${JSON.stringify(got)}, want ${JSON.stringify(want)})`, got === want);
}

// -------------------------------------------------------------- bannerState
const base = { phase: "up", stale: false, equipConnected: true, view: "capture", authRequired: false } as const;

function state(over: Partial<Parameters<typeof bannerState>[0]>): BannerKind {
  return bannerState({ ...base, ...over });
}

// Link up, fresh, rig connected → hidden.
eq("up+fresh+rig → hidden", state({}), "hidden");

// Link down, NOT auth → "down".
eq("down (no auth) → down", state({ phase: "down" }), "down");
eq("connecting → down", state({ phase: "connecting" }), "down");
eq("reconnecting → down", state({ phase: "reconnecting" }), "down");

// THE H1 DISTINCTION: link down BUT auth required → "signin", never "down".
eq("down + authRequired → signin", state({ phase: "down", authRequired: true }), "signin");
eq("connecting + authRequired → signin", state({ phase: "connecting", authRequired: true }), "signin");

// signin outranks down, but a HEALTHY link never shows signin (auth is handled by
// the full Login screen; the banner only speaks when the socket is not up).
eq("up + authRequired → hidden (not signin)", state({ authRequired: true }), "hidden");

// Telemetry stale (link up) → "stale".
eq("up + stale → stale", state({ stale: true }), "stale");
// A down link outranks stale.
eq("down + stale → down", state({ phase: "down", stale: true }), "down");

// No rig, link healthy, not on the Rig page → quiet "norig".
eq("up + no rig + not connect view → norig", state({ equipConnected: false }), "norig");
// …but suppressed on the Rig ('connect') page.
eq("up + no rig + connect view → hidden", state({ equipConnected: false, view: "connect" }), "hidden");

// -------------------------------------------------------------- isConnecting
eq("isConnecting(connecting)", isConnecting("connecting"), true);
eq("isConnecting(reconnecting)", isConnecting("reconnecting"), true);
eq("isConnecting(up)", isConnecting("up"), false);
eq("isConnecting(down)", isConnecting("down"), false);

// ---------------------------------------------------- authRequiredForBanner
const M = (methods: string[], first_run = false): AuthMethods => ({
  methods,
  google_configured: false,
  first_run,
});
const P = (role: Principal["role"], email: string | null, caps: Principal["caps"]): Principal => ({
  role,
  email,
  caps,
});

// No signal yet → not required (fail open to today's behavior).
eq("null authMethods → false", authRequiredForBanner(null, null), false);
// Open LAN (no methods) → never required, regardless of principal.
eq("methods [] → false", authRequiredForBanner(M([]), null), false);
eq("methods [] + admin → false", authRequiredForBanner(M([]), P("admin", null, ["view.status"])), false);

// First-run (create the initial admin) → required.
eq("first_run → true", authRequiredForBanner(M(["local"], true), null), true);

// Method enabled, identity still resolving (null principal) → required (the
// banner's honest read during the /api/me fetch — differs from shouldShowLogin).
eq("methods [local] + null principal → true", authRequiredForBanner(M(["local"]), null), true);

// Method enabled, anonymous viewer sentinel (401 fail-closed) → required.
eq("methods [local] + anon viewer → true", authRequiredForBanner(M(["local"]), P("viewer", null, [])), true);

// Method enabled, genuinely signed in → NOT required (their link-down reads as
// "server unreachable", not "sign in").
eq("methods [local] + admin caps → false", authRequiredForBanner(M(["local"]), P("admin", null, ["view.status"])), false);
eq("methods [local] + operator → false", authRequiredForBanner(M(["local"]), P("operator", null, [])), false);
eq("methods [google] + email → false", authRequiredForBanner(M(["google"]), P("viewer", "a@b.co", [])), false);

if (failures > 0) {
  // eslint-disable-next-line no-console
  console.error(`\n${failures} assertion(s) failed`);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
} else {
  // eslint-disable-next-line no-console
  console.log("connection.test.ts: all assertions passed");
}
