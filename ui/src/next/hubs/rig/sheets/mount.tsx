// mount.tsx - the MOUNT device sheet (plan hub-rig.md B.3, screenshot
// 15-device-mount.png, fragment proto/15-device-mount.html).
//
// WHAT THIS FILE OWNS AND WHAT IT ONLY HOSTS.
//
// Hosted as-is, because re-implementing any of them would fork behaviour that
// cost real nights to get right:
//   GotoStrip   narrates an in-flight goto and carries the stuck-mount alert.
//               Renders null when nothing is live, so it costs an idle sheet
//               nothing.
//   SlewPad     the pad, its rate selector, its STOP bar, the reverse toggles,
//               the below-horizon auto-stop, the pointer-capture verification,
//               the multi-touch guard - and the PANIC STOP GATED ON OWNERSHIP.
//               That last one is why this sheet may mount the pad at all: fired
//               unconditionally on unmount, `/api/mount/stop` bumps the SERVER's
//               global motion epoch, and on 2026-08-07 backgrounding the tab
//               aborted a running polar alignment. A sheet is dismissable, so
//               the pad is unmounted constantly here - the guard is load-bearing
//               and `__tests__/rigMountDom.test.tsx` asserts it.
//
// Owned here: the header live line, the four readout tiles and the one dial,
// the offset readout, the three actions, the GO TO A TARGET disclosure with its
// preflight refusals, and the POLAR ALIGNMENT row.
//
// DEVIATIONS (plan E7-E10), all engine facts, none of them cosmetic:
//   E7  no `king` tracking rate - `TRACKING_RATES` is sidereal/lunar/solar and
//       the server's own comment says King is out of scope.
//   E10 the offset readout is the last SOLVE's `pointing.error_arcmin`, not a
//       cumulative pad offset: the engine publishes no such number, and the
//       prototype's is fixture state.
//
// E8 AND E9 ARE CLOSED (D-RIG-4), and what closed them was the engine, not a
// change of mind up here:
//   E8  said there was no SLEW RATE tile because every mount was clamped to
//       0.6 deg/s. The clamp now prefers the driver's own measured ceiling
//       (`hub.py:221-232`; the AM5N reports 1.44 deg/s) and publishes it as
//       `status.mount.max_rate_deg_s`, so there is a real number to offer. The
//       tile is the editor and the pad's centre cell is the same value lifted -
//       ONE state, two views, which is what E8 was protecting.
//   E9  said there was no per-tap arcminute verb. `POST /api/mount/nudge`
//       (`mount_offset.py`) is one: it takes a signed size in arcminutes, does
//       the cos(dec) division a client cannot be trusted with, and slews the
//       answer through the same horizon and sun guards a goto passes.

import { useEffect, useMemo, useRef, useState, type JSX, type SyntheticEvent } from "react";
import type { SheetProps } from "../../sheets";
import {
  ActionButton, BannerCard, Card, Dial, Label, ListRow, Mono,
  ReadoutGrid, ReadoutTile, Sheet, Switch, TextInput,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useLock } from "../../../lib/gateHook";
import { usePendingValue } from "../lib/pendingValue";
import {
  ceilingNote, deadmanNote, nudgeStopLabel, nudgeStops, slewStops,
} from "../lib/slewStops";
import { api } from "../../../../api";
import {
  NUDGE_ABSENT_NOTE, NUDGE_OUT_OF_RANGE_NOTE, isNudgeAbsent, isNudgeOutOfRange, nudgeMount,
} from "../../../../api/mount";
import { useConfig, usePolar, useSequence, useStatus, useStore } from "../../../../store";
import { useBusyOrPending } from "../../../../lib/useBusy";
import { useCanControlMount } from "../../../../lib/caps";
import { useTouchSettings } from "../../../../lib/touchStore";
import type { Axis, Dir } from "../../../../lib/slewController";
import { altTone, fmtAlt, fmtMag } from "../../../../lib/catalogFormat";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import SlewPad from "../../../../components/SlewPad";
import GotoStrip from "../../../../components/GotoStrip";
import type { CatalogEntry, LogLine, PreflightAlt } from "../../../../types";

// ------------------------------------------------------------------- copy
//
// Every sentence here names its blocker or the consequence of pressing again.
// Hyphens, never em-dashes (ARCHITECTURE non-negotiable 5) - these read as the
// shipped MountView strings with that one substitution.

/** plan 0.5 - the mount's flow-ownership reason. Exported so the test asserts
 *  the SAME string the sheet renders rather than a copy of it. */
export const FLOW_OWNS_MOUNT =
  "A flow owns the mount. Pause the run on Session - Now to move it yourself.";

export const HOMING_TITLE =
  "On its way home - pressing again would cancel this and start over.";
export const HOME_MOVING_REASON =
  "The mount is already moving - Home comes back when it stops. Homing unparks, "
  + "walks to the sensor and unparks again, and interrupting that can leave the "
  + "mount parked.";
export const PARKING_TITLE =
  "Parking - 30-60 s. Pressing again would cancel this park and start another.";
export const FOREIGN_MOTION_TITLE =
  "The mount is already moving. Park takes that move over and stows it - and if "
  + "the move is itself a park, this restarts it.";

