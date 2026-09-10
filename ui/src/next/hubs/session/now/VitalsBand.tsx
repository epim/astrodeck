// VitalsBand.tsx - the four numbers the README puts under the picture, plus the
// three GAP-ANALYSIS asked for.
//
// The README says ETA / GUIDE / SENSOR / FLIP. The prototype ships a different
// four. GAP-ANALYSIS section 11 asks for "to dawn" and "next target", and
// section 13 for the disk. Rather than choose, the band is FOUR CELLS THAT
// SCROLL TO SEVEN: the README's four are first and always visible, and the rest
// are one thumb-drag away instead of deleted.
//
// EVERY CELL IS ALLOWED TO SAY IT DOES NOT KNOW, and none of them guesses:
//
//   * TO DAWN is absent entirely without a tonight payload. A dawn time made up
//     from a default site is the failure the whole panel exists to avoid.
//   * FLIP reads `MeridianStatus`, not the number: a fork mount has no flip and
//     "-" with "no flip needed" is the true answer, while `flip_disabled` on a
//     GEM is a PIER RISK and says so.
//   * GUIDE downgrades to "stale" whatever the number says. A frozen guider
//     reading 0.4" must not keep asserting a confident "good".
//
// THE STALL LINE IS THE AMBER HALF of the no-progress ladder. `stallLevel`'s
// "red" becomes the ninth incident and owns the card; "amber" is this line, and
// it reads the RIG's frame counter (`lastCaptureAtMs`, advanced from the
// server's own `frames_done`), never preview arrival - which is the #206 fix.

import { useEffect, useState, type JSX } from "react";

import { fmtClock, fmtCountdown, fmtDuration, GUIDE_STALE_S, stallLevel } from "../../../../lib/eta";
import { warmReadout } from "../../../../lib/cooling";
import {
  useCamera, useGuideRms, useLiveness, useMeridian, useSeq, useStore,
} from "../../../../store";
import { useCan } from "../../../../lib/caps";
import { FLIP_SITE_REASON } from "../../monitor/live/FlipTile";
import { Mono, ReadoutTile } from "../../../ui";
import { useActiveSession } from "./sessionData";
import { useCampaign } from "./useCampaign";
import { useEta } from "./useEta";

interface Cell {
  id: string;
  label: string;
  value: string;
  sub?: string;
  tone?: "warn" | "bad" | "good";
}

/** RMS word, transcribed from `components/monitor.tsx`'s `RmsVerdict`: under 1"
 *  good, 1-2" soft, over 2" poor - and STALE beats all three. */
export function rmsWord(rms: number | null | undefined, stale: boolean): {
  word: string; tone: "good" | "warn" | "bad" | undefined;
} {
  if (rms == null) return { word: "no guider", tone: undefined };
  if (stale) return { word: "stale", tone: "warn" };
  if (rms < 1) return { word: "good", tone: "good" };
  if (rms <= 2) return { word: "soft", tone: "warn" };
  return { word: "poor", tone: "bad" };
}

/** The FLIP cell, from `MeridianInfo`. Exported for the test: every
 *  `MeridianStatus` has to produce a sentence, and three of the six have no
 *  number at all.
 *
 *  `canSiteDerived` comes FIRST. The countdown is computed from the site and
 *  the server withholds it from a principal without `view.site_derived` - so
 *  what such a caller receives is `status: "unknown"` with a null countdown,
 *  and the old cell rendered that as "- / unknown", which reads as a broken
 *  mount. One sentence, shared with the Monitor tile and the Safety sheet. */
export function flipCell(m: {
  status: string; hours_to_flip: number | null; flip_enabled: boolean; pier_side: string;
} | null, canSiteDerived: boolean): Cell {
  const pier = m && m.pier_side !== "unknown" ? ` · pier ${m.pier_side}` : "";
  if (!canSiteDerived) return { id: "flip", label: "FLIP", value: "-", sub: FLIP_SITE_REASON };
  if (!m || m.status === "unknown") return { id: "flip", label: "FLIP", value: "-", sub: "unknown" };
  if (m.status === "n_a_fork" || m.status === "n_a_over_pole") {
    return { id: "flip", label: "FLIP", value: "-", sub: `no flip needed${pier}` };
  }
  if (m.status === "flip_disabled") {
    return { id: "flip", label: "FLIP", value: "-", sub: `flip disabled - pier risk${pier}`, tone: "warn" };
  }
  if (m.status === "due") {
    return { id: "flip", label: "FLIP", value: "DUE", sub: `${m.flip_enabled ? "auto" : "disabled"}${pier}`, tone: "warn" };
  }
  const h = m.hours_to_flip;
  return {
    id: "flip",
    label: "FLIP",
    value: h != null ? fmtCountdown(h * 3600) : "-",
    sub: `${m.flip_enabled ? "auto" : "disabled"}${pier}`,
  };
}

