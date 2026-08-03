// providerSave.ts — the ONE write path for a capability-provider override
// (#132). Thin on purpose: every decision it makes lives next door in the pure
// `providerWrite.ts`, so the branch can be unit-tested without a window, a
// fetch or a store.
//
// It is a separate module from `providerWrite.ts` for exactly one reason: it
// imports the API client, which reads `window.location` at module scope. Keeping
// that import out of the pure module is what lets the decision half run under
// the bare `tsx` harness.
//
// Both surfaces that own a provider dropdown call THIS function — the Equipment
// Tasks rows and the Guide view's provider panel. Two copies of "which route
// does this save go to" is the duplication that produced the bug in the first
// place: one copy learned about the profile layer and the other did not.

import type { ProvidersConfig } from "../types";
import { setProfileProviders, setProvidersConfig } from "../api/backends";
import type { ProviderCap } from "./effective";
import {
  globalProvidersBody,
  type ProviderWriteTarget,
} from "./providerWrite";

/**
 * Persist one capability's provider to the layer that is actually in force,
 * then reload config so every badge re-derives from the WINNING layer.
 *
 * The reload is not optional and not a nicety: the client must never assert a
 * layer the server has not confirmed. It is also what makes the profile write
 * visible at all — the profile route mutates the at-rest record and invalidates
 * the hub's active-profile cache, and `/api/config`'s `effective` block is the
 * only place that recomputation surfaces.
 */
export async function writeProviderOverride(opts: {
  cap: ProviderCap;
  value: string;
  target: ProviderWriteTarget;
  /** the RAW global block (`config.providers`), never a panel draft seeded from
   *  the effective values — see `globalProvidersBody` for what that costs. */
  globals: Partial<ProvidersConfig> | null | undefined;
}): Promise<void> {
  const { cap, value, target, globals } = opts;
  if (target.layer === "profile" && target.profileId) {
    // Only the edited capability is sent. A whole-block body would let a client
    // holding a stale config silently re-pin capabilities the user never
    // touched, which is the same class of accident as the global-write bug.
    await setProfileProviders(target.profileId, { [cap]: value });
  } else {
    await setProvidersConfig(globalProvidersBody(globals, cap, value));
  }
  // Imported lazily so this module stays usable from a plain node test process
  // (the store pulls in the WS client and a browser-only stack) — the same
  // idiom `useClearOverride` uses.
  const { useStore } = await import("../store");
  await useStore.getState().loadConfig();
}
