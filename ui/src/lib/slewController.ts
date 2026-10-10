// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// slewController.ts — framework-agnostic manual-slew controller (touch spec §4.2).
//
// THE REVISED HEADLINE MODEL (post-critique R1): there is NO time-based
// acceleration ramp. "Hold longer = faster" was removed because it causes
// overshoot-oscillate when centering. Instead:
//   - TAP (quick press+release, no slide)  -> one fixed pulse/nudge.
//   - HOLD (finger held down)              -> slew at the CURRENTLY SELECTED fixed
//                                             rate ONLY, never auto-accelerating.
// A ~1.7Hz keepalive (KEEPALIVE_MS≈600) re-asserts the rate to feed the server
// move-axis deadman (hub._move_watchdog, 1200ms): refreshing at ~½ the deadman
// lands ~3 stamps per window, so a single throttled/dropped/jittered tick is
// survivable (~600ms jitter tolerance) without a false mid-slew STOP.
// SCOPE OF THE ≤1.2s GUARANTEE: it covers NETWORK loss only — on a dropped
// connection mid-hold the server halts the mount within ≤1.2s. The TRAVEL that
// costs is the ceiling times 1.2s, so it is per-mount since D-RIG-4: ≤0.72° at
// the 0.6°/s fallback, ≤1.73° on an AM5N reporting 1.44°/s. The mount sheet
// states whichever applies (`rig/lib/slewStops.ts deadmanNote`). It does NOT
// cover client timer starvation (backgrounded tab / setInterval clamp); the
// widened margin is what keeps a merely-jittered foreground tick from tripping
// the deadman early.
//
// Safety invariants this controller guarantees:
//   - rate is clamped to +/- THE DRIVER'S OWN CEILING when it reports one
//     (`getMaxRate`, from status.mount.max_rate_deg_s), and to
//     +/-TOUCH_MAX_RATE_DEG_S when it does not. Both halves mirror the server
//     clamp, which since D-RIG-4 reads `getattr(tel, "max_rate_deg_s", None) or
//     TOUCH_MAX_RATE_DEG_S` (hub.py:221-232): 0.6 is what a mount that cannot
//     say gets, not what every mount gets. A caller that passes no getMaxRate -
//     `#/classic` - is clamped at 0.6 exactly as before.
//   - reverse-RA / reverse-Dec flip the commanded sign at post time.
//   - an alt-guard checks status.mount.alt every keepalive tick; below
//     MIN_SLEW_ALT_DEG it forceStops and emits a `belowHorizon` state. It is
//     OFF while the mount says it does not know where it points
//     (`getPositionKnown`, #144): that altitude is the mount's home reading.
//   - forceStop() is idempotent and POSTs rate 0 (panic path for lock/blur/
//     visibility-hidden). Calling it when already stopped is harmless.
//   - in NINA mode hold is disabled for ALL rates (NINA move_axis raises); a tap
//     becomes a small relative GOTO. The `pulse` rate is tap-only in every mode.

import type { SlewRateOption } from "../types";
import { haptics } from "./haptics";

// ----------------------------------------------------------------- constants
// Rate labels computed from the REAL sidereal rate 15.041 arcsec/s = 0.004178°/s
// (R23 — the draft's labels were ~4x off). "8x SID" = 8 * 0.004178 = 0.0334°/s.
export const SIDEREAL_DEG_S = 0.004178;

export const SLEW_RATES: SlewRateOption[] = [
  { id: "pulse", label: "GUIDE", rateDegS: 0, pulseMs: 250 }, // tap-only fine nudge
  { id: "fine", label: "8× SID", rateDegS: 0.0334 },           // 8 × sidereal
  { id: "set", label: "0.5°/s", rateDegS: 0.5 },
];

//: Two adjacent stops closer than this ratio are one stop as far as a thumb
//: is concerned (mirrors `hubs/rig/lib/slewStops.ts`'s own `DISTINCT_RATIO`,
//: kept as a separate copy here because that file is next/rig-specific and
//: this one has to stay importable by every caller of `SLEW_RATES`, next or
//: classic). On a 1.44 deg/s mount the gap from the shipped top (0.5) to the
//: ceiling is a factor of nearly three and the extra rung earns its place; on
//: a 0.7 deg/s mount it would sit 0.2 from the neighbour it is supposed to be
//: distinct from and teach a thumb nothing.
export const CEILING_DISTINCT_RATIO = 1.4;