const FOOTER_NOTE =
  "At the GUIDE rate a pad tap moves the mount by the RA STEP or DEC STEP above; "
  + "at any faster rate a tap does nothing and holding a pad key slews until you "
  + "let go. Tracking follows the object unless you pin it here. The pad and the "
  + "steps are locked while a flow owns the mount.";

/** The step each tile starts on, in arcminutes.
 *
 *  10' is a centring correction: far enough to see move on a wide field, small
 *  enough that a mistaken tap is one more tap to undo. It is NOT remembered
 *  between visits, and that is deliberate - `finder/prefs.ts`'s own header
 *  argues that a control which reopens where it was left is worse than one that
 *  reopens usefully, and a 10-degree step left over from last night is exactly
 *  the shape of thing that argument is about. */
const DEFAULT_STEP_ARCMIN = 10;

/** The step ladder as dial stops. Module scope because `nudgeStops()` answers
 *  the same ten every time - it is bounded by the route's limits, not by
 *  anything on this screen. */
const STEP_OPTIONS = nudgeStops().map((s) => ({ value: s.arcmin, label: s.label }));

/** The sentence the SERVER's sun-cone refusal cannot carry: where the cone is
 *  set. The refusal itself is surfaced verbatim in the error toast. */
export function sunConeNote(deg: number | null | undefined): string {
  return deg == null
    ? "Sun avoidance is armed - change it on the Safety sheet."
    : `Sun avoidance is armed at ${deg}° - change it on the Safety sheet.`;
}

// The pad's arrows ship at 56 px (`min-h-[56px]`, plus index.css's `.tap-lg`).
// The design draws a 3x3 grid of 64 px keys. Raising them without editing the
// component (not this task's file) or next.css (out of bounds for this task)
// leaves one honest mechanism: a scoped rule this sheet publishes, keyed on a
// wrapper class only this sheet sets.
//
// THE SPECIFICITY IS NOT AN ACCIDENT. Three rules already claim `.tap-lg`, and
// the one that actually wins on a phone is index.css's
// `:root:not(.no-touch-ui) .tap-lg { min-height: 60px }` inside the
// coarse-pointer media query - (0,3,0). A plain `.nx-mount-pad .tap-lg` is
// (0,2,0) and would silently lose, which is the failure mode where a rule
// exists, reads correctly, and changes nothing. Matching that selector's own
// shape puts this at (0,4,1) and keeps the user's forced-compact preference
// (`.no-touch-ui`, desktop-on-tablet) intact: a person who asked for small
// targets is not overruled by a device sheet.
//
// The pad's grid is `max-w-[260px]` with an 8 px gap, so 3 x 64 + 2 x 8 = 208 px
// still fits.
const PAD_CSS =
  ":root:not(.no-touch-ui) .nx-mount-pad button.tap-lg"
  + "{min-height:64px;min-width:64px}";

// ------------------------------------------------------------------ helpers

/** The newest line the SOLVER wrote, by identity. Comparing line IDENTITY and
 *  not a timestamp is what stops an old success line being re-announced when a
 *  request never reached the lane at all (`views/MountView.tsx`). */
function newestSolveLine(): LogLine | null {
  const logs = useStore.getState().logs;
  for (let i = logs.length - 1; i >= 0; i--) {
    if (logs[i].data.source === "solve") return logs[i];
  }
  return null;
}

/** The first reason that applies, in gate.ts's fixed priority. Each clause is
 *  produced by `useLock` - this only composes them, because `useLock` takes one
 *  busy lane and several controls here are blocked by two. */
function firstReason(...reasons: (string | null | undefined)[]): string | null {
  for (const r of reasons) if (r) return r;
  return null;
}

/** Arcminutes for a readout, switching to degrees at 60' (the prototype's
 *  `fmtOff` rule). The SIGN is dropped: `pointing.error_arcmin` is a distance,
 *  not a signed offset, and "+1.2'" would imply a direction nothing measured. */
export function fmtArcmin(v: number): string {
  if (!Number.isFinite(v)) return "-";
  const a = Math.abs(v);
  return a >= 60 ? `${(a / 60).toFixed(1)}°` : `${a.toFixed(1)}′`;
}

/** "1h 38m" from a fractional hour count. */
export function fmtFlipIn(hours: number): string {
  const total = Math.max(0, Math.round(hours * 60));
  return `${Math.floor(total / 60)}h ${total % 60}m`;
}

type TrackStop = "sidereal" | "lunar" | "solar" | "off" | "on";

const RATE_SUB: Record<string, string> = {
  sidereal: "15.04″/s · stars",
  lunar: "14.5″/s · Moon",
  solar: "15.0″/s · Sun",
  off: "stopped",
  on: "tracking · rate not reported",
};

// ==========================================================================

