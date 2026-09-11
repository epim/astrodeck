// CoolerRow.tsx - the cooler gate and the dew heater, compact.
//
// Not the Camera sheet (that is hub 4). This is here because a manual shot's
// cooler gate is the difference between a usable frame and a warm one: the
// setpoint has to settle before the shutter fires, and an operator who cannot
// see the sensor temperature from the bench will shoot anyway.
//
// THREE BEHAVIOURS CARRIED VERBATIM FROM CaptureView, each of which was a bug
// once:
//
//  * The target box FOLLOWS THE DEVICE until somebody types in it, and only
//    while the cooler is ON and not ramping. `cooler.target_c` is the DRIVER's
//    live set-point, not the user's intent: during a warm ramp the hub re-asserts
//    a set-point walking up to ambient every 15 s, so an unconditional follow
//    drags the box with it and SET - pressed to abort the ramp - would command
//    the ramp's own -3.2 instead of the -20 on screen. After a ramp the driver
//    keeps reporting the last asserted value, so an OFF cooler at +12 would put
//    room temperature in the box and COOL would drive the TEC against an
//    above-ambient target. That one survives to the next evening.
//  * SET lights only when there is an UNSENT EDIT, never because the cooler is
//    merely on. It used to carry aria-pressed driven by `cooler.on`, i.e. lit
//    BECAUSE the cooler was running, which is the one thing it does not control.
//  * WARM starts a RAMP; mid-ramp the same slot becomes STOP RAMP, which sends
//    the old non-ramped `{on:false, ramp:false}`. That is a deliberate escape
//    hatch, not a leftover.
//
// THE DEW HEATER DOES READ BACK, where the camera can be asked. `hub.py`
// publishes `status.camera.dew_heater` alongside `has_dew_heater`, and the key
// is ABSENT (not 0) on a camera that cannot answer - so this panel seeds and
// re-syncs from it exactly as the Camera sheet does, and falls back to "what
// THIS browser last sent" only where there is genuinely nothing to read. It had
// seeded 0 and printed "the camera reports none back" while the Camera sheet
// one tap away showed 60, and a SEND from here then commanded 0 - which is not
// merely a wrong number on screen: a hand write pauses the dew loop for
// `manual_override_s` (`dew.py:444-502`), so it costs the night's dew margin.
// The explicit SEND stays: a stepper on a bench is a coarse gesture and the
// register is one the loop is also driving.

import { useEffect, useRef, useState, type JSX } from "react";
import { warmReadout } from "../../../../lib/cooling";
import {
  ActionButton, Bar, Field, Label, Mono, RingGauge, Stepper2, TextInput,
} from "../../../ui";
import { COOLER_MAX_C, COOLER_MIN_C, isCoolerTargetInvalid } from "./captureGate";
import type { RigStatus } from "../../../../types";

export interface CoolerRowProps {
  status: RigStatus | null;
  /** The access floor plus the range guard (captureGate.coolerReason). */
  coolReason: string | null;
  warmReason: string | null;
  /** The access floor alone (link -> capability -> camera connected). Locks the
   *  target box and the dew heater, neither of which has a value guard of its
   *  own. NOT `coolReason`: a set-point that is out of range must stay editable,
   *  or the only way to fix a typo is to reload. */
  accessReason: string | null;
  onExplain: (reason: string) => void;
  onCooler: (body: { on: boolean; target_c?: number; ramp?: boolean }) => void;
  onDew: (power: number) => void;
  /** The target box's text, owned by the screen so a shot and a darks prefill
   *  can both write it. */
  targetText: string;
  setTargetText: (v: string) => void;
  /** True once the operator has typed in the box - stops the device follow. */
  edited: boolean;
}