// Never rounds up: this label is read as the mount's ceiling, and a mount
// labelled faster than it is will be believed at the worst possible moment
// (driving by eye right after a reset, #144).
function ceilingLabel(degS: number): string {
  const floored = Math.floor(degS * 100) / 100;
  return `${String(floored)} deg/s`;
}

/** The default ladder, extended with the driver's own ceiling (#144).
 *
 *  `SLEW_RATES` ships three stops topping out at 0.5 deg/s, because 0.6 was
 *  the only ceiling the client used to know -- `TOUCH_MAX_RATE_DEG_S`, the
 *  conservative figure the server used to apply to every mount alike. A
 *  driver that publishes `Telescope.max_rate_deg_s` can go much faster (the
 *  AM5N measures 1.44 deg/s), and #144 was filed because the shipped ladder
 *  was the ONLY way to drive the tube by eye after a reset: three minutes of
 *  holding "0.5 deg/s" to cover ninety degrees of sky. `#/next`'s mount sheet
 *  already builds this ladder for itself (`slewStops`); this is the same
 *  rung made available to any OTHER caller of `SLEW_RATES` -- in particular
 *  `SlewPad`'s `ratesProp ?? SLEW_RATES` default, which is what `#/classic`
 *  still falls through to -- without that caller needing its own copy of the
 *  arithmetic.
 *
 *  `null`/`undefined`/non-finite/`<= 0` returns `SLEW_RATES` UNCHANGED, BY
 *  IDENTITY: that is "the driver did not say", not "no limit", and a caller
 *  that never passes a ceiling must keep seeing exactly the three stops it
 *  always has. A ceiling that is not meaningfully above the fastest shipped
 *  stop (`CEILING_DISTINCT_RATIO`) earns no rung of its own either: labelling
 *  a 0.52 deg/s mount's pad "0.52 deg/s" next to "0.5 deg/s" teaches a thumb
 *  nothing and gives it one more number to misread under red light. */
export function slewRatesWithCeiling(
  maxRateDegS: number | null | undefined,
): SlewRateOption[] {
  if (maxRateDegS == null || !Number.isFinite(maxRateDegS) || maxRateDegS <= 0) {
    return SLEW_RATES;
  }
  const top = SLEW_RATES[SLEW_RATES.length - 1].rateDegS;
  if (maxRateDegS < top * CEILING_DISTINCT_RATIO) {
    return SLEW_RATES;
  }
  return [...SLEW_RATES,
    { id: "ceiling", label: ceilingLabel(maxRateDegS), rateDegS: maxRateDegS }];
}

/** Index of the fastest rung on a ladder: the ceiling rung when the mount
 *  reported a ceiling (it is always last), the shipped 0.5 deg/s top stop when
 *  it did not. The one definition of "the ceiling rung" the pad uses when it
 *  moves the selection for an operator who has to drive a tube by eye (#144),
 *  so the pad and a test cannot disagree about which rung that is. An empty
 *  ladder answers 0, never -1: the caller clamps into the array. */
export function fastestRungIndex(rates: readonly SlewRateOption[]): number {
  return Math.max(0, rates.length - 1);
}

// ------------------------------------------------- the mount does not know (#144)
//
// After a power cycle the AM5 reports its home position, pointing at the pole,
// wherever the tube physically is. The server latches `status.mount.position_known`
// False on that signature (devices/base.py `Telescope.position_known`) and keeps
// it so until a sync from a solved frame or the operator's word (`POST
// /api/mount/trust-position`). On the bench, WITH THE TUBE AT HOME, the AM5
// refused every sync while it reported its home position (2026-10-08, #850), so
// a solve and sync right after power-up is refused, and the driver says so.
// While it is False a step is a goto from a position that is wrong, so both UIs
// lock the steps, and the pad stops consulting anything that reads the believed
// position.
//
// NO SURFACED ADVICE MAY TELL THE OPERATOR TO SLEW OR GO TO A TARGET IN THIS
// STATE (#850). A goto is not locked here (neither route nor driver checks it,
// and neither UI gates its goto on it), but it is aimed from the position the
// mount believes. If the tube is not really at home, the goto lands somewhere
// unknown, and that is how a tube meets a pier. The safe order is: if the tube
// really is at home, TRUST POSITION. If it is not, hold a pad key and bring it
// home by eye first (hold-to-move computes no destination), then TRUST POSITION.
// Only after that does going to a target away from the pole, and solving and
// syncing there, refine the pointing.
//
// ONLY AN EXPLICIT `false` LOCKS ANYTHING. The server always sends the key now,
// so ABSENT is an engine older than #144 and reads as known; a client that read
// absence as "unknown" would lock every pad on every older rig.
export function positionKnown(
  mount: { position_known?: boolean } | null | undefined,
): boolean {
  return mount?.position_known !== false;
}

