import { api } from "../api";
import { useFrameSettings, useStore, usePolar, useProviders, useStatus } from "../store";
import { PolarReticle, knobHint, polarTier, polarInstruction, type KnobDir } from "../components/polar";
import GuideFramePreview from "../components/GuideFramePreview";
import { Icon } from "../components/icons";
import { Panel, Led, HonestButton } from "../components/ui";
import ProviderBadge from "../components/ProviderBadge";
import { accessPhrase, useCanControlMount } from "../lib/caps";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import PolarQuickBar from "../components/PolarQuickBar";
import PolarSolveRing from "../components/PolarSolveRing";
import CameraDial from "../components/ui/CameraDial";
import { cameraDialCategories } from "../components/ui/CameraPickers";
import type { PolarState } from "../types";
import { useEffect, useState } from "react";

/* Fields the native TPPA engine (server/astrodeck/polar/native.py) adds to the
   canonical `polar` payload beyond PolarState. The store forwards the whole
   event object, so they're present at runtime; typed here (this view owns the
   wizard) since the shared PolarState is intentionally backend-agnostic. */
type NativePolar = Omit<PolarState, "state"> & {
  /* "pausing" — Pause has been ASKED FOR but the rig has not stopped yet. The
     server's pause is a flag the drivers read between steps, so a 12° RA slew
     already in flight runs on for another 5-15 s; it publishes "pausing" on the
     POST and only "paused" once a driver has actually parked
     (server/astrodeck/polar/session.py). This view is the one that must not
     round that up: the user is at the mount with a hex key. */
  state: PolarState["state"] | "pausing";
  phase?: "measuring" | "adjusting";
  point_index?: number;
  az_direction?: KnobDir | null;
  alt_direction?: KnobDir | null;
  flags?: string[];
  position_angle_spread_deg?: number | null;
  /* consecutive failed live updates during the adjust phase — how far behind
     the displayed number is right now. Published as it happens (2026-08-08);
     before that the count only reached the log, so a panel could sit frozen
     for half a minute while someone turned a bolt against it. */
  stale_updates?: number;
  /* what the native driver is doing THIS second — published around each solve
     frame so 15 s of ASTAP never looks like a hang (2026-08-07). */
  activity?: "exposing" | "solving" | null;
  /* NOT solve_settings. Those left this event in #176: `start()` resets the
     session state to `_idle()`, which carries no such key, and the client
     applies polar events wholesale — so beginning an alignment reverted every
     face on this screen to a default while the engine kept using the
     operator's values. They ride their own `frames` event now. */
};

