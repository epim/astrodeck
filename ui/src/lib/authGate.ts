// lib/authGate.ts — what the console may SAY, and what it may still be HOLDING,
// while a sign-in gate stands in front of it (#117).
//
// THE FAILURE. A user looking at the AstroDeck LOGIN screen was shown a modal
// reading "High cloud forecast tonight — Forecast peak N% total cloud (LAYER
// layer dominant) between HH:MM and HH:MM — at/above your N% threshold." Three
// things leaked to someone with no session: that this address runs an
// observatory, what tonight's LOCAL sky is doing (a location signal), and the
// operator's own cloud threshold.
//
// THE MECHANISM was not a server hole — /api/weather, /api/status, /api/config,
// /api/site/sky and /api/catalog/tonight all 401 unauthenticated and the login
// HTML carries no weather or location strings. It was CLIENT state that outlived
// its session. React hooks are unconditional, so App's weather effect kept
// firing behind the gate, over a `weather` slice the store had been handed while
// the session was still live. The gate engages LATER than the delivery — session
// expiry, an auth-epoch bump, a relay reconnect — so "non-holders never receive
// weather events" (true, and what the old comment relied on) says nothing about
// this case at all.
//
// SO THERE ARE THREE RULES, and only the last two are durable:
//   1. nothing user-facing SPEAKS while a gate screen is up — no confirm, no
//      toast, no OS notification, no beep;
//   2. the store DROPS the rig the moment the gate engages; and
//   3. it stops TAKING THE RIG IN for as long as the gate is up.
// Fixing only (1) would fix the one dialog we know about and leave every future
// consumer of weather/status/previews/site free to make the same mistake — and
// there will be future consumers.
//
// RULE 3 IS NOT BELT-AND-BRACES. It is what makes rule 2 more than a two-second
// flicker, and the first version of this module shipped without it on the theory
// that the transport already refused a gated client. The transport does not.
// Close-1008-before-accept applies to a NEW handshake; it says nothing about a
// socket that was accepted while the session was valid and is STILL OPEN when
// the gate engages — which is this ticket's case, and the most deliberate one:
//   - signing out does not close the socket (SignInButton / AccountPanel call
//     logout() then re-resolve /api/me; nothing calls reconnectWs), and there is
//     no global 401 interceptor in api.ts to close it either;
//   - the server re-authenticates an already-open /ws only every
//     WS_AUTH_RECHECK_S (60s, api/redact.py) and closes 4401 only at that check.
//     Until it fires, `principal` is still the pre-logout admin, so frames go out
//     UNREDACTED — precise site included;
//   - hub publishes `status` every 2s.
// So without rule 3: gate engages -> store cleared -> ~2s later a `status` frame
// restores the site name, its precise coordinates, where the mount is pointed
// and what is connected, followed by weather (threshold + site_lat/site_lon),
// previews and error toasts — all behind the sign-in form, for up to a minute
// after the operator pressed Sign out and handed the tablet over.
//
// All three rules live here, store-free and React-free, so there is one place to
// read them and one place to test them.

import type { GuideRmsByKind } from "./guideRms";
import type { MasterRow } from "./calibrationLibrary";
import type {
  LogLine,
  NinaHealth,
  PolarState,
  PreviewInfo,
  SequenceState,
  Toast,
} from "../types";

// ---------------------------------------------------------------- the gate
/**
 * Which screen is standing in for the console right now.
 *   "open"      — the operational shell; a signed-in (or open-LAN) viewer.
 *   "resolving" — the neutral boot splash: a sign-in method IS enabled but the
 *                 principal has not resolved, so we do not yet know who is
 *                 looking. Treated as "not entitled" for anything user-facing.
 *   "login"     — the full-screen Login has replaced the whole app.
 * App computes this in the same order it renders those three branches, so the
 * store's copy can never disagree with what is actually on the glass.
 */
export type AuthGate = "open" | "resolving" | "login";

/**
 * Rule 1. May the console SAY anything to whoever is looking — a confirm dialog,
 * a toast, an OS notification, a beep? Only with the operational shell actually
 * on screen.
 *
 * Both gate screens keep <Toasts/> and <ConfirmHost/> mounted (they must, so
 * they are there the instant the gate lifts), so "the console tree is unmounted"
 * is NOT a reason a rig sentence cannot reach a gated viewer. This is.
 */
export function announcementsBlocked(gate: AuthGate): boolean {
  return gate !== "open";
}