/** Where the mount says it points, or `null` when that is not a pointing.
 *
 *  The ONE gate every reader of `status.mount.alt` / `.az` goes through (#791,
 *  the #692 lesson: a capability flag published without sweeping its readers).
 *  `null` in three cases, and the caller cannot tell them apart on purpose:
 *   - there is no mount block;
 *   - the mount does not know where it points (`position_known === false`). It
 *     then reports its HOME position, the pole, wherever the tube is, and at
 *     the pole the altitude IS the site latitude (#140), so printing, plotting
 *     or measuring it is both a lie about the tube and a coordinate leak;
 *   - alt or az is absent or not a finite number. A principal without
 *     `view.site_derived` (a viewer) never receives them (api/redact.py
 *     `_MOUNT_DERIVED_KEYS`), so the type saying `number` is not the wire.
 *
 *  It does NOT clip below the horizon: "alt < 0" is a fact several readers
 *  state, and each decides for itself whether that is a marker, a banner or
 *  nothing. Use `positionKnown(mount)` instead when the reader needs to say
 *  WHY it is showing nothing; this returns only the pointing. */
export function believedPointing(
  mount: { alt?: number; az?: number; position_known?: boolean } | null | undefined,
): { alt: number; az: number } | null {
  if (!mount || !positionKnown(mount)) return null;
  const { alt, az } = mount;
  if (typeof alt !== "number" || !Number.isFinite(alt)) return null;
  if (typeof az !== "number" || !Number.isFinite(az)) return null;
  return { alt, az };
}

/** Where the mount says it points in RA/Dec, or `null` when that is not a
 *  pointing: `believedPointing`'s twin for the equatorial reading (#913).
 *
 *  The ONE gate every reader of `status.mount.ra_hours` / `.dec_deg` / `.ra_str`
 *  / `.dec_str` goes through. #791 gated alt and az and left these. A mount that
 *  does not know where it points reports its HOME position, the pole, and its
 *  RA there is not the tube's: a parked or stationary mount's RA follows the
 *  site's sidereal clock (#883, #166), so the header, the lock screen, the
 *  atlas footprint and a guided arrival check each showed a precise position
 *  that nothing backs, and `atPosition` could say "at the target" for a target
 *  near the pole. `null` when there is no mount block, the position is unknown
 *  (`position_known === false`, absent reads as known), or RA or Dec is absent
 *  or not a finite number. The strings are the server's own rendering of the
 *  same reading and are withheld with it; they are "" when the wire carries
 *  none. Use `positionKnown(mount)` instead to say WHY nothing is shown. */
export function believedRaDec(
  mount: {
    ra_hours?: number; dec_deg?: number; ra_str?: string; dec_str?: string;
    position_known?: boolean;
  } | null | undefined,
): { ra_hours: number; dec_deg: number; ra_str: string; dec_str: string } | null {
  if (!mount || !positionKnown(mount)) return null;
  const { ra_hours, dec_deg, ra_str, dec_str } = mount;
  if (typeof ra_hours !== "number" || !Number.isFinite(ra_hours)) return null;
  if (typeof dec_deg !== "number" || !Number.isFinite(dec_deg)) return null;
  return {
    ra_hours, dec_deg,
    ra_str: typeof ra_str === "string" ? ra_str : "",
    dec_str: typeof dec_str === "string" ? dec_str : "",
  };
}

