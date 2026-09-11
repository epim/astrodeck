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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { getCloudmap, getCloudmapAt, getCloudmapDome,
         type CloudmapAt, type CloudmapDome, type CloudmapStatus } from "../../api/cloudmap";
import { ageWords, domeGapFraction, domeStatus, occlusionWord } from "../../lib/domeProjection";
import { Panel } from "../ui";
import { SkyDome, type DomeGeometry } from "./SkyDome";
import { altAzOf } from "../../lib/altaz";
import { getResumeArm, getSession } from "../../api/sessions";
import { useSequence, useSite } from "../../store";

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

/** How often the age readout re-renders between polls. The server recomputes
 *  age_s from the granule's observation time on every request, so it is right
 *  whenever it arrives -- but it is a NUMBER, not a clock, and the panel held
 *  it unchanged for the whole 60 s between polls and indefinitely once the
 *  feed stopped answering. "9m old" sitting frozen on a dead feed is the same
 *  defect as the stale dome: a measurement that quietly became a memory. */
const AGE_TICK_MS = 15_000;

/** Server rule, restated: STALE_POLLS (3) x poll_minutes. The panel needs its
 *  own copy because it extrapolates the age past the last successful poll and
 *  must be able to cross the line without being told. Ten minutes is the
 *  config default; the status payload does not carry poll_minutes, so this is
 *  the default's horizon and the server's own flag still wins when it is
 *  fresher. */
const ASSUMED_STALE_AFTER_S = 3 * 10 * 60;

/** What an overlay is handed. The geometry is the canvas's own -- including the
 *  yaw, which stays PRIVATE state here (see `yawDeg` below) and reaches a
 *  second renderer only through the geometry the canvas actually painted with.
 *  `grid` is null unless there are rows worth drawing, and `stale` is the same
 *  flag that makes the cloud recede, so an overlay can decline to extrapolate a
 *  granule the panel has already stopped believing. */
export interface DomeOverlayArgs {
  geom: DomeGeometry;
  grid: CloudmapDome | null;
  stale: boolean;
}

