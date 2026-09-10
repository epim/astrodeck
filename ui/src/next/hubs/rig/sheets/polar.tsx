// polar.tsx - the POLAR ALIGNMENT sub-sheet, two deep under MOUNT
// (plan hub-rig.md B.4, fragment proto/device-polar.html).
//
// This is the screen a person reads CROUCHED AT THE MOUNT with a hex key in the
// other hand, so three rules run through the whole file:
//
//   1. Nothing here re-implements the instrument. `PolarReticle` owns its
//      hysteresis zoom, its eased dot, its log-space ring tween, its
//      reduced-motion snap and its twice-announced rescale; its skew vector is
//      drawn DOT -> POLE so the arrowhead names the CORRECTION, not the error
//      (a 2026-09-07 fix). Re-pointing that arrow here would undo it.
//   2. STOP is never gated on anything but "who are you" and "is anything
//      running". It is deliberately NOT routed through the double-fire guard: a
//      40 ms in-flight Pause POST must never disable the emergency stop, and the
//      `starting` latch is released by the stop that LANDED, not by the press.
//   3. Every warning the engine can raise has a home, verbatim. A fit measured
//      through a meridian crossing, a starting error too large for the rescale
//      model, an adjust phase that has stopped answering, a simulator provider
//      fabricating the numbers - each one is the difference between turning a
//      bolt usefully and turning it against a reading that is not real.
//
// Deviations (plan E17, E18, E27): the design's `CameraDial` fan-out over the
// reticle is dropped (there is no preview stage in this sheet to hang it over -
// the QuickBar pickers carry the same `solve`-scope writes); the summary's
// check glyph is dropped (no glyphs, house rule); and the MOUNT MODEL card's
// "counterweights down" is a fixture, replaced by what the mount actually
// reports.

import { useEffect, useState, type JSX } from "react";
import type { SheetProps } from "../../sheets";
import {
  ActionButton, Bar, BannerCard, Card, Label, Mono, Sheet,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useLock } from "../../../lib/gateHook";
import { api } from "../../../../api";
import {
  useConfig, useFrameSettings, useLogs, usePolar, useProviders, useStatus, useStore,
} from "../../../../store";
import { accessPhrase, useCanConfigBackend, useCanControlMount } from "../../../../lib/caps";
import { eligibleTaskDrivers } from "../../../../lib/equipment";
import {
  effectiveProviders, entryOf, isProfileOverride, providerKey,
} from "../../../../lib/effective";
import {
  DEFAULT_PROVIDERS, providerWriteNote, providerWriteTarget,
} from "../../../../lib/providerWrite";
import { writeProviderOverride } from "../../../../lib/providerSave";
import { clearProfileOverrides, listDrivers } from "../../../../api/backends";
import {
  PolarReticle, knobHint, polarInstruction, polarTier, type KnobDir,
} from "../../../../components/polar";
import PolarQuickBar from "../../../../components/PolarQuickBar";
import PolarSolveRing from "../../../../components/PolarSolveRing";
import GuideFramePreview from "../../../../components/GuideFramePreview";
import { fmtArcmin, fmtFlipIn } from "./mount";
import type { DriverInfo, LogLine, PolarState } from "../../../../types";

/* Fields the native TPPA engine adds to the canonical `polar` payload beyond
   `PolarState`. The store forwards the whole event object, so they are present
   at runtime; typed here because this sheet is the wizard.

   "pausing" is the one that must not be rounded up: the server's pause is a
   flag the drivers read BETWEEN steps, so a 12 degree RA slew already in flight
   runs on for another 5-15 s. The user is at the mount. */
type NativePolar = Omit<PolarState, "state"> & {
  state: PolarState["state"] | "pausing";
  phase?: "measuring" | "adjusting";
  point_index?: number;
  az_direction?: KnobDir | null;
  alt_direction?: KnobDir | null;
  flags?: string[];
  position_angle_spread_deg?: number | null;
  stale_updates?: number;
  activity?: "exposing" | "solving" | null;
};

// ------------------------------------------------------------------- copy

export const PA_SPREAD_WARNING = (spread: string): string =>
  `Camera angle moved ${spread} across the three measurement frames - that is `
  + "not a pure rotation in RA, so this fit measured that motion too and the "
  + "numbers below are not trustworthy. Re-run the alignment without a meridian "
  + "crossing (and without moving the rotator) before you turn a bolt.";