// The copy is written once, here, because three surfaces say it (the classic
// view, the new sheet, and the pad both of them host) and a sentence that
// differs between them is a sentence one of them has wrong. It says "solve and
// sync" and never a button's name: the classic view's button reads "Solve &
// Sync" and the sheet's "SOLVE + SYNC", and a sentence that named one would be
// wrong on the other. TRUST POSITION is the one label both share.
//
// IT NEVER SAYS A SYNC AT HOME UNLOCKS ANYTHING, AND IT NEVER ADVISES A GOTO
// (#850). The bench saw the AM5 refuse every sync with the tube at home, and a
// goto is aimed from a position nobody vouches for, so the advice is the safe
// order above: TRUST POSITION when the tube really is at home, and otherwise a
// pad key held to bring it home by eye first. The word "plate" never sits beside
// "solve" here, a habit from when `humanizeLog` rewrote any line holding both to
// "Plate-solve failed". It maps only a line that IS a failed solve now (#792), so
// the habit is not needed; a test still holds the copy to it, in case a later
// humanizer rule reads those two words again.
//
// A MOUNT POWERED UP PARKED AT HOME READS THE POLE TOO (WP-103's design note),
// so this state is the ORDINARY start of every night, until the operator trusts
// the position or a sync from a solved frame. The wording therefore says what the mount is doing and what
// clears it, and does not say "error", "lost" or "reset" as though something had
// gone wrong. Hyphens, never em-dashes: the new UI forbids them and the classic
// strings read the same either way.

/** Why a step cannot run, and what unlocks it. Shown as the lock reason on RA
 *  STEP / DEC STEP and as the toast when a locked tap is pressed. It says "steps
 *  and nudges" because the new sheet calls them steps and the classic pad's NINA
 *  arrows call them nudges; both are a move measured from the believed position. */
export const POSITION_UNKNOWN_STEPS_REASON =
  "Steps and nudges are measured from where the mount thinks it points, and it "
  + "does not know: it is reporting its home position. If the tube really is at "
  + "home, TRUST POSITION unlocks them. If it is not, hold a pad key to bring it "
  + "home by eye first, then use TRUST POSITION.";

/** The note over the pad. Says what the mount is doing (ordinary after any
 *  power-up), what still works, and what teaches it where it is. */
export const POSITION_UNKNOWN_NOTE =
  "The mount is reporting its home position, as it does after any power-up or "
  + "reset, so it does not know where the tube points. That is normal at the "
  + "start of a night. Holding a pad key still moves the tube. With the tube at "
  + "home, TRUST POSITION tells the mount where it is; after that, the first "
  + "solve and sync away from the pole measures it.";

/** The second line of a toast that refuses to COPY the mount's RA/Dec into a
 *  view (recentre on the mount, "use mount position", #928): the reading it
 *  would have copied is the home position, not the tube's. Written once for the
 *  same reason as the two above, and it likewise advises no slew (#850). */
export const POSITION_UNKNOWN_COPY_DETAIL =
  "The mount is reporting its home position, not the tube's. TRUST POSITION "
  + "tells it where it is once the tube really is at home.";

/** What the pad lost with the believed position: the horizon guard reads it. */
export const ALT_GUARD_OFF_NOTE =
  "Horizon guard is off while the mount does not know where it points: the pad "
  + "cannot tell how low the tube is, so watch it.";

export const TRUST_POSITION_LABEL = "TRUST POSITION";

/** The attestation, in full, for the confirmation: what the operator is saying,
 *  what it changes, and what to do instead when it is not true. */
export const TRUST_POSITION_CONFIRM_TITLE = "Is the tube at home?";
export const TRUST_POSITION_CONFIRM_BODY =
  "You are saying the tube is physically at the mount's home or park position, "
  + "the one it reports after a power-up. The mount's coordinates are then taken "
  + "as true: moves measured from them unlock, and manual moves check the Sun "
  + "against them. If the tube is anywhere else, cancel and hold a pad key to "
  + "bring it home by eye first: a move to a target now would be aimed from the "
  + "wrong position.";
export const TRUST_POSITION_CONFIRM_LABEL = "The tube is at home";

export const TOUCH_MAX_RATE_DEG_S = 0.6; // mirror of server clamp (R2/R24)
export const MIN_SLEW_ALT_DEG = 10;      // client alt-guard (R30)
// ~½ the 1200ms server deadman (F-A2): ~3 stamps per window so one throttled/
// dropped/jittered foreground tick survives. NOT an 8Hz tick storm; NOT 1000ms
// (only 200ms of jitter margin → false mid-slew STOP under a TCP retransmit).
export const KEEPALIVE_MS = 600;         // re-assert at ~½ the deadman (R6/F-A2)
export const TAP_MAX_MS = 200;           // press shorter than this (no slide) = tap

