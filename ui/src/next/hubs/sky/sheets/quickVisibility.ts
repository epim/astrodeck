// quickVisibility.ts - tonight's ephemeris for one position, for the sheets that
// draw a night arc or a brief (hub-sky plan B.4, A.11, D.3).
//
// WHY A SECOND COPY OF THIS FETCH EXISTS. `finder/model.ts` has one, but it is
// bound to the finder's own anchor (whatever is tracked) and lives inside a hook
// that also opens the AR camera, starts the gyro and polls the cloud dome. A
// sheet needs one number - the dark window - for the target IT was opened with,
// and mounting the finder's model to get it would start a second camera stream
// behind a sheet the user cannot see it through.
//
// THE FETCH KEYS ARE ROUNDED (RA to 0.001 h, Dec to 0.01 deg - 54" and 36"
// respectively, far below anything a chart can show) so a re-render with a
// recomputed coordinate does not re-trigger the server's astropy ephemeris. That
// rounding is `VisibilityPanel.tsx:95-96`'s, for the same reason.

import { useEffect, useState } from "react";
import { api } from "../../../../api";
import type { VisibilityNight } from "../../../../types";

const DEBOUNCE_MS = 300;

export function useVisibilityNight(
  raHours: number | null,
  decDeg: number | null,
  altLimitDeg: number,
): VisibilityNight | null {
  const [night, setNight] = useState<VisibilityNight | null>(null);
  const keyRa = raHours == null || !Number.isFinite(raHours)
    ? null : Math.round(raHours * 1000) / 1000;
  const keyDec = decDeg == null || !Number.isFinite(decDeg)
    ? null : Math.round(decDeg * 100) / 100;
  const limit = Number.isFinite(altLimitDeg) ? altLimitDeg : 0;

  useEffect(() => {
    if (keyRa == null || keyDec == null) { setNight(null); return; }
    let alive = true;
    const timer = setTimeout(() => {
      void api
        .get<VisibilityNight>(
          `/api/visibility?ra=${encodeURIComponent(keyRa)}&dec=${encodeURIComponent(keyDec)}`
            + `&alt_limit=${encodeURIComponent(limit)}`,
        )
        .then((n) => { if (alive) setNight(n); })
        // Nothing is invented on a failure: the chart says it has no curve
        // rather than drawing a plausible one.
        .catch(() => { if (alive) setNight(null); });
    }, DEBOUNCE_MS);
    return () => { alive = false; clearTimeout(timer); };
  }, [keyRa, keyDec, limit]);

  return night;
}