/**
 * Rule 3. May the store still TAKE IN the rig's telemetry? Not while the login
 * screen is up — see the module header for why the open socket keeps delivering
 * for up to a minute after sign-out, and what it delivers.
 *
 * The two predicates deliberately draw DIFFERENT lines, because holding and
 * speaking are different risks:
 *   - SPEAKING is blocked under the boot splash too. Nobody has been identified
 *     yet, and "we do not know who is looking" is not "anyone" — and a beep plus
 *     an OS notification leave the page entirely, so they reach further than any
 *     overlay does.
 *   - HOLDING is not. The splash renders none of these slices, and the one-shot
 *     `hello` hydration (config, site, the safety snapshot) arrives in exactly
 *     that window and never comes again on this socket — refusing it would leave
 *     an entitled viewer with a console that never got its cold snapshot, to
 *     protect a screen that displays nothing. If the splash resolves INTO the
 *     login screen, gateEngaged drops everything it accumulated; if it resolves
 *     into the console, that viewer was entitled to it all along.
 */
export function intakeBlocked(gate: AuthGate): boolean {
  return gate === "login";
}

/**
 * Why the gate refused — a blocked thing must be able to say what blocked it.
 * This one answers to a DEVELOPER reading a suppressed confirm or a dropped
 * frame, never to the gated viewer: telling them anything is the bug this whole
 * module exists for. null when nothing is blocked.
 *
 * NAMES: an early review draft of #117 referred to `dialogsBlocked()` and
 * `dialogBlockedReason()`. No such exports were ever written — the pair shipped
 * as announcementsBlocked/gateBlockedReason because "dialog" names one of the
 * four things the gate has to keep quiet (confirm, toast, OS notification,
 * beep), and naming it after the narrowest one is how gating only the confirm
 * came to look like a complete fix in the first place.
 */
export function gateBlockedReason(gate: AuthGate): string | null {
  switch (gate) {
    case "login":
      return "the sign-in screen is up — there is no signed-in viewer to tell";
    case "resolving":
      return "sign-in has not resolved — we do not yet know who is looking";
    default:
      return null;
  }
}

/**
 * The rising edge that must drop the rig state: any transition INTO "login".
 * Deliberately an edge, not a level. A level test would re-clear on every
 * commit while the login screen is up, which would also wipe anything the login
 * screen itself produces (an error toast raised after the edge) — and the point
 * is to drop what the PREVIOUS session left behind, once.
 */
export function gateEngaged(prev: AuthGate, next: AuthGate): boolean {
  return next === "login" && prev !== "login";
}

// -------------------------------------------------------- pristine rig state
// The three rig slices whose pristine value is an object rather than null.
// store.ts seeds its INITIAL state from these same constants: "cleared" and
// "never had a rig" must be the same state, or signing out would leave the
// console in a state a cold boot can never produce.
export const EMPTY_SEQUENCE: SequenceState = { state: "idle" };
export const EMPTY_POLAR: PolarState = {
  state: "idle",
  az_error: 0,
  alt_error: 0,
  total_error: 0,
  progress: 0,
  message: "",
  source: null,
};
export const EMPTY_NINA_HEALTH: NinaHealth = {
  active: false,
  ageMs: null,
  state: "na",
  lastError: null,
};

/**
 * Every store slice that describes the observatory, its night, or its hardware.
 * Anything listed here is something an unauthenticated viewer must not be able
 * to be shown, however indirectly.
 *
 * THIS LIST IS ENFORCED, not asserted. authGate.test.ts walks the store's OWN
 * runtime key set and requires every slice to be either cleared here or in its
 * NON_RIG_KEYS map, which carries a per-key reason the slice is safe to keep
 * behind a sign-in form. A slice added to store.ts that is in neither fails the
 * test rather than defaulting to "kept" — the previous version of that test
 * checked a hardcoded list of 18 names against this one, which is a claim about
 * the list and not about the store, so it passed no matter what store.ts grew.
 * State outliving the reason it was safe is the whole of #117.
 *
 * NOT listed, on purpose (the reasons live per-key in the test's map):
 *   - `principal` / `authMethods` — these ARE the gate; clearing them would
 *     dissolve the login screen we are gating behind.
 *   - `plan` and `loadedPlanId` — the user's own target draft, persisted to
 *     localStorage by setPlan. Blanking the in-memory copy would not remove it
 *     from the disk it is reloaded from, so it would be theatre, not privacy.
 *   - client prefs (night, dimmer, touch, coach marks, photometry, preview
 *     stretch/overlays/viewport) — they describe the person's screen, not the
 *     sky, and they survive a reload anyway.
 *   - `view` and transport state (wsPhase/telemetryStale) — where the user was
 *     and whether the socket is up. Neither says anything about the rig.
 */