export type Axis = "ra" | "dec";
export type Dir = -1 | 1;

// The controller's externally-observable state, surfaced for the UI label and the
// alt-guard flash. `belowHorizon` is the latched alt-guard trip (cleared on the
// next successful start/tap).
export type SlewMode = "idle" | "holding" | "belowHorizon";

export interface SlewState {
  mode: SlewMode;
  axis: Axis | null;
  dir: Dir | null;
  rate: SlewRateOption;
}

export interface SlewControllerOpts {
  getRate: () => SlewRateOption;                       // current selection
  reverseRa: () => boolean;
  reverseDec: () => boolean;
  getAlt: () => number | null;                         // status.mount.alt for the guard
  /** Does the mount know where it points (`status.mount.position_known`, #144)?
   *  While this answers false the altitude guard is NOT consulted, at the gate
   *  or on the keepalive: `getAlt` then reads the mount's home position, and an
   *  altitude read at the pole is the site latitude, not the tube's height. A
   *  guard on that number trips on nothing or waves a slew toward the horizon
   *  through. Optional, so a caller that never passes it keeps the guard exactly
   *  as it was. */
  getPositionKnown?: () => boolean;
  /** How fast THIS mount will actually slew, deg/s (D-RIG-4), from
   *  `status.mount.max_rate_deg_s`. `null` means the driver did not say, which
   *  is NOT "no limit": the clamp falls back to TOUCH_MAX_RATE_DEG_S, exactly
   *  as the server's does. Optional, so a caller that never passes it (the
   *  classic MountView) behaves byte-identically to before. */
  getMaxRate?: () => number | null;
  isNina?: () => boolean;                              // mode === "nina" -> hold disabled
  postMove: (axis: Axis, rateDegS: number) => Promise<void>;
  postNudge: (axis: Axis, dir: Dir) => Promise<void>;  // pulse OR small relative GOTO
  onStateChange?: (s: SlewState) => void;              // UI label + alt-guard flash
  onError?: (e: unknown) => void;                      // toast + debounced haptics.error()
  // injectable clock/timers for deterministic tests (default to real globals).
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (h: unknown) => void;
}

export class SlewController {
  private o: Required<Pick<SlewControllerOpts,
    "getRate" | "reverseRa" | "reverseDec" | "getAlt" | "postMove" | "postNudge">> &
    SlewControllerOpts;

  private holding = false;
  private mode: SlewMode = "idle";
  private curAxis: Axis | null = null;
  private curDir: Dir | null = null;

  private keepalive: unknown = null;
  private pressIsHold = false;

  // `now` is no longer needed: tap-vs-hold is decided on PRESS by rate (F-D4), not
  // by a release-time delta, so the controller never reads the clock. It remains an
  // accepted (ignored) opt for backward compatibility with existing call sites/tests.
  private setTimer: (fn: () => void, ms: number) => unknown;
  private clearTimer: (h: unknown) => void;

  constructor(opts: SlewControllerOpts) {
    this.o = opts as typeof this.o;
    this.setTimer = opts.setTimer ?? ((fn, ms) => setInterval(fn, ms));
    this.clearTimer = opts.clearTimer ?? ((h) => clearInterval(h as ReturnType<typeof setInterval>));
  }

  // --------------------------------------------------------------- introspection
  isHolding(): boolean {
    return this.holding;
  }

  getState(): SlewState {
    return {
      mode: this.mode,
      axis: this.curAxis,
      dir: this.curDir,
      rate: this.o.getRate(),
    };
  }

  private emit(): void {
    this.o.onStateChange?.(this.getState());
  }

  private fail(e: unknown): void {
    this.o.onError?.(e);
  }

  // The ceiling this clamp uses, in the server's own order of preference: the
  // driver's measured number when it has one, 0.6 when it does not.
  //
  // `> 0` and `isFinite` are not belt-and-braces, they are the `or` in the
  // server's `getattr(tel, "max_rate_deg_s", None) or TOUCH_MAX_RATE_DEG_S`
  // (hub.py:227-231): a driver that answers 0.0 is a driver that did not say,
  // and taking it literally would clamp every command to zero and leave a pad
  // that lights up, posts, and never moves the mount.
  private ceiling(): number {
    const said = this.o.getMaxRate?.() ?? null;
    return said != null && Number.isFinite(said) && said > 0 ? said : TOUCH_MAX_RATE_DEG_S;
  }

