// camera.tsx - the CAMERA device sheet (plan hub-rig.md B.1, task T-RIG-2).
//
// WHAT THIS SHEET IS FOR. It holds the DEFAULTS every flow and quick session
// starts from - the cooler, the gain, the offset, the binning - plus the two
// policies that make "park and warm" true. It is not the capture bench: there
// is no exposure box, no frame type and no shutter here, because those are per
// shot and live on Rig - Capture (design README section 8, and the sentence
// this sheet prints at the bottom).
//
// THREE THINGS THAT WOULD BE LIES IF WRITTEN THE OBVIOUS WAY, and are not:
//
//  1. THE COOLING CURVE. The prototype fills it from an exponential, so it
//     descends smoothly whatever the sensor is doing (E25). This one plots the
//     temperatures this browser has actually received, and below three of them
//     draws only the set-point line plus "building the curve". `lib/coolerCurve`
//     owns that maths and is tested on its own.
//  2. THE RING WITH NO READING. `status.camera.temperature` is nullable - a
//     driver that cannot answer publishes null, not a number. The ring then
//     renders EMPTY with `--` in the middle rather than an arc at 0 C, which is
//     a real temperature and would read as one.
//  3. THE FOURTH TILE. The design's is USB bandwidth. There is no USB-bandwidth
//     setting anywhere in this server (E2), so the tile is E-GAIN - real
//     (`status.camera.egain` / `egain_learned`), read-only, and the home for
//     the e-/ADU learn loop, which would otherwise have none in the new IA.
//
// WRITE SEMANTICS (plan 0.4). Gain / offset / binning go through
// `setFrameSettings`, which is optimistic-then-authoritative and rolls back on
// a refusal. Everything else here is CONFIRMED-ONLY: the cooler switch and the
// two config toggles show what the rig says, never what was asked for. The warm
// ramp in particular is a config block and is sent WHOLESALE through
// `setCoolingConfig`, which strips `setpoint_c` on purpose (api/backends.ts).

import { useEffect, useMemo, useRef, useState, type JSX } from "react";
import type { SheetProps } from "../../sheets";
import {
  ActionButton, Card, Dial, EmptyCard, Field, Label, ListRow, Mono,
  ReadoutGrid, ReadoutTile, Segmented, Sheet, Stepper2, Switch, TextInput,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useBreakpoint } from "../../../breakpoint";
import { useLock } from "../../../lib/gateHook";
import {
  useConfig, useEgainLearn, useEquipConnected, useFrameSettings, usePhotometry,
  usePolar, usePreview, useSequence, useStatus, useStore,
} from "../../../../store";
// The polar sentence and its two-channel test, shared with Rig - Capture rather
// than re-spelled here: one alignment, one string (r4 #25).
import { POLAR_REASON, isPolarBusy } from "../capture/captureGate";
import { resolveRoleConnected } from "../../../../lib/caps";
import { warmReadout } from "../../../../lib/cooling";
import { suggestSubLength } from "../../../../lib/photometry";
import { api } from "../../../../api";
import { setCoolingConfig } from "../../../../api/backends";
import type { CoolingConfig, RigStatus } from "../../../../types";
import {
  BUILDING_NOTE, CURVE_H, CURVE_W, coolerCurve, pushTemp, ringDash, ringFraction,
} from "../lib/coolerCurve";

type CameraInfo = NonNullable<RigStatus["camera"]>;

// ------------------------------------------------------------------- the copy

/** Plan 0.5: a run owns the camera, and the escape hatch is the run's own
 *  screen, not this one. Two variants because "paused" is not "not running" -
 *  a paused sequence still holds the camera between frames. */
export const FLOW_OWNS_CAMERA =
  "A run has the camera. Focusing needs the camera to itself, so these controls "
  + "stay locked until the run stops. Stop or pause it on Session - Now; nothing "
  + "here will interrupt it for you.";
export const FLOW_OWNS_CAMERA_PAUSED =
  "A run has the camera (paused between frames). Focusing needs the camera to "
  + "itself, so these controls stay locked until the run stops. Stop or pause it "
  + "on Session - Now; nothing here will interrupt it for you.";

/** The shipped title on the ramp escape hatch (views/CaptureView.tsx:1749). */
const STOP_RAMP_TITLE =
  "Stop the ramp and switch the cooler off now. The sensor will then equalise "
  + "with the air on its own.";

