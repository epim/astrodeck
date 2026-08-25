// SkyDomePanel.tsx — the dome, plus the words the picture cannot say.
//
// The panel exists to answer two questions the operator actually asks at the
// eyepiece: "is there cloud between me and where I'm pointing", and "is it
// coming this way". The dome answers the first; the motion line and the
// look-ahead answer the second.
//
// IT NEVER GATES ANYTHING, and says so. Nothing in the sequence engine, the
// safety gate or auto-resume consults this model (a named server test pins
// that). The frames decide whether tonight is worth exposing; this says WHERE
// in the sky the cloud is, which no scalar forecast can express.
import { useCallback, useEffect, useRef, useState } from "react";

import { getCloudmap, getCloudmapAt, getCloudmapDome,
         type CloudmapAt, type CloudmapDome, type CloudmapStatus } from "../../api/cloudmap";
import { occlusionWord } from "../../lib/domeProjection";
import { Panel } from "../ui";
import { SkyDome } from "./SkyDome";

/** The satellite publishes every five minutes and the service polls on its own
 *  cadence; re-reading faster than this buys nothing but relay traffic. */
const POLL_MS = 60_000;

/** How far ahead to ask about the current pointing. Half an hour is about as
 *  far as a phase-correlation motion estimate is worth trusting -- the cloud
 *  pattern's own lifetime is under forty minutes. */
const LOOK_AHEAD_S = 1800;

export function SkyDomePanel({ pointing, target }: {
  pointing?: { alt: number; az: number } | null;
  target?: { alt: number; az: number; name?: string } | null;
}) {
  const [status, setStatus] = useState<CloudmapStatus | null>(null);
  const [dome, setDome] = useState<CloudmapDome | null>(null);
  const [now, setNow] = useState<CloudmapAt | null>(null);
  const [soon, setSoon] = useState<CloudmapAt | null>(null);
  const alive = useRef(true);

  // Read the pointing through a ref: it changes on every 2 s status frame and
  // must not restart the poll, but the fetch needs its current value.
  const pointingRef = useRef(pointing);
  useEffect(() => { pointingRef.current = pointing; });

  const load = useCallback(async () => {
    try {
      const st = await getCloudmap();
      if (!alive.current) return;
      setStatus(st);
      if (!st.enabled) { setDome(null); setNow(null); setSoon(null); return; }
      // 6 x 10 degrees is 15 x 36 = 540 rays. The server walks each one through
      // the cloud volume, so this is the resolution/latency trade the panel can
      // actually draw -- finer looks no better at this size.
      const d = await getCloudmapDome(6, 10);
      if (!alive.current) return;
      setDome(d);
      const p = pointingRef.current;
      if (p && p.alt >= 0) {
        const [a, b] = await Promise.all([
          getCloudmapAt(p.alt, p.az, 0),
          getCloudmapAt(p.alt, p.az, LOOK_AHEAD_S),
        ]);
        if (!alive.current) return;
        setNow(a); setSoon(b);
      } else {
        setNow(null); setSoon(null);
      }
    } catch {
      // Silent by design. This is a decorative read on the screen someone runs
      // a night from; a 500 or a dropped relay hop has no business putting an
      // error banner over the run.
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void load();
    const h = setInterval(() => void load(), POLL_MS);
    return () => { alive.current = false; clearInterval(h); };
  }, [load]);

  const off = status != null && !status.enabled;
  const ageS = status?.age_s;
  const ageLabel = typeof ageS === "number"
    ? (ageS < 90 ? `${Math.round(ageS)}s old` : `${Math.round(ageS / 60)}m old`)
    : null;

  return (
    <Panel
      className="col-span-full sm:col-span-2 lg:col-span-6"
      title="Sky dome"
      right={
        <span className="text-[10px] text-dim">
          {off ? "off" : (status?.stale ? `stale · ${ageLabel ?? "?"}` : ageLabel ?? "")}
        </span>
      }
    >
      <SkyDome
        grid={dome && dome.rows?.length ? dome : null}
        pointing={pointing}
        target={target}
        emptyNote={off ? "cloud model is switched off" : "waiting for a granule"}
        height={280}
      />

      <div className="mt-2 flex flex-col gap-1 text-[11px]">
        {off && (
          <p className="text-dim">
            Settings &gt; enable the GOES cloud model. It pulls about 4.4 MB per
            cycle, which is why it is opt-in.
          </p>
        )}

        {!off && now && (
          <p>
            <span className="text-dim">where you are pointing: </span>
            <span className="text-ink">{occlusionWord(now.probability)}</span>
            {typeof now.probability === "number" && (
              <span className="text-dim"> ({(now.probability * 100).toFixed(1)}%)</span>
            )}
            {soon && soon.basis === "forecast" && (
              <>
                <span className="text-dim"> · in 30 min: </span>
                <span className="text-ink">{occlusionWord(soon.probability)}</span>
              </>
            )}
            {soon && soon.basis === "no_data" && (
              // Honest rather than blank: the forecast needs two granules to
              // correlate, so it is genuinely absent for the first cycle after
              // the model is switched on.
              <span className="text-dim"> · no forecast yet (needs two granules)</span>
            )}
          </p>
        )}

        {!off && status?.motion && (
          <p className="text-dim">
            drift {status.motion.speed_kmh.toFixed(1)} km/h toward{" "}
            {Math.round(status.motion.toward_deg)}°
            {!status.motion.corroborated && " · not corroborated by the wind column"}
          </p>
        )}

        {!off && status?.last_error && (
          <p className="text-warn">{status.last_error}</p>
        )}

        <p className="text-dim text-[10px]">
          Modelled from {status?.credit?.source ?? "NOAA GOES"}. Advisory only —
          nothing in the sequencer reads it.
        </p>
      </div>
    </Panel>
  );
}
