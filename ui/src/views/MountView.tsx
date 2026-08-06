import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStore, useStatus } from "../store";
import { Panel, Stat, Toggle, IconButton, LockedNote, SegmentedControl } from "../components/ui";
import { confirmDialog } from "../components/ConfirmDialog";
import SlewPad from "../components/SlewPad";
import { Icon } from "../components/icons";
import { useCanControlMount } from "../lib/caps";
import { useBusyOrPending } from "../lib/useBusy";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import type { CatalogEntry, LogLine, PreflightAlt } from "../types";

/** Severity glyph for an altitude cell — shape, not colour-only (spec §5 / critique3 #7). */
function AltGlyph({ alt }: { alt: number }) {
  if (alt < 0) return <span className="text-bad" aria-label="below horizon" title="below the visible horizon">⚠</span>;
  if (alt < 20) return <span className="text-warn" aria-label="low on the horizon" title="low on the horizon">↓</span>;
  return null;
}

/** Catalog columns. `cls` carries the narrow-width collapse: Type and Mag are
 *  lg-only so the table fits the tablet-portrait catalog column without a
 *  sideways scroll (review #6). Header + body cells share this list so they can
 *  never disagree. */
const COLUMNS: { key: string; cls: string }[] = [
  { key: "ID", cls: "" },
  { key: "Name", cls: "" },
  { key: "Type", cls: "hidden lg:table-cell" },
  { key: "Mag", cls: "hidden lg:table-cell" },
  { key: "Alt", cls: "" },
  { key: "", cls: "" },
];

const TRACKING_RATE_OPTIONS: { value: "sidereal" | "lunar" | "solar"; label: string }[] = [
  { value: "sidereal", label: "Sidereal" },
  { value: "lunar", label: "Lunar" },
  { value: "solar", label: "Solar" },
];

/** Show what the user just chose until the rig's own telemetry says the same.
 *
 *  Tracking and tracking-rate are answered by the device the moment the command
 *  lands, but everything this view RENDERS comes off the 2 s status frame — so
 *  the switch sat visibly unmoved for up to two seconds after a deliberate
 *  press, which is exactly how a control teaches you to press it twice. The
 *  chosen value paints immediately and is dropped the moment the mount reports
 *  the same thing, on `revert()` when the POST is refused, or after `timeoutMs`
 *  — so a mount that never adopts it cannot strand a value on screen that no
 *  hardware agrees with. */
function usePendingValue<T>(actual: T | undefined, timeoutMs = 6000) {
  const [pending, setPending] = useState<T | null>(null);
  useEffect(() => {
    if (pending == null) return;
    if (actual === pending) { setPending(null); return; }
    const t = setTimeout(() => setPending(null), timeoutMs);
    return () => clearTimeout(t);
  }, [pending, actual, timeoutMs]);
  return {
    value: pending ?? actual,
    pending: pending != null,
    show: (v: T) => setPending(() => v),
    revert: () => setPending(null),
  };
}

/** The newest line the solver wrote, or null.
 *
 *  Read imperatively out of the store rather than subscribed: this view has no
 *  reason to re-render on every log line, it only needs to know what a solve
 *  left behind when its lane retired. */
function newestSolveLine(): LogLine | null {
  const logs = useStore.getState().logs;
  for (let i = logs.length - 1; i >= 0; i--) {
    if (logs[i].data.source === "solve") return logs[i];
  }
  return null;
}