/** SafetyLimitsPanel's own warning, with its "above" repointed at the control
 *  that is actually above it here (the ramp switch on this sheet). */
const RAMP_OFF_WARNING =
  'With the ramp off, "Park and warm" cuts the cooler dead. It is still logged '
  + "as a warning every time, but nothing slows it down.";

/** E23: the design says this heater "follows the dew margin from Weather". No
 *  part of the engine drives the camera window heater from the dew margin. */
const DEW_NOTE = "anti-dew on the sensor window · set the power by hand";

/** The design's closing sentence, verbatim. */
const DEFAULTS_LINE =
  "These are the defaults every flow and quick session starts from; Manual "
  + "Capture can override them per shot.";

/** CaptureView:1885-1893's reason chain, in the same order. */
const EGAIN_RUNNING_REASON = "a measurement is already running";

const DEFAULT_COOLING: CoolingConfig = {
  warm_ramp: true,
  warm_rate_c_per_min: 2,
  warm_ambient_c: null,
};

// ------------------------------------------------------------------ formatting

/** One decimal and an explicit sign, because "+8.4" and "8.4" read differently
 *  beside a column of negatives. */
function degC(t: number | null | undefined): string {
  if (t == null || !Number.isFinite(t)) return "--";
  return `${t > 0 ? "+" : ""}${t.toFixed(1)}°C`;
}

/** The header's ONE live line. Every clause is dropped when the field behind it
 *  is absent - a camera that cannot report cooler power must not be described
 *  as running at 0%. */
export function cameraLiveLine(
  cam: CameraInfo | undefined,
  connected: boolean,
  gain: number,
  binning: number,
): string {
  if (!connected) return "NOT CONNECTED";
  if (!cam) return "connected · the driver has not reported yet";
  const bits: string[] = [];
  const cooler = cam.cooler;
  const warm = warmReadout(cam.warm);
  const t = cam.temperature;
  if (warm?.active) bits.push(`warming · ${degC(t)}`);
  else if (cooler?.on && cooler.at_target) bits.push(`cooled · ${degC(t)}`);
  else if (cooler?.on) bits.push(`cooling · ${degC(t)} -> ${degC(cooler.target_c)}`);
  else bits.push(`warm · ${degC(t)}`);

  if (cooler?.can_report_power && cooler.power != null) bits.push(`${cooler.power}% power`);
  else if (!cooler?.on && !warm?.active) bits.push("cooler off");

  bits.push(`gain ${gain}`);
  bits.push(`bin ${binning}x${binning}`);
  return bits.join(" · ");
}

/** The gain tile's sub, from the prototype's own three bands. */
function gainSub(gain: number): string {
  if (gain === 100) return "unity · 16-bit";
  return gain < 100 ? "low · max dynamic range" : "high · lucky imaging";
}

/** The dial's stops for a tile, with the LIVE value merged in.
 *
 *  Merging matters: `Dial` finds its index with `findIndex`, so a gain of 120
 *  against stops of 0/50/100/... resolves to -1 and the dial centres on the
 *  FIRST stop - a control showing 0 while the camera holds 120. */
export function dialStops(base: readonly number[], current: number, max?: number | null): number[] {
  const capped = max != null && max > 0 ? base.filter((v) => v <= max) : [...base];
  const withMax = max != null && max > 0 && !capped.includes(max) && capped.length < base.length
    ? [...capped, max] : capped;
  const all = withMax.includes(current) ? withMax : [...withMax, current];
  return [...new Set(all)].sort((a, b) => a - b);
}

/** Powers of two inside the driver's own ceiling (E1: the design asks for
 *  1/2/3; this camera stack does 1/2/4 and `max_bin` defaults to 4). */
export function binOptions(maxBin: number | null | undefined): number[] {
  const cap = maxBin && maxBin > 0 ? maxBin : 4;
  return [1, 2, 4].filter((b) => b <= cap);
}

/** The spec line, from the fields the driver ACTUALLY reported (E26). The
 *  design's "ZWO ASI2600MM Pro · IMX571 mono · 26 MP · 3.76 um · 16-bit ..." is
 *  a fixture; `status.camera` carries no sensor name, no bit depth and no bayer
 *  pattern, so those clauses are not written at all. */
