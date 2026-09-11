// relay.ts - is this tab reaching the rig over the RELAY, or on the LAN?
//
// WHY A MODULE OF ITS OWN. Two places need the answer and they must never give
// two different ones:
//
//   * Settings > Connection draws a card per origin and has to say which one
//     you are on (`connectionModel.ts`, which used to own this derivation).
//   * Every control that issues a write the server fences to the LAN has to be
//     honest-disabled BEFORE the press (`gate.ts`'s `needsLan`). The fence is
//     `app.py`'s `_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES` + `_REMOTE_LOCAL_ONLY_
//     EXACT` list: /api/config, /api/alerts, /api/drivers, /api/profiles,
//     /api/connect, /api/survey/pack, /api/ephemeris, /api/locations,
//     /api/switch/ports, the update routes, /api/users, /api/auth/*. A
//     tunnelled session is a replayable bearer credential, so the rig answers
//     403 `code: "local_only"` to every one of them - which is a refusal the
//     user learns AFTER pressing an armed-looking button.
//
// So the derivation lives here, once, and `connectionModel` imports it.
//
// TWO SOURCES, AND WHICH ONE WINS. The pathname is the cheap one: the relay
// mounts the whole app at `/h/<home_id>/` and `lib/base.ts`'s `deriveBase`
// already reads that prefix to build every API URL, so a non-empty base IS the
// tunnel. `GET /api/remote/status`'s `via` is the authoritative one: it is
// stamped by the relay client onto the ASGI scope of the request in your hand,
// which is exactly the flag the fence itself branches on. When the two
// disagree the rig's answer wins (`connectionModel` renders the disagreement as
// a note rather than swallowing it).
//
// NO FETCH HERE. `useLock` is called by dozens of controls on every render and
// must stay synchronous and free; starting a request from it would be a network
// call per screen. Instead the surfaces that ALREADY read `GET
// /api/remote/status` hand their answer to `noteRemoteStatus()`, and until one
// does, the pathname answers. That means the first paint of a fenced control is
// gated on the mount point - correct in every deployment the relay serves - and
// the rig's own word refines it the moment any surface has asked.

import { deriveBase } from "../../lib/base";
import type { RemoteStatus } from "../../types";

export type ReachId = "direct" | "relay";

/** What THIS origin's pathname says. `/h/<home_id>/...` is the relay's mount
 *  and nothing else uses that prefix; "" (root, or loopback) is the LAN. */
export function reachFromPath(pathname: string): ReachId {
  return deriveBase(pathname) === "" ? "direct" : "relay";
}

/** The one formula: the rig's `via` when it has answered, the pathname
 *  otherwise. Pure, so both the Connection sheet's model and a test can call
 *  it with an explicit pathname. */
export function resolveHere(
  pathname: string,
  remote: Pick<RemoteStatus, "via"> | null | undefined,
): ReachId {
  return remote ? remote.via : reachFromPath(pathname);
}

// ------------------------------------------------------- the shared answer

/** The rig's own `via`, once some surface has read it. Null until then - not
 *  "direct", because "nobody has asked" and "the rig says LAN" are different
 *  claims and only one of them may override the pathname. */
let notedVia: ReachId | null = null;
const listeners = new Set<() => void>();

/** Hand over a `GET /api/remote/status` answer from whichever surface fetched
 *  it (the Connection sheet does, on mount). Cheap and idempotent: a repeat of
 *  the same answer notifies nobody. */
export function noteRemoteStatus(remote: Pick<RemoteStatus, "via"> | null | undefined): void {
  const via = remote?.via === "relay" || remote?.via === "direct" ? remote.via : null;
  if (via === notedVia) return;
  notedVia = via;
  for (const fn of listeners) fn();
}

function currentPathname(): string {
  if (typeof window === "undefined" || !window.location) return "/";
  return window.location.pathname;
}

/** Which origin this tab is on, synchronously, for a non-React caller. */
export function reachHere(): ReachId {
  return notedVia ?? reachFromPath(currentPathname());
}

/** True while this tab is tunnelled through the relay - i.e. while the rig
 *  will refuse the LAN-only writes with 403 `local_only`. */
export function onRelay(): boolean {
  return reachHere() === "relay";
}

/** `useSyncExternalStore`'s subscribe half, so a control rendered before the
 *  rig answered re-renders when it does. */
export function subscribeRelay(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/** Test hatch, matching `resetPlanningForTests`: drop the module's memory so a
 *  file can grade a second first-read. */
export function resetRelayForTests(): void {
  notedVia = null;
  listeners.clear();
}
