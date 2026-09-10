// wheel.tsx - the FILTER WHEEL device sheet (plan hub-rig.md B.6, deviations
// E19-E21). Route `#/rig/devices/wheel`.
//
// THE RING IS THE POINT. A carousel is a physical object with an orientation,
// and the one thing an operator wants to know at the scope is which piece of
// glass is in the light path right now. So the slots are drawn where they
// actually are, the group turns so the chosen slot arrives under the fixed
// marker at the top, and a tap on a slot IS the move command. The geometry
// lives in `../lib/wheelRing.ts` because two frames of reference (the rotating
// group, and the tap targets that must NOT rotate or the labels end up upside
// down) is exactly the arithmetic worth testing without a DOM.
//
// FOUR THINGS THE ENGINE FORCED (plan E19-E21 and the seam inventory):
//
//  1. `moving` ABSENT MEANS "WATCH THE POSITION INSTEAD", never "not moving"
//     (`types.ts:132-137`). Every backend that can answer pulses its own flag;
//     the ones that cannot would otherwise read as a wheel that never turned.
//     So the narration comes from `filterMotion` (`lib/filterSlots.ts`), which
//     holds the slot THIS session asked for, watches the flag, and after
//     FILTER_STUCK_AFTER_MS says which of the two faults it is - a carousel
//     that never budged, or one that turned and mis-seated.
//  2. THERE IS NO TYPE ENUM on the wire (`server-routes.md` 4.2). The word under
//     each filter name is derived: opaque -> blackout, else narrowband ->
//     narrowband, else broadband. That is also where GAP-3's missing blackout
//     type lands.
//  3. REAL WHEELS ARE NOT SEVEN SLOTS. The design draws exactly 7; a ZWO EFW is
//     5, 7 or 8 and there are 12-slot carousels. Circle radius is sized from the
//     gap between neighbours, and past twelve the ring is dropped for a position
//     chip plus the table - a 13-slot ring at 236 px cannot carry a legible
//     label, and an illegible ring is worse than the table it replaced.
//  4. EVERY EDIT IN THE TABLE IS ONE `POST /api/filterwheel/names`. There is no
//     Save button because there is no modal: this is a sheet, and a sheet that
//     could be closed with unsaved slot offsets in it would lose them. Each edit
//     sends `names` plus the ONE array it changed, and an explicit `null` for
//     the others, which the server reads as "leave the stored value alone"
//     (`server-routes.md` 4.2). That is deliberate and it is the anti-clobber
//     property: an offset edit here cannot revert somebody else's name edit
//     landing at the same moment. Held presses are debounced into one POST and
//     a refusal rolls the optimistic value back.
//
// WHAT THIS SHEET DOES NOT BUILD: a focus trap. `FilterNamesModal` needs one
// (and needed the `onCloseRef` fix so a status frame could not tear it down
// mid-keystroke); a sheet's focus belongs to `SheetHost`, and a second trap
// here would fight it.

import { useEffect, useMemo, useRef, useState, type JSX, type ReactNode } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import {
  ActionButton, Card, Checkbox22, Chip, EmptyCard, Field, Label, ListRow, Mono,
  Sheet, Stepper2, TextInput,
} from "../../../ui";
import { useLock } from "../../../lib/gateHook";
import { useBreakpoint } from "../../../breakpoint";
import { api } from "../../../../api";
import { useCanControlCapture } from "../../../../lib/caps";
import {
  DARK_SLOT_NAME, filterMotion, nameForOpaqueToggle, slotLabel,
  type FilterCommand,
} from "../../../../lib/filterSlots";
import { deriveAutofocusParams, narrowbandSweepSettings } from "../../../../lib/autofocus";
import { standardsOrDefault } from "../../../../lib/standards";
import {
  useConfig, useFilterOffsetsLearn, useLivePreview, usePolar, useSequence,
  useStatus, useStore,
} from "../../../../store";
// The polar sentence and its two-channel test, shared with Rig - Capture rather
// than re-spelled here: one alignment, one string (r4 #25).
import { POLAR_REASON, isPolarBusy } from "../capture/captureGate";
import {
  MARKER_POINT, RING_C, RING_PX, SLOT_HIT_PX, filterColor, slotType, wheelRing,
} from "../lib/wheelRing";

/** The design's own motion for the carousel (README "Design tokens"). */
export const RING_SPIN_MS = 500;
export const RING_EASING = "cubic-bezier(.2,.8,.2,1)";

/** The design's step for the offset stepper (`logic.js:700`:
 *  `dec: () => setOff(w.f, -2), inc: () => setOff(w.f, 2)`). Two steps is about
 *  the repeatability of a focuser, so a smaller step would be adjusting noise. */
