// SkyDomePanel.tsx — the dome, plus the words the picture cannot say.
//
// The panel exists to answer two questions the operator actually asks at the
// eyepiece: is there cloud between me and where I am pointing, and is it
// coming this way. The dome answers the first; the look-ahead ladder and the
// motion line answer the second.
//
// IT NEVER GATES ANYTHING, and says so. Nothing in the sequence engine, the
// safety gate or auto-resume consults this model (a named server test pins
// that). The frames decide whether tonight is worth exposing; this says WHERE
// in the sky the cloud is, which no scalar forecast can express.
import { useCallback, useEffect, useRef, useState } from "react";

import { getCloudmap, getCloudmapAt, getCloudmapDome,
         type CloudmapAt, type CloudmapDome, type CloudmapStatus } from "../../api/cloudmap";
import { domeGapFraction, occlusionWord } from "../../lib/domeProjection";
import { Panel } from "../ui";
import { SkyDome } from "./SkyDome";

/** The satellite publishes every five minutes and the service polls on its own
 *  cadence; re-reading faster than this buys nothing but relay traffic. */
const POLL_MS = 60_000;

/** How far ahead to ask about the current pointing. Half an hour is about as
 *  far as a phase-correlation motion estimate is worth trusting -- the cloud
 *  pattern's own lifetime is under forty minutes. The intermediate rung is what
 *  tells the operator whether the trend is arriving or leaving: two points can
 *  only ever draw a straight line. */
const LOOK_AHEAD_S = [0, 900, 1800] as const;

const AHEAD_LABEL: Record<number, string> = { 0: "now", 900: "+15m", 1800: "+30m" };

/** Consecutive failed polls before the panel admits it. One dropped relay hop
 *  is noise; three in a row over three minutes is a dead feed, and leaving the
 *  last good dome on screen without saying so is the same lie as painting a
 *  gap as clear sky. */
const QUIET_FAILURES = 3;

