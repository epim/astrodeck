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
// The dew heater has NO READ-BACK anywhere - no camera backend exposes one and
// the hub publishes only `has_dew_heater` - so the only honest thing this panel
// can say is what THIS browser last got the server to accept. Hence a level and
// an explicit SEND rather than a control that looks like a reading.

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

  const [dew, setDew] = useState(0);
  const [dewSent, setDewSent] = useState<number | null>(null);
  const dewPending = dewSent == null ? dew > 0 : dew !== dewSent;

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
            {dewSent == null
              ? "No heater level has been sent from this browser. The camera reports none back, so nothing here can read the current one."
              : `Last sent from this browser: ${dewSent}%. The camera reports no level back.`}
          </Mono>
        </div>
      )}
    </div>
  );
}