export function SkyDomePanel({ pointing, target, overlay, height = 280, onGeometry,
                              chrome = "panel" }: {
  pointing?: { alt: number; az: number } | null;
  target?: { alt: number; az: number; name?: string } | null;
  /** Drawn over the dome canvas, in its own projection. Absent = the panel is
   *  exactly what it was before this prop existed. */
  overlay?: (o: DomeOverlayArgs) => ReactNode;
  height?: number;
  onGeometry?: (g: DomeGeometry) => void;
  /** Who draws the frame around all this.
   *
   *  `panel` is the legacy `Panel` - a bordered section titled "Sky dome" -
   *  and it is the DEFAULT so `#/classic`'s monitor grid renders byte for byte
   *  what it always has. The next UI mounts this inside its own `Card`, which
   *  already carries a SKYDOME label and a border, so `panel` there drew a
   *  second title inside a second box. `bare` drops the wrapper and NOTHING
   *  else: the freshness chip keeps its own line, because "9m old" is the one
   *  thing in that header the caller's title cannot say and losing it would
   *  leave an old granule looking like a current one. */
  chrome?: "panel" | "bare";
}) {
  const [status, setStatus] = useState<CloudmapStatus | null>(null);
  const [dome, setDome] = useState<CloudmapDome | null>(null);
  const [ladder, setLadder] = useState<(CloudmapAt | null)[]>([]);
  const [failures, setFailures] = useState(0);
  /** How far the dome is turned. Kept HERE and not in the canvas so a redraw
   *  on the 2 s status frame cannot reset the operator's view mid-drag. */
  const [yawDeg, setYawDeg] = useState(0);
  /** The loaded plan's targets as RA/Dec, refetched only when the SESSION
   *  changes -- a plan is edited between runs, not during one. */
  const [planTargets, setPlanTargets] = useState<
    { name?: string; ra_hours: number; dec_deg: number }[]>([]);
  /** The server's age_s and the wall-clock moment it arrived, so the readout
   *  can be extrapolated rather than frozen. */
  const [ageAt, setAgeAt] = useState<{ ageS: number; at: number } | null>(null);
  // Read as well as written: the target alt/az recompute rides this same
  // one-second tick, so a target moves with the clock like the age does.
  const [ageTick, setAgeTick] = useState(0);
  const alive = useRef(true);

  // Read the pointing through a ref: it changes on every 2 s status frame and
  // must not restart the poll, but the fetch needs its current value.
  const pointingRef = useRef(pointing);
  useEffect(() => { pointingRef.current = pointing; });

  // WHOSE targets. The running session if there is one, else the session
  // auto-resume has armed for tonight -- those are the only two plans that are
  // actually going to happen. A plan merely open in the editor is not on the
  // sky and drawing it would claim otherwise.
  const seq = useSequence();
  const site = useSite();
  const sessionId = seq?.session?.id ?? null;

  useEffect(() => {
    let alive2 = true;
    void (async () => {
      try {
        let id = sessionId;
        if (!id) {
          const arm = await getResumeArm();
          id = arm?.armed?.id ?? null;
        }
        if (!id) { if (alive2) setPlanTargets([]); return; }
        const sess = await getSession(id);
        if (!alive2) return;
        setPlanTargets(
          (sess?.plan?.targets ?? [])
            .filter((t) => typeof t.ra_hours === "number"
                        && typeof t.dec_deg === "number")
            .map((t) => ({ name: t.name, ra_hours: t.ra_hours as number,
                           dec_deg: t.dec_deg as number })));
      } catch {
        // Decorative. A plan we cannot read leaves the dome exactly as it was
        // before this feature existed, which is a fine place to fall back to.
        if (alive2) setPlanTargets([]);
      }
    })();
    return () => { alive2 = false; };
  }, [sessionId]);

  // Alt/az is a function of the CLOCK, so this recomputes on the panel's own
  // tick rather than being stored -- a target frozen where it was an hour ago
  // is the exact lie the stale-cloud rule exists to prevent.
  const domeTargets = useMemo(() => {
    const lat = site?.latitude;
    const lon = site?.longitude;
    if (typeof lat !== "number" || typeof lon !== "number") return null;
    const now = Date.now() / 1000;
    return planTargets.map((t) => {
      const { altDeg, azDeg } = altAzOf(t.ra_hours, t.dec_deg, lat, lon, now);
      return { alt: altDeg, az: azDeg, name: t.name };
    });
  }, [planTargets, site?.latitude, site?.longitude, ageTick]);

  const load = useCallback(async () => {
    try {
      const st = await getCloudmap();
      if (!alive.current) return;
      setStatus(st);
      setFailures(0);
      setAgeAt(typeof st.age_s === "number" ? { ageS: st.age_s, at: Date.now() } : null);
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
    const a = setInterval(() => setAgeTick((n) => n + 1), AGE_TICK_MS);
    return () => { alive.current = false; clearInterval(h); clearInterval(a); };
  }, [load]);

  const off = status != null && !status.enabled;
  const dead = failures >= QUIET_FAILURES;
  // Extrapolated, not echoed. See AGE_TICK_MS.
  const ageS = ageAt ? ageAt.ageS + (Date.now() - ageAt.at) / 1000 : null;
  // All four states in one tested place -- see domeStatus. Three separate bugs
  // lived in the inline conditionals this replaced, and every one of them was
  // found by looking at a rendered panel rather than by reading the code.
  const st = domeStatus({
    dead, off,
    observedAt: status?.observed_at,
    serverStale: status?.stale,
    ageS,
    staleAfterS: ASSUMED_STALE_AFTER_S,
  });

  const gridUsable = dome != null && (dome.rows?.length ?? 0) > 0;
  // The server writes three DIFFERENT sentences here -- switched off, no site
  // set, no granule read (naming the last failure) -- precisely so a UI can
  // tell them apart. The first version of this panel fetched the field and
  // threw it away, which drew "no observing site has been set" and a build
  // missing h5py as the transient "waiting for a granule".
  const serverReason = !gridUsable ? (dome?.reason ?? null) : null;
  const gapFrac = gridUsable ? domeGapFraction(dome) : 1;
  const now = ladder[0] ?? null;

  // One satellite cell against the beam the telescope actually looks through.
  // The probability is an average over the whole cell, so this ratio IS the
  // caveat: a hole narrower than a cell cannot appear in the number at all.
  const cellM = dome?.cell_km ? Math.max(dome.cell_km[0], dome.cell_km[1]) * 1000 : null;
  const beamM = typeof now?.beam_m === "number" ? now.beam_m : null;
  const beamRatio = cellM && beamM && beamM > 0 ? cellM / beamM : null;


  const body = (
    <>
      <SkyDome
        grid={gridUsable ? dome : null}
        pointing={pointing}
        target={target}
        targets={domeTargets}
        yawDeg={yawDeg}
        onYaw={setYawDeg}
        emptyNote={off ? "cloud model is switched off"
                       : serverReason ?? "waiting for a granule"}
        stale={st.stale}
        staleNote={st.kind === "dead" ? "feed down" : ageWords(ageS) ?? undefined}
        height={height}
        onGeometry={onGeometry}
        overlay={overlay
          ? (g) => overlay({ geom: g, grid: gridUsable ? dome : null, stale: st.stale })
          : undefined}
      />

      {/* THE PAN IS INVISIBLE WITHOUT THIS. A canvas that happens to respond to
          a drag is a feature nobody finds; the hint costs one line and the
          reset is the only way back to a known orientation once you have
          turned it, since "which way am I looking" is answered by the
          cardinals and those have moved too. */}
      <div className="mt-1 flex items-center justify-between text-[10px] text-dim">
        <span>drag the dome to turn it</span>
        {Math.round(((yawDeg % 360) + 360) % 360) !== 0 && (
          <button
            type="button"
            className="tap min-h-[28px] px-2 text-accent hover:underline"
            onClick={() => setYawDeg(0)}
          >
            facing south again
          </button>
        )}
      </div>

      {/* The numbered dots need a key, or they are decoration. Order is plan
          order, which is the order the night runs in. */}
      {domeTargets && domeTargets.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px]">
          {domeTargets.map((t, i) => (
            <span key={`${t.name ?? "t"}-${i}`}
                  className={t.alt >= 0 ? "text-ink" : "text-dim"}>
              <span className="mono text-accent">{i + 1}</span>{" "}
              {t.name ?? "target"}{" "}
              {t.alt >= 0
                ? <span className="text-dim">{Math.round(t.alt)}&deg;</span>
                : <span className="text-dim">not up</span>}
            </span>
          ))}
        </div>
      )}

      <div className="mt-2 flex flex-col gap-1 text-[11px]">
        {off && (
          <p className="text-dim">
            Switch it on under Settings &gt; Connect &gt; Cloud model. It pulls
            about 4.4 MB per cycle, which is why it is opt-in.
          </p>
        )}

        {dead && (
          <p className="text-warn">
            {failures} polls in a row failed. Anything on the dome above is the
            last reading that arrived, not the sky now.
          </p>
        )}

        {/* THE SERVER'S OWN SENTENCE, when it has one. It knows things this
            panel cannot infer -- that no site is set, that the last fetch
            ended in SiteOutsideSector -- and each is a different thing for
            the operator to do. */}
        {!off && serverReason && (
          <p className="text-warn">{serverReason}</p>
        )}

        {/* An entirely blank dome is the one case that must never pass without
            words: hatching says "no reading" per cell, but only the words can
            say whether the model has simply not fetched yet or the site is
            somewhere neither satellite can see.

            NOT gated on a non-empty grid. A site outside the sector comes back
            two different ways depending on how far outside it is -- 540 null
            cells for a near miss, no rows at all for London -- and the second
            was landing on the transient "waiting for a granule". */}
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
            vs {beamM.toFixed(0)} m) - a gap narrower than that cannot show
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
          Modelled from {status?.credit?.source ?? "NOAA GOES"}. Advisory only -
          nothing in the sequencer reads it.
        </p>
      </div>
    </>
  );

  if (chrome === "bare") {
    return (
      <div data-dome-bare="">
        {/* The chip, and only the chip. See the `chrome` prop: the caller owns
            the title and the border, but nobody except this component knows
            whether the granule under the dome is fresh, stale, or a feed that
            stopped answering three minutes ago. */}
        <div className="mb-1 flex justify-end text-[10px] text-dim">{st.chip}</div>
        {body}
      </div>
    );
  }

  return (
    <Panel
      className="col-span-full sm:col-span-2 lg:col-span-6"
      title="Sky dome"
      right={
        <span className="text-[10px] text-dim">
          {st.chip}
        </span>
      }
    >
      {body}
    </Panel>
  );
}
