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
// SO THERE ARE TWO RULES, and only the second one is durable:
//   1. nothing user-facing speaks while a gate screen is up, and
//   2. the store stops HOLDING the rig the moment the gate engages.
// Fixing only (1) would fix the one dialog we know about and leave every future
// consumer of weather/status/previews/site free to make the same mistake — and
// there will be future consumers. Both rules live here, store-free and
// React-free, so there is one place to read them and one place to test them.

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

/** May a user-facing dialog be raised? Only with the console actually on screen. */
export function dialogsBlocked(gate: AuthGate): boolean {
  return gate !== "open";
}

/**
 * Why a dialog was refused — a blocked thing must be able to say what blocked
 * it (this one answers to a developer reading a suppressed confirm, never to the
 * gated viewer: telling them anything is the bug). null when nothing is blocked.
 */
export function dialogBlockedReason(gate: AuthGate): string | null {
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
 * NOT listed, on purpose:
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
 * that stays mounted over the login screen.
 *
 * Dropping all of this costs nothing on the way back IN: the whole console tree
 * unmounts behind the login screen, so its panels re-fetch on mount when the
 * gate lifts, and the post-sign-in reconnect (Login.refreshSession ->
 * reconnectWs -> ws.onopen) re-hydrates config, principal, update, the log
 * history and the status/sequence snapshot before anything is rendered.
 */
export function clearedRigState(): ClearedRigState {
  return {
    status: null,
    site: null,
    config: null,
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
