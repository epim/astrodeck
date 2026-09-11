// setupSteps.ts - the FIRST-TIME SETUP machine, PURE (plan section C.2.1).
//
// THE DESIGN HAS FIVE STEPS; THE PRODUCT HAS SIX. `lib/firstRunWizard.ts` is
// the ONE source of truth for step completion (`isDone`, quoted in the plan),
// and this module does NOT fork it: it CALLS `computeWizard()` for the four
// steps the wizard already owns and derives the other two itself. Two rules
// follow from that and both are load-bearing:
//
//   - the wizard's `target` step (a target is in the plan) has no design step.
//     It is tracked here, reported by `targetInPlan`, and is NOT a gate: a user
//     can take a first light from Rig - Capture with no plan at all, and gating
//     step 5 on a plan would tell them to build one to prove their focus.
//   - the wizard's `connect` and `profile` steps are ONE design step
//     ("CONNECT THE DEVICES"), so it is done only when both are.
//
// EVERY PREDICATE IS DERIVED LIVE, never from a stored done-map. That is what
// makes the design's own footer sentence true - "Steps re-open if a device
// disappears or you image from a new site" - and a cached map would make it a
// lie the moment the rig changed.
//
// `go` is DATA, not a callback: this module must stay importable by a plain
// `npx tsx` unit test with no router, no store and no DOM. The card and the
// sheet dispatch it (`nav.sheet` / `nav.go`).

import { computeWizard, type WizardSnapshot } from "../../../../lib/firstRunWizard";

export type SetupStepId = "pair" | "devices" | "optics" | "site" | "light";

/** Where a step's GO button lands. A sheet name or a hub route - the two
 *  navigations `ARCHITECTURE.md` section 3 allows. */
export type SetupGo =
  | { kind: "sheet"; name: string; params?: Record<string, string> }
  | { kind: "route"; path: string };

export interface SetupSnapshot extends WizardSnapshot {
  // --- step 1, PAIR THE RIG COMPUTER (no wizard step; new here) -------------
  /** The WebSocket is up: this phone is actually reaching the rig. */
  linkUp: boolean;
  /** `/api/me` has answered. On an open LAN it answers without a sign-in, so
   *  this is "identity resolved", not "signed in with an account". */
  identityResolved: boolean;
  /** The account's email, or null on an open LAN / an anonymous viewer. */
  email: string | null;
  /** Which origin served this tab. Printed rather than the design's
   *  "direct Wi-Fi or the relay", which names both and tells the user neither. */
  reach: "direct" | "relay";

  // --- step 2, CONNECT THE DEVICES ----------------------------------------
  /** Devices reporting `connected` right now - what the row is about. */
  deviceCount: number;
  /** The ACTIVE profile's name, or null when nothing names it. */
  profileName: string | null;

  // --- step 3, OPTICS (no wizard step; new here) ---------------------------
  /** `config.optics_computed.have_optics`. */
  haveOptics: boolean;
  fovW: number | null;
  fovH: number | null;

  // --- step 4, SITE + HORIZON ---------------------------------------------
  siteName: string | null;
  /** `next/lib/horizonModel.ts` `summary(points)`, or null when no line is
   *  drawn. Never a fabricated "horizon drawn". */
  horizonSummary: string | null;
}

export interface SetupStep {
  id: SetupStepId;
  /** 1..5, the number the ring shows while the step is unfinished. */
  n: number;
  title: string;
  sub: string;
  done: boolean;
  /** The right-hand action word, verbatim from `screenshots/23-first-time-setup.png`. */
  act: "GO ›" | "REVIEW";
  go: SetupGo;
}

export interface SetupView {
  steps: SetupStep[];
  doneCount: number;
  total: 5;
  complete: boolean;
  /** The FIRST incomplete step, or null at 5/5. The card's sub-line names this
   *  one - naming the last incomplete step would send a user to the end of a
   *  list whose first row is the one blocking them. */
  next: SetupStep | null;
  /** The wizard's sixth step, surfaced for the report rather than gated. */
  targetInPlan: boolean;
}