export default function PolarView() {
  const polar = usePolar() as NativePolar;
  const showToast = useStore((s) => s.showToast);
  const canMount = useCanControlMount(); // polar alignment slews the mount
  // UX-04: warn when the resolved polar provider is the SIMULATOR (fabricated az/alt).
  const isSimProvider = useProviders()?.polar_align?.kind === "sim";
  // "pausing" is still a LIVE run — the driver holds the camera and the mount
  // may be mid-slew — so Stop stays armed and Start stays locked out.
  const running = polar.state === "running" || polar.state === "paused"
    || polar.state === "pausing";
  const pausing = polar.state === "pausing";

  // THE GAP BETWEEN "STARTED" AND "RUNNING". POST /api/polar/start returns the
  // instant `asyncio.create_task` hands back a task object; the driver's first
  // `_publish(state="running")` lands a beat later (a solve setup, a provider
  // resolve). For that second or so `polar.state` is still "idle", so the panel
  // read "Not started — press Start Alignment to measure." over a run that had
  // already committed the mount, Stop sat grey, and the re-enabled Start 409'd
  // ("polar alignment is already running") on the second tap people reasonably
  // gave it.
  //
  // Same contract as lib/useBusy's `useBusyOrPending` — latch locally, hand over
  // to server truth the moment it arrives, and EXPIRE so a start that never took
  // (403, dropped socket) can never leave Start dead — but keyed on the polar
  // stream rather than a busy lane: the polar session runs on its own task, not
  // through the hub's `_spawn` lanes, so `useBusy("polar")` has nothing to read.
  //
  // The handover test is "the stream reports a LIVE state", not "the state
  // changed": `start()` resets its own state dict without publishing, so a
  // second alignment begun from a finished one leaves `polar.state === "done"`
  // on the wire until the new driver's first frame.
  const [starting, setStarting] = useState(false);
  useEffect(() => {
    if (!starting) return;
    if (running || polar.state === "error") { setStarting(false); return; }
    const t = setTimeout(() => setStarting(false), 6000);
    return () => clearTimeout(t);
  }, [starting, running, polar.state]);
  // Everything that must treat the alignment as LIVE — Start locked out, Stop
  // armed, the reticle panel not claiming "not started".
  const live = running || starting;

  const az = polar.az_error, alt = polar.alt_error, total = polar.total_error;
  const src = polar.source as string | null;

  // A fitted error exists once we've left the measuring phase (native emits
  // phase:"measuring" during the 3 solves; NINA/sim leave phase undefined and
  // just start streaming a non-zero error).
  const hasReading =
    polar.phase !== "measuring" && (total > 0 || polar.state === "done");
  const measuring = polar.phase === "measuring";
  // `hasReading` deliberately admits a terminal "done" with no number so the
  // readout stops saying "waiting"; a CLAIM about the alignment needs the
  // stronger test — a session that ended before any fit has nothing to report.
  const measuredTotal = Number.isFinite(total) && total > 0;

  // Measure→Adjust progress. point_index advances 0..2 as each solve lands;
  // adjusting (or any streamed reading) means all three are in.
  const measured = polar.phase === "adjusting"
    ? 3
    : polar.phase === "measuring"
      ? Math.min(3, (polar.point_index ?? -1) + 1)
      : hasReading ? 3 : 0;
  const adjusting = polar.phase === "adjusting" || (polar.phase === undefined && hasReading);
  const showPhase = polar.state !== "idle";

  // Tiered verdict (spec §HERO2): <2′ excellent · 2–10′ good · >10′ keep going.
  const tier = polarTier(total);
  const verdict = tier === "excellent"
    ? { led: "on" as const, tone: "text-good", text: "Excellent — stop here" }
    : tier === "good"
      ? { led: "warn" as const, tone: "text-warn", text: "Good — keep refining" }
      : { led: "bad" as const, tone: "text-bad", text: "Keep going" };

  const azHint = hasReading ? knobHint(polar.az_direction, az, "az") : null;
  const altHint = hasReading ? knobHint(polar.alt_direction, alt, "alt") : null;

  // The engine's one warning that survives the pole guard: the three measurement
  // frames differed by more than a pure RA rotation (a meridian flip or a rotator
  // step moved the camera/pier angle), so the fit is measuring that motion too.
  // A warning, never a refusal — but the number has to reach the person holding
  // the bolt, or they turn it on a reading that isn't real.
  const paSpreadLarge = polar.flags?.includes("position_angle_spread_large") ?? false;
  const paSpread = polar.position_angle_spread_deg;

  // The engine's OTHER warning, which used to arrive on the wire and go
  // nowhere: the starting error was large enough that the small-angle model the
  // live re-scale uses does not hold, so the number moves the wrong distance per
  // turn of the bolt. The pole guard already refuses the truly absurd case; this
  // is the band where the fit is real but the ADJUST phase is not reliable, and
  // it looked exactly like a trustworthy one.
  const initialErrorLarge = polar.flags?.includes("initial_error_large") ?? false;

  // How far behind the number on screen is. The adjust loop counts consecutive
  // failed live updates and used to keep them to the log until the session
  // ended — so a panel could sit frozen for half a minute while someone turned
  // a bolt against a reading that had stopped responding to them.
  const staleUpdates = polar.stale_updates ?? 0;

  const sourceLabel = src === "nina" ? "NINA TPPA"
    : src === "native" ? "AstroDeck native"
    : src === "sim" ? "simulator" : null;

  // UX-16: local in-flight guard so a slow POST round-trip disables the trigger
  // buttons immediately (no dead-feeling double-fire before the server publishes state).
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
  };
  const act = async (fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    try { await run(fn); } finally { setBusy(false); }
  };
  // STOP IS NOT ONE OF THEM. `busy` is a guard against double-firing ONE
  // control; routing the emergency stop through it made Pause's in-flight POST
  // — 40 ms of it, or several seconds on a phone over a relay — the thing that
  // disabled the red button while the mount was still swinging. A stop is
  // idempotent server-side, so there is nothing here worth guarding against.
  /* The solve frame's live settings + the dial that edits them. The values are
     the `solve` SCOPE (#176) — server truth, cold-seeded by the WS hello, no
     longer a mirrored default spread over a field of the polar event that
     `start()` erases. The categories/presets come from the SHARED builder, so
     the dial over the reticle and the pickers in the sticky bar can never
     offer different numbers for the same setting. */
  const solveSettings = useFrameSettings("solve");
  const setFrameSettings = useStore((s) => s.setFrameSettings);
  const putSolve = (patch: Parameters<typeof setFrameSettings>[1]) =>
    setFrameSettings("solve", patch);
  const wheel = useStatus()?.filterwheel;
  const currentFilter = typeof wheel?.position === "number"
    ? wheel?.names?.[wheel.position] ?? null : null;
  const solveDial = cameraDialCategories({
    values: solveSettings,
    // The FULL slot list: the builder drops the opaque ones itself now, so no
    // caller can forget (FocusView did).
    filters: wheel?.names ?? [],
    opaqueSlots: wheel?.opaque ?? [],
    currentFilter,
    onExposure: (s) => putSolve({ exposure_s: s }),
    onGain: (g) => putSolve({ gain: g }),
    onBinning: (b) => putSolve({ binning: b }),
    onOffset: (o) => putSolve({ offset: o }),
    onFilter: (f) => putSolve({ filter: f }),
  });

  const stopReason: string | null =
    !canMount ? `Stopping an alignment needs ${accessPhrase("control.mount")}.`
      : !live ? "No alignment is running — nothing to stop."
        : null;

  return (
    <div className="flex flex-col gap-4">
      {/* Always-visible status: activity + the error number + the solve-frame
          settings fold. The three things this screen made you scroll for
          (operator feedback 2026-08-07); renders nothing while idle. */}
      <PolarQuickBar polar={polar} />

      {/* Tier-2 (doc 04 §6): a solve/geometry failure is sticky and unmissable —
          shape (square Led) + word, not color alone, so it survives night. */}
      {polar.state === "error" && (
        <div className="flex items-start gap-3 border border-bad bg-bad/10 px-4 py-3 rounded"
          role="alert">
          <Led state="bad" label="alignment error" />
          <div className="min-w-0">
            <p className="text-bad text-sm font-medium">Polar alignment stopped</p>
            <p className="text-dim text-xs mt-0.5">{polar.message || "the alignment could not complete"}</p>
          </div>
        </div>
      )}

      {/* TWO COLUMNS FROM sm, NOT lg. Polar alignment is done crouched at the
          mount with the phone in one hand, turning a bolt with the other, and a
          phone in landscape is 667-932px wide — under lg's 1024, so the reticle
          and the error readout used to stack and you had to scroll between the
          thing you aim and the number that says whether you are winning.
          sm (640) covers the narrowest phone landscape (SE, 667). The middle
          band gets plain even columns; the fixed 360px sidebar only returns at
          lg where there is room for it. Portrait is unchanged. */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-[1fr_360px]">
        <Panel title="Polar Alignment"
          right={
            <div className="flex items-center gap-2">
              <ProviderBadge cap="polar_align" />
              {/* "pausing" keeps the blink: something is still MOVING, and the
                  blink is the channel that survives a red-light screen where
                  hue barely reads. */}
              <span className={`text-[11px] tracking-widest uppercase ${
                starting ? "text-accent blink"
                  : polar.state === "done" ? "text-good"
                  : polar.state === "running" ? "text-accent blink"
                  : pausing ? "text-warn blink"
                  : polar.state === "paused" ? "text-warn"
                  : polar.state === "error" ? "text-bad" : "text-dim"}`}>
                {starting ? "starting" : pausing ? "stopping" : polar.state}
              </span>
            </div>
          }>
          {/* relative: PolarSolveRing overlays the reticle's top-RIGHT corner
              (the zoom announce owns the top-left, inside the SVG). The ring
              is the "why hasn't the error shown up yet" answer, rendered
              where the eye already is. */}
          <div className="relative">
            <PolarReticle
              az={az}
              alt={alt}
              azDir={polar.az_direction}
              altDir={polar.alt_direction}
              active={hasReading}
            />
            <PolarSolveRing
              activity={polar.activity}
              exposureS={solveSettings.exposure_s}
            />
            {/* The solve frame's settings, as the app's one fan-out dial —
                the same control the preview surfaces carry (2026-08-08).
                Tap the disc, the categories bloom, tap one and its values
                replace them. Offset is a text box because its useful values
                are a continuum. */}
            {canMount && (
              <CameraDial
                label="Solve frame settings"
                summary={`${solveSettings.exposure_s}s g${solveSettings.gain}`}
                categories={solveDial}
              />
            )}
          </div>
          <p className="text-center text-xs text-dim mt-2 min-h-4">{polar.message || " "}</p>
        </Panel>

        <div className="flex flex-col gap-4">
          {/* ORDER IS A REACH DECISION (phone-layout rule: order panels by
              what the user touches). Portrait stacks this column under the
              reticle, so the error panel — the number the whole screen exists
              to drive toward — comes FIRST and shares the first screenful
              with the reticle; the Phase wizard and Control follow. The panel
              is deliberately COMPACT (one verdict row, tight knob rows): at
              text-5xl with stacked rows it did not fit beside the reticle on
              any phone, which is what kept it below the fold. */}
          <Panel title="Total error">
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className={`font-display font-semibold text-3xl mono tabular-nums ${
                hasReading ? "text-accent" : "text-faint"}`}>
                {hasReading ? total.toFixed(1) : "—"}
              </span>
              <span className="text-dim text-sm">arcmin</span>
              {hasReading && (
                <span className="flex items-center gap-2 ml-auto">
                  <Led state={verdict.led} label={verdict.text} />
                  <span className={`text-sm font-medium ${verdict.tone}`}>{verdict.text}</span>
                </span>
              )}
            </div>

            {hasReading ? (
              /* WHICH ADJUSTMENT, not how good it is — the verdict beside the
                 number already says that. Its thresholds (30′/10′/1′) are its
                 own and deliberately do not match the verdict's 2′/10′: above
                 30′ no bolt has the travel to fix it, so "keep going" would
                 send the user turning a knob that cannot reach. */
              <p className="text-xs text-dim mt-1.5 leading-relaxed">
                {polarInstruction(total)}
              </p>
            ) : measuring || live ? (
              <div className="flex items-center gap-2 mt-2">
                <Led state="busy" label="measuring" />
                <span className="text-sm text-dim">
                  {measuring ? "Measuring axis…"
                    : starting ? "Starting — the mount is committed."
                      : "Waiting for solve…"}
                </span>
              </div>
            ) : (
              /* UX-19: idle — no busy LED / "Waiting for solve…" before Start is pressed. */
              <p className="text-sm text-faint mt-2">Not started — press Start Alignment to measure.</p>
            )}

            {/* ABOVE the knob rows on purpose: those arrows are what the user
                would otherwise act on, and this says the reading behind them is
                not trustworthy. Same shape as the sim-provider warning below so
                it reads as a caveat under red light, where hue alone doesn't. */}
            {paSpreadLarge && (
              <p className="text-xs text-warn mt-3 leading-relaxed border border-warn/40 bg-warn/5 px-2.5 py-2"
                role="alert">
                Camera angle moved {paSpread != null ? `${paSpread.toFixed(1)}°` : "well past 5°"} across
                the three measurement frames — that is not a pure rotation in RA, so this fit
                measured that motion too and the numbers below are not trustworthy. Re-run the
                alignment without a meridian crossing (and without moving the rotator) before you
                turn a bolt.
              </p>
            )}

            {initialErrorLarge && (
              <p className="text-xs text-warn mt-3 leading-relaxed border border-warn/40 bg-warn/5 px-2.5 py-2"
                data-polar-initial-large role="alert">
                The starting error was large enough that the live number below re-scales
                approximately — it will move, but not by the amount you actually turned. Get the
                bolts roughly right, then re-run the alignment to refine from a smaller error.
              </p>
            )}

            {/* STALENESS, WHILE IT IS HAPPENING. This used to be log-only until
                the session ended, so the panel could sit frozen for half a
                minute while someone turned a bolt against a number that had
                stopped answering them. */}
            {staleUpdates > 0 && (
              <p className="text-xs text-warn mt-3 leading-relaxed border border-warn/40 bg-warn/5 px-2.5 py-2"
                data-polar-stale role="alert">
                Not updating — the last {staleUpdates} measurement{staleUpdates === 1 ? "" : "s"} did
                not solve, so the number below is older than your most recent adjustment. Wait for it
                to catch up before turning anything else.
              </p>
            )}

            {/* Which way to turn each bolt — arrow + magnitude + word, from the
                native engine's knob labels (fallback: the error's sign). */}
            <div className="flex flex-col mt-2">
              <div className="flex items-center gap-3 border-t border-line py-1.5">
                <span className="label w-16">Azimuth</span>
                <span className="text-accent text-base w-4 text-center">{azHint?.arrow ?? ""}</span>
                <span className="text-dim text-xs">{azHint?.text ?? "az bolt"}</span>
                <span className="mono text-sm tabular-nums ml-auto">
                  {hasReading ? `${Math.abs(az).toFixed(1)}′` : "—"}
                </span>
              </div>
              <div className="flex items-center gap-3 border-t border-line py-1.5">
                <span className="label w-16">Altitude</span>
                <span className="text-accent text-base w-4 text-center">{altHint?.arrow ?? ""}</span>
                <span className="text-dim text-xs">{altHint?.text ?? "alt bolt"}</span>
                <span className="mono text-sm tabular-nums ml-auto">
                  {hasReading ? `${Math.abs(alt).toFixed(1)}′` : "—"}
                </span>
              </div>
            </div>

            {polar.progress > 0 && polar.progress < 1 && (
              <div className="progress-track mt-3">
                <div className="progress-fill" style={{ width: `${polar.progress * 100}%` }} />
              </div>
            )}
          </Panel>

          {/* Phase wizard: Measure (3 solve tiles) → Adjust */}
          {showPhase && (
            <Panel title="Phase" right={sourceLabel && (
              <span className="text-[11px] tracking-widest uppercase text-dim">{sourceLabel}</span>
            )}>
              <div className="flex items-center gap-2 flex-wrap">
                <div className="flex items-center gap-1.5">
                  {[1, 2, 3].map((n) => {
                    const filled = measured >= n;
                    return (
                      <span key={n}
                        className={`led-letter ${filled ? "bg-good text-accent-ink" : "border border-line text-faint"}`}
                        aria-label={`solve ${n} ${filled ? "done" : "pending"}`}>
                        {n}
                      </span>
                    );
                  })}
                </div>
                <span className={`text-[11px] tracking-widest uppercase ${adjusting ? "text-dim" : "text-accent"}`}>
                  measure
                </span>
                <span className="text-faint">→</span>
                <span className={`text-[11px] tracking-widest uppercase ${adjusting ? "text-accent" : "text-faint"}`}>
                  adjust
                </span>
              </div>
              {measuring && (
                <p className="text-xs text-dim mt-2">Plate-solving the mount's axis — hold steady.</p>
              )}
            </Panel>
          )}

          <Panel title="Control" right={!canMount && <ReadOnlyBadge />}>
            <p className="text-xs text-dim mb-3 leading-relaxed">
              Runs three-point polar alignment: rotate in RA, plate-solve, and stream the
              live error here as you turn the mount's altitude / azimuth bolts. The engine in
              use is shown in the header.
            </p>
            {isSimProvider && (
              <p className="text-xs text-warn mb-3 leading-relaxed border border-warn/40 bg-warn/5 px-2.5 py-2">
                Simulator provider — alignment values are fabricated, not measured from your sky.
                Bundle or install a plate solver (ASTAP) for a real polar alignment.
              </p>
            )}
            {/* The gap Pause cannot close. The server's pause is a flag the
                driver reads BETWEEN steps, so the slew or exposure already
                running has to finish first — and this is the screen where the
                user's hands are on the mount, so "paused" arriving early is not
                a cosmetic lie. Say what is still true and name the way out. */}
            {pausing && (
              <p className="text-xs text-warn mb-3 leading-relaxed border border-warn/40 bg-warn/5 px-2.5 py-2"
                role="alert">
                Stopping — the exposure or RA rotation already under way has to finish
                first, usually 5–15 s. <strong className="font-medium">The mount may still be
                moving: keep clear of the bolts</strong> until this says paused. Stop aborts it
                now if you need it stopped sooner.
              </p>
            )}
            <div className="flex flex-col gap-2">
              <button className="btn btn-accent" disabled={!canMount || live || busy}
                aria-busy={starting || undefined}
                onClick={() => {
                  setStarting(true);
                  void act(async () => {
                    // A refused start (409 "already running", 403) never becomes
                    // a run, so drop the latch at once rather than making the
                    // user wait out its expiry to try again.
                    try { await api.post("/api/polar/start"); }
                    catch (e) { setStarting(false); throw e; }
                  });
                }}>
                <Icon name="align" size={14} className="inline -mt-0.5 mr-1" />
                {starting ? "Starting…" : "Start Alignment"}
              </button>
              <div className="grid grid-cols-2 gap-2">
                {pausing ? (
                  /* Neither Pause (already asked) nor Resume (nothing has
                     stopped yet, so resuming would be resuming a run that never
                     paused) — the button reports, and stays dead until the
                     driver acks. Stop beside it is the live escape. */
                  <button className="btn" disabled aria-live="polite">Stopping…</button>
                ) : polar.state === "paused" ? (
                  <button className="btn" disabled={!canMount || !running || busy}
                    onClick={() => act(() => api.post("/api/polar/resume"))}>Resume</button>
                ) : (
                  <button className="btn" disabled={!canMount || !running || busy}
                    onClick={() => act(() => api.post("/api/polar/pause"))}>Pause</button>
                )}
                {/* The one control on this screen whose whole job is to be
                    reachable. `disabled` would take its reason out of the
                    accessibility tree and leave a grey rectangle that says
                    nothing to the person crouched at the mount; HonestButton
                    (house rule §11.8) stays focusable and pressable and STATES
                    which of the two refusals applies. */}
                {/* THE LATCH IS RELEASED BY THE STOP THAT LANDED, NOT BY THE
                    PRESS. `setStarting(false)` used to run synchronously in
                    this handler — before /api/polar/stop had been sent, let
                    alone answered — and on that one frame the whole screen
                    un-committed itself over a rig that was still moving: the
                    accent Start button came back to life reading "Start
                    Alignment", the header chip dropped from a blinking
                    "starting" to a grey "idle", the readout reverted to "Not
                    started — press Start Alignment to measure.", and this red
                    button dimmed to its locked face whose stated reason is "No
                    alignment is running — nothing to stop." If the stop then
                    never landed (403, 409, a dropped relay), nothing restored
                    any of it: the Start press the re-enabled button invited was
                    answered 409 "polar alignment is already running", and a
                    second press of THIS button no longer re-sent the stop — it
                    toasted "nothing to stop" at someone whose hands were about
                    to go on the bolts. Keeping the latch until the POST
                    resolves means a refused or lost stop leaves Stop armed and
                    re-pressable, which is the one behaviour this control exists
                    for. (Deliberately not routed through the `starting` expiry
                    effect above: that only hands over on a LIVE state, and a
                    stop that works publishes "idle".) */}
                <HonestButton
                  className="btn btn-danger"
                  reason={stopReason}
                  onExplain={(r) => showToast("info", r)}
                  onClick={() => {
                    void run(async () => {
                      await api.post("/api/polar/stop");
                      setStarting(false);
                    });
                  }}
                >
                  Stop
                </HonestButton>
              </div>
            </div>
            {/* WHAT ACTUALLY HAPPENED, not merely that it stopped happening.
                "done" is the terminal state for every ending the drivers have:
                the native engine converging inside its threshold, but ALSO its
                240-update safety cap expiring, and NINA closing the TPPA socket
                after any single measurement. So a run abandoned at 25′ used to
                print a green "✓ aligned to 25.3′" directly under a red "Keep
                going" — the one line on the screen a user reads as permission to
                walk away from the mount. */}
            {polar.state === "done" && !starting && (measuredTotal ? (
              tier === "excellent" ? (
                <p className="text-good text-xs mono mt-3">✓ aligned to {total.toFixed(1)}′ total error</p>
              ) : (
                <p className={`text-xs mt-3 leading-relaxed ${verdict.tone}`}>
                  Session ended at {total.toFixed(1)}′ — that is where it stopped, not
                  where it should be. {polarInstruction(total)} Then run Start Alignment
                  again to re-measure.
                </p>
              )
            ) : (
              <p className="text-dim text-xs mt-3 leading-relaxed">
                Session ended before any error was measured — nothing was aligned.
              </p>
            ))}
          </Panel>

          {/* Guide view so the user can watch the field during alignment. */}
          <GuideFramePreview compact />
        </div>
      </div>
    </div>
  );
}