export const INITIAL_ERROR_WARNING =
  "The starting error was large enough that the live number below re-scales "
  + "approximately - it will move, but not by the amount you actually turned. "
  + "Get the bolts roughly right, then re-run the alignment to refine from a "
  + "smaller error.";

export const staleWarning = (n: number): string =>
  `Not updating - the last ${n} measurement${n === 1 ? "" : "s"} did not solve, `
  + "so the number below is older than your most recent adjustment. Wait for it "
  + "to catch up before turning anything else.";

export const SIM_PROVIDER_WARNING =
  "Simulator provider - alignment values are fabricated, not measured from your "
  + "sky. Bundle or install a plate solver (ASTAP) for a real polar alignment.";

export const PAUSING_WARNING =
  "Stopping - the exposure or RA rotation already under way has to finish first, "
  + "usually 5-15 s. The mount may still be moving: keep clear of the bolts "
  + "until this says paused. Stop aborts it now if you need it stopped sooner.";

export const NO_ALIGNMENT_TO_STOP = "No alignment is running - nothing to stop.";

const FOOTER_NOTE =
  "The 3-point routine slews the mount three times, then shows the live error "
  + "and knob arrows here, big enough to read at the mount. Solve + sync "
  + "refreshes the pointing model in one tap.";

// ==========================================================================