export function cameraSpecLine(
  cam: CameraInfo | undefined,
  name: string | undefined,
  pixelUm: number | null,
): string {
  if (!cam) return "";
  const bits: string[] = [];
  if (name) bits.push(name);
  if (cam.width > 0 && cam.height > 0) {
    bits.push(`${cam.width} x ${cam.height}`);
    const mp = (cam.width * cam.height) / 1e6;
    if (mp >= 0.1) bits.push(`${mp.toFixed(1)} MP`);
  }
  if (pixelUm != null && pixelUm > 0) bits.push(`${pixelUm} µm pixels`);
  if (cam.max_gain > 0) bits.push(`gain to ${cam.max_gain}`);
  if (cam.max_bin && cam.max_bin > 0) bits.push(`bin to ${cam.max_bin}x${cam.max_bin}`);
  if (!cam.can_cool) bits.push("no cooler");
  return bits.join(" · ");
}

// -------------------------------------------------------------------- the ring

/** The 92 px cooler ring.
 *
 *  DEVIATION from plan 0.3's "use the `RingGauge` primitive": `RingGauge` takes
 *  `value: number` and prints it raw, so it can render neither the `--` a
 *  camera with no reading owes the user nor the degree sign. It is drawn here
 *  with the primitive's own `nx-ring*` classes, so the look is the primitive's
 *  and only the two honest cases differ. Named in the task report. */