export const OFFSET_STEP = 2;

/** A held press is one POST, not one per repeat. Trailing, so the value on
 *  screen is the one that lands. */
export const WRITE_DEBOUNCE_MS = 400;

/** The exposures the DEFAULT SUB picker offers, plus the unpinned state. Blank
 *  means UNPINNED and can never be 0, because 0 is a real gain and a pinned
 *  zero-second exposure is not a frame (`lib/filterSettings.ts`). */
export const SUB_CHOICES = [30, 60, 120, 180, 300] as const;

export const RING_CAPTION = "tap a slot to move the wheel · the marker is the light path";

export const NAMES_NOTE =
  "Names come from the wheel. Edit one only when the driver reports it wrong.";

export const PHONE_NAMES_ROW = "NAMES AND TYPES · edit on a tablet";

/** Flow ownership (plan section 0.5) - the wheel is the flow's while a run
 *  holds the camera. */
export const FLOW_OWNS_WHEEL =
  "A run has the camera. Focusing needs the camera to itself, so these controls "
  + "stay locked until the run stops. Stop or pause it on Session - Now; nothing "
  + "here will interrupt it for you.";

const EM_DASH = "—";

/** The arrays `POST /api/filterwheel/names` writes, parallel to `names`. */
interface WheelArrays {
  names: string[];
  offsets: number[];
  opaque: boolean[];
  narrowband: boolean[];
  exposures: (number | null)[];
  gains: (number | null)[];
}
type ArrayKey = keyof WheelArrays;

function first(...reasons: (string | null | undefined)[]): string | null {
  for (const r of reasons) if (r) return r;
  return null;
}

/** A paragraph note - see the same helper's reasoning on the focuser sheet. */
function Note({ children, tone = "dim", ...rest }: {
  children: ReactNode;
  tone?: "dim" | "warn" | "bad" | "good";
  "data-testid"?: string;
}): JSX.Element {
  const color = tone === "warn" ? "var(--warn)"
    : tone === "bad" ? "var(--bad)"
      : tone === "good" ? "var(--good)" : "var(--text-faint)";
  return (
    <p
      style={{ fontSize: "11.5px", lineHeight: 1.5, color, padding: "0 2px", margin: 0 }}
      data-testid={rest["data-testid"]}
    >
      {children}
    </p>
  );
}

/** Honour `prefers-reduced-motion` for the one animation this sheet owns. The
 *  stylesheet cannot reach an inline `transition`, and the ring's turn is the
 *  largest movement in the app. */
function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    let m: MediaQueryList;
    try { m = window.matchMedia("(prefers-reduced-motion: reduce)"); } catch { return; }
    setReduced(!!m.matches);
    const onChange = () => setReduced(!!m.matches);
    if (typeof m.addEventListener === "function") {
      m.addEventListener("change", onChange);
      return () => m.removeEventListener("change", onChange);
    }
    return undefined;
  }, []);
  return reduced;
}

