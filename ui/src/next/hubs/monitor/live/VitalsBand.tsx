// VitalsBand.tsx - the six numbers the design puts under the guiding trace.
//
// The screenshot has four (flip, sensor, dew margin, disk); GAP-ANALYSIS §11
// asks for the two countdowns the old header strip had and the design dropped:
// "to dawn" and "next target".
//
// EVERY TILE IS HONEST-ABSENT (plan §D.2). A tile whose wire field is missing
// says WHY in its own words and keeps its slot: a hidden tile reads as "not
// applicable to this rig", and a zero reads as a measurement.
//
// The faces are pure functions of what was read, exported so they can be
// reasoned about (and asserted) without a DOM.

import { useEffect, useState, type JSX } from "react";
import { api } from "../../../../api";
import { useCamera, useMeridian, useSeq, useStatus, useWeather, usePlan } from "../../../../store";
import { accessPhrase, useCan, useCanViewWeather } from "../../../../lib/caps";
import type { DiskInfo, SequencePlan, SequenceState, WeatherState } from "../../../../types";
import { fmtClock, fmtDuration } from "../../../lib/format";
import { ReadoutGrid, ReadoutTile, type Tone } from "../../../ui";
import { FlipTile, type TileFace } from "./FlipTile";

type Camera = NonNullable<ReturnType<typeof useCamera>>;

export interface DarkWindow { start_iso: string; end_iso: string }

/** SENSOR. `CoolingCountdown` (`MonitorView.tsx:1367`) transcribed into a
 *  sub-line: at target with its power draw, or the ramp in progress with both
 *  ends of it - a bare "cooling" cannot tell 2 degrees to go from 20. */
export function sensorFace(camera: Camera | null, detail: string | undefined): TileFace {
  if (!camera) return { value: "no camera", sub: "connect a camera to read the sensor", tone: "dim" };
  const t = camera.temperature;
  const cooler = camera.cooler;
  const value = t != null ? `${t.toFixed(1)}°` : "not reported";
  if (camera.can_cool === false || !cooler) {
    return { value, sub: "no cooler on this camera", tone: t != null ? "accent" : "dim" };
  }
  const tgt = cooler.target_c;
  const tgtStr = tgt != null ? `${tgt > 0 ? "+" : ""}${tgt}°C` : "no setpoint";
  if (cooler.at_target) {
    const power = cooler.can_report_power && cooler.power != null
      ? ` · ${Math.round(cooler.power)}% power` : "";
    return { value, sub: `at target${power}`, tone: "good" };
  }
  const cooling = (detail ?? "").toLowerCase().startsWith("cooling");
  if (cooling || cooler.on) {
    return {
      value,
      sub: `${t != null ? `${t.toFixed(1)}°C` : "--"} to ${tgtStr} · ${cooling ? "cooling" : "settling"}`,
      tone: "warn",
    };
  }
  return { value, sub: `cooler off · setpoint ${tgtStr}`, tone: "dim" };
}

/** DEW MARGIN = ambient - dew point (plan §A.1.3). Both fields are nullable
 *  INDEPENDENTLY, so one of them missing prints the absence, never a NaN. */
export function dewFace(weather: WeatherState | null, canViewWeather: boolean): TileFace {
  if (!canViewWeather) {
    return { value: "--", sub: `needs ${accessPhrase("view.weather")}`, tone: "dim" };
  }
  const now = weather?.now ?? null;
  if (!weather?.enabled) {
    return { value: "--", sub: "the weather feed is off", tone: "dim" };
  }
  if (!now || now.temp_c == null || now.dewpoint_c == null) {
    return { value: "not reported", sub: "this feed carries no dew point", tone: "dim" };
  }
  const margin = now.temp_c - now.dewpoint_c;
  return {
    value: `${margin.toFixed(1)}°C`,
    sub: `ambient ${now.temp_c.toFixed(1)} · dew ${now.dewpoint_c.toFixed(1)}`,
    tone: margin <= 0 ? "bad" : margin < 2 ? "warn" : "good",
  };
}

/** DISK.
 *
 *  The design's sub-line is "~9 nights free". Nothing on this wire measures
 *  bytes per night, and D.2 forbids a figure that was not measured, so the
 *  sub-line states the engine's own rule instead: it stops cleanly at 2 GB. */
export function diskFace(disk: DiskInfo | undefined): TileFace {
  if (!disk || typeof disk.free_gb !== "number") {
    return { value: "--", sub: "this rig does not report free space", tone: "dim" };
  }
  const value = `${disk.free_gb.toFixed(disk.free_gb < 10 ? 1 : 0)} GB`;
  if (disk.critical) return { value, sub: "critical - the run stops cleanly at 2 GB", tone: "bad" };
  if (disk.low) return { value, sub: "getting low - free space before the next target", tone: "warn" };
  return { value, sub: "free on the capture volume", tone: "accent" };
}

