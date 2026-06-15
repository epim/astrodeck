// FrameStats.tsx — min/median/mean/max/σ + HFR/stars readout, extracted from the
// old inline CaptureView block so Focus can reuse it (spec §3 stream O, §10).
//
// Honest clip tone: max gets a `warn` tone only when full_well is known AND the
// frame hits it (not a hardcoded 65535 — decisions #3). HFR tone uses the store's
// session thresholds.
import type { PreviewInfo } from "../../types";
import { Stat } from "../ui";

export function FrameStats({
  preview,
  hfrGood,
  hfrWarn,
  compact = false,
}: {
  preview: PreviewInfo;
  hfrGood: number;
  hfrWarn: number;
  compact?: boolean;
}) {
  const fw = preview.full_well;
  const clipped = fw != null && preview.data_is_linear && preview.stats.max >= fw;
  const hfr = preview.hfr;
  const hfrTone = hfr == null ? undefined : hfr <= hfrGood ? "good" : hfr <= hfrWarn ? "warn" : "bad";

  return (
    <div className="flex flex-col gap-2">
      <div className={`grid ${compact ? "grid-cols-3" : "grid-cols-5"} gap-3`}>
        <Stat label="min" value={preview.stats.min} />
        <Stat label="median" value={preview.stats.median} />
        <Stat label="mean" value={preview.stats.mean} />
        <Stat
          label="max"
          value={preview.stats.max}
          tone={clipped ? "warn" : undefined}
          hint={clipped ? `Saturated — pixels reach full well (${fw} ADU)` : undefined}
        />
        <Stat label="σ" value={preview.stats.std} />
      </div>
      {(hfr != null || preview.stars != null) && (
        <div className="grid grid-cols-3 gap-3 border-t border-line pt-2">
          {hfr != null && (
            <Stat
              label="HFR"
              value={hfr.toFixed(2)}
              unit={preview.pixel_scale_arcsec ? "px" : "px"}
              tone={hfrTone}
              hint="Half-flux radius — lower is sharper"
            />
          )}
          {hfr != null && preview.pixel_scale_arcsec != null && (
            <Stat label="HFR″" value={(hfr * preview.pixel_scale_arcsec).toFixed(2)} unit="″" tone={hfrTone} />
          )}
          {preview.stars != null && <Stat label="stars" value={preview.stars} />}
        </div>
      )}
    </div>
  );
}