function CoolerRing({ tempC, on }: { tempC: number | null; on: boolean }): JSX.Element {
  const frac = ringFraction(tempC);
  const tone = frac == null ? "dim" : on ? "accent" : "warn";
  return (
    <div
      className="nx-ring"
      data-tone={tone}
      style={{ width: 92, flexShrink: 0 }}
      role="img"
      aria-label={tempC == null ? "Sensor temperature not reported" : `Sensor ${degC(tempC)}`}
      data-temp={tempC == null ? "" : tempC.toFixed(1)}
      data-testid="camera-ring"
    >
      <div className="nx-ring-dial" style={{ width: 92, height: 92 }}>
        <svg viewBox="0 0 100 100" width={92} height={92} aria-hidden="true">
          <circle className="nx-ring-back" cx="50" cy="50" r="42" fill="none" strokeWidth="7" />
          <circle
            className="nx-ring-fill"
            cx="50" cy="50" r="42" fill="none" strokeWidth="7" strokeLinecap="round"
            strokeDasharray={ringDash(frac)}
            data-testid="camera-ring-arc"
          />
        </svg>
        <div className="nx-ring-inner">
          <span className="nx-ring-value">{tempC == null ? "--" : degC(tempC)}</span>
          <span className="nx-ring-label">SENSOR</span>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------- the sheet

export function CameraSheet(_p: SheetProps): JSX.Element {
  const status = useStatus();
  const equipConnected = useEquipConnected();
  const frame = useFrameSettings("capture");
  const config = useConfig();
  const sequence = useSequence();
  const egainLearn = useEgainLearn();
  const preview = usePreview();
  const photometry = usePhotometry();
  const breakpoint = useBreakpoint();

  const setFrameSettings = useStore((s) => s.setFrameSettings);
  const setPhotometry = useStore((s) => s.setPhotometry);
  const showToast = useStore((s) => s.showToast);

  const cam = status?.camera;
  const cooler = cam?.cooler;
  const warm = warmReadout(cam?.warm);
  const temp = cam?.temperature ?? null;
  const role = resolveRoleConnected("camera", status?.backend_links, status?.connected, equipConnected);

  // ---- who owns the camera right now
  const polar = usePolar();
  // r4 #25: an alignment holds the camera for its whole run and spawns its own
  // lane (server/astrodeck/hub.py:297). This sheet named the sequence and never
  // named polar, so MEASURE GAIN - which exposes repeatedly at two gains - was
  // live through an alignment and came back as a raw 409.
  const polarOwns = isPolarBusy(polar.state, status?.busy_lanes) ? POLAR_REASON : null;
  const seqState = sequence?.state ?? null;
  const flowOwns = seqState === "running" || seqState === "paused";
  const ownExtra = !flowOwns ? null
    : seqState === "paused" ? FLOW_OWNS_CAMERA_PAUSED : FLOW_OWNS_CAMERA;

  // ---- locks (plan section C). `useLock` takes ONE lane, and three of these
  // controls are blocked by two, so the results are combined in the gate's own
  // priority order: both calls run the identical link/cap/role checks first, so
  // taking the first non-null reason cannot reorder them.
  const coolerLock = useLock({ cap: "control.capture", needsRole: "camera", busyLane: "capture", extra: ownExtra });
  const frameCapture = useLock({ cap: "control.capture", needsRole: "camera", busyLane: "capture", extra: ownExtra });
  const frameLooping = useLock({ cap: "control.capture", needsRole: "camera", busyLane: "looping", extra: ownExtra });
  const egainLane = useLock({ cap: "control.capture", needsRole: "camera", busyLane: "egain", extra: ownExtra });
  const dewLock = useLock({ cap: "control.capture", needsRole: "camera" });
  const rampLock = useLock({ cap: "config.safety" });

  const frameReason = frameCapture.lockedReason ?? frameLooping.lockedReason;
  const measuring = egainLearn?.state === "running";
  const egainReason = egainLane.lockedReason ?? frameLooping.lockedReason
    ?? polarOwns ?? (measuring ? EGAIN_RUNNING_REASON : null);
  const explain = coolerLock.onExplain;

  // ---- the cooling curve: what THIS browser has been told the sensor is at.
  // Fed from the whole `status` object, not from `temp`, because the store
  // replaces it wholesale every poll: keying on the number alone would drop
  // every repeat reading and a camera holding -10.0 would never build a curve.
  const [samples, setSamples] = useState<number[]>([]);
  useEffect(() => {
    setSamples((buf) => pushTemp(buf, status?.camera?.temperature));
  }, [status]);
  // The buffer is component state, so closing the sheet drops it: re-opening it
  // starts from nothing rather than redrawing last night's cool-down.

  // ---- the set-point the SWITCH would send. Staged locally: turning the cooler
  // on is what sends it, and while the cooler is off the driver's `target_c` is
  // whatever it last asserted (after a warm ramp, room temperature) - following
  // that would put an above-ambient target under a button labelled COOL.
  const deviceTarget = cooler?.target_c ?? null;
  const configured = (config?.cooling as { setpoint_c?: number | null } | undefined)?.setpoint_c;
  const [setpoint, setSetpoint] = useState<number>(() =>
    deviceTarget ?? (typeof configured === "number" ? configured : -10));
  const followed = useRef<number | null>(null);
  useEffect(() => {
    // Follow the camera only while it is actually HOLDING a set-point, and only
    // when that number moves - so a value picked here is not yanked back by the
    // next poll, and a ramp's climbing set-point never drags the tile.
    if (deviceTarget == null || !cooler?.on || warm?.active) return;
    if (followed.current === deviceTarget) return;
    followed.current = deviceTarget;
    setSetpoint(Number(deviceTarget.toFixed(1)));
  }, [deviceTarget, cooler?.on, warm?.active]);
  const disagrees = cooler?.on && !warm?.active && deviceTarget != null
    && Math.abs(deviceTarget - setpoint) >= 0.05;

  const [dial, setDial] = useState<"setpoint" | "gain" | "offset" | "egain">("setpoint");

  const post = async (path: string, body: unknown): Promise<void> => {
    try { await api.post(path, body); }
    catch (e) { showToast("error", (e as Error).message); }
  };

  const cooling = config?.cooling ?? DEFAULT_COOLING;
  const rampRate = cooling.warm_rate_c_per_min ?? DEFAULT_COOLING.warm_rate_c_per_min;
  const writeCooling = async (patch: Partial<CoolingConfig>): Promise<void> => {
    try {
      // Wholesale replace (plan 0.4): read the block, spread it, patch one field.
      await setCoolingConfig({ ...cooling, ...patch });
      await useStore.getState().loadConfig();
    } catch (e) {
      showToast("error", (e as Error).message);
    }
  };

  // ---- dew heater. `status.camera.dew_heater` IS published when the camera can
  // be asked (server hub.py: absent means "cannot be asked", which is not 0), so
  // the level is read back where it exists and is otherwise this browser's own
  // last write, said out loud. The field is declared on `RigStatus.camera` in
  // types.ts, so this reads the typed field and no cast narrows it.
  const dewReported = cam?.dew_heater ?? null;
  const [dewSent, setDewSent] = useState<number | null>(null);
  const dewLevel = dewReported ?? dewSent ?? 0;

  const curve = useMemo(() => coolerCurve(samples, cooler?.on ? deviceTarget : setpoint),
    [samples, cooler?.on, deviceTarget, setpoint]);

  const optics = config?.optics_computed;
  const pixelUm = optics?.source === "camera" && optics.pixel_size_um > 0 ? optics.pixel_size_um : null;
  const scale = optics?.have_optics ? optics.image_scale_arcsec_px : null;

  const gainStops = dialStops([0, 50, 100, 150, 200, 300], frame.gain, cam?.max_gain);
  const offsetStops = dialStops([10, 30, 50, 70, 100], frame.offset);
  const setpointStops = dialStops([-20, -15, -10, -5, 0, 5], setpoint);
  const bins = binOptions(cam?.max_bin);

  const learned = cam?.egain_learned ?? {};
  const gainKey = String(Math.round(frame.gain));
  const egainValue = cam?.egain && cam.egain > 0 ? cam.egain : learned[gainKey];
  const egainSub = cam?.egain && cam.egain > 0 ? "e-/ADU · from the driver"
    : egainValue ? `e-/ADU · measured at gain ${gainKey}` : "measure to enable SNR";

  // ---- cooler sub-line: the ONE sentence about what the cooler is doing.
  const coolerSub = warm && (warm.active || warm.unramped)
    ? (warm.detail ? `${warm.headline} · ${warm.detail}` : warm.headline)
    : !cooler ? "no cooler on this camera"
      : !cooler.on ? "off · sensor at ambient"
        : cooler.at_target ? `at ${degC(deviceTarget)} · gate open`
          : cooler.can_report_power && cooler.power != null
            ? `cooling to ${degC(deviceTarget)} · ${cooler.power}% power`
            : `cooling to ${degC(deviceTarget)}`;

  const sendSetpoint = (next: number) => {
    setSetpoint(next);
    followed.current = next;
    // Only a cooler that is ALREADY on takes the number now; while it is off the
    // tile stages it and the switch is what sends it.
    if (cooler?.on && !warm?.active) void post("/api/camera/cooler", { on: true, target_c: next });
  };

  const live = cameraLiveLine(cam, role.connected, frame.gain, frame.binning);

  return (
    <Sheet
      title="CAMERA"
      backLabel="RIG"
      icon={<NxIcon name="camera" size={18} />}
      live={live}
      onBack={() => nav.back()}
      data-testid="rig-camera"
    >
      {!role.connected && (
        <EmptyCard
          title="No camera connected"
          hint={role.error ?? "Assign a camera on ADD A DEVICE, then connect the rig."}
          action={(
            <ActionButton kind="secondary" onPress={() => nav.sheet("addDevice")}>
              GO TO ADD A DEVICE
            </ActionButton>
          )}
          data-testid="camera-empty"
        />
      )}

      {/* ------------------------------------------------------- cooler card */}
      <Card padding={12} data-testid="camera-cooler">
        <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
          <CoolerRing tempC={temp} on={!!cooler?.on} />
          <div style={{ flex: 1, minWidth: 180, display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <span style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
                <Label>COOLER</Label>
                <Mono size={10} tone={warm?.unramped ? "warn" : "dim"}>{coolerSub}</Mono>
              </span>
              <Switch
                checked={!!cooler?.on}
                hideLabel
                label={cooler?.on ? "Cooler on - switch it off and warm the sensor"
                  : `Cooler off - switch it on and cool to ${setpoint}°C`}
                onChange={(next) => post("/api/camera/cooler",
                  next ? { on: true, target_c: setpoint } : { on: false })}
                lockedReason={coolerLock.lockedReason}
                onExplain={explain}
                data-testid="cooler-switch"
              />
            </div>

            <svg viewBox={`0 0 ${CURVE_W} ${CURVE_H}`} preserveAspectRatio="none"
              style={{ width: "100%", height: CURVE_H, display: "block" }}
              role="img"
              aria-label={curve.ready
                ? `Sensor temperature over the last ${samples.length} readings, ${curve.hi.toFixed(1)} to ${curve.lo.toFixed(1)} degrees`
                : BUILDING_NOTE}
              data-testid="camera-curve"
              data-ready={curve.ready ? "true" : "false"}>
              {curve.ready && (
                <polyline points={curve.points} fill="none" stroke="var(--accent)"
                  strokeWidth="1.5" strokeLinejoin="round" data-testid="camera-curve-line" />
              )}
              {curve.setpointY != null && (
                <line x1="0" y1={curve.setpointY} x2={CURVE_W} y2={curve.setpointY}
                  stroke="var(--accent)" strokeOpacity="0.35" strokeDasharray="3 3" />
              )}
            </svg>
            {!curve.ready && <Mono size={10} tone="dim">{BUILDING_NOTE}</Mono>}

            {warm?.active && (
              <>
                <ActionButton kind="ghost" onPress={() => {
                  void post("/api/camera/cooler", { on: false, ramp: false });
                  showToast("info", "Warm ramp stopped - cooler off");
                }}
                  lockedReason={coolerLock.lockedReason}
                  onExplain={explain}
                  data-testid="cooler-stop-ramp">
                  STOP RAMP
                </ActionButton>
                {/* What it costs, as VISIBLE text. The shipped control carried
                    this in `title=`, which never fires on a touch screen - the
                    one place a ten-minute ramp gets abandoned. */}
                <Mono size={10} tone="dim">{STOP_RAMP_TITLE}</Mono>
              </>
            )}

            <Mono size={10} tone={cooling.warm_ramp ? "dim" : "warn"}>
              {cooling.warm_ramp
                ? `ramp-limited ${rampRate}°/min · capture gate opens when stable`
                : "ramp OFF - warming cuts the cooler dead"}
            </Mono>
          </div>
        </div>
      </Card>

      {/* --------------------------------------------------- readouts + dial */}
      <ReadoutGrid data-testid="camera-readouts">
        <ReadoutTile label="SETPOINT" value={`${setpoint}°C`} sub="cooler target"
          selected={dial === "setpoint"} onSelect={() => setDial("setpoint")}
          data-testid="tile-setpoint" />
        <ReadoutTile label="GAIN" value={String(frame.gain)} sub={gainSub(frame.gain)}
          selected={dial === "gain"} onSelect={() => setDial("gain")}
          data-testid="tile-gain" />
        <ReadoutTile label="OFFSET" value={String(frame.offset)} sub="no clipped blacks"
          selected={dial === "offset"} onSelect={() => setDial("offset")}
          data-testid="tile-offset" />
        <ReadoutTile label="E-GAIN"
          value={egainValue ? egainValue.toFixed(2) : "not measured"} sub={egainSub}
          selected={dial === "egain"} onSelect={() => setDial("egain")}
          data-testid="tile-egain" />
      </ReadoutGrid>

      {disagrees && deviceTarget != null && (
        <div data-testid="setpoint-disagrees">
          <Mono size={10.5} tone="warn">
            {`The camera is holding ${deviceTarget.toFixed(1)}°C - the switch sends ${setpoint}°C.`}
          </Mono>
        </div>
      )}

      {dial === "setpoint" && (
        <Dial label="SETPOINT" options={setpointStops.map((v) => ({ value: v, label: `${v}°C` }))}
          value={setpoint} onChange={sendSetpoint}
          lockedReason={coolerLock.lockedReason} onExplain={explain}
          data-testid="dial-setpoint" />
      )}
      {dial === "gain" && (
        <Dial label="GAIN" options={gainStops.map((v) => ({ value: v, label: String(v) }))}
          value={frame.gain} onChange={(gain) => setFrameSettings("capture", { gain })}
          lockedReason={frameReason} onExplain={explain}
          data-testid="dial-gain" />
      )}
      {dial === "offset" && (
        <Dial label="OFFSET" options={offsetStops.map((v) => ({ value: v, label: String(v) }))}
          value={frame.offset} onChange={(offset) => setFrameSettings("capture", { offset })}
          lockedReason={frameReason} onExplain={explain}
          data-testid="dial-offset" />
      )}
      {dial === "egain" && (
        <div data-testid="egain-note"><Mono size={10.5} tone="dim">
          e-/ADU is MEASURED, not set - there is no dial for it. MEASURE GAIN below
          runs the learn loop at gain {gainKey}.
        </Mono></div>
      )}

      {/* ---------------------------------------------------------- binning */}
      <Card padding={0} data-testid="camera-rows">
        {/* The row is NOT itself pressable. It holds a Segmented, and a row that
            both taps through and contains a control puts one button inside
            another - ambiguous to a pointer and invalid to a screen reader. When
            the pixel scale is unknown the way to the optics is its own button
            underneath. */}
        <ListRow
          title="BINNING"
          sub={cam
            ? `${Math.floor(cam.width / frame.binning)} x ${Math.floor(cam.height / frame.binning)}`
              + (scale != null ? ` · ${(scale * frame.binning).toFixed(2)}"/px` : " · set the optics for a pixel scale")
            : "set the optics for a pixel scale"}
          right={(
            <Segmented
              label="Binning"
              options={bins.map((b) => ({ value: b, label: `${b}x${b}` }))}
              value={frame.binning}
              onChange={(binning) => setFrameSettings("capture", { binning })}
              lockedReason={frameReason}
              onExplain={explain}
              data-testid="binning"
            />
          )}
          data-testid="row-binning"
        />
        {scale == null && (
          <div style={{ padding: "0 14px 10px" }}>
            <ActionButton kind="ghost" onPress={() => nav.go("/settings/general/optics")}
              data-testid="go-optics">
              SET THE OPTICS
            </ActionButton>
          </div>
        )}

        <div style={{ padding: "10px 14px", display: "flex", flexDirection: "column", gap: 6 }}>
          <Switch
            checked={cooling.warm_ramp}
            label="WARM UP BEFORE PARK"
            note={`ramps to ambient at ${rampRate}°C/min so the sensor never sees a thermal shock`}
            onChange={(warm_ramp) => void writeCooling({ warm_ramp })}
            lockedReason={rampLock.lockedReason}
            onExplain={rampLock.onExplain}
            data-testid="warm-ramp"
          />
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <Label>RAMP RATE</Label>
            <Stepper2
              value={rampRate}
              onChange={(v) => void writeCooling({ warm_rate_c_per_min: Number(v.toFixed(1)) })}
              step={0.5} min={0.1} max={20}
              format={(v) => `${v}°C/min`}
              label="Warm ramp rate"
              lockedReason={rampLock.lockedReason}
              onExplain={rampLock.onExplain}
              data-testid="ramp-rate"
            />
          </div>
          {!cooling.warm_ramp && (
            <div data-testid="ramp-off-warning"><Mono size={10.5} tone="warn">{RAMP_OFF_WARNING}</Mono></div>
          )}
        </div>

        {cam?.has_dew_heater && (
          <div style={{ padding: "10px 14px", display: "flex", flexDirection: "column", gap: 6,
            borderTop: "1px solid var(--line)" }}>
            <Switch
              checked={dewLevel > 0}
              label="SENSOR WINDOW HEATER"
              note={DEW_NOTE}
              onChange={(on) => {
                const power = on ? 100 : 0;
                setDewSent(power);
                void post("/api/camera/dew-heater", { power });
              }}
              lockedReason={dewLock.lockedReason}
              onExplain={dewLock.onExplain}
              data-testid="dew-switch"
            />
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <Stepper2
                value={dewLevel}
                // A stepper has no drag, so each press IS a completed gesture and
                // commits. The alternative - a separate SEND - would invent a
                // control the design does not have and leave the number on screen
                // describing nothing.
                onChange={(power) => { setDewSent(power); void post("/api/camera/dew-heater", { power }); }}
                step={10} min={0} max={100}
                format={(v) => `${v}%`}
                label="Sensor window heater power"
                lockedReason={dewLock.lockedReason}
                onExplain={dewLock.onExplain}
                data-testid="dew-power"
              />
              <Mono size={10} tone="dim">
                {dewReported != null ? "read back from the camera"
                  : dewSent != null ? "the level this browser last sent - this camera reports none back"
                    : "this camera reports no level back"}
              </Mono>
            </div>
          </div>
        )}
      </Card>

      {/* --------------------------------------------------------- actions */}
      <ActionButton
        kind="secondary"
        onPress={() => {
          void post("/api/camera/egain/learn", { gain: Number(gainKey) });
          showToast("info", `Measuring gain at ${gainKey}…`);
        }}
        lockedReason={egainReason}
        onExplain={explain}
        busy={measuring}
        data-testid="measure-gain"
      >
        {measuring
          ? `MEASURING ${egainLearn?.step ?? 0}/${egainLearn?.of ?? 0}`
          : `MEASURE GAIN AT ${gainKey}`}
      </ActionButton>

      {/* ------------------------------------------------- tuning (>= tablet) */}
      {breakpoint !== "phone" && (
        <Tuning
          camEgain={cam?.egain}
          photometry={photometry}
          setPhotometry={setPhotometry}
          linearMedian={preview && preview.data_is_linear ? preview.stats.median : null}
          previewExposureS={preview?.exposure_s ?? null}
          lockedReason={frameReason}
          onExplain={explain}
          onApply={(s) => setFrameSettings("capture", { exposure_s: s })}
          toast={showToast}
        />
      )}

      {/* ------------------------------------------------------- spec + line */}
      <div data-testid="camera-spec">
        <Mono size={10} tone="dim">{cameraSpecLine(cam, status?.connected?.camera?.name, pixelUm)}</Mono>
      </div>
      <Mono size={10} tone="dim">{DEFAULTS_LINE}</Mono>
      <div style={{ height: 8 }} />
    </Sheet>
  );
}

// ------------------------------------------------------------------- tuning

function Tuning({
  camEgain, photometry, setPhotometry, linearMedian, previewExposureS,
  lockedReason, onExplain, onApply, toast,
}: {
  camEgain: number | undefined;
  photometry: { egain: number; readNoiseE: number; biasAdu: number };
  setPhotometry: (p: Partial<{ egain: number; readNoiseE: number; biasAdu: number }>) => void;
  linearMedian: number | null;
  previewExposureS: number | null;
  lockedReason: string | null;
  onExplain: (reason: string) => void;
  onApply: (seconds: number) => void;
  toast: (level: string, message: string) => void;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const usingCameraEgain = photometry.egain <= 0 && !!camEgain && camEgain > 0;
  const effectiveEgain = usingCameraEgain ? camEgain! : photometry.egain;
  const canSuggest = effectiveEgain > 0 && photometry.readNoiseE > 0 && linearMedian != null;
  const suggestReason = canSuggest ? null
    : effectiveEgain <= 0 || photometry.readNoiseE <= 0
      ? "Add camera gain + read noise above to enable Suggest"
      : "Take a light frame first - Suggest needs a linear preview";

  return (
    <Card padding={12} data-testid="camera-tuning">
      <ActionButton kind="ghost" onPress={() => setOpen((v) => !v)}
        ariaLabel="Camera photometry tuning" data-testid="tuning-toggle">
        {open ? "HIDE TUNING" : "TUNING · PHOTOMETRY"}
      </ActionButton>
      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
          <Field label="E-/ADU"
            hint={usingCameraEgain ? "Reported by the camera - not editable here."
              : "From the read-noise harness or the datasheet, at the gain above."}>
            <TextInput
              value={usingCameraEgain ? camEgain!.toFixed(3) : String(photometry.egain || "")}
              onChange={(v) => { if (!usingCameraEgain) setPhotometry({ egain: Number(v) || 0 }); }}
              mono
              ariaLabel="Gain in electrons per ADU"
              lockedReason={usingCameraEgain ? "The camera reports this one" : lockedReason}
              data-testid="phot-egain"
            />
          </Field>
          <Field label="READ NOISE (e-)" hint="At this gain, from the harness or the datasheet.">
            <TextInput value={String(photometry.readNoiseE || "")}
              onChange={(v) => setPhotometry({ readNoiseE: Number(v) || 0 })} mono
              ariaLabel="Read noise in electrons" lockedReason={lockedReason}
              data-testid="phot-readnoise" />
          </Field>
          <Field label="BIAS (ADU)" hint="Median of a Bias frame. 0 slightly overestimates sky, which is safe.">
            <TextInput value={String(photometry.biasAdu || "")}
              onChange={(v) => setPhotometry({ biasAdu: Number(v) || 0 })} mono
              ariaLabel="Bias pedestal in ADU" lockedReason={lockedReason}
              data-testid="phot-bias" />
          </Field>
          <ActionButton kind="secondary"
            lockedReason={suggestReason ?? lockedReason}
            onExplain={onExplain}
            onPress={() => {
              if (linearMedian == null || previewExposureS == null) return;
              const s = suggestSubLength({
                medianAdu: linearMedian, biasAdu: photometry.biasAdu,
                egain: effectiveEgain, readNoiseE: photometry.readNoiseE,
                exposureS: previewExposureS,
              });
              if (!s.ok || s.suggestedS == null) { toast("warning", s.reason); return; }
              onApply(s.suggestedS);
              toast("success", `Suggested ${s.suggestedS}s - ${s.reason}`);
            }}
            data-testid="suggest">
            SUGGEST SETTINGS
          </ActionButton>
        </div>
      )}
    </Card>
  );
}