export function MountSheet(_props: SheetProps): JSX.Element {
  const status = useStatus();
  const config = useConfig();
  const sequence = useSequence();
  const canMount = useCanControlMount();
  const showToast = useStore((s) => s.showToast);
  const enqueueToast = useStore((s) => s.enqueueToast);
  const openFraming = useStore((s) => s.openFraming);

  // The polar session, for the POLAR ALIGNMENT row's one line. `usePolar` and
  // not `getState()`: a row that only re-read on some other render would go on
  // saying "not measured" over a running alignment.
  const polar = usePolar() as {
    state?: string; total_error?: number; phase?: string; point_index?: number;
  } | null;

  const m = status?.mount;
  const meridian = status?.meridian;

  // plan 0.5: a run holds the mount while it is running or paused.
  const flowOwns = sequence?.state === "running" || sequence?.state === "paused";
  const flowExtra = flowOwns ? FLOW_OWNS_MOUNT : null;

  // ------------------------------------------------------------------ gates
  //
  // ONE helper, composed. `useLock` answers link -> cap -> role -> ONE lane ->
  // extra; PARK, UNPARK, HOME and GOTO are each blocked by TWO lanes, so the
  // clauses are taken separately and `firstReason` re-applies the same order.
  const base = useLock({ cap: "control.mount", needsRole: "telescope" });
  const laneGoto = useLock({ busyLane: "goto" });
  const lanePark = useLock({ busyLane: "park" });
  const laneSolve = useLock({ busyLane: "solve" });
  const explain = base.onExplain;

  // ------------------------------------------------------- tracking, in flight
  //
  // `/api/mount/tracking` is NOT a `_spawn` route, so `useBusy` would answer
  // false forever: the honest in-flight signal is the POST itself, and on a
  // serial LX200 mount that is a real round trip.
  const [sending, setSending] = useState<string | null>(null);
  const act = async (what: string, fn: () => Promise<unknown>): Promise<boolean> => {
    if (sending) return false;
    setSending(what);
    try { await fn(); return true; }
    catch (e) { showToast("error", (e as Error).message, { verbatim: true }); return false; }
    finally { setSending(null); }
  };

  const tracking = usePendingValue<boolean>(m?.tracking);
  const trackingRate = usePendingValue<"sidereal" | "lunar" | "solar">(m?.tracking_rate);

  // ------------------------------------ park / home / goto: ONE server lane
  const motionLane = useBusyOrPending("goto");
  const [motion, setMotion] = useState<{ kind: "park" | "home" | "goto"; id?: string } | null>(null);
  const motionLaneWasBusy = useRef(false);
  useEffect(() => {
    if (motionLane.busy) { motionLaneWasBusy.current = true; return; }
    if (!motionLaneWasBusy.current) return;
    motionLaneWasBusy.current = false;
    setMotion(null);
  }, [motionLane.busy]);

  const parking = motion?.kind === "park";
  const homing = motion?.kind === "home";
  const slewingTo = motion?.kind === "goto" ? motion.id : null;
  // Motion this tab did not start. The lane survives a reload and another
  // client's park; what it cannot say is WHICH of park/home/goto is on it, so
  // nothing here claims "parking" - only "moving", which is certainly true.
  const foreignMotion = motionLane.busy && motion == null;
  const laneBusy = motionLane.busy || motion != null;

  const [sunRefusal, setSunRefusal] = useState<string | null>(null);

  /** Fire one of the three motions that share the `goto` lane. `arm()` runs
   *  only AFTER the server accepts: arming on a refusal would leave every mount
   *  control dead for the latch's six seconds over a request the rig never
   *  took. */
  const runMotion = async (
    kind: "park" | "home" | "goto", id: string | undefined,
    url: string, body?: unknown,
  ): Promise<boolean> => {
    setMotion({ kind, id });
    try {
      await api.post(url, body);
      motionLane.arm();
      return true;
    } catch (e) {
      setMotion(null);
      const msg = (e as Error).message;
      showToast("error", msg, { verbatim: true });
      // The sun cone is the SERVER's refusal (`solar_avoidance` /
      // `solar_exclusion_deg`). Its message is shown verbatim above; this adds
      // the one thing the server cannot say - where the cone is set.
      if (/sun|solar/i.test(msg)) setSunRefusal(msg);
      return false;
    }
  };

  // ------------------------------------------------- solve + sync completion
  //
  // The FAILURE half already reaches the user (the store toasts every error
  // line). A solve that WORKED only ever wrote an info line into the collapsed
  // log, so success and "nothing happened" looked identical.
  const solve = useBusyOrPending("solve");
  const solving = solve.busy || sending === "solve";
  const solveAskedFrom = useRef<LogLine | null | undefined>(undefined);
  const solveLaneWasBusy = useRef(false);
  useEffect(() => {
    if (solving) { solveLaneWasBusy.current = true; return; }
    if (!solveLaneWasBusy.current) return;
    solveLaneWasBusy.current = false;
    const before = solveAskedFrom.current;
    if (before === undefined) return;
    solveAskedFrom.current = undefined;
    const line = newestSolveLine();
    if (line && line !== before && line.data.message.startsWith("solved & synced")) {
      enqueueToast({
        level: "success",
        title: "Solved and synced",
        detail: "The mount's model now agrees with where the camera is actually pointing.",
      });
    }
  }, [solving, enqueueToast]);

  // ------------------------------------------------------------ the tiles
  //
  // Only TRACKING is editable, so it is the tile the one dial points at. The
  // other three are readouts with no verb behind them (plan 0.3 allows exactly
  // that); the selection still lives in per-sheet state so a later editable
  // tile drops in without moving the dial.
  const [dialTile, setDialTile] = useState<"tracking">("tracking");

  const canSetRate = m?.can_set_tracking_rate === true;
  const trackingOn = tracking.value === true;
  const stopValue: TrackStop = !trackingOn
    ? "off"
    : canSetRate ? (trackingRate.value ?? "sidereal") : "on";
  const stops: { value: TrackStop; label: string }[] = canSetRate
    ? [
        { value: "sidereal", label: "sidereal" },
        { value: "lunar", label: "lunar" },
        { value: "solar", label: "solar" },
        { value: "off", label: "off" },
      ]
    : [{ value: "on", label: "on" }, { value: "off", label: "off" }];

  const trackingReason = firstReason(
    base.lockedReason, laneGoto.lockedReason, lanePark.lockedReason,
    sending ? "a mount command is already on the wire" : null,
    flowExtra,
  );

  const pickStop = (next: TrackStop) => {
    if (trackingReason) { explain(trackingReason); return; }
    if (next === "off") {
      tracking.show(false);
      void act("tracking", () => api.post("/api/mount/tracking?on=false"))
        .then((ok) => { if (!ok) tracking.revert(); });
      return;
    }
    if (next === "on" || !canSetRate) {
      tracking.show(true);
      void act("tracking", () => api.post("/api/mount/tracking?on=true"))
        .then((ok) => { if (!ok) tracking.revert(); });
      return;
    }
    const rate = next as "sidereal" | "lunar" | "solar";
    trackingRate.show(rate);
    void act("tracking_rate", () => api.post(`/api/mount/tracking_rate?rate=${rate}`))
      .then(async (ok) => {
        if (!ok) { trackingRate.revert(); return; }
        // A rate is not a start. A mount sitting with tracking off would take
        // the rate and go on sitting there, so the tile would read "lunar" over
        // a stopped drive.
        if (m?.tracking !== true) {
          tracking.show(true);
          const started = await act("tracking", () => api.post("/api/mount/tracking?on=true"));
          if (!started) tracking.revert();
        }
      });
  };

  // What the mount has ANSWERED, and why the dial is out of service when it is.
  // A control that merely dims tells a screen-reader user nothing.
  const trackingLine = sending === "tracking"
    ? (trackingOn ? "starting…" : "stopping…")
    : sending === "tracking_rate" ? "setting the rate…"
      : laneBusy ? (parking ? "parking" : homing ? "going home" : "mount is moving")
        : tracking.pending || trackingRate.pending ? "waiting for the mount"
          : trackingOn ? "tracking" : "not tracking";

  const pierSide = meridian?.pier_side ?? "unknown";
  const flipSub = meridian?.hours_to_flip != null
    ? `flip in ${fmtFlipIn(meridian.hours_to_flip)}`
    : "no flip tonight";

  const pointing = m?.pointing;
  const pointingSub = pointing
    ? (pointing.reason
        || (pointing.error_arcmin != null ? `${fmtArcmin(pointing.error_arcmin)} off` : "no reason given"))
    : "not reported";

  // ------------------------------------------------------------------ the pad
  //
  // Locked AS A WHOLE while a flow owns the mount (plan B.3 step 3), which
  // SlewPad has no prop for - so the lock lives on the wrapper: dimmed,
  // aria-disabled, still focusable, and a press STATES the reason instead of
  // reaching the pad. The one exception is the STOP bar: plan section C's
  // never-blocked list names it, and a fence around the escape hatch is the
  // exact bug the inventory records three times.
  const padReason = firstReason(base.lockedReason, flowExtra);

  // ------------------------------------------------- the rate and step tiles
  //
  // ONE slew-rate state, and the tile is where it is edited. The pad's centre
  // cell renders the same index through `rateIndex`/`onRateIndex`, so the two
  // are views of one value: a tile that kept its own copy would sit there
  // reading `1.44 deg/s` over a pad still commanding 0.5, and the pad is the
  // half that reaches the mount.
  const maxRate = m?.max_rate_deg_s ?? null;
  const rates = useMemo(() => slewStops(maxRate), [maxRate]);
  const [rateIdx, setRateIdx] = useState(0);
  // The ladder shrinks when a mount reconnects reporting a lower ceiling; the
  // index is clamped rather than reset, so the choice survives where it can.
  const rateAt = Math.min(rateIdx, rates.length - 1);

  // Per-visit, per-axis. Separate because the two axes are not interchangeable:
  // a dec correction and an RA correction on a mis-framed target are routinely
  // different sizes, and one shared step would make the second tap wrong.
  const [raStep, setRaStep] = useState(DEFAULT_STEP_ARCMIN);
  const [decStep, setDecStep] = useState(DEFAULT_STEP_ARCMIN);
  // The last step the ROUTE accepted, per axis. A 422 means the picker asked
  // for something outside 1..600, which is a defect in the picker and not
  // something the operator did, so the tile goes back to a stop that is known
  // to work instead of sitting on one that cannot.
  const lastLegal = useRef({ ra: DEFAULT_STEP_ARCMIN, dec: DEFAULT_STEP_ARCMIN });

  // The reverse toggles are the PAD's, read here at post time from the same
  // store slice the controller reads (`lib/touchStore.ts`). Not copied into
  // local state: the toggles live inside SlewPad, so a copy would go stale the
  // moment somebody flipped one, and the pad and the tiles would disagree about
  // which way west is - on the one screen where that is not survivable.
  const touch = useTouchSettings();
  const touchRef = useRef(touch);
  touchRef.current = touch;

  const nudge = async (axis: Axis, dir: Dir): Promise<void> => {
    if (padReason) { explain(padReason); return; }
    const step = axis === "ra" ? raStep : decStep;
    const reversed = axis === "ra"
      ? touchRef.current.reverseRa
      : touchRef.current.reverseDec;
    const arcmin = step * dir * (reversed ? -1 : 1);
    try {
      await nudgeMount(axis, arcmin);
      lastLegal.current[axis] = step;
    } catch (e) {
      if (isNudgeOutOfRange(e)) {
        // The picker's fault, said in the picker's units, and the picker is put
        // back where it worked.
        showToast("error", NUDGE_OUT_OF_RANGE_NOTE);
        if (axis === "ra") setRaStep(lastLegal.current.ra);
        else setDecStep(lastLegal.current.dec);
        return;
      }
      if (isNudgeAbsent(e)) {
        // An engine older than the route. Say what still works rather than
        // passing on a bare "Not Found", which reads as a broken mount.
        showToast("error", NUDGE_ABSENT_NOTE);
        return;
      }
      // 409: the horizon guard, the sun cone, or the goto lane. The server's
      // sentence is the only one that knows which, so it is shown verbatim.
      showToast("error", (e as Error).message, { verbatim: true });
    }
  };

  const lastExplain = useRef(0);
  const padGuard = (e: SyntheticEvent) => {
    if (!padReason) return;
    const t = e.target as Element | null;
    if (t && typeof t.closest === "function"
      && t.closest('[aria-label="Stop all mount motion"]')) return;
    e.preventDefault();
    e.stopPropagation();
    const now = Date.now();
    if (now - lastExplain.current < 500) return;   // pointerdown then click
    lastExplain.current = now;
    explain(padReason);
  };

  // --------------------------------------------------------- the actions
  const parkReason = firstReason(base.lockedReason, lanePark.lockedReason);
  const unparkReason = firstReason(
    base.lockedReason, laneGoto.lockedReason, lanePark.lockedReason, flowExtra,
  );
  const homeReason = firstReason(
    base.lockedReason, laneGoto.lockedReason, lanePark.lockedReason,
    homing ? HOMING_TITLE : null,
    m?.slewing ? HOME_MOVING_REASON : null,
    flowExtra,
  );
  const solveReason = firstReason(base.lockedReason, laneSolve.lockedReason, flowExtra);
  const gotoReason = firstReason(
    base.lockedReason, laneGoto.lockedReason, lanePark.lockedReason, flowExtra,
  );

  const pressSolve = () => {
    if (sending || solving) return;
    solveAskedFrom.current = newestSolveLine();
    setSending("solve");
    // NOT routed through `act`: the handover has to be ordered. `arm()` must
    // take over BEFORE the request flag drops, or there is a render in between
    // where the button is neither sending nor busy - and that momentary idle
    // reads as the solve having finished.
    api.post("/api/mount/solve_sync")
      .then(() => solve.arm())
      .catch((e) => {
        solveAskedFrom.current = undefined;
        showToast("error", (e as Error).message, { verbatim: true });
      })
      .finally(() => setSending(null));
  };

  // ------------------------------------------------- GO TO A TARGET disclosure
  const [gotoOpen, setGotoOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CatalogEntry[]>([]);
  const [center, setCenter] = useState(true);
  const queryId = useRef(0);
  useEffect(() => {
    // Closed means SILENT: a sheet nobody opened must not put a catalog request
    // on the wire, least of all for a viewer who could never act on the answer.
    if (!gotoOpen) return;
    const id = ++queryId.current;
    const t = setTimeout(async () => {
      try {
        const rows = await api.get<CatalogEntry[]>(`/api/catalog?q=${encodeURIComponent(query)}`);
        // Only the newest query may write: `clearTimeout` cancels a debounce
        // that has not fired, but a request already on the wire still lands.
        if (queryId.current === id) setResults(rows);
      } catch { /* keep the last good list rather than blanking it */ }
    }, 250);
    return () => clearTimeout(t);
  }, [query, gotoOpen]);

  const doGoto = async (r: CatalogEntry) => {
    if (gotoReason) { explain(gotoReason); return; }
    // ALWAYS re-query live altitude at the tap; the catalog row is stale by the
    // time a finger reaches it.
    let pf: PreflightAlt | null = null;
    try {
      pf = await api.get<PreflightAlt>(
        `/api/sequence/preflight?ra_hours=${r.ra_hours}&dec_deg=${r.dec_deg}`,
      );
    } catch {
      pf = null;   // the server's own horizon guard is the net
    }

    if (!pf || pf.verdict === "unknown") {
      const ok = await confirmDialog({
        title: "Location not set",
        body: "Altitude can't be checked until you set your location in Settings. Slew anyway?",
        tone: "warn", mode: "confirm", confirmLabel: "Slew anyway",
      });
      if (!ok) return;
    } else if (pf.verdict === "below") {
      await confirmDialog({
        title: "Below the visible horizon",
        body: pf.alt != null
          ? `${r.id} is at ${pf.alt}° - below the horizon, so it isn't visible now.`
          : `${r.id} is below the horizon, so it isn't visible now.`,
        tone: "danger", mode: "ok",   // single dismiss, no slew, no hold
      });
      return;
    } else if (pf.verdict === "low") {
      const ok = await confirmDialog({
        title: "Low on the horizon",
        body: pf.alt != null
          ? `${r.id} is only ${pf.alt}° up - expect heavy atmosphere and possible obstructions. Slew anyway?`
          : `${r.id} is low on the horizon - expect heavy atmosphere and possible obstructions. Slew anyway?`,
        tone: "warn", mode: "confirm", confirmLabel: "Slew anyway",
      });
      if (!ok) return;
    }

    setSunRefusal(null);
    const ok = await runMotion("goto", r.id, "/api/mount/goto", {
      ra_hours: r.ra_hours, dec_deg: r.dec_deg, center,
      force: pf?.verdict === "low",
    });
    if (ok) {
      enqueueToast({
        level: "success",
        title: `Slewing to ${r.id}`,
        detail: center
          ? "It will centre itself on the target when it arrives."
          : "Centre-after-slew is off, so it will stop wherever the mount thinks the target is.",
      });
    }
  };

  const laneNote = slewingTo
    ? `Slewing to ${slewingTo} - GOTO comes back when the mount stops.`
    : parking ? "Parking - GOTO comes back when the mount stops."
      : homing ? "Going home - GOTO comes back when the mount stops."
        : "The mount is already moving - GOTO comes back when it stops.";

  // ------------------------------------------------------------- header line
  const rateWord = !trackingOn ? "off" : (trackingRate.value ?? "sidereal");
  const stateLine = flowOwns
    ? `owned by the flow · ${sequence?.target ?? "a run"}`
    : !m ? "not connected"
      : m.parked ? "parked · tracking off"
        : m.slewing ? "slewing"
          : `tracking ${rateWord} · idle`;
  const live = (
    <>
      {stateLine}
      {pointing && !pointing.verified && (
        <span style={{ color: "var(--warn)" }}> · pointing not verified</span>
      )}
    </>
  );

  const polarSub = polarRowSub(polar);

  return (
    <Sheet
      title="MOUNT"
      icon={<NxIcon name="mount" size={18} />}
      live={live}
      backLabel="RIG"
      onBack={() => nav.back()}
      data-testid="rig-mount"
    >
      {/* Sticky, top of the body: a goto in flight - or a mount that stopped
          executing slews - narrates itself on the screen that launched it. */}
      <GotoStrip />

      {/* `nx-readouts-wrap` (next.css) wraps the row inside the 420 px panel
          instead of clipping NOT VERIFIED and the azimuth at the sheet's
          edge. */}
      <ReadoutGrid className="nx-readouts-wrap" data-testid="mount-tiles">
        <ReadoutTile
          label="TRACKING"
          value={stopValue}
          sub={RATE_SUB[stopValue]}
          selected={dialTile === "tracking"}
          onSelect={() => setDialTile("tracking")}
          lockedReason={trackingReason}
          onExplain={explain}
          data-testid="tile-tracking"
        />
        <ReadoutTile
          label="PIER"
          value={pierSide}
          sub={flipSub}
          tone={pierSide === "unknown" ? "dim" : undefined}
          data-testid="tile-pier"
        />
        <ReadoutTile
          label="ALT / AZ"
          value={m ? `${m.alt}° / ${m.az}°` : "-"}
          sub={m ? `RA ${m.ra_str} · Dec ${m.dec_str}` : "no pointing reported"}
          tone={m && m.alt < 20 ? "warn" : undefined}
          data-testid="tile-altaz"
        />
        <ReadoutTile
          label="POINTING"
          value={pointing?.verified ? "VERIFIED" : "NOT VERIFIED"}
          sub={pointingSub}
          tone={pointing?.verified ? undefined : "warn"}
          data-testid="tile-pointing"
        />
      </ReadoutGrid>

      <Dial<TrackStop>
        label={`TRACKING · ${stopValue}`}
        hint="drag or tap"
        options={stops}
        value={stopValue}
        onChange={pickStop}
        lockedReason={trackingReason}
        onExplain={explain}
        data-testid="mount-dial"
      />
      <div aria-live="polite" data-testid="mount-tracking-line">
        <Mono size={10.5} tone="dim">{trackingLine}</Mono>
      </div>

      {/* The three tiles that drive the pad (D-RIG-4). They ride the same lock
          the pad does, because a flow that owns the mount owns the steps too. */}
      <Dial<number>
        label={`SLEW RATE · ${rates[rateAt].label}`}
        hint="drag or tap"
        options={rates.map((r, i) => ({ value: i, label: r.label }))}
        value={rateAt}
        onChange={setRateIdx}
        lockedReason={padReason}
        onExplain={explain}
        data-testid="mount-slew-rate"
      />
      <div data-testid="mount-rate-note">
        <Mono size={10.5} tone="dim">{ceilingNote(maxRate)}</Mono>
        <div><Mono size={10} tone="dim">{deadmanNote(maxRate)}</Mono></div>
      </div>

      <Dial<number>
        label={`RA STEP · ${nudgeStopLabel(raStep)}`}
        hint="per tap, east or west"
        options={STEP_OPTIONS}
        value={raStep}
        onChange={setRaStep}
        lockedReason={padReason}
        onExplain={explain}
        data-testid="mount-ra-step"
      />
      <Dial<number>
        label={`DEC STEP · ${nudgeStopLabel(decStep)}`}
        hint="per tap, north or south"
        options={STEP_OPTIONS}
        value={decStep}
        onChange={setDecStep}
        lockedReason={padReason}
        onExplain={explain}
        data-testid="mount-dec-step"
      />

      {/* The pad, as-is. The wrapper carries the flow lock and the 64 px rule. */}
      <style>{PAD_CSS}</style>
      <div
        className={padReason ? "nx-mount-pad nx-locked" : "nx-mount-pad"}
        data-testid="mount-pad"
        aria-disabled={padReason ? true : undefined}
        data-locked={padReason ? "true" : undefined}
        title={padReason ?? undefined}
        onPointerDownCapture={padGuard}
        onClickCapture={padGuard}
        onKeyDownCapture={padGuard}
      >
        <SlewPad
          rates={rates}
          rateIndex={rateAt}
          onRateIndex={setRateIdx}
          maxRateDegS={maxRate}
          onNudge={nudge}
        />
      </div>
      {padReason && (
        <Mono size={10.5} tone="warn">{padReason}</Mono>
      )}

      {/* Offset readout (deviation E10). */}
      <div style={{ textAlign: "center" }} data-testid="mount-offset">
        <Mono size={10.5} tone="dim">
          {pointing?.error_arcmin != null
            ? `offset from target · ${fmtArcmin(pointing.error_arcmin)}`
            : "offset unknown - run SOLVE + SYNC"}
        </Mono>
        {pointing?.error_arcmin != null && (
          <div><Mono size={10} tone="dim">measured at the last solve</Mono></div>
        )}
      </div>

      {/* The three motion verbs. `nx-btn-row` (next.css) wraps them rather
          than running them off the panel's right edge, which is how SOLVE +
          SYNC used to lose half its label. */}
      <div className="nx-btn-row" data-testid="mount-actions">
        <span
          title={parking ? PARKING_TITLE : foreignMotion ? FOREIGN_MOTION_TITLE : undefined}
        >
          {m?.parked ? (
            <ActionButton
              kind="secondary" full
              lockedReason={unparkReason} onExplain={explain}
              busy={sending === "unpark"}
              onPress={() => void act("unpark", () => api.post("/api/mount/unpark"))}
              data-testid="mount-unpark"
            >
              UNPARK
            </ActionButton>
          ) : (
            <ActionButton
              kind="secondary" full
              // Park is the motion-committing ABORT: it stays live during a
              // slew, and during a run, on purpose. Only a park already in
              // flight blocks it, and even then it is a cancel-and-restart,
              // which the accessible name says out loud.
              lockedReason={parkReason} onExplain={explain}
              busy={parking}
              ariaLabel={!parking && foreignMotion ? FOREIGN_MOTION_TITLE : undefined}
              onPress={() => void runMotion("park", undefined, "/api/mount/park")}
              data-testid="mount-park"
            >
              {parking ? "PARKING…" : "PARK MOUNT"}
            </ActionButton>
          )}
        </span>
        {m?.can_find_home && (
          <span>
            <ActionButton
              kind="secondary" full
              lockedReason={homeReason} onExplain={explain}
              busy={homing}
              onPress={() => void runMotion("home", undefined, "/api/mount/home")}
              data-testid="mount-home"
            >
              {homing ? "HOMING…" : "FIND HOME"}
            </ActionButton>
          </span>
        )}
        <span>
          <ActionButton
            kind="secondary" full
            lockedReason={solveReason} onExplain={explain}
            busy={solving}
            onPress={pressSolve}
            data-testid="mount-solve"
          >
            {solving ? "SOLVING…" : "SOLVE + SYNC"}
          </ActionButton>
        </span>
      </div>

      {sunRefusal && (
        <BannerCard
          tone="warn"
          text={sunConeNote(config?.safety?.solar_exclusion_deg)}
          cta={{ label: "SAFETY", onPress: () => nav.sheet("safety") }}
          onDismiss={() => setSunRefusal(null)}
          data-testid="mount-sun-note"
        />
      )}

      {/* GO TO A TARGET. The design puts search in the Sky hub; the catalog
          GOTO and its below-horizon refusals have no other home, and the
          refusals are the load-bearing half. */}
      <ListRow
        icon={<NxIcon name="search" size={16} />}
        title="GO TO A TARGET"
        sub={gotoOpen ? "search the catalog, then slew" : "catalog search and GOTO"}
        right={<Mono size={10} tone="dim">{gotoOpen ? "HIDE" : "SHOW"}</Mono>}
        onPress={() => setGotoOpen((v) => !v)}
        data-testid="mount-goto-toggle"
      />

      {gotoOpen && (
        <Card data-testid="mount-goto">
          <Switch
            checked={center}
            onChange={setCenter}
            label="Centre after slew"
            note="Plate-solves at the target and corrects, instead of trusting the mount's model."
          />
          <div style={{ marginTop: 8 }}>
            <TextInput
              value={query}
              onChange={setQuery}
              placeholder="Search - M42, Andromeda, nebula, galaxy…"
              ariaLabel="Search the catalog by name, catalogue id or object type"
              mono
            />
          </div>

          {canMount && laneBusy && (
            <div style={{ marginTop: 8 }}>
              <BannerCard tone="warn" text={laneNote} data-testid="mount-lane-note" />
            </div>
          )}

          <div style={{ marginTop: 8 }}>
            {results.length === 0 ? (
              <Mono size={10.5} tone="dim">no matches</Mono>
            ) : results.map((r) => (
              <div
                key={r.id}
                style={{
                  display: "flex", alignItems: "center", gap: 8,
                  padding: "8px 0", borderTop: "1px solid var(--line)",
                }}
                data-goto-row={r.id}
              >
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: "block" }}>
                    <Mono size={11} tone={slewingTo === r.id ? "accent" : undefined}>{r.id}</Mono>
                  </span>
                  <span style={{ display: "block" }}>
                    <Mono size={10} tone="dim">
                      {r.name} · {r.type} · mag {fmtMag(r.mag)}
                    </Mono>
                  </span>
                </span>
                <span className={altTone(r.alt)} style={{ fontSize: 11 }}>{fmtAlt(r.alt)}</span>
                {/* Never disabled: framing works offline, with no mount. */}
                <ActionButton
                  kind="ghost"
                  ariaLabel={`Frame ${r.id} in the sky atlas`}
                  onPress={() => openFraming(r)}
                  data-testid={`frame-${r.id}`}
                >
                  FRAME
                </ActionButton>
                <ActionButton
                  kind="secondary"
                  lockedReason={gotoReason} onExplain={explain}
                  busy={slewingTo === r.id}
                  onPress={() => void doGoto(r)}
                  ariaLabel={`Go to ${r.id}`}
                  data-testid={`goto-${r.id}`}
                >
                  GOTO
                </ActionButton>
              </div>
            ))}
          </div>
        </Card>
      )}

      <ListRow
        icon={<BullseyeGlyph />}
        title="POLAR ALIGNMENT"
        sub={polarSub}
        onPress={() => nav.sheet("polar")}
        chevron
        data-testid="mount-polar-row"
      />

      <div style={{ padding: "0 2px" }}>
        <Label size={10}>THE PAD</Label>
        <p style={{
          margin: "4px 0 0", fontSize: 11.5, lineHeight: 1.5, color: "var(--text-faint)",
        }}>
          {FOOTER_NOTE}
        </p>
      </div>
      <div style={{ height: 8 }} />
    </Sheet>
  );
}