const TOTAL = 5 as const;

function reachWord(r: "direct" | "relay"): string {
  return r === "relay" ? "the relay" : "direct Wi-Fi";
}

function fovClause(w: number | null, h: number | null): string | null {
  if (w == null || h == null || !Number.isFinite(w) || !Number.isFinite(h)) return null;
  return `${w.toFixed(2)}° × ${h.toFixed(2)}°`;
}

export function computeSetup(snap: SetupSnapshot): SetupView {
  // The wizard owns location / connect / profile / frame (and cool, when the
  // camera can cool). Read its verdicts rather than re-deriving them.
  const w = computeWizard(snap);
  const doneOf = (id: string): boolean => w.steps.find((s) => s.id === id)?.done ?? false;

  const connected = doneOf("connect");
  const profiled = doneOf("profile");

  const pairDone = snap.linkUp && snap.identityResolved;
  const pairSub = pairDone
    ? `${reachWord(snap.reach)} · signed in as ${snap.email ?? "this device"}`
    : !snap.linkUp
      ? "the phone is not reaching the rig"
      : "reaching the rig, but nobody is signed in yet";

  const devicesDone = connected && profiled;
  const devicesSub = devicesDone
    ? `${snap.deviceCount} devices in the ${snap.profileName ?? "saved"} profile`
    : !connected
      ? "no devices connected"
      : `${snap.deviceCount} connected · no profile saved yet`;

  const fov = fovClause(snap.fovW, snap.fovH);
  const opticsSub = snap.haveOptics
    ? fov
      ? `focal length, aperture, pixel size → ${fov}`
      : "focal length and pixel size are set"
    : "the frame size is unknown";

  const siteDone = doneOf("location");
  const siteSub = siteDone
    ? snap.horizonSummary
      ? `${snap.siteName ?? "this site"} · ${snap.horizonSummary}`
      : `${snap.siteName ?? "this site"} · no horizon drawn yet`
    : "using the default location (0, 0)";

  const lightDone = doneOf("frame");
  // The cool step is the only one the wizard gates on `isApplicable`, so this
  // clause appears only when the camera can actually cool. A rig with an
  // uncooled camera must not be told to prove its cooling.
  const lightSub = lightDone
    ? "done - a frame is in the gallery"
    : snap.hasCooler
      ? "nothing captured yet - one frame proves focus, cooling and the wheel"
      : "nothing captured yet - one frame proves focus and the wheel";

  const raw: { id: SetupStepId; title: string; sub: string; done: boolean; go: SetupGo }[] = [
    { id: "pair", title: "PAIR THE RIG COMPUTER", sub: pairSub, done: pairDone,
      go: { kind: "sheet", name: "connection" } },
    { id: "devices", title: "CONNECT THE DEVICES", sub: devicesSub, done: devicesDone,
      go: { kind: "route", path: "/rig/devices" } },
    { id: "optics", title: "OPTICS", sub: opticsSub, done: snap.haveOptics,
      go: { kind: "sheet", name: "optics" } },
    { id: "site", title: "SITE + HORIZON", sub: siteSub, done: siteDone,
      go: { kind: "sheet", name: "sites" } },
    { id: "light", title: "FIRST LIGHT", sub: lightSub, done: lightDone,
      go: { kind: "route", path: "/rig/capture" } },
  ];

  const steps: SetupStep[] = raw.map((s, i) => ({
    ...s,
    n: i + 1,
    act: s.done ? "REVIEW" : "GO ›",
  }));

  const doneCount = steps.filter((s) => s.done).length;
  return {
    steps,
    doneCount,
    total: TOTAL,
    complete: doneCount === TOTAL,
    next: steps.find((s) => !s.done) ?? null,
    targetInPlan: doneOf("target"),
  };
}

/** The card's one-line sub, verbatim in shape from the prototype
 *  (`logic.js:730` `setupNext`): the FIRST incomplete step, named. */
export function nextLine(view: SetupView): string {
  if (!view.next) return "all five done";
  return `next: ${view.next.title.toLowerCase()} - ${view.next.sub}`;
}