export function PolarSheet(_props: SheetProps): JSX.Element {
  const polar = usePolar() as NativePolar;
  const status = useStatus();
  const config = useConfig();
  const providers = useProviders();
  const canMount = useCanControlMount();
  const canConfig = useCanConfigBackend();
  const showToast = useStore((s) => s.showToast);
  const solveSettings = useFrameSettings("solve");
  const logs = useLogs();

  const base = useLock({ cap: "control.mount", needsRole: "telescope" });
  const lanePolar = useLock({ busyLane: "polar" });
  const laneGoto = useLock({ busyLane: "goto" });
  const laneSolve = useLock({ busyLane: "solve" });
  const explain = base.onExplain;

  // "pausing" is still a LIVE run - the driver holds the camera and the mount
  // may be mid-slew - so Stop stays armed and Start stays locked out.
  const running = polar.state === "running" || polar.state === "paused"
    || polar.state === "pausing";
  const pausing = polar.state === "pausing";

  // THE GAP BETWEEN "STARTED" AND "RUNNING". POST /api/polar/start returns the
  // instant the task object exists; the driver's first publish lands a beat
  // later. For that second the panel read "Not started" over a run that had
  // already committed the mount, and the re-enabled Start 409'd on the second
  // tap people reasonably gave it. Latch locally, hand over to server truth,
  // and EXPIRE so a start that never took cannot leave Start dead.
  const [starting, setStarting] = useState(false);
  useEffect(() => {
    if (!starting) return;
    if (running || polar.state === "error") { setStarting(false); return; }
    const t = setTimeout(() => setStarting(false), 6000);
    return () => clearTimeout(t);
  }, [starting, running, polar.state]);
  const live = running || starting;

  const az = polar.az_error, alt = polar.alt_error, total = polar.total_error;

  const hasReading = polar.phase !== "measuring" && (total > 0 || polar.state === "done");
  const measuring = polar.phase === "measuring";
  // `hasReading` deliberately admits a terminal "done" with no number so the
  // readout stops saying "waiting"; a CLAIM about the alignment needs the
  // stronger test.
  const measuredTotal = Number.isFinite(total) && total > 0;

  const measured = polar.phase === "adjusting" ? 3
    : polar.phase === "measuring" ? Math.min(3, (polar.point_index ?? -1) + 1)
      : hasReading ? 3 : 0;
  const adjusting = polar.phase === "adjusting" || (polar.phase === undefined && hasReading);
  const showPhase = polar.state !== "idle";

  const tier = polarTier(total);
  const verdict = tier === "excellent" ? { tone: "good" as const, text: "Excellent - stop here" }
    : tier === "good" ? { tone: "warn" as const, text: "Good - keep refining" }
      : { tone: "bad" as const, text: "Keep going" };

  const azHint = hasReading ? knobHint(polar.az_direction, az, "az") : null;
  const altHint = hasReading ? knobHint(polar.alt_direction, alt, "alt") : null;

  const paSpreadLarge = polar.flags?.includes("position_angle_spread_large") ?? false;
  const paSpread = polar.position_angle_spread_deg;
  const initialErrorLarge = polar.flags?.includes("initial_error_large") ?? false;
  const staleUpdates = polar.stale_updates ?? 0;
  const isSimProvider = providers?.polar_align?.kind === "sim";

  // Local in-flight guard so a slow POST round trip stops a double-fire.
  // STOP IS NOT ONE OF THEM: routing the emergency stop through it made Pause's
  // in-flight POST the thing that disabled the red button while the mount was
  // still swinging. A stop is idempotent server-side.
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
  };
  const act = async (fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    try { await run(fn); } finally { setBusy(false); }
  };

  // ------------------------------------------------------------- the gates
  const startReason = base.lockedReason
    ?? lanePolar.lockedReason
    ?? laneGoto.lockedReason
    ?? (live ? "An alignment is already running - stop it before starting another." : null)
    ?? (busy ? "A polar command is already on the wire." : null);

  const pauseReason = base.lockedReason
    ?? (!running ? "No alignment is running - nothing to pause." : null)
    ?? (busy ? "A polar command is already on the wire." : null);

  // The ONE control on this screen whose whole job is to be reachable. No link
  // clause, no lane clause, no busy clause - only who you are and whether
  // anything is running.
  const stopReason: string | null = !canMount
    ? `Stopping an alignment needs ${accessPhrase("control.mount")}.`
    : !live ? NO_ALIGNMENT_TO_STOP : null;

  const solveReason = base.lockedReason ?? laneSolve.lockedReason;

  // ---------------------------------------------------------- provider row
  //
  // Fetched only for a principal who could write it: a viewer's read-only
  // render must not put a request on the wire it can never act on.
  const [drivers, setDrivers] = useState<DriverInfo[]>([]);
  const [providerBusy, setProviderBusy] = useState(false);
  const [providerErr, setProviderErr] = useState<string | null>(null);
  useEffect(() => {
    if (!canConfig) return;
    let alive = true;
    void listDrivers()
      .then((r) => { if (alive) setDrivers(r.drivers); })
      .catch(() => { /* the row degrades to the resolved label alone */ });
    return () => { alive = false; };
  }, [canConfig]);

  const providerEntry = entryOf<string>(config, providerKey("polar_align"));
  const providerTarget = providerWriteTarget(providerEntry);
  const providerNote = providerWriteNote(providerTarget);
  // The WINNING layer, with the raw global block underneath as the degradation
  // path for the WS hello bootstrap (which carries no `effective`).
  const providerValue: string = effectiveProviders(config).polar_align
    ?? config?.providers?.polar_align
    ?? DEFAULT_PROVIDERS.polar_align;
  const providerEligible = eligibleTaskDrivers("polar_align", drivers);
  const providerReason = useLock({ cap: "config.backend" }).lockedReason;

  const pickProvider = async (value: string) => {
    if (providerReason) { explain(providerReason); return; }
    if (providerBusy || value === providerValue) return;
    setProviderBusy(true);
    setProviderErr(null);
    try {
      await writeProviderOverride({
        cap: "polar_align",
        value,
        target: providerTarget,
        // The RAW global block, never a draft seeded from the effective values:
        // posting the latter copies every profile-won value down into global
        // config as a side effect of editing this one row.
        globals: config?.providers,
      });
    } catch (e) {
      setProviderErr((e as Error).message);
    } finally {
      setProviderBusy(false);
    }
  };

  const unpin = async () => {
    const id = providerEntry?.profile_id;
    if (!id) return;
    setProviderBusy(true);
    try { await clearProfileOverrides(id, { providers: ["polar_align"] }); }
    catch (e) { setProviderErr((e as Error).message); }
    finally { setProviderBusy(false); }
  };

  // ------------------------------------------------------- the mount model
  const meridian = status?.meridian;
  const m = status?.mount;
  const lastSolve = newestSolveLine(logs);
  const lastSolveText = lastSolve
    ? `${new Date(lastSolve.ts * 1000).toLocaleTimeString()} · ${lastSolve.data.message}`
    : "not solved this session";
  const pierText = meridian && meridian.pier_side !== "unknown"
    ? `${meridian.pier_side}${meridian.hours_to_flip != null ? ` · flip in ${fmtFlipIn(meridian.hours_to_flip)}` : ""}`
    : "unknown";
  const homeParkText = !m ? "no mount connected"
    : m.can_find_home
      ? `home sensor · ${m.parked ? "parked" : "unparked"}`
      : `no home sensor · ${m.parked ? "parked" : "unparked"}`;

  // --------------------------------------------------------- header line
  const headLine = polar.state === "error"
    ? (polar.message || "the alignment could not complete")
    : starting ? "starting - the mount is committed"
      : pausing ? "stopping - the mount may still be moving"
        : polar.state === "running"
          ? (measuring ? `measuring ${Math.max(1, measured)} of 3` : "adjusting - turn the bolts")
          : polar.state === "paused" ? "paused"
            : hasReading && measuredTotal
              ? `${fmtArcmin(total)} total error · ${verdict.text.toLowerCase()}`
              : "not started";
  const headTone = polar.state === "error" ? "var(--bad)"
    : live ? "var(--warn)"
      : hasReading && tier === "excellent" ? "var(--good)"
        : undefined;

  return (
    <Sheet
      title="POLAR ALIGNMENT"
      icon={<NxIcon name="mount" size={18} />}
      live={<span style={headTone ? { color: headTone } : undefined}>{headLine}</span>}
      backLabel="MOUNT"
      onBack={() => nav.back()}
      data-testid="rig-polar"
    >
      {/* Sticky: activity, the az/alt split and the solve-frame pickers. The
          three things this screen made you scroll for. `solveSettings` rides
          its own scope and is NOT reset by start(). */}
      <PolarQuickBar polar={polar} />

      {polar.state === "error" && (
        <Card tone="accent" data-testid="polar-error">
          <div role="alert">
            <Label size={11}>POLAR ALIGNMENT STOPPED</Label>
            <p style={{ margin: "4px 0 0", fontSize: 11.5, color: "var(--bad)" }}>
              {polar.message || "the alignment could not complete"}
            </p>
          </div>
        </Card>
      )}

      {/* ---------------------------------------------------- reticle card */}
      <Card data-testid="polar-reticle-card">
        <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ position: "relative", flex: "1 1 160px", maxWidth: 220 }}>
            <PolarReticle
              az={az}
              alt={alt}
              azDir={polar.az_direction}
              altDir={polar.alt_direction}
              active={hasReading}
            />
            <PolarSolveRing activity={polar.activity} exposureS={solveSettings.exposure_s} />
          </div>
          <div style={{ flex: "1 1 160px", minWidth: 0, display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <span
                style={{
                  fontFamily: "'IBM Plex Mono', monospace", fontSize: 24,
                  fontVariantNumeric: "tabular-nums",
                  color: hasReading ? `var(--${verdict.tone})` : "var(--text-faint)",
                }}
                data-testid="polar-total"
              >
                {hasReading ? `${total.toFixed(1)}′` : "-"}
              </span>
              <Label size={10}>POLAR ERROR</Label>
            </div>

            {/* The arrow is a SHAPE channel, and it is the one that survives a
                red-light screen where every hue collapses. It comes from
                `knobHint`, which decodes the engine's own knob label and falls
                back to the signed error's sense - never re-derived here. */}
            <Mono size={11}>
              {`alt ${hasReading ? fmtSigned(alt) : "-"}`}
              {altHint ? ` ${altHint.arrow} ${altHint.text} the alt knob` : ""}
            </Mono>
            <Mono size={11}>
              {`az ${hasReading ? fmtSigned(az) : "-"}`}
              {azHint ? ` ${azHint.arrow} ${azHint.text}` : ""}
            </Mono>

            {hasReading ? (
              <>
                <Mono size={11} tone={verdict.tone}>{verdict.text}</Mono>
                <Mono size={10.5} tone="dim">{polarInstruction(total)}</Mono>
              </>
            ) : (
              <Mono size={10.5} tone="dim">
                {measuring ? "Plate-solving the mount's axis - hold steady."
                  : starting ? "Starting - the mount is committed."
                    : live ? "Waiting for solve…"
                      : "Not started - press Start Alignment to measure."}
              </Mono>
            )}
          </div>
        </div>
      </Card>

      {/* --------------------------------------------------- warning stack */}
      {paSpreadLarge && (
        <BannerCard
          tone="warn"
          text={PA_SPREAD_WARNING(paSpread != null ? `${paSpread.toFixed(1)}°` : "well past 5°")}
          data-testid="polar-pa-spread"
        />
      )}
      {initialErrorLarge && (
        <BannerCard tone="warn" text={INITIAL_ERROR_WARNING} data-testid="polar-initial-large" />
      )}
      {staleUpdates > 0 && (
        <BannerCard tone="warn" text={staleWarning(staleUpdates)} data-testid="polar-stale" />
      )}
      {isSimProvider && (
        <BannerCard tone="warn" text={SIM_PROVIDER_WARNING} data-testid="polar-sim" />
      )}
      {pausing && (
        <BannerCard tone="warn" text={PAUSING_WARNING} data-testid="polar-pausing" />
      )}

      {/* ------------------------------------------------------ phase panel */}
      {showPhase && (
        <Card data-testid="polar-phase">
          <Label size={10}>PHASE</Label>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}>
            {[1, 2, 3].map((n) => (
              <span
                key={n}
                aria-label={`solve ${n} ${measured >= n ? "done" : "pending"}`}
                style={{
                  width: 22, height: 22, borderRadius: 6,
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  fontFamily: "'IBM Plex Mono', monospace", fontSize: 11,
                  border: "1px solid var(--line-bright)",
                  background: measured >= n ? "var(--good)" : "transparent",
                  color: measured >= n ? "var(--bg)" : "var(--text-faint)",
                }}
              >
                {n}
              </span>
            ))}
            <Mono size={10} tone={adjusting ? "dim" : "accent"}>measure</Mono>
            <Mono size={10} tone="dim">to</Mono>
            <Mono size={10} tone={adjusting ? "accent" : "dim"}>adjust</Mono>
          </div>
          {polar.progress > 0 && polar.progress < 1 && (
            <div style={{ marginTop: 8 }}>
              <Bar value={polar.progress} tone="accent" height={3} label="Alignment progress" />
            </div>
          )}
          {measuring && (
            <p style={{ margin: "8px 0 0", fontSize: 11, color: "var(--text-dim)" }}>
              Plate-solving the mount&apos;s axis - hold steady.
            </p>
          )}
        </Card>
      )}

      {/* ------------------------------------------------------ mount model */}
      <Card data-testid="polar-model">
        <Label size={10}>MOUNT MODEL</Label>
        <ModelRow k="last solve" v={lastSolveText} />
        <ModelRow k="pier side" v={pierText} />
        <ModelRow k="home / park" v={homeParkText} />
      </Card>

      {/* ---------------------------------------------------------- actions */}
      <div style={{ display: "flex", gap: 8 }}>
        <span style={{ flex: 1, display: "flex" }}>
          <ActionButton
            kind="primary" size="lg" full
            lockedReason={startReason} onExplain={explain}
            busy={starting}
            onPress={() => {
              setStarting(true);
              void act(async () => {
                // A refused start never becomes a run, so drop the latch at once
                // rather than making the user wait out its expiry.
                try { await api.post("/api/polar/start"); }
                catch (e) { setStarting(false); throw e; }
              });
            }}
            data-testid="polar-start"
          >
            {starting ? "STARTING…" : "START POLAR ALIGN"}
          </ActionButton>
        </span>
        <span style={{ flex: 1, display: "flex" }}>
          <ActionButton
            kind="secondary" size="lg" full
            lockedReason={solveReason} onExplain={explain}
            onPress={() => void act(() => api.post("/api/mount/solve_sync"))}
            data-testid="polar-solve"
          >
            SOLVE + SYNC
          </ActionButton>
        </span>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <span style={{ flex: 1, display: "flex" }}>
          {pausing ? (
            // Neither Pause (already asked) nor Resume (nothing has stopped yet).
            // The button reports and stays dead until the driver acks; STOP
            // beside it is the live escape.
            <ActionButton
              kind="secondary" full
              lockedReason="Already stopping - the driver has not acknowledged yet."
              onExplain={explain}
              onPress={() => { /* unreachable while locked */ }}
              data-testid="polar-pause"
            >
              STOPPING…
            </ActionButton>
          ) : polar.state === "paused" ? (
            <ActionButton
              kind="secondary" full
              lockedReason={pauseReason} onExplain={explain}
              onPress={() => void act(() => api.post("/api/polar/resume"))}
              data-testid="polar-pause"
            >
              RESUME
            </ActionButton>
          ) : (
            <ActionButton
              kind="secondary" full
              lockedReason={pauseReason} onExplain={explain}
              onPress={() => void act(() => api.post("/api/polar/pause"))}
              data-testid="polar-pause"
            >
              PAUSE
            </ActionButton>
          )}
        </span>
        <span style={{ flex: 1, display: "flex" }}>
          <ActionButton
            kind="danger" full
            lockedReason={stopReason} onExplain={explain}
            // THE LATCH IS RELEASED BY THE STOP THAT LANDED, NOT BY THE PRESS.
            // Dropping it synchronously un-committed the whole screen over a rig
            // that was still moving, and a stop that never landed left nothing
            // to press again.
            onPress={() => {
              void run(async () => {
                await api.post("/api/polar/stop");
                setStarting(false);
              });
            }}
            data-testid="polar-stop"
          >
            STOP
          </ActionButton>
        </span>
      </div>

      {/* --------------------------------------------- session-ended summary */}
      {polar.state === "done" && !starting && (
        <div data-testid="polar-summary">
          {measuredTotal ? (
            tier === "excellent" ? (
              <Mono size={11} tone="good">{`aligned to ${total.toFixed(1)}′ total error`}</Mono>
            ) : (
              <Mono size={11} tone={verdict.tone}>
                {`Session ended at ${total.toFixed(1)}′ - that is where it stopped, not where `
                  + `it should be. ${polarInstruction(total)} Then run Start Alignment again to re-measure.`}
              </Mono>
            )
          ) : (
            <Mono size={11} tone="dim">
              Session ended before any error was measured - nothing was aligned.
            </Mono>
          )}
        </div>
      )}

      {/* -------------------------------------------------------- provider */}
      <Card data-testid="polar-provider">
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <Label size={10}>PROVIDER</Label>
          <select
            className="nx-input"
            style={{ maxWidth: 220, flex: 1 }}
            value={providerValue}
            aria-label="Polar alignment provider"
            aria-disabled={providerReason ? true : undefined}
            data-locked={providerReason ? "true" : undefined}
            title={providerReason ?? undefined}
            data-testid="polar-provider-pick"
            onChange={(e) => void pickProvider(e.target.value)}
          >
            <option value="auto">Auto (best available)</option>
            {providerEligible.map((d) => (
              <option key={d.id} value={d.id}>{d.label}</option>
            ))}
            {providerValue !== "auto" && !providerEligible.some((d) => d.id === providerValue) && (
              // Sticky: a stored value no longer offered stays listed rather
              // than vanishing, because that is exactly when the user most needs
              // to see the literal string the profile is carrying.
              <option value={providerValue}>
                {providerValue === "backend" ? "Backend (legacy)" : providerValue}
              </option>
            )}
          </select>
        </div>
        {providers?.polar_align && (
          <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--text-dim)" }}>
            {providers.polar_align.label}
            {providers.polar_align.reason ? ` - ${providers.polar_align.reason}` : ""}
          </p>
        )}
        {providerNote && (
          <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--warn)" }}
            data-testid="polar-provider-note">
            {providerNote}
          </p>
        )}
        {isProfileOverride(providerEntry) && providerEntry?.profile_id && canConfig && (
          <div style={{ marginTop: 6 }}>
            <ActionButton
              kind="ghost"
              busy={providerBusy}
              onPress={() => void unpin()}
              data-testid="polar-provider-unpin"
            >
              CLEAR THE PROFILE PIN
            </ActionButton>
            <Mono size={10.5} tone="dim">
              Clearing it hands this row back to the global setting.
            </Mono>
          </div>
        )}
        {providerErr && <Mono size={10.5} tone="bad">{providerErr}</Mono>}
      </Card>

      {/* The field, while the bolts turn. Polls only while its own toggle is
          open, so a closed preview costs the rig nothing. */}
      <GuideFramePreview compact />

      <div style={{ padding: "0 2px" }}>
        <p style={{
          margin: 0, fontSize: 11.5, lineHeight: 1.5, color: "var(--text-faint)",
        }}>
          {FOOTER_NOTE}
        </p>
      </div>
      <div style={{ height: 8 }} />
    </Sheet>
  );
}

/** Signed arcminutes for a knob row: the SIGN is the information here (which
 *  way the axis sits), unlike the mount sheet's unsigned pointing error. */
function fmtSigned(v: number): string {
  if (!Number.isFinite(v)) return "-";
  const s = v > 0 ? "+" : v < 0 ? "-" : "";
  return `${s}${fmtArcmin(v)}`;
}

function ModelRow({ k, v }: { k: string; v: string }): JSX.Element {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", gap: 10, marginTop: 6,
    }}>
      <Mono size={11} tone="dim">{k}</Mono>
      <span style={{ textAlign: "right", minWidth: 0 }}><Mono size={11}>{v}</Mono></span>
    </div>
  );
}

/** The newest line the SOLVER wrote. PURE over the log ring the sheet already
 *  subscribes to - reading `getState()` here would leave the row frozen on
 *  whatever was in the log the last time something else re-rendered. */
export function newestSolveLine(logs: LogLine[]): LogLine | null {
  for (let i = logs.length - 1; i >= 0; i--) {
    if (logs[i].data.source === "solve") return logs[i];
  }
  return null;
}

export default PolarSheet;