/** The alignment summary on the POLAR ALIGNMENT row. PURE, so the row's one
 *  line can be graded without a store: live state outranks the stored verdict,
 *  and "not measured" is said only when nothing was. */
export function polarRowSub(polar: {
  state?: string; total_error?: number; phase?: string; point_index?: number;
} | null | undefined): string {
  if (!polar) return "not measured";
  if (polar.state === "running") {
    return polar.phase === "measuring"
      ? `measuring ${Math.min(3, (polar.point_index ?? 0) + 1)} of 3`
      : "adjusting";
  }
  if (polar.state === "paused") return "paused";
  if (polar.state === "error") return "stopped - open for the reason";
  if (polar.state === "done" && (polar.total_error ?? 0) > 0) {
    const t = polar.total_error as number;
    const verdict = t < 2 ? "excellent" : t < 10 ? "good" : "keep going";
    return `error ${t.toFixed(1)}′ · ${verdict}`;
  }
  return "not measured";
}

/** The fragment's own bullseye, drawn inline: outer circle, dashed green inner
 *  ring, amber dot, white centre. It is not in `icons.tsx` and this task does
 *  not own that file, so it lives here rather than borrowing a glyph that means
 *  something else. */
function BullseyeGlyph(): JSX.Element {
  return (
    <svg viewBox="0 0 40 40" width={22} height={22} aria-hidden="true">
      <circle cx="20" cy="20" r="17" fill="none" stroke="var(--line-bright)" />
      <circle cx="20" cy="20" r="7" fill="none" stroke="var(--good)" strokeDasharray="2 2" />
      <circle cx="15" cy="15" r="2.5" fill="var(--warn)" />
      <circle cx="20" cy="20" r="1.2" fill="var(--text)" />
    </svg>
  );
}

export default MountSheet;