export default function MountView() {
  const status = useStatus();
  const showToast = useStore((s) => s.showToast);
  const enqueueToast = useStore((s) => s.enqueueToast);
  const openFraming = useStore((s) => s.openFraming);
  const canMount = useCanControlMount(); // viewer => pointing visible, controls read-only
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CatalogEntry[]>([]);
  const [center, setCenter] = useState(true);

  const m = status?.mount;

  // ---------------------------------------------------------- in-flight state
  // TWO KINDS OF ROUTE, TWO KINDS OF TRUTH.
  //
  // tracking / tracking_rate / unpark answer when the DEVICE answers, so their
  // POST promise is an honest source of in-flight state — that is what `pending`
  // covers, and threading it into the disabled expressions is what stops a
  // repeat tap being swallowed by the guard with no feedback at all.
  //
  // park / home / goto / solve_sync do NOT. They `_spawn` a background task and
  // return {"started": …} the instant it is CREATED, so `await api.post(…)`
  // resolves in ~40 ms while ASTAP is still solving and the mount is still
  // swinging. One flag off those promises is how Solve & Sync read ready and
  // re-pressable for a whole solve, and how "Solving…" appeared over an Unpark.
  // Those four read the rig's own lanes instead (lib/useBusy.ts), below.
  const [pending, setPending] = useState<string | null>(null);
  const act = async (what: string, fn: () => Promise<unknown>): Promise<boolean> => {
    if (pending) return false;
    setPending(what);
    try { await fn(); return true; }
    catch (e) { showToast("error", (e as Error).message); return false; }
    finally { setPending(null); }
  };

  // The mount's own answer, shown at once instead of two seconds later.
  const tracking = usePendingValue<boolean>(m?.tracking);
  const trackingRate = usePendingValue<"sidereal" | "lunar" | "solar">(m?.tracking_rate);

  // ---- Solve & Sync: driven by the rig's `solve` lane, not by the request ----
  const solve = useBusyOrPending("solve");
  const solving = solve.busy || pending === "solve";
  // Which line the solver had last written when we asked. The FAILURE half of a
  // solve already reaches the user — `_spawn` logs "solve failed: …" at error
  // level and the store toasts every error line — but a solve that WORKED only
  // ever wrote an info line into the collapsed log drawer, so success and
  // "nothing happened" looked identical. Comparing line IDENTITY rather than a
  // timestamp is what stops an old success line being re-announced when a
  // request never reached the lane at all. `undefined` = not ours to report.
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
        title: "Solved & synced",
        detail: "The mount's model now agrees with where the camera is actually pointing.",
      });
    }
  }, [solving, enqueueToast]);

  // ---- park / home / goto: ONE server lane, three controls ----
  // All three `_spawn("goto", …)`, and park/home do it with replace=True — a
  // second press CANCELS the operation in flight and starts it over, which on a
  // park is another 30–60 s of travel. So the lane drives the in-flight state,
  // and `motion` remembers which of the three THIS tablet asked for: the lane
  // name cannot tell a park from a slew, and neither can `m.slewing`, which the
  // native AM5 leaves reading IDLE for the whole park.
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
  // MOTION THIS TAB DID NOT START. `motion` is a per-tab memory of which of the
  // three we asked for, and it is gone the moment the tab reloads — so after a
  // reload mid-park, or on a second tablet, or when the sequencer parks, every
  // control below fell back to the mount's own flags, and the native AM5 leaves
  // `slewing` false and `parked` false for the WHOLE park. State read IDLE over
  // a mount that was travelling, and Home offered itself normally over a
  // `replace=True` route that would have cancelled the park mid-walk.
  //
  // The lane is the rig's own answer and it survives a reload. What it cannot
  // say is WHICH of park/home/goto is on it — so nothing here may claim
  // "parking"; it says "moving", which is the part that is certainly true.
  const foreignMotion = motionLane.busy && motion == null;
  const mountState = !m ? "—"
    : parking ? "PARKING"
      : homing ? "HOMING"
        : m.parked ? "PARKED"
          : (m.slewing || slewingTo) ? "SLEWING"
            : foreignMotion ? "MOVING"
              : m.tracking ? "TRACKING" : "IDLE";
  const mountStateTone: "warn" | "good" | undefined =
    mountState === "PARKING" || mountState === "HOMING" || mountState === "SLEWING"
      || mountState === "MOVING" ? "warn"
      : mountState === "TRACKING" ? "good" : undefined;
  // Any motion on the shared lane — ours or another client's — blocks a GOTO,
  // because the server 409s a second `goto` outright (it spawns without replace).
  const laneBusy = motionLane.busy || motion != null;

  // ------------------------------------------------------ tracking, in flight
  // `/api/mount/tracking` is NOT one of the `_spawn` routes, so `useBusy` would
  // answer false forever here and a control wired to it would never look busy.
  // The honest in-flight signal is the POST itself — the route awaits the
  // device, and on a serial LX200 mount that is a real round trip, not a
  // microtask. `pending` is that signal, named so the row can say which command
  // is on the wire instead of only dimming (finding 88).
  const trackingSending = pending === "tracking";
  // The `goto` lane IS a useBusy lane, and it is the one that matters to this
  // switch: it carries park, home and goto, and park's entire job is to stop
  // tracking. A tracking command sent into a committed park is a command
  // fighting the rig, from a control that looked completely idle — so the lane
  // takes the switch out of service, with the reason in the row beside it.
  const trackingBlocked = !canMount || !m || pending !== null || laneBusy;

  /** Fire one of the three motions that share the `goto` lane.
   *
   *  `arm()` runs only AFTER the server accepts: the local latch exists to cover
   *  the ≤2 s before the first status frame carries the lane, and arming it on a
   *  REFUSAL (below-horizon, sun cone, 409) would leave every mount control dead
   *  for the latch's six seconds over a request the rig never took. */
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
      showToast("error", (e as Error).message);
      return false;
    }
  };

  // Same debounce as the Atlas's CatalogSearch, and it had the same race:
  // `clearTimeout` cancels a debounce that has not fired, but a request already
  // ON THE WIRE still lands and still calls setResults. Over a phone's link to
  // the Pi an earlier, slower answer can therefore arrive after a later one and
  // repaint the table with results for a query the user has already moved past
  // — a target list that does not match the box you typed in, which on this
  // view is one tap away from a GOTO. Only the newest query may write.
  const queryId = useRef(0);
  useEffect(() => {
    const id = ++queryId.current;
    const t = setTimeout(async () => {
      try {
        const rows = await api.get<CatalogEntry[]>(`/api/catalog?q=${encodeURIComponent(query)}`);
        if (queryId.current === id) setResults(rows);
      }
      catch { /* server not up yet — keep the last good list rather than blanking it */ }
    }, 250);
    return () => clearTimeout(t);
  }, [query]);

  // GOTO re-queries LIVE altitude at the tap (never trusts the stale catalog row,
  // critique2 #6) and guards by verdict (spec §5):
  //   below  -> single-OK dialog, NO slew, NO hold (critique3 #12)
  //   low    -> OK/Cancel "slew anyway", threads force=true so the server guard
  //             doesn't re-block an accepted low slew
  //   unknown-> default site / fetch issue: OK/Cancel "slew anyway"
  const doGoto = async (r: CatalogEntry) => {
    if (!canMount) return; // viewer: GOTO is read-only (buttons are disabled too)
    if (laneBusy) return;  // the lane is taken; the button is disabled to match
    let pf: PreflightAlt | null = null;
    try {
      pf = await api.get<PreflightAlt>(
        `/api/sequence/preflight?ra_hours=${r.ra_hours}&dec_deg=${r.dec_deg}`,
      );
    } catch {
      // preflight fetch failed — treat as unknown (the server horizon guard is the net)
      pf = null;
    }

    if (!pf || pf.verdict === "unknown") {
      const ok = await confirmDialog({
        title: "Location not set",
        body: "Altitude can't be checked until you set your location in Settings. Slew anyway?",
        tone: "warn",
        mode: "confirm",
        confirmLabel: "Slew anyway",
      });
      if (!ok) return;
    } else if (pf.verdict === "below") {
      await confirmDialog({
        title: "Below the visible horizon",
        body: pf.alt != null
          ? `${r.id} is at ${pf.alt}° — below the horizon, so it isn't visible now.`
          : `${r.id} is below the horizon, so it isn't visible now.`,
        tone: "danger",
        mode: "ok", // single dismiss, no slew, NO hold
      });
      return;
    } else if (pf.verdict === "low") {
      const ok = await confirmDialog({
        title: "Low on the horizon",
        body: pf.alt != null
          ? `${r.id} is only ${pf.alt}° up — expect heavy atmosphere and possible obstructions. Slew anyway?`
          : `${r.id} is low on the horizon — expect heavy atmosphere and possible obstructions. Slew anyway?`,
        tone: "warn",
        mode: "confirm",
        confirmLabel: "Slew anyway",
      });
      if (!ok) return;
    }

    const ok = await runMotion("goto", r.id, "/api/mount/goto", {
      ra_hours: r.ra_hours, dec_deg: r.dec_deg, center,
      force: pf?.verdict === "low",
    });
    // Say what the rig accepted and what it will do next, the way the Atlas's
    // identical handoff does. Without it a GOTO that worked and a GOTO that was
    // never sent looked the same: the row went back to normal and the mount, on
    // a mount that reports IDLE while it slews, said nothing either.
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

  // BOTH tracks must be `minmax(0,…)`. An implicit/`1fr` grid track floors at
  // its item's MIN-CONTENT, and the target-catalog table below is legitimately
  // ~423px wide (six columns), so a `1fr` track can never shrink below that.
  //
  // The base track was fixed first (phone: the column was forced to 456px
  // inside 348px). The md+ track was NOT, and three reviewers then measured the
  // identical failure one breakpoint up on the primary field device: at 820
  // portrait `main` is 732 wide but this grid rendered 763, every GOTO landed
  // at x=787→850 (a 33px sliver reading "GO…") and CENTER AFTER SLEW was cut in
  // half. `main` is overflow-x-hidden and the document does not scroll
  // horizontally, so that content was unreachable by any finger gesture.
  // `minmax(0,1fr)` on the second track lets the catalog column shrink to the
  // space that actually exists (376px at 820), and the narrow-width column
  // collapse below (Type/Mag are lg-only) keeps the table itself inside it.
  return (
    <div className="grid gap-4 grid-cols-[minmax(0,1fr)]
      md:grid-cols-[minmax(240px,300px)_minmax(0,1fr)]
      lg:grid-cols-[minmax(290px,340px)_minmax(0,1fr)]">
      <div className="flex flex-col gap-4 min-w-0">
        <Panel title="Pointing" right={!canMount && <ReadOnlyBadge />}>
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            <Stat label="RA (J2000)" value={m?.ra_str ?? "—"} />
            <Stat label="Dec (J2000)" value={m?.dec_str ?? "—"} />
            <Stat label="Altitude" value={m ? `${m.alt}°` : "—"}
              tone={m && m.alt < 20 ? "warn" : undefined} />
            <Stat label="Azimuth" value={m ? `${m.az}°` : "—"} />
            {/* The lane-derived states come FIRST. A native AM5 leaves
                `slewing` false for the whole park and the whole home walk, so
                this stat read IDLE while the mount was physically travelling —
                the same lie the Park button beside it used to tell. */}
            <Stat label="State" value={mountState} tone={mountStateTone} />
          </div>
          <div className="mt-4 border-t border-line pt-3 flex flex-col gap-3">
            <div className="flex items-center gap-3">
              <Toggle checked={!!tracking.value} disabled={trackingBlocked}
                onChange={(v) => {
                  // Guard BEFORE the optimistic paint. `disabled` is what makes
                  // this unreachable today, but `act()` also silently answers
                  // false while another command is in flight — and reaching
                  // `tracking.show(v)` first would flip the switch and flip it
                  // straight back. A control that visibly moves and then undoes
                  // itself is worse than one that does not move.
                  if (trackingBlocked) return;
                  tracking.show(v);
                  void act("tracking", () => api.post(`/api/mount/tracking?on=${v}`))
                    .then((ok) => { if (!ok) tracking.revert(); });
                }} label="Tracking" />
              {/* The switch's own state is visible; what is NOT visible is
                  whether the mount has answered yet, and why the control is
                  dead when it is. That is what this line carries — so it is a
                  live region, because a screen-reader user gets nothing at all
                  from a switch that merely dims. (The `Toggle` primitive takes
                  no aria-busy and is shared by thirty call sites; the reason
                  belongs beside this control, not inside the primitive.) */}
              <span className="label" aria-live="polite">
                {trackingSending
                  ? (tracking.value ? "starting…" : "stopping…")
                  : laneBusy
                    ? (parking ? "parking" : homing ? "going home" : "mount is moving")
                    : "tracking"}
              </span>
              <div className="flex-1" />
              {/* Home — the reference position you START from, and the missing
                  third of this row. Park says "stop and stay stopped"; Home says
                  "go to the known place and be ready". Offered only when the
                  mount advertises can_find_home, so a scope without a home
                  sensor never sees a control that would 400. It stays available
                  while PARKED because homing a parked mount is exactly how you
                  begin a session: the server unparks as part of the move. */}
              {m?.can_find_home && (
                <button
                  className="btn tap min-h-[44px]"
                  // Hard-disabled while OUR home is running, not merely
                  // relabelled: the route spawns with replace=True, so a second
                  // press cancels the walk in progress and starts another one.
                  //
                  // …and while ANY motion holds the shared lane, ours or not.
                  // Home is not an abort — Park is, which is why Park stays live
                  // below and this does not. `find_home()` is unpark → park →
                  // unpark, so a replace=True cancel landing mid-sequence leaves
                  // the mount PARKED: the button looks like it worked and the
                  // next slew is refused, which is the exact trap that function's
                  // own docstring says it exists to avoid.
                  disabled={!canMount || homing || laneBusy || !!m?.slewing}
                  aria-busy={homing || undefined}
                  title={homing
                    ? "On its way home — pressing again would cancel this and start over"
                    : (laneBusy || m?.slewing)
                      ? "The mount is already moving — Home comes back when it stops. "
                        + "Homing unparks, walks to the sensor and unparks again, and "
                        + "interrupting that can leave the mount parked."
                      : "Slew to the mount's home position and leave it ready to use"}
                  onClick={() => void runMotion("home", undefined, "/api/mount/home")}
                >
                  {homing ? "Homing…" : "Home"}
                </button>
              )}
              {m?.parked ? (
                <button className="btn tap min-h-[44px]" disabled={!canMount || pending !== null}
                  onClick={() => void act("unpark", () => api.post("/api/mount/unpark"))}>Unpark</button>
              ) : (
                // Disabled only while THIS park is running. It stays live during a
                // slew on purpose — park is the server's motion-committing abort
                // (replace=True cancels an in-flight goto and stows the mount),
                // and taking that away would remove a way to stop a bad slew.
                <button className="btn tap min-h-[44px]" disabled={!canMount || parking}
                  aria-busy={parking || undefined}
                  // The one control here that must NOT go quiet on a motion it
                  // did not start: park is the abort. But it may not go on
                  // presenting itself as a fresh, consequence-free action
                  // either — if the motion already on the lane IS a park (a
                  // reload, the other tablet, the dawn-park daemon), this press
                  // cancels it and starts the travel over. Said in the
                  // accessible name as well as the tooltip, because `title`
                  // never fires on the tablet this rig is driven from.
                  aria-label={!parking && foreignMotion
                    ? "Park — the mount is already moving. Parking takes that move over; "
                      + "if it is itself a park, this restarts it."
                    : undefined}
                  title={parking
                    ? "Parking — 30–60 s. Pressing again would cancel this park and start another."
                    : foreignMotion
                      ? "The mount is already moving. Park takes that move over and stows it — "
                        + "and if the move is itself a park, this restarts it."
                      : "Stop tracking and stow the mount at its park position"}
                  onClick={() => void runMotion("park", undefined, "/api/mount/park")}>
                  {parking ? "Parking…" : "Park"}
                </button>
              )}
            </div>
            {/* Tracking rate (2026-07-21): only mounts that support it advertise
                can_set_tracking_rate; disabled for viewers like every motion control. */}
            {m?.can_set_tracking_rate && (
              <div className="flex items-center gap-3">
                <span className="label shrink-0">rate</span>
                <div className="flex-1" />
                <SegmentedControl<"sidereal" | "lunar" | "solar">
                  options={TRACKING_RATE_OPTIONS}
                  value={trackingRate.value}
                  // Same gate as the switch above it, for the same reason: a
                  // rate change is a tracking command, and the row above states
                  // why both are out of service.
                  disabled={trackingBlocked}
                  ariaLabel="Tracking rate"
                  onChange={(v) => {
                    trackingRate.show(v);
                    void act("tracking_rate", () => api.post(`/api/mount/tracking_rate?rate=${v}`))
                      .then((ok) => { if (!ok) trackingRate.revert(); });
                  }}
                />
              </div>
            )}
          </div>
        </Panel>

        <Panel title="Slew Pad" right={!canMount && <ReadOnlyBadge />}>
          {/* 3B predictable fixed-rate slew + tap-to-pulse pad (replaces the old
              slow/med/fast 3-chip pad). Owns rate selector, STOP bar, reverse
              toggles, alt-guard, NINA mode. SlewPad itself hard-guards on the cap
              (it can't post moves for a viewer); the disabled inputs here are the
              visible read-only affordance. */}
          <SlewPad />
          <div className="flex items-center justify-center gap-2 mt-4 border-t border-line pt-3">
            <button className="btn tap min-h-[44px]" disabled={!canMount || solving}
              aria-busy={solving || undefined}
              title={solving ? "Exposing and solving — this takes a few seconds" : undefined}
              onClick={() => {
                if (pending || solving) return;
                solveAskedFrom.current = newestSolveLine();
                setPending("solve");
                // NOT routed through `act`: the handover has to be ordered.
                // `arm()` must take over BEFORE the request flag is dropped, or
                // there is a render in between where the button is neither
                // sending nor busy — and that momentary "idle" is read as the
                // solve having finished, which retires the completion report
                // for a solve that has not started yet.
                api.post("/api/mount/solve_sync")
                  .then(() => solve.arm())
                  .catch((e) => {
                    solveAskedFrom.current = undefined;
                    showToast("error", (e as Error).message);
                  })
                  .finally(() => setPending(null));
              }}>
              {solving ? "Solving…" : <><Icon name="align" size={14} className="inline -mt-0.5 mr-1" />Solve &amp; Sync</>}
            </button>
          </div>
          <p className="text-[12px] text-dim text-center mt-2">
            plate-solves current frame, syncs mount model
          </p>
        </Panel>
      </div>

      {/* `min-w-0` belt-and-braces with the `minmax(0,1fr)` track: a grid item's
          `min-width: auto` resolves to its content-based minimum, which would
          let the panel overflow a track that is legitimately narrower. */}
      <Panel title="Target Catalog" className="min-w-0"
        right={
          <label className="flex items-center gap-2 shrink-0">
            <span className="label whitespace-nowrap">center after slew</span>
            <Toggle checked={center} onChange={setCenter} label="Center after slew" />
          </label>
        }>
        {/* A placeholder is NOT an accessible name — it is not exposed by the
            accname algorithm in every AT, and it disappears as soon as the user
            types. The other of the two controls still reported unnamed after the
            UX-review sweep (#26). */}
        <input className="field mb-3" placeholder="Search — M42, Andromeda, nebula, galaxy…"
          aria-label="Search the catalog by name, catalogue id or object type"
          value={query} onChange={(e) => setQuery(e.target.value)} />
        {/* Why every GOTO in the table is inert, and when it stops being. The
            server spawns `goto` without replace, so a second one is refused
            outright — and the refusal ("'goto' is already running") is the kind
            of sentence nobody should have to read. Names the target when the
            slew is ours, because "already moving" is not an answer to "moving
            where?". */}
        {canMount && laneBusy && (
          <LockedNote className="mb-3" reason={
            slewingTo
              ? `Slewing to ${slewingTo} — GOTO comes back when the mount stops.`
              : parking
                ? "Parking — GOTO comes back when the mount stops."
                : homing
                  ? "Going home — GOTO comes back when the mount stops."
                  : "The mount is already moving — GOTO comes back when it stops."} />
        )}
        {/* `overflow-x-auto` is load-bearing on a phone. The catalog table has
            six columns and a min-content of ~423px, and a container that only
            scrolls VERTICALLY still propagates that min-content outward — so
            the panel rendered 457px inside a 348px column and `main`, being
            overflow-x-hidden, CLIPPED the excess instead of scrolling it. The
            Alt column and the GOTO button were unreachable on a 380px screen.
            Making this a horizontal scroll container both drops its
            min-content contribution to 0 (the panel can now shrink) and gives
            the wide table somewhere legitimate to scroll. */}
        <div className="overflow-auto min-w-0 max-h-[58vh] -mx-1 px-1">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left">
                {/* Narrow-width column collapse: below lg the catalog column is
                    ~376px, and Type/Mag are the two cells a user does not need
                    to press GOTO. Hiding them (rather than letting the table
                    force a sideways scroll) is what keeps ID / Name / Alt /
                    GOTO all on screen at 820 portrait. */}
                {COLUMNS.map((c) => (
                  <th key={c.key} className={`label pb-2 pr-3 font-medium ${c.cls}`}>{c.key}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                // THE ROW CARRIES THE SLEW, not just the button in it. While a
                // slew runs every GOTO in the table is `disabled` and painted at
                // 0.35 opacity (index.css:407), and the target's own button was
                // marked only by accent border + accent text — two colour
                // channels, both multiplied by that 0.35 against a near-black
                // panel, which crushes the border's 3.17:1 step to 1.39:1. Under
                // :root.night everything in play is red, so the row being slewed
                // to looked exactly like the twenty rows merely blocked behind
                // it. index.css:154-157 states the rule: any status that must
                // survive night mode needs a NON-HUE channel. Three, here — a
                // 2px bar in the margin, a ▸ before the id, and a fill — none of
                // which the disabled button's opacity applies to, because none
                // of them are on the button.
                <tr key={r.id} className={`border-t border-line/60 hover:bg-raise/80 transition-colors
                  ${slewingTo === r.id ? "border-l-2 border-l-accent bg-accent/10" : ""}`}>
                  <td className="mono py-2 pr-3 text-accent whitespace-nowrap">
                    {slewingTo === r.id && <span aria-hidden className="mr-1">▸</span>}
                    {r.id}
                  </td>
                  {/* Type + Mag fold into the Name cell below lg so the
                      information is not LOST by the column collapse — it just
                      stops occupying two columns the GOTO button needs. */}
                  <td className="pr-3">
                    {r.name}
                    <span className="lg:hidden block text-[10px] text-dim">
                      {r.type} · mag {r.mag.toFixed(1)}
                    </span>
                  </td>
                  <td className="pr-3 text-dim hidden lg:table-cell">{r.type}</td>
                  <td className="mono pr-3 hidden lg:table-cell">{r.mag.toFixed(1)}</td>
                  <td className={`mono pr-3 whitespace-nowrap ${r.alt < 20 ? "text-warn" : r.alt > 40 ? "text-good" : ""}`}>
                    <span className="inline-flex items-center gap-1">
                      {r.alt.toFixed(0)}°<AltGlyph alt={r.alt} />
                    </span>
                  </td>
                  <td className="text-right">
                    <div className="inline-flex items-center gap-1.5 justify-end">
                      {/* Frame this object in the Atlas (telescope/frame glyph, NOT
                          the Align icon — spec §6 / C3-A10). Works offline; no mount
                          needed, so it is never disabled. */}
                      <IconButton
                        icon="frame"
                        label={`Frame ${r.id} in the Sky Atlas`}
                        onClick={() => openFraming(r)}
                      />
                      {/* The label stays four characters in every state on
                          purpose: this column is the one the tablet-portrait
                          work above fought for, and a wider in-flight word
                          ("SLEWING") pushes the table's min-content back past
                          the space that exists. The row still says it — accent
                          chrome plus aria-busy — and the line above the table
                          says it in words, including which target. */}
                      {/* `!` on both, and none to give: index.css carries no
                          @layer wrapper, so `.btn`'s unlayered
                          `background: var(--bg-raise)` beats any Tailwind
                          background utility from @layer utilities no matter the
                          specificity. The `bg-accent/10` that used to sit here
                          never rendered a pixel; the row above carries the fill
                          instead, where nothing unlayered is competing. */}
                      <button className={`btn tap min-h-[44px] !px-3
                          ${slewingTo === r.id ? "!border-accent !text-accent" : ""}`}
                        disabled={!canMount || !m || laneBusy}
                        aria-busy={slewingTo === r.id || undefined}
                        onClick={() => doGoto(r)}>
                        GOTO
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {results.length === 0 && <p className="text-dim text-xs py-4">no matches</p>}
        </div>
      </Panel>
    </div>
  );
}