  private clampRate(r: number): number {
    const cap = this.ceiling();
    return Math.max(-cap, Math.min(cap, r));
  }

  // The signed, clamped, reverse-applied rate for a held axis/dir.
  private signedRate(axis: Axis, dir: Dir): number {
    const base = this.o.getRate().rateDegS;
    const rev = axis === "ra" ? this.o.reverseRa() : this.o.reverseDec();
    const sign = (rev ? -1 : 1) * dir;
    return this.clampRate(base * sign);
  }

  // --------------------------------------------------------------- press lifecycle
  // The SlewPad wires pointerdown -> beginPress and pointerup -> endPress. The press
  // model is decided ONCE on press by the selected rate (touch spec §4.3):
  //   - continuous rate (alpaca, non-pulse): beginPress starts a real hold; endPress
  //     just stops it. A quick stab is therefore a brief continuous slew, NOT a
  //     pulse — we no longer fire an extra tapNudge on a fast release (F-D4: that
  //     produced a double-move). TAP_MAX_MS now documents the tap-vs-slide intent
  //     only; it no longer branches the release.
  //   - tap-only rate (pulse) / NINA: no hold ever starts; endPress fires one nudge.
  beginPress(axis: Axis, dir: Dir): void {
    // pulse rate and NINA mode are tap-only: never start a hold for them.
    this.pressIsHold = this.o.getRate().rateDegS > 0 && !this.isNina();
    if (this.pressIsHold) this.startHold(axis, dir);
  }

  endPress(axis: Axis, dir: Dir): void {
    const wasHold = this.pressIsHold;
    if (wasHold) {
      // F-D4 (one model): a continuous-rate press already commanded a real slew on
      // beginPress. Releasing it just stops — we DO NOT also fire a tapNudge. The
      // old "quick stab still nudges at any rate" branch produced a double-move (a
      // brief continuous slew AND a pulse nudge from one tap). tapNudge is now
      // reserved for the genuine tap-only path (pulse rate / NINA) below.
      this.stopHold();
    } else {
      // tap-only path (pulse rate / NINA): always a single nudge.
      this.tapNudge(axis, dir);
    }
    this.pressIsHold = false;
  }

  private isNina(): boolean {
    return this.o.isNina?.() ?? false;
  }

  // The altitude the guard may act on: null when the mount cannot vouch for it.
  // One place, so the gate and the keepalive cannot disagree about when the
  // guard is off (the two sites that read it are startHold and keepaliveTick).
  private guardAlt(): number | null {
    if (this.o.getPositionKnown && !this.o.getPositionKnown()) return null;
    return this.o.getAlt();
  }

  // --------------------------------------------------------------- hold (slew)
  startHold(axis: Axis, dir: Dir): void {
    if (this.isNina()) return;                 // NINA: no continuous slew (R8)
    if (this.o.getRate().rateDegS <= 0) return; // pulse rate is tap-only
    // Alt-guard at the gate: don't even start below the horizon limit.
    const alt = this.guardAlt();
    if (alt != null && alt < MIN_SLEW_ALT_DEG) {
      this.tripBelowHorizon();
      return;
    }
    this.holding = true;
    this.mode = "holding";
    this.curAxis = axis;
    this.curDir = dir;
    haptics.start();
    void this.postMove(axis, this.signedRate(axis, dir));
    this.armKeepalive();
    this.emit();
  }

  stopHold(): void {
    if (!this.holding && this.mode !== "holding") {
      // still ensure no dangling keepalive
      this.disarmKeepalive();
      return;
    }
    const axis = this.curAxis;
    this.holding = false;
    this.disarmKeepalive();
    if (axis) {
      haptics.stop();
      void this.postMove(axis, 0);
    }
    this.curAxis = null;
    this.curDir = null;
    if (this.mode === "holding") this.mode = "idle";
    this.emit();
  }

  private armKeepalive(): void {
    this.disarmKeepalive();
    this.keepalive = this.setTimer(() => this.keepaliveTick(), KEEPALIVE_MS);
  }

  private disarmKeepalive(): void {
    if (this.keepalive != null) {
      this.clearTimer(this.keepalive);
      this.keepalive = null;
    }
  }