export function VitalsBand({ cells = 4 }: { cells?: 4 | 7 }): JSX.Element {
  const seq = useSeq();
  const camera = useCamera();
  const guide = useGuideRms();
  const meridian = useMeridian();
  const canSiteDerived = useCan("view.site_derived");
  const liveness = useLiveness();
  const disk = useStore((s) => s.status?.disk ?? null);
  const eta = useEta();
  const { session } = useActiveSession();
  const { night } = useCampaign();

  // A one-second tick, but only while there is something counting down.
  const [now, setNow] = useState(() => Date.now());
  const counting = seq.state === "running" || seq.state === "holding";
  useEffect(() => {
    if (!counting) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [counting]);

  const out: Cell[] = [];

  // ---- ETA ---------------------------------------------------------------
  if (eta.paused) {
    out.push({ id: "eta", label: "ETA", value: "-", sub: "no ETA while paused", tone: "warn" });
  } else if (eta.remainingS == null || eta.finishAtMs == null) {
    out.push({ id: "eta", label: "ETA", value: "-", sub: "estimating" });
  } else {
    const tilde = eta.confident ? "" : "~";
    out.push({
      id: "eta", label: "ETA",
      value: `${tilde}${fmtCountdown(eta.remainingS)}`,
      sub: `finishes ${tilde}${fmtClock(eta.finishAtMs, now)}`,
    });
  }

  // ---- GUIDE -------------------------------------------------------------
  const guideAgeMs = liveness.guide != null ? now - liveness.guide : null;
  const guideStale = guideAgeMs != null && guideAgeMs > GUIDE_STALE_S * 1000;
  const rms = guide?.rms_total ?? null;
  const verdict = rmsWord(rms, guideStale);
  // `is_arcsec: false` means the guider had no focal length and is reporting
  // PIXELS. Printing an arcsecond mark over pixels is the sort of unit lie that
  // sends someone chasing a mount problem that is not there.
  const unit = guide?.is_arcsec === false ? " px" : "″";
  out.push({
    id: "guide", label: "GUIDE",
    value: rms != null ? `${rms.toFixed(2)}${unit}` : "-",
    sub: verdict.word, tone: verdict.tone,
  });

  // ---- SENSOR ------------------------------------------------------------
  const cooler = camera?.cooler ?? null;
  const warm = warmReadout(camera?.warm);
  let sensorSub = "no cooler";
  if (warm?.active) sensorSub = warm.headline;
  else if (cooler?.on && cooler.at_target) {
    sensorSub = cooler.can_report_power && cooler.power != null
      ? `at target · ${cooler.power}% power` : "at target";
  } else if (cooler?.on) {
    sensorSub = cooler.target_c != null ? `cooling to ${cooler.target_c}°` : "cooling";
  } else if (cooler) sensorSub = "cooler off";
  out.push({
    id: "sensor", label: "SENSOR",
    value: typeof camera?.temperature === "number" ? `${camera.temperature.toFixed(1)}°` : "-",
    sub: sensorSub,
  });

  // ---- FLIP --------------------------------------------------------------
  out.push(flipCell(meridian, canSiteDerived));

  // ---- TO DAWN -----------------------------------------------------------
  // Absent, not guessed: this number exists only when the tonight payload has
  // been read for a real site.
  const dawnUnix = night?.dark_end_unix ?? night?.dawn_unix ?? null;
  if (dawnUnix != null) {
    out.push({
      id: "dawn", label: "TO DAWN",
      value: fmtCountdown(dawnUnix - now / 1000),
      sub: `dark ends ${fmtClock(dawnUnix * 1000, now)}`,
    });
  }

  // ---- NEXT TARGET -------------------------------------------------------
  const targets = session?.plan?.targets ?? [];
  const idx = seq.target_index ?? 0;
  const next = targets[idx + 1];
  if (next) {
    const start = next.schedule?.start_mode === "time" && next.schedule.start_time
      ? next.schedule.start_time : null;
    out.push({
      id: "next", label: "NEXT TARGET",
      value: next.name,
      sub: start ? `at ${start}` : "after this one",
    });
  }

  // ---- DISK --------------------------------------------------------------
  if (disk) {
    out.push({
      id: "disk", label: "DISK",
      value: `${disk.free_gb.toFixed(0)} GB`,
      sub: disk.critical ? "critical" : disk.low ? "low" : "ok",
      tone: disk.critical ? "bad" : disk.low ? "warn" : undefined,
    });
  }

  // ---- the amber half of the stall ladder --------------------------------
  const sinceCaptureS = liveness.capture != null ? (now - liveness.capture) / 1000 : null;
  const level = stallLevel(seq.state, sinceCaptureS, seq.progress?.current_exposure_s ?? 0);

  const shown = cells === 4 ? out.slice(0, 4) : out;

  return (
    <div data-testid="now-vitals" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{
        display: "flex", gap: 6, overflowX: "auto", minWidth: 0, paddingBottom: 2,
      }}>
        {shown.map((c) => (
          <div key={c.id} style={{ flex: "1 0 auto", minWidth: 86 }}>
            <ReadoutTile
              label={c.label}
              value={c.value}
              sub={c.sub}
              tone={c.tone}
              data-testid={`vital-${c.id}`}
            />
          </div>
        ))}
      </div>
      {level === "amber" && sinceCaptureS != null && (
        <div data-testid="now-stall-line">
          <Mono size={10} tone="warn">
            CAPTURE STALLED? last frame {fmtDuration(sinceCaptureS)} ago
          </Mono>
        </div>
      )}
    </div>
  );
}
