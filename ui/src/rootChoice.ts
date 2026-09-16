// rootChoice.ts - WHICH of the two roots the bare hash opens on.
//
// The app ships two complete front ends (ARCHITECTURE.md section 2): the
// classic `App` and the six-hub `next/NextApp`. Exactly one is mounted at a
// time, and this module owns the single decision of which one a given hash
// asks for.
//
// WHY THIS IS ITS OWN FILE AND NOT PART OF `next/router.ts`. The decision needs
// two things the router deliberately cannot see at once:
//
//   * the new UI's hub names (`router.ts` has them), and
//   * the LEGACY view names (`next/legacyBridge.ts` has them, keyed by the
//     `ViewName` union so the compiler keeps the list honest).
//
// `legacyBridge.ts` imports `nav` from `router.ts`, so `router.ts` importing
// the legacy table back would be an import cycle around two module-level
// constants - the shape that fails at load time, intermittently, depending on
// which entry point pulled which module first. This file sits ABOVE both and
// imports from each, so there is no cycle and no registration handshake whose
// only failure mode is a silently empty set.
//
// The bridge does NOT import `DEFAULT_ROOT`, on purpose - see the direction
// comment in `legacyBridge.ts`. It has no behaviour to change: it only runs
// while the NEW UI is mounted, and a `store.view` write from inside the new UI
// means "go to this screen of the new UI" under either setting.

import {
  NEXT_ROOT_ALIAS, classicView, firstSegment, isClassicHash, isHubHash,
} from "./next/router";
import { LEGACY_VIEW_ROUTE } from "./next/legacyBridge";

export type RootChoice = "classic" | "next";

/** THE constant. `"classic"` (2026-09-15): the bare root opens the classic app,
 *  which is what the operator uses; the new UI stays reachable at `#/next` and
 *  at every one of its own hub routes. Flip this one word to `"next"` and the
 *  roots swap back with no other edit - `rootChoice.test.tsx` proves both
 *  settings, which is what makes it a switch rather than a comment. */
export const DEFAULT_ROOT: RootChoice = "classic";

// A mutable mirror so a test can exercise BOTH settings in one process. Nothing
// in production ever calls the setter; `DEFAULT_ROOT` above is the value that
// ships. Same hatch shape as `router.ts`'s `resetRouterCacheForTests`.
let defaultRoot: RootChoice = DEFAULT_ROOT;

/** The setting in force. Read this, never `DEFAULT_ROOT` directly, so the test
 *  hatch actually covers every caller. */
export function defaultRootNow(): RootChoice {
  return defaultRoot;
}

/** Test hatch: temporarily run as if `DEFAULT_ROOT` were the other value.
 *  Restore it in the same test file, or every later test reads a lie. */
export function setDefaultRootForTests(v: RootChoice): void {
  defaultRoot = v;
}

/** The legacy view names, derived from the bridge's `Record<ViewName, string>`
 *  rather than hand-listed here: adding a view to `types.ts` without a bridge
 *  row is already a type error, and a second hand-written copy would go stale
 *  the first time that happened, with the symptom being a `#/<view>` deep link
 *  that silently opens the classic default screen instead. */
const LEGACY_VIEW_NAMES: ReadonlySet<string> = new Set(Object.keys(LEGACY_VIEW_ROUTE));

export function isLegacyViewName(name: string): boolean {
  return LEGACY_VIEW_NAMES.has(name);
}

/** Which root a hash asks for.
 *
 *  Two answers are the same under BOTH settings, because they are explicit:
 *  `#/classic[/<view>]` is always the classic root, and `#/next` is always the
 *  new one. Everything else follows `DEFAULT_ROOT`.
 *
 *  THE HUB NAMES WIN OVER THE LEGACY NAMES, and two names are in both lists:
 *  `monitor` and `settings`. So under `"classic"`, `#/monitor` and `#/settings`
 *  are the NEW UI's Monitor and Settings hubs, not the classic views of those
 *  names - the new UI's own routes have to keep working unchanged, and the
 *  classic pair is addressable as `#/classic/monitor` and `#/classic/settings`.
 *  Resolving it the other way would break a live route to fix a bookmark. */
export function rootForHash(hash: string): RootChoice {
  if (isClassicHash(hash)) return "classic";
  if (firstSegment(hash) === NEXT_ROOT_ALIAS) return "next";
  if (defaultRootNow() === "next") return "next";
  return isHubHash(hash) ? "next" : "classic";
}

/** The classic view a hash asks for, or null for "the classic default view".
 *
 *  Two forms reach the same place: `#/classic/<view>` (always, both settings)
 *  and, under `DEFAULT_ROOT` = "classic", a bare `#/<view>` at the root - which
 *  is what makes an old `#/atlas` or `#/mount` bookmark land on the screen it
 *  names instead of on Equipment.
 *
 *  Not validated against `ViewName` here (this module has no business importing
 *  the legacy union); the caller checks membership before writing the store,
 *  exactly as it already did for `classicView`. */
export function classicViewForHash(hash: string): string | null {
  const explicit = classicView(hash);
  if (explicit) return explicit;
  if (rootForHash(hash) !== "classic") return null;
  const first = firstSegment(hash);
  return first && isLegacyViewName(first) ? first : null;
}