  // Re-assert the current rate once per second (feeds the server deadman) AND
  // re-check the alt-guard — a slew that drifts below the horizon auto-stops.
  private keepaliveTick(): void {
    if (!this.holding || !this.curAxis || !this.curDir) return;
    const alt = this.guardAlt();
    if (alt != null && alt < MIN_SLEW_ALT_DEG) {
      this.tripBelowHorizon();
      return;
    }
    void this.postMove(this.curAxis, this.signedRate(this.curAxis, this.curDir));
  }

  private tripBelowHorizon(): void {
    // forceStop first (posts 0, clears keepalive) THEN latch the trip state so the
    // UI flashes "below horizon limit." forceStop resets mode to idle, so we set
    // belowHorizon after and emit once.
    this.forceStop();
    this.mode = "belowHorizon";
    this.emit();
  }

  // --------------------------------------------------------------- tap (nudge)
  // One fixed move: a pulse-guide-sized nudge (pulse rate) or, in NINA mode, a
  // small relative GOTO. The component's postNudge closure encodes which.
  tapNudge(axis: Axis, dir: Dir): void {
    // clear a latched below-horizon flash on a fresh deliberate action
    if (this.mode === "belowHorizon") {
      this.mode = "idle";
      this.emit();
    }
    haptics.tap();
    Promise.resolve(this.o.postNudge(axis, dir)).catch((e) => this.fail(e));
  }

  // --------------------------------------------------------------- panic
  // External stop: lock engaged, window blur/visibility-hidden, move POST failure.
  // Idempotent — posts 0 for whatever axis was last commanded (and clears state).
  forceStop(): void {
    const axis = this.curAxis;
    const wasHolding = this.holding;
    this.holding = false;
    this.disarmKeepalive();
    if (axis) {
      void this.postMove(axis, 0);
    }
    this.curAxis = null;
    this.curDir = null;
    this.mode = "idle";
    if (wasHolding) haptics.stop();
    this.emit();
  }

  // Wrap postMove so a failed command never leaves the mount "thinking it moves":
  // on error we forceStop (post 0) and surface via onError (toast + debounced buzz).
  private async postMove(axis: Axis, rate: number): Promise<void> {
    try {
      await this.o.postMove(axis, rate);
    } catch (e) {
      // Avoid recursion: only escalate to a stop if this was a non-zero command.
      if (rate !== 0) {
        this.holding = false;
        this.disarmKeepalive();
        this.curAxis = null;
        this.curDir = null;
        this.mode = "idle";
        // THE ONE EXIT THAT USED TO GO QUIET. Every other transition out of a
        // hold — stopHold, forceStop, tripBelowHorizon — ends in emit(), which
        // is the only thing that repaints the pad. This one did not, so a move
        // POST that failed left the arrow lit and the feedback line reading
        // "HOLD · 0.50°/s" over a mount that had already stopped: the error
        // toast said one thing and the control said another. Emitted BEFORE the
        // best-effort zero so the pad tells the truth even if that POST is lost
        // as well.
        this.emit();
        try {
          await this.o.postMove(axis, 0);
        } catch {
          /* best-effort stop */
        }
      }
      this.fail(e);
    }
  }
}

/** Convenience: look up a rate option by id (UI segmented control). */
export function rateById(id: SlewRateOption["id"]): SlewRateOption {
  return SLEW_RATES.find((r) => r.id === id) ?? SLEW_RATES[0];
}

/** Speed glyph (R21 - encode by shape, never color): pulse=▰ fine=▰▰ set=▰▰▰
 *  ceiling=▰▰▰▰.
 *
 *  The fourth bar is for a stop that exists only because the mount reported a
 *  ceiling above the shipped ladder. Without it a 1.44 deg/s mount drew the
 *  same three bars on 0.5 and on 1.44 - a shape encoding that says the two are
 *  the same speed class when one of them is nearly three times the other, and
 *  shape is the only channel a red-adapted eye has here. `#/classic` never
 *  builds a `ceiling` stop, so its pad is unchanged. */
export function rateGlyph(id: SlewRateOption["id"]): string {
  return id === "pulse" ? "▰"
    : id === "fine" ? "▰▰"
      : id === "ceiling" ? "▰▰▰▰" : "▰▰▰";
}