export function WheelSheet(_p: SheetProps): JSX.Element {
  const status = useStatus();
  const wheel = status?.filterwheel;
  const cam = status?.camera;
  const foc = status?.focuser;
  const config = useConfig();
  const standards = standardsOrDefault(config?.standards);
  const sequence = useSequence();
  const learn = useFilterOffsetsLearn();
  const live = useLivePreview();
  const showToast = useStore((s) => s.showToast);
  const canCapture = useCanControlCapture();
  const breakpoint = useBreakpoint();
  const reducedMotion = useReducedMotion();

  const seqOwnsCamera = sequence.state === "running" || sequence.state === "paused";
  const flowOwns = seqOwnsCamera ? FLOW_OWNS_WHEEL : null;
  // r4 #25: LEARN OFFSETS steps the focuser through every filter, exposing at
  // each stop. Polar alignment owns the camera for its whole run and spawns its
  // own lane (server/astrodeck/hub.py:297), which this sheet never named - so
  // the run started, collided, and came back as a raw 409.
  const polar = usePolar();
  const polarOwns = isPolarBusy(polar.state, status?.busy_lanes) ? POLAR_REASON : null;

  // ------------------------------------------------------------- the gate
  const link = useLock({});
  const capRole = useLock({ cap: "control.capture", needsRole: "filterwheel" });
  const laneWheel = useLock({ busyLane: "filterwheel" });
  const laneOffsets = useLock({ busyLane: "filter_offsets" });
  const laneCapture = useLock({ busyLane: "capture" });
  const laneLooping = useLock({ busyLane: "looping" });
  const laneAutofocus = useLock({ busyLane: "autofocus" });
  const onExplain = link.onExplain;

  const moveReason = first(
    capRole.lockedReason, laneWheel.lockedReason, laneOffsets.lockedReason,
    laneCapture.lockedReason, flowOwns,
  );
  const editReason = first(capRole.lockedReason, laneOffsets.lockedReason, flowOwns);
  // STOP LEARNING is never blocked by a lane: it is the only way to end a run
  // that steps through every filter, and gating it on the lane it exists to end
  // is the bug the inventory records three times.
  const stopLearnReason = capRole.lockedReason;

  // ------------------------------------------------- the arrays, with drafts
  const n = wheel?.names?.length ?? 0;
  const server: WheelArrays = useMemo(() => ({
    names: wheel?.names ?? [],
    offsets: wheel?.offsets ?? new Array<number>(n).fill(0),
    opaque: wheel?.opaque ?? new Array<boolean>(n).fill(false),
    narrowband: wheel?.narrowband ?? new Array<boolean>(n).fill(false),
    exposures: wheel?.exposures ?? new Array<number | null>(n).fill(null),
    gains: wheel?.gains ?? new Array<number | null>(n).fill(null),
  }), [wheel, n]);

  // What THIS browser has asked for and the rig has not yet confirmed. Cleared
  // per key on the server's answer, whichever way it goes: on success the
  // server's own arrays are the truth, and on refusal the optimistic value must
  // not survive the sentence that says it failed.
  const [pending, setPending] = useState<Partial<WheelArrays>>({});
  // NOT cleared on unmount, deliberately. The debounce exists to COALESCE
  // presses, not to cancel them: a user who steps an offset and immediately
  // presses BACK would otherwise lose the edit with nothing on screen to say
  // so. The trailing write still lands, and its `setPending` on an unmounted
  // tree is a no-op while its error toast still reaches the store.
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const shown: WheelArrays = {
    names: pending.names ?? server.names,
    offsets: pending.offsets ?? server.offsets,
    opaque: pending.opaque ?? server.opaque,
    narrowband: pending.narrowband ?? server.narrowband,
    exposures: pending.exposures ?? server.exposures,
    gains: pending.gains ?? server.gains,
  };

  /**
   * Write ONE array, and say nothing about the others.
   *
   * `null` on an array means "leave the stored value alone" (`server-routes.md`
   * 4.2), so an offset edit cannot revert a name edit that landed between this
   * browser's last status frame and this POST. `names` always rides along
   * because the body requires it and because it is what the other arrays are
   * parallel TO - a wheel that gained a slot between the read and the write
   * would otherwise apply this array against a different carousel.
   */
  const writeArray = (key: ArrayKey, value: WheelArrays[ArrayKey]) => {
    setPending((p) => ({ ...p, [key]: value }));
    if (timers.current[key]) clearTimeout(timers.current[key]);
    timers.current[key] = setTimeout(() => {
      delete timers.current[key];
      const body: Record<string, unknown> = {
        names: key === "names" ? value : shown.names,
        offsets: null,
        opaque: null,
        narrowband: null,
        exposures: null,
        gains: null,
      };
      if (key !== "names") body[key] = value;
      void api.post("/api/filterwheel/names", body)
        .then(() => { setPending((p) => { const q = { ...p }; delete q[key]; return q; }); })
        .catch((e: Error) => {
          setPending((p) => { const q = { ...p }; delete q[key]; return q; });
          showToast("error", e.message);
        });
    }, WRITE_DEBOUNCE_MS);
  };

  // ------------------------------------------------------------- the move
  const [cmd, setCmd] = useState<FilterCommand | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const motion = filterMotion(cmd, wheel?.position, wheel?.moving, shown.names, now);
  useEffect(() => {
    if (!cmd || motion.problem) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [cmd, motion.problem]);
  // The command is retired once the wheel is demonstrably where it was asked to
  // be; leaving it would narrate the sequencer's next filter change as this
  // session's tap.
  useEffect(() => {
    if (cmd && wheel?.position === cmd.slot && !wheel?.moving) {
      const t = setTimeout(() => setCmd(null), 4000);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [cmd, wheel?.position, wheel?.moving]);

  const moveTo = (slot: number) => {
    if (wheel == null || slot === wheel.position) return;
    setCmd({ slot, startedAt: Date.now(), from: wheel.position });
    setNow(Date.now());
    void api.post("/api/filterwheel/position", { position: slot })
      .catch((e: Error) => {
        // The command never reached the wheel, so there is no move to narrate -
        // leaving it would pulse "-> L" over a request the server refused.
        setCmd(null);
        showToast("error", e.message);
      });
  };

  // ------------------------------------------------------- learn offsets
  const learnRunning = learn?.state === "running";
  const [refSlot, setRefSlot] = useState(0);
  const effectiveRef = shown.opaque[refSlot]
    ? Math.max(0, shown.opaque.findIndex((b) => !b))
    : refSlot;
  const derived = deriveAutofocusParams({
    focuserMax: typeof foc?.max === "number" && foc.max > 0 ? foc.max : null,
    maxBin: cam?.max_bin ?? null,
    maxGain: cam?.max_gain ?? null,
    liveExposureS: live?.exposure_s ?? null,
    liveGain: live?.gain ?? null,
    liveBinning: live?.binning ?? null,
    liveStars: live?.stars ?? null,
    liveHfr: live?.hfr ?? null,
    hasLiveFrame: !!live,
    serverSweep: foc?.sweep ?? null,
  });
  const [sweepExposure, setSweepExposure] = useState<number | null>(null);
  const [sweepGain, setSweepGain] = useState<number | null>(null);
  const [nbExposure, setNbExposure] = useState<number | null>(null);
  const [nbGain, setNbGain] = useState<number | null>(null);
  const sweepExposureS = sweepExposure ?? derived.exposure_s;
  const sweepGainValue = sweepGain ?? derived.gain;
  const hcg = (cam as { hcg_threshold_gain?: number | null } | undefined)
    ?.hcg_threshold_gain ?? null;
  const [derivedNbExposure, derivedNbGain] =
    narrowbandSweepSettings(sweepExposureS, sweepGainValue, hcg);
  const nbSlots = shown.narrowband.filter(Boolean).length;

  // Verbatim from CaptureView:1599-1605 - the sentence order is the fact order.
  const learnDisabledReason = !canCapture
    ? "this session can't change equipment"
    : !foc
      ? "no focuser is connected"
      : null;
  const learnReason = first(
    capRole.lockedReason, learnDisabledReason, polarOwns, laneOffsets.lockedReason,
    laneAutofocus.lockedReason, laneCapture.lockedReason, laneLooping.lockedReason,
    flowOwns,
  );

  const startLearn = () => {
    void api.post("/api/filterwheel/learn-offsets", {
      ref_slot: effectiveRef,
      exposure_s: sweepExposureS,
      gain: sweepGainValue,
      step: derived.step,
      steps_each_side: derived.steps_each_side,
      binning: derived.binning,
      narrowband: [...shown.narrowband],
      nb_exposure_s: nbExposure ?? derivedNbExposure,
      nb_gain: nbGain ?? derivedNbGain,
    }).then(() => showToast("info", "Learning filter offsets…"))
      .catch((e: Error) => showToast("error", e.message));
  };

  // ------------------------------------------------------------ the ring
  // While a change is in flight the ring turns to the TARGET, so the tap has an
  // answer before the carousel lands - every backend reports the OLD slot right
  // up to the landing, which is why the wheel looked like it ignored the pick.
  const shownSlot = cmd ? cmd.slot : (wheel?.position ?? 0);
  const shownRing = wheelRing(n, shownSlot);
  const curName = slotLabel(shown.names, shownSlot);
  const curType = slotType(shown.opaque[shownSlot], shown.narrowband[shownSlot]);

  const [subPicker, setSubPicker] = useState<number | null>(null);

  const liveLine = (() => {
    const device = status?.connected?.filterwheel?.name ?? null;
    const where = cmd && wheel?.position !== cmd.slot
      ? `moving to ${slotLabel(shown.names, cmd.slot)}`
      : `at ${slotLabel(shown.names, wheel?.position ?? 0)}`;
    return [
      device,
      n ? `${n} slots` : null,
      wheel ? where : "not connected",
      `offsets ${standards.apply_filter_offsets ? "on" : "off"}`,
    ].filter(Boolean).join(" · ");
  })();

  const priorNames = useRef<Record<number, string>>({});
  const toggleOpaque = (i: number, nowOpaque: boolean) => {
    const nextNames = [...shown.names];
    if (nowOpaque) priorNames.current[i] = shown.names[i] ?? "";
    nextNames[i] = nameForOpaqueToggle(
      shown.names[i] ?? "", i, nowOpaque, priorNames.current[i],
    );
    const nextOpaque = [...shown.opaque];
    nextOpaque[i] = nowOpaque;
    // Both arrays, because the rename is the POINT of the flag: a slot called
    // "L" that no longer passes light files its darks under L in the FITS
    // header and in the saved filename.
    writeArray("opaque", nextOpaque);
    if (nextNames[i] !== shown.names[i]) writeArray("names", nextNames);
    // …and a blackout slot carries no narrowband claim, because there is no
    // passband behind it.
    if (nowOpaque && shown.narrowband[i]) {
      const nextNb = [...shown.narrowband];
      nextNb[i] = false;
      writeArray("narrowband", nextNb);
    }
  };

  return (
    <Sheet
      data-testid="rig-wheel"
      title="FILTER WHEEL"
      backLabel="RIG"
      icon={<NxIcon name="wheel" size={18} />}
      live={liveLine}
      onBack={() => nav.back()}
    >
      {flowOwns && <Note tone="warn">{flowOwns}</Note>}

      {!wheel || n === 0 ? (
        <EmptyCard
          title="No filter wheel connected"
          hint="Assign a filter wheel on ADD A DEVICE, then connect the rig. Offsets, blackout slots and per-filter exposures are stored on the wheel's own config, so they come back with it."
          action={(
            <ActionButton kind="secondary" onPress={() => nav.sheet("addDevice")}>
              GO TO ADD A DEVICE
            </ActionButton>
          )}
        />
      ) : (
        <>
          {/* 1. The carousel, or the chip that replaces it above 12 slots. */}
          <Card>
            <div
              data-testid="wheel-ring"
              data-mode={shownRing.mode}
              data-slots={String(n)}
              style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}
            >
              {shownRing.mode === "ring" ? (
                <div style={{ position: "relative", width: RING_PX, height: RING_PX, maxWidth: "100%" }}>
                  <svg
                    viewBox={`0 0 ${RING_PX} ${RING_PX}`}
                    style={{ position: "absolute", inset: 0, width: "100%", height: "100%", display: "block" }}
                    aria-hidden="true"
                  >
                    <circle cx={RING_C} cy={RING_C} r={96} fill="var(--bg)" stroke="var(--line-bright)" />
                    <circle cx={RING_C} cy={RING_C} r={52} fill="none" stroke="var(--line)" />
                    {/* the light path: a fixed tick the carousel turns beneath */}
                    <path
                      d={`M${MARKER_POINT.x} 8v14`}
                      stroke="var(--accent)"
                      strokeWidth={2}
                      strokeLinecap="round"
                    />
                    <g
                      style={{
                        transform: `rotate(${shownRing.rot}deg)`,
                        transformOrigin: `${RING_C}px ${RING_C}px`,
                        transition: reducedMotion
                          ? "none"
                          : `transform ${RING_SPIN_MS}ms ${RING_EASING}`,
                      }}
                    >
                      {shownRing.slots.map((s) => {
                        const col = filterColor(shown.names[s.index]);
                        return (
                          <circle
                            key={s.index}
                            cx={s.cx}
                            cy={s.cy}
                            r={shownRing.radius}
                            fill={s.current ? `color-mix(in srgb, ${col} 20%, transparent)` : "var(--bg)"}
                            stroke={s.current ? col : "var(--line-bright)"}
                            strokeWidth={s.current ? 2 : 1}
                          />
                        );
                      })}
                    </g>
                  </svg>
                  {shownRing.slots.map((s) => {
                    const name = slotLabel(shown.names, s.index);
                    const exp = shown.exposures[s.index];
                    return (
                      <button
                        key={s.index}
                        type="button"
                        data-testid={`wheel-slot-${s.index}`}
                        aria-label={`Move to ${name}`}
                        aria-pressed={s.current}
                        onClick={() => {
                          if (moveReason) { onExplain(moveReason); return; }
                          moveTo(s.index);
                        }}
                        aria-disabled={moveReason ? true : undefined}
                        title={moveReason ?? undefined}
                        className={moveReason ? "nx-locked" : undefined}
                        style={{
                          position: "absolute",
                          left: `${s.bx}px`,
                          top: `${s.by}px`,
                          width: SLOT_HIT_PX,
                          height: SLOT_HIT_PX,
                          borderRadius: "50%",
                          border: 0,
                          background: "transparent",
                          color: s.current ? filterColor(shown.names[s.index]) : "var(--text-dim)",
                          display: "flex",
                          flexDirection: "column",
                          alignItems: "center",
                          justifyContent: "center",
                          cursor: "pointer",
                          padding: 0,
                          transition: reducedMotion
                            ? "none"
                            : `left ${RING_SPIN_MS}ms ${RING_EASING}, top ${RING_SPIN_MS}ms ${RING_EASING}`,
                        }}
                      >
                        <span className="nx-display" style={{ fontSize: 11, letterSpacing: ".06em" }}>
                          {name}
                        </span>
                        <span className="nx-mono" style={{ fontSize: 10, color: "var(--text-faint)" }}>
                          {exp != null ? `${exp} s` : ""}
                        </span>
                      </button>
                    );
                  })}
                  <div
                    style={{
                      position: "absolute", left: "50%", top: "50%",
                      transform: "translate(-50%,-50%)", display: "flex",
                      flexDirection: "column", alignItems: "center", gap: 1,
                      pointerEvents: "none",
                    }}
                  >
                    <span
                      className="nx-display"
                      data-testid="wheel-current"
                      style={{
                        fontSize: 18, letterSpacing: ".06em",
                        color: filterColor(shown.names[shownSlot]),
                      }}
                    >
                      {curName}
                    </span>
                    <span className="nx-mono" style={{ fontSize: 10, color: "var(--text-faint)" }}>
                      {motion.pulsing ? "moving" : curType}
                    </span>
                  </div>
                </div>
              ) : (
                // Above 12 slots the ring cannot carry a legible label, so the
                // position becomes a chip and the table below IS the wheel.
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "center" }}>
                  <Mono data-testid="wheel-current">{`at ${curName} · ${curType}`}</Mono>
                  {shown.names.map((name, i) => (
                    <Chip
                      key={`${name}-${i}`}
                      data-testid={`wheel-slot-${i}`}
                      active={i === shownSlot}
                      onClick={() => moveTo(i)}
                      lockedReason={moveReason}
                      onExplain={onExplain}
                    >
                      {slotLabel(shown.names, i)}
                    </Chip>
                  ))}
                </div>
              )}
              <Mono tone="dim">{RING_CAPTION}</Mono>
              {motion.problem && (
                <Note tone="warn" data-testid="wheel-problem">{motion.problem}</Note>
              )}
            </div>
          </Card>

          {/* 2. The slot table. Every cell is a write. */}
          <Card>
            <div
              style={{
                display: "grid", gridTemplateColumns: "1fr 84px 128px", gap: 8,
                padding: "0 0 4px",
              }}
            >
              <Label>SLOT · FILTER</Label>
              <div style={{ textAlign: "right" }}><Label>DEFAULT SUB</Label></div>
              <div style={{ textAlign: "center" }}><Label>FOCUS OFFSET</Label></div>
            </div>
            {shown.names.map((name, i) => {
              const type = slotType(shown.opaque[i], shown.narrowband[i]);
              const col = filterColor(name);
              const exp = shown.exposures[i];
              const off = shown.offsets[i] ?? 0;
              return (
                <div
                  key={`${name}-${i}`}
                  data-testid={`wheel-row-${i}`}
                  style={{
                    display: "grid", gridTemplateColumns: "1fr 84px 128px", gap: 8,
                    alignItems: "center", minHeight: 48,
                    borderTop: "1px solid var(--line)",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                    <span style={{ width: 8, height: 8, borderRadius: "50%", background: col, flexShrink: 0 }} />
                    <span className="nx-display" style={{ fontSize: 11, letterSpacing: ".08em", color: col }}>
                      {slotLabel(shown.names, i)}
                    </span>
                    <span className="nx-mono" style={{ fontSize: 10, color: "var(--text-faint)" }}>
                      {type}
                    </span>
                  </div>

                  <div style={{ textAlign: "right" }}>
                    {type === "blackout" ? (
                      <span
                        className="nx-mono"
                        style={{ color: "var(--text-faint)", opacity: .6 }}
                        aria-label={`Slot ${i + 1} default sub - not applicable, blackout slot`}
                      >
                        {EM_DASH}
                      </span>
                    ) : (
                      <button
                        type="button"
                        data-testid={`wheel-sub-${i}`}
                        className={`nx-mono${editReason ? " nx-locked" : ""}`}
                        aria-label={exp != null
                          ? `Slot ${i + 1} default sub, ${exp} seconds`
                          : `Slot ${i + 1} default sub - not pinned`}
                        aria-disabled={editReason ? true : undefined}
                        title={editReason ?? undefined}
                        onClick={() => {
                          if (editReason) { onExplain(editReason); return; }
                          setSubPicker(subPicker === i ? null : i);
                        }}
                        style={{
                          minHeight: 44, minWidth: 44, border: 0, background: "transparent",
                          color: "var(--text-dim)", cursor: "pointer", width: "100%",
                          textAlign: "right",
                        }}
                      >
                        {exp != null ? `${exp} s` : EM_DASH}
                      </button>
                    )}
                  </div>

                  <div style={{ display: "flex", justifyContent: "center" }}>
                    {type === "blackout" ? (
                      <span
                        className="nx-mono"
                        style={{ color: "var(--text-faint)", opacity: .6 }}
                        aria-label={`Slot ${i + 1} focuser offset - not applicable, blackout slot`}
                      >
                        {EM_DASH}
                      </span>
                    ) : (
                      <Stepper2
                        data-testid={`wheel-offset-${i}`}
                        label={`Slot ${i + 1} focus offset in steps`}
                        value={off}
                        onChange={(v) => {
                          const next = [...shown.offsets];
                          while (next.length < n) next.push(0);
                          next[i] = v;
                          writeArray("offsets", next);
                        }}
                        step={OFFSET_STEP}
                        format={(v) => (v > 0 ? `+${v}` : String(v))}
                        lockedReason={editReason}
                        onExplain={onExplain}
                      />
                    )}
                  </div>

                  {subPicker === i && (
                    <div style={{ gridColumn: "1 / -1", display: "flex", gap: 6, flexWrap: "wrap", paddingBottom: 8 }}>
                      {SUB_CHOICES.map((s) => (
                        <Chip
                          key={s}
                          active={exp === s}
                          onClick={() => {
                            const next = [...shown.exposures];
                            while (next.length < n) next.push(null);
                            next[i] = s;
                            writeArray("exposures", next);
                            setSubPicker(null);
                          }}
                          lockedReason={editReason}
                          onExplain={onExplain}
                        >
                          {`${s} s`}
                        </Chip>
                      ))}
                      <Chip
                        active={exp == null}
                        onClick={() => {
                          const next = [...shown.exposures];
                          while (next.length < n) next.push(null);
                          next[i] = null;
                          writeArray("exposures", next);
                          setSubPicker(null);
                        }}
                        lockedReason={editReason}
                        onExplain={onExplain}
                      >
                        not pinned
                      </Chip>
                    </div>
                  )}
                </div>
              );
            })}
          </Card>

          {/* 3. Learn the offsets rather than typing them. */}
          <Card>
            <Label>LEARN OFFSETS</Label>
            <Note>
              {"A per-filter autofocus sweep measures where each filter comes to focus, "
                + `relative to ${slotLabel(shown.names, effectiveRef)}. It steps through every slot, so it takes a while.`}
            </Note>
            <Field label="REFERENCE FILTER" hint="the slot every offset is measured against - it stays at 0">
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {shown.names.map((name, i) => (
                  shown.opaque[i] ? null : (
                    <Chip
                      key={`ref-${name}-${i}`}
                      active={effectiveRef === i}
                      onClick={() => setRefSlot(i)}
                      lockedReason={editReason}
                      onExplain={onExplain}
                    >
                      {slotLabel(shown.names, i)}
                    </Chip>
                  )
                ))}
              </div>
            </Field>
            <Field label="SWEEP EXPOSURE" hint="what each sweep frame is shot at - the server used to pick 2 s and say nothing">
              <Stepper2
                label="Offset sweep exposure in seconds"
                value={sweepExposureS}
                onChange={setSweepExposure}
                step={1}
                min={1}
                max={120}
                format={(v) => `${v} s`}
                lockedReason={editReason}
                onExplain={onExplain}
              />
            </Field>
            <Field label="SWEEP GAIN">
              <Stepper2
                label="Offset sweep gain"
                value={sweepGainValue}
                onChange={setSweepGain}
                step={10}
                min={0}
                max={cam?.max_gain && cam.max_gain > 0 ? cam.max_gain : 600}
                lockedReason={editReason}
                onExplain={onExplain}
              />
            </Field>
            {nbSlots > 0 && (
              <>
                <Field
                  label="NARROWBAND EXPOSURE"
                  hint={`${nbSlots} narrowband slot${nbSlots > 1 ? "s" : ""} - a 3-7 nm passband is the same star tens of times fainter, so they sweep longer`}
                >
                  <Stepper2
                    label="Narrowband sweep exposure in seconds"
                    value={nbExposure ?? derivedNbExposure}
                    onChange={setNbExposure}
                    step={5}
                    min={1}
                    max={600}
                    format={(v) => `${v} s`}
                    lockedReason={editReason}
                    onExplain={onExplain}
                  />
                </Field>
                <Field label="NARROWBAND GAIN">
                  <Stepper2
                    label="Narrowband sweep gain"
                    value={nbGain ?? derivedNbGain}
                    onChange={setNbGain}
                    step={10}
                    min={0}
                    max={cam?.max_gain && cam.max_gain > 0 ? cam.max_gain : 600}
                    lockedReason={editReason}
                    onExplain={onExplain}
                  />
                </Field>
              </>
            )}
            {learnRunning ? (
              <>
                <Mono tone="accent" data-testid="wheel-learn-progress">
                  {`measuring ${learn?.name ?? `slot ${(learn?.slot ?? 0) + 1}`}`
                    + (learn?.of ? ` · ${(learn.slot ?? 0) + 1} of ${learn.of}` : "")}
                </Mono>
                <ActionButton
                  kind="danger"
                  full
                  onPress={() => void api.post("/api/filterwheel/learn-offsets/cancel", {})
                    .catch((e: Error) => showToast("error", e.message))}
                  lockedReason={stopLearnReason}
                  onExplain={onExplain}
                >
                  STOP LEARNING
                </ActionButton>
              </>
            ) : (
              <ActionButton
                kind="secondary"
                full
                onPress={startLearn}
                lockedReason={learnReason}
                onExplain={onExplain}
              >
                LEARN OFFSETS
              </ActionButton>
            )}
            {learn?.error && <Note tone="bad">{learn.error}</Note>}
          </Card>

          {/* 4. Names and types. A CORRECTION surface, not a naming one. */}
          {breakpoint === "phone" ? (
            <ListRow
              data-testid="wheel-names-phone"
              title={PHONE_NAMES_ROW}
              sub={NAMES_NOTE}
              tone="dim"
            />
          ) : (
            <Card>
              <Label>NAMES AND TYPES</Label>
              <Note>{NAMES_NOTE}</Note>
              {shown.names.map((name, i) => (
                <div
                  key={`edit-${i}`}
                  data-testid={`wheel-edit-${i}`}
                  style={{
                    display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
                    borderTop: "1px solid var(--line)", paddingTop: 8,
                  }}
                >
                  <Mono tone="dim">{i + 1}</Mono>
                  <div style={{ flex: 1, minWidth: 120 }}>
                    <TextInput
                      value={name}
                      onChange={(v) => {
                        const next = [...shown.names];
                        next[i] = v;
                        writeArray("names", next);
                      }}
                      ariaLabel={`Slot ${i + 1} name`}
                      lockedReason={editReason}
                    />
                  </div>
                  <Checkbox22
                    data-testid={`wheel-dark-${i}`}
                    label="blackout"
                    checked={!!shown.opaque[i]}
                    onChange={(v) => toggleOpaque(i, v)}
                    lockedReason={editReason}
                    onExplain={onExplain}
                  />
                  {shown.opaque[i] ? (
                    <span
                      className="nx-mono"
                      style={{ color: "var(--text-faint)", opacity: .6 }}
                      aria-label={`Slot ${i + 1} narrowband - not applicable, blackout slot`}
                    >
                      {EM_DASH}
                    </span>
                  ) : (
                    <Checkbox22
                      data-testid={`wheel-nb-${i}`}
                      label="narrowband"
                      checked={!!shown.narrowband[i]}
                      onChange={(v) => {
                        const next = [...shown.narrowband];
                        next[i] = v;
                        writeArray("narrowband", next);
                      }}
                      lockedReason={editReason}
                      onExplain={onExplain}
                    />
                  )}
                  <Stepper2
                    label={`Slot ${i + 1} default gain`}
                    value={shown.gains[i] ?? 0}
                    onChange={(v) => {
                      const next = [...shown.gains];
                      while (next.length < n) next.push(null);
                      next[i] = v;
                      writeArray("gains", next);
                    }}
                    step={10}
                    min={0}
                    max={cam?.max_gain && cam.max_gain > 0 ? cam.max_gain : 600}
                    format={(v) => (shown.gains[i] == null ? "not pinned" : `gain ${v}`)}
                    lockedReason={editReason}
                    onExplain={onExplain}
                  />
                </div>
              ))}
              <Note>
                {`Blank is UNPINNED, which can never be 0: a pinned gain of 0 is a real `
                  + `setting and a slot called ${DARK_SLOT_NAME} with no light path carries `
                  + "no capture settings at all."}
              </Note>
            </Card>
          )}

          {/* 5. Footer. The reference slot is named, because "relative to L" is
              only true when the reference IS L. */}
          <Note data-testid="wheel-footer">
            {`Offsets are steps relative to ${slotLabel(shown.names, effectiveRef)} and apply on `
              + "every filter change without a refocus. The default sub is what quick "
              + "sessions start from; the picker still lets you change it per night."}
          </Note>
        </>
      )}
    </Sheet>
  );
}