export function CoolerRow(props: CoolerRowProps): JSX.Element {
  const cam = props.status?.camera;
  const cooler = cam?.cooler;
  const warm = warmReadout(cam?.warm);
  const temp = cam?.temperature ?? null;

  const deviceTargetC = cooler?.target_c;
  const coolerHolding = !!cooler?.on && !warm?.active;
  const setTargetText = props.setTargetText;
  useEffect(() => {
    if (props.edited || deviceTargetC == null || !coolerHolding) return;
    setTargetText(String(Number(deviceTargetC.toFixed(1))));
  }, [deviceTargetC, coolerHolding, props.edited, setTargetText]);

  const targetInvalid = isCoolerTargetInvalid(props.targetText);
  const targetNum = Number(props.targetText);
  // Derived from the two NUMBERS, not from an "edited" ref, so it self-corrects
  // when the driver clamps the request and when another client moves the
  // set-point. Gated on `coolerHolding` because during a ramp the two differ BY
  // DESIGN and SET there means "abort the ramp", not "apply an edit".
  const editPending = coolerHolding && !targetInvalid && deviceTargetC != null
    && Math.abs(targetNum - deviceTargetC) >= 0.05;

  // ---- cool-down progress: determinate, from numbers the poll already carries.
  const coolingToTarget = !!cooler?.on && !warm?.active && !cooler?.at_target
    && temp != null && deviceTargetC != null && temp > deviceTargetC + 0.3;
  const hist = useRef<Array<[number, number]>>([]);
  const startTemp = useRef<number | null>(null);
  useEffect(() => {
    if (temp == null) return;
    const now = Date.now();
    hist.current = [...hist.current.filter(([ts]) => now - ts < 120_000), [now, temp]];
  }, [temp]);
  useEffect(() => {
    if (coolingToTarget && startTemp.current == null) startTemp.current = temp;
    else if (!coolingToTarget) startTemp.current = null;
  }, [coolingToTarget, temp]);
  let coolPct: number | null = null;
  let coolEtaMin: number | null = null;
  if (coolingToTarget && temp != null && deviceTargetC != null) {
    const start = startTemp.current ?? temp;
    const span = start - deviceTargetC;
    if (span > 0.5) coolPct = Math.max(0, Math.min(100, ((start - temp) / span) * 100));
    const anchor = hist.current.find(([ts]) => Date.now() - ts > 20_000);
    if (anchor) {
      const ratePerMin = (temp - anchor[1]) / ((Date.now() - anchor[0]) / 60_000);
      // Never an ASSUMED rate: only one measured over the last minute (#154 is
      // what assuming an ambient cost).
      if (ratePerMin < -0.1) coolEtaMin = (temp - deviceTargetC) / -ratePerMin;
    }
  }

  const ringSub = warm?.active ? warm.headline
    : !cooler ? "no cooler on this camera"
      : !cooler.on ? "cooler off"
        : cooler.at_target
          ? (cooler.can_report_power && cooler.power != null
            ? `at target · ${cooler.power}% power` : "at target")
          : `cooling to ${cooler.target_c ?? "?"}°C`;

  // ---- the dew heater. `status.camera.dew_heater` IS the register read back
  // where the camera can be asked (absent means "cannot be asked", which is not
  // 0 - hub.py publishes the key only when there is a level). Same resolution
  // as the Camera sheet one tap away (`sheets/camera.tsx`'s
  // `dewReported ?? dewSent ?? 0`), because the two panels describing one
  // heater differently is how a SEND from this bench commanded 0 over a rig
  // running 60 - and a hand write pauses the dew loop for `manual_override_s`,
  // so that zero costs the night's dew margin, not just a number on screen.
  const dewReported = cam?.dew_heater ?? null;
  const [dewSent, setDewSent] = useState<number | null>(null);
  const [dew, setDew] = useState<number>(() => dewReported ?? 0);
  // RE-SYNC ON A MOVE, not on every poll. The same shape as the cooler
  // set-point above and as `sheets/camera.tsx`'s `followed` ref: adopting the
  // rig's number whenever it CHANGES keeps the stepper honest when the dew loop
  // re-ramps or another client writes, while a level picked here is not yanked
  // back by the next 2 s frame repeating what it already said.
  const dewFollowed = useRef<number | null>(null);
  useEffect(() => {
    if (dewReported == null || dewFollowed.current === dewReported) return;
    dewFollowed.current = dewReported;
    setDew(dewReported);
  }, [dewReported]);
  // SEND lights only when the number on screen differs from what the RIG is at
  // (or, on a camera that reports none back, from this browser's last write).
  const dewPending = dewReported != null
    ? dew !== dewReported
    : dewSent == null ? dew > 0 : dew !== dewSent;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }} data-testid="capture-cooler">
      <Label>COOLER</Label>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <RingGauge
          value={temp ?? 0}
          min={-30}
          max={25}
          label="SENSOR"
          sub={ringSub}
          tone={warm?.unramped ? "warn" : cooler?.at_target ? "accent" : "dim"}
        />
        <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: 1, minWidth: 180 }}>
          <Field label={`TARGET °C (${COOLER_MIN_C} to ${COOLER_MAX_C})`}
            hint={targetInvalid ? `A number between ${COOLER_MIN_C} and ${COOLER_MAX_C}` : undefined}>
            <TextInput
              value={props.targetText}
              onChange={props.setTargetText}
              mono
              ariaLabel="Cooler target in Celsius"
              lockedReason={props.accessReason}
              data-testid="cooler-target"
            />
          </Field>
          <div style={{ display: "flex", gap: 6 }}>
            <ActionButton
              kind={editPending ? "primary" : "secondary"}
              onPress={() => props.onCooler({ on: true, target_c: targetNum })}
              lockedReason={props.coolReason}
              onExplain={props.onExplain}
              data-testid="cooler-set"
            >
              {editPending ? `SET ${targetNum}°C` : "COOL"}
            </ActionButton>
            {warm?.active ? (
              <ActionButton
                kind="danger"
                onPress={() => props.onCooler({ on: false, ramp: false })}
                lockedReason={props.warmReason}
                onExplain={props.onExplain}
                data-testid="cooler-warm"
              >
                STOP RAMP
              </ActionButton>
            ) : (
              <ActionButton
                kind="secondary"
                onPress={() => props.onCooler({ on: false })}
                lockedReason={props.warmReason}
                onExplain={props.onExplain}
                data-testid="cooler-warm"
              >
                WARM
              </ActionButton>
            )}
          </div>
        </div>
      </div>

      {coolPct != null && (
        <div>
          <Bar value={coolPct / 100} height={3} label="Cool-down progress" />
          <Mono size={10} tone="dim">
            {coolEtaMin != null
              ? `cooling · about ${Math.max(1, Math.round(coolEtaMin))} min left at the rate measured`
              : "cooling · rate not measurable yet"}
          </Mono>
        </div>
      )}

      {warm?.active && (
        <div>
          <Bar value={warm.pct / 100} height={3} tone={warm.unramped ? "warn" : "accent"}
            label="Warm ramp" />
          <Mono size={10} tone={warm.unramped ? "warn" : "dim"}>
            {warm.detail ? `${warm.headline} · ${warm.detail}` : warm.headline}
          </Mono>
        </div>
      )}
      {warm && !warm.active && warm.unramped && (
        <Mono size={10} tone="warn">{warm.detail ? `${warm.headline} · ${warm.detail}` : warm.headline}</Mono>
      )}

      {cam?.has_dew_heater && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <Label>DEW HEATER</Label>
          <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            <Stepper2
              value={dew}
              onChange={setDew}
              step={10}
              min={0}
              max={100}
              format={(v) => `${v}%`}
              label="Dew heater power"
              lockedReason={props.accessReason}
              onExplain={props.onExplain}
              data-testid="dew-stepper"
            />
            <ActionButton
              kind={dewPending ? "primary" : "secondary"}
              onPress={() => { props.onDew(dew); setDewSent(dew); }}
              lockedReason={props.accessReason}
              onExplain={props.onExplain}
              data-testid="dew-send"
            >
              SEND
            </ActionButton>
          </div>
          <Mono size={10} tone="dim">
            {dewReported != null
              ? `The rig reports ${dewReported}% on the window heater. A level set here pauses `
                + "the dew loop until its override expires - the Camera sheet says for how long."
              : dewSent == null
                ? "This camera reports no heater level back, and nothing has been sent from this "
                  + "browser, so the number above is a starting point rather than a reading."
                : `This camera reports no heater level back. Last sent from this browser: ${dewSent}%.`}
          </Mono>
        </div>
      )}
    </div>
  );
}