export interface ClearedRigState {
  status: null;
  site: null;
  config: null;
  // Found by walking the store rather than re-reading this list: helpTopic is
  // not a rig payload but a rig VERDICT. Its values are "camera-offline",
  // "guiding-lost", "mount-move-failed", "cooler-stuck", "autofocus-failed",
  // "nina-error" (types.TroubleshootTopic), and the only things that set it are
  // diagnoseFailure's "How to fix →" on a failure toast and the same button in
  // Monitor/Sequence. So a non-null value states that THIS rig's named hardware
  // failed tonight. Nothing renders it behind the login screen today — which is
  // exactly what was true of `weather` until an unconditional hook did.
  helpTopic: null;
  update: null;
  equipConnected: false;
  preview: null;
  previews: PreviewInfo[];
  selectedPreviewId: null;
  livePreviewId: null;
  lastFrameAtMs: null;
  focus: null;
  lastAutofocusResult: null;
  guide: null;
  guideAssistant: null;
  guideRmsByKind: GuideRmsByKind;
  lastGuideAtMs: null;
  egainLearn: null;
  filterOffsetsLearn: null;
  sequence: SequenceState;
  runBanner: null;
  polar: PolarState;
  ninaHealth: NinaHealth;
  safety: null;
  alert: null;
  weather: null;
  weatherAlertKey: number;
  lastReportId: null;
  lastLight: null;
  masters: MasterRow[];
  logs: LogLine[];
  toasts: Toast[];
  unseenError: number;
  framing: null;
  atlasBannerPending: null;
}

/**
 * The pristine value of every rig-describing slice, as a patch to spread into
 * the store. Fresh arrays each call so a cleared slice can never alias the one
 * that was just dropped.
 *
 * `weatherAlertKey` goes back to 0 with the weather itself: it is the once-per-
 * night latch the alert dialog fires on, and leaving it high would mean the
 * NEXT session's first alert (which bumps 0 -> 1) still edge-matched against a
 * key minted for a night the new viewer never saw.
 *
 * `toasts` is in here because a toast is a sentence about the rig — "UNSAFE:
 * rain detected", "Sequence failed: mount lost" — sitting in the one overlay
 * that stays mounted over the login screen. Emptying the queue is only half of
 * the job: announcementsBlocked has to keep the PRODUCER shut for as long as the
 * gate is up, or the next `safety` frame refills what this just emptied.
 *
 * Dropping all of this costs nothing on the way back IN: the whole console tree
 * unmounts behind the login screen, so its panels re-fetch on mount when the
 * gate lifts, and the post-sign-in reconnect (Login.refreshSession ->
 * reconnectWs -> ws.onopen) re-hydrates config, principal, update, the log
 * history and the status/sequence snapshot before anything is rendered.
 *
 * `weather` is the ONE exception and it is App's job, not this module's:
 * ws.onopen does not fetch it and the server republishes only every 15 minutes
 * (OPEN_METEO_INTERVAL_S), so a high-cloud alert that arrived while intake was
 * blocked would not be re-offered for up to a quarter of an hour. App re-fetches
 * /api/weather on the login -> open transition for exactly that reason.
 */
export function clearedRigState(): ClearedRigState {
  return {
    status: null,
    site: null,
    config: null,
    helpTopic: null,
    update: null,
    equipConnected: false,
    preview: null,
    previews: [],
    selectedPreviewId: null,
    livePreviewId: null,
    lastFrameAtMs: null,
    focus: null,
    lastAutofocusResult: null,
    guide: null,
    guideAssistant: null,
    guideRmsByKind: {},
    lastGuideAtMs: null,
    egainLearn: null,
    filterOffsetsLearn: null,
    sequence: EMPTY_SEQUENCE,
    runBanner: null,
    polar: EMPTY_POLAR,
    ninaHealth: EMPTY_NINA_HEALTH,
    safety: null,
    alert: null,
    weather: null,
    weatherAlertKey: 0,
    lastReportId: null,
    lastLight: null,
    masters: [],
    logs: [],
    toasts: [],
    unseenError: 0,
    framing: null,
    atlasBannerPending: null,
  };
}

/** The slice names clearedRigState() covers — the checkable form of the list
 *  above, so a test can walk the store and assert each one landed pristine. */
export const RIG_STATE_KEYS = Object.keys(
  clearedRigState(),
) as (keyof ClearedRigState)[];