export function SkyDomePanel({ pointing, target }: {
  pointing?: { alt: number; az: number } | null;
  target?: { alt: number; az: number; name?: string } | null;
}) {
  const [status, setStatus] = useState<CloudmapStatus | null>(null);
  const [dome, setDome] = useState<CloudmapDome | null>(null);
  const [ladder, setLadder] = useState<(CloudmapAt | null)[]>([]);
  const [failures, setFailures] = useState(0);
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
      setFailures(0);
      if (!st.enabled) { setDome(null); setLadder([]); return; }
      // 6 x 10 degrees is 15 x 36 = 540 rays. The server walks each one through
      // the cloud volume, so this is the resolution/latency trade the panel can
      // actually draw -- finer looks no better at this size.
      const d = await getCloudmapDome(6, 10);
      if (!alive.current) return;
      setDome(d);
      const p = pointingRef.current;
      if (p && p.alt >= 0) {
        const rungs = await Promise.all(
          LOOK_AHEAD_S.map((s) => getCloudmapAt(p.alt, p.az, s).catch(() => null)));
        if (!alive.current) return;
        setLadder(rungs);
      } else {
        setLadder([]);
      }
    } catch {
      // Counted, not silent. This is a decorative read on the screen someone
      // runs a night from, so one failure has no business putting a banner over
      // the run -- but a feed that has been dead for three minutes does.
      if (alive.current) setFailures((n) => n + 1);
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

  const gridUsable = dome != null && (dome.rows?.length ?? 0) > 0;
  const gapFrac = gridUsable ? domeGapFraction(dome) : 1;
  const now = ladder[0] ?? null;

  // One satellite cell against the beam the telescope actually looks through.
  // The probability is an average over the whole cell, so this ratio IS the
  // caveat: a hole narrower than a cell cannot appear in the number at all.
  const cellM = dome?.cell_km ? Math.max(dome.cell_km[0], dome.cell_km[1]) * 1000 : null;
  const beamM = typeof now?.beam_m === "number" ? now.beam_m : null;
  const beamRatio = cellM && beamM && beamM > 0 ? cellM / beamM : null;

  const dead = failures >= QUIET_FAILURES;

  return (
    <Panel
      className="col-span-full sm:col-span-2 lg:col-span-6"
      title="Sky dome"
      right={
        <span className="text-[10px] text-dim">
          {dead ? "not answering"
            : off ? "off"
            : status?.stale ? `stale · ${ageLabel ?? "?"}`
            : ageLabel ?? ""}
        </span>
      }
    >
      <SkyDome
        grid={gridUsable ? dome : null}
        pointing={pointing}
        target={target}
        emptyNote={off ? "cloud model is switched off" : "waiting for a granule"}
        stale={!off && (status?.stale === true || dead)}
        staleNote={dead ? "feed down" : ageLabel ?? undefined}
        height={280}
      />

      <div className="mt-2 flex flex-col gap-1 text-[11px]">
        {off && (
          <p className="text-dim">
            Settings &gt; enable the GOES cloud model. It pulls about 4.4 MB per
            cycle, which is why it is opt-in.
          </p>
        )}

        {dead && (
          <p className="text-warn">
            {failures} polls in a row failed. Anything on the dome above is the
            last reading that arrived, not the sky now.
          </p>
        )}

        {/* An entirely blank dome is the one case that must never pass without
            words: hatching says "no reading" per cell, but only the words can
            say whether the model has simply not fetched yet or the site is
            somewhere neither satellite can see. */}
        {!off && gridUsable && gapFrac >= 0.999 && (
          <p className="text-warn">
            No reading anywhere on the dome. Either no granule has covered this
            site yet, or the site is outside this satellite view.
          </p>
        )}
        {!off && gridUsable && gapFrac > 0.02 && gapFrac < 0.999 && (
          <p className="text-dim">
            no reading for {(gapFrac * 100).toFixed(0)}% of the sky (hatched)
          </p>
        )}

        {!off && now && (
          <p>
            <span className="text-dim">where you are pointing: </span>
            <span className="text-ink">{occlusionWord(now.probability)}</span>
            {typeof now.probability === "number" && (
              <span className="text-dim"> ({(now.probability * 100).toFixed(1)}%)</span>
            )}
          </p>
        )}

        {!off && ladder.length > 0 && (
          <p className="text-dim tabular-nums">
            {LOOK_AHEAD_S.map((s, i) => {
              const r = ladder[i];
              const label = AHEAD_LABEL[s] ?? `+${Math.round(s / 60)}m`;
              // "no_data" is honest rather than blank: the forecast needs two
              // granules to correlate, so it is genuinely absent for the first
              // cycle after the model is switched on.
              const body = !r ? "?"
                : r.basis === "no_data" ? "no forecast yet"
                : `${occlusionWord(r.probability)}${
                    typeof r.probability === "number"
                      ? ` ${(r.probability * 100).toFixed(1)}%` : ""}`;
              return (
                <span key={s}>
                  {i > 0 && <span className="text-dim"> · </span>}
                  <span className="text-dim">{label} </span>
                  <span className="text-ink">{body}</span>
                </span>
              );
            })}
          </p>
        )}

        {!off && beamRatio != null && cellM != null && beamM != null && (
          // The 6b spec asks for this beside the probabilities, and it is the
          // honest limit of the whole model: the scope looks through a 91 m
          // patch of cloud and the answer above is averaged over 2.9 km of it.
          <p className="text-dim">
            one cell is {beamRatio.toFixed(0)}x the beam ({(cellM / 1000).toFixed(1)} km
            vs {beamM.toFixed(0)} m) &mdash; a gap narrower than that cannot show
            up above
          </p>
        )}

        {!off && status?.motion && (
          <p className="text-dim">
            drift {status.motion.speed_kmh.toFixed(1)} km/h toward{" "}
            {Math.round(status.motion.toward_deg)}&deg;
            {!status.motion.corroborated && " · not corroborated by the wind column"}
          </p>
        )}

        {!off && status?.last_error && (
          <p className="text-warn">{status.last_error}</p>
        )}

        <p className="text-dim text-[10px]">
          Modelled from {status?.credit?.source ?? "NOAA GOES"}. Advisory only &mdash;
          nothing in the sequencer reads it.
        </p>
      </div>
    </Panel>
  );
}