/** TO DAWN. `dark_window.end_iso` is when astronomical dark ends, which is the
 *  end of usable sky, not sunrise. */
export function dawnFace(
  dark: DarkWindow | null,
  canSiteDerived: boolean,
  nowMs: number,
): TileFace {
  if (!canSiteDerived) {
    return { value: "--", sub: `needs ${accessPhrase("view.site_derived")}`, tone: "dim" };
  }
  if (!dark) {
    return { value: "no dark tonight", sub: "the sun never drops 18° below the horizon", tone: "dim" };
  }
  const endMs = Date.parse(dark.end_iso);
  if (!Number.isFinite(endMs)) {
    return { value: "--", sub: "the dark window did not parse", tone: "dim" };
  }
  const leftS = (endMs - nowMs) / 1000;
  if (leftS <= 0) {
    return { value: "dark is over", sub: `it ended at ${fmtClock(endMs, nowMs)}`, tone: "dim" };
  }
  return {
    value: fmtDuration(leftS),
    sub: `dark until ${fmtClock(endMs, nowMs)}`,
    tone: leftS < 1800 ? "warn" : "accent",
  };
}

/** NEXT TARGET - the one AFTER the one being shot, because the current target
 *  is already named at the top of the screen. */
export function nextTargetFace(seq: SequenceState, plan: SequencePlan): TileFace {
  const targets = plan?.targets ?? [];
  if (targets.length === 0) {
    return { value: "--", sub: "no plan loaded", tone: "dim" };
  }
  const n = targets.length;
  const idx = seq.target_index;
  if (idx == null) {
    const startsAt = seq.schedule?.start_ts;
    const starts = seq.schedule?.state === "waiting" && startsAt
      ? ` · starts ${fmtClock(startsAt * 1000)}` : "";
    return { value: targets[0].name, sub: `1 of ${n}${starts}`, tone: "accent" };
  }
  const next = targets[idx + 1];
  if (!next) {
    return { value: "none queued", sub: `${Math.min(idx + 1, n)} of ${n} · last target`, tone: "dim" };
  }
  return { value: next.name, sub: `${idx + 2} of ${n}`, tone: "accent" };
}

/** Tonight's dark window. One read per mount (plan §2.3): it changes once a
 *  day, and the cap gate is at the CALLER so a viewer never issues a request
 *  whose answer the server would strip anyway. */
export function useDarkWindow(enabled: boolean): DarkWindow | null {
  const [dark, setDark] = useState<DarkWindow | null>(null);
  useEffect(() => {
    if (!enabled) { setDark(null); return; }
    let live = true;
    void api.get<{ dark_window: DarkWindow | null }>("/api/site/sky")
      .then((r) => { if (live) setDark(r.dark_window ?? null); })
      .catch(() => { /* no site yet, or offline - the tile says so on its own */ });
    return () => { live = false; };
  }, [enabled]);
  return dark;
}

function Tile({ label, face, testId }: { label: string; face: TileFace; testId: string }): JSX.Element {
  return (
    <ReadoutTile
      label={label}
      value={face.value}
      sub={face.sub}
      tone={face.tone as Tone}
      data-testid={testId}
    />
  );
}

export function VitalsBand({ runActive, nowMs }: { runActive: boolean; nowMs: number }): JSX.Element {
  const meridian = useMeridian();
  const camera = useCamera();
  const status = useStatus();
  const weather = useWeather();
  const seq = useSeq();
  const plan = usePlan();
  const canWeather = useCanViewWeather();
  const canSiteDerived = useCan("view.site_derived");
  const dark = useDarkWindow(canSiteDerived);

  return (
    <ReadoutGrid cols={3} data-testid="monitor-vitals">
      <FlipTile meridian={meridian} runActive={runActive} />
      <Tile label="SENSOR" face={sensorFace(camera, seq.detail)} testId="vital-sensor" />
      <Tile label="DEW MARGIN" face={dewFace(weather, canWeather)} testId="vital-dew" />
      <Tile label="DISK" face={diskFace(status?.disk)} testId="vital-disk" />
      <Tile label="TO DAWN" face={dawnFace(dark, canSiteDerived, nowMs)} testId="vital-dawn" />
      <Tile label="NEXT TARGET" face={nextTargetFace(seq, plan)} testId="vital-next" />
    </ReadoutGrid>
  );
}
