// SessionStack.tsx: the run's colour composite, under the last sub on Monitor.
//
// The tile above this one shows ONE frame. Five minutes of a nine-hour night,
// through whichever filter happened to be in the beam, at whatever the seeing
// was doing right then. It is the right thing to look at when you are asking
// "is the rig still working", and it cannot answer "is this going to be a
// picture", which is the question the person who set the alarm for 3am has.
//
// So: opt-in, off by default, and when it is on every sub the sequence ACCEPTS
// is stacked into its filter's running mean on the server and the filters are
// composited into colour (server: imaging/sessionstack.py). This component is
// the switch, the picture, and an honest readout of what went into it.
//
// It polls, which the rest of the monitor page does not: there is no WS event
// for the stack, and inventing one for a panel that changes once per exposure
// would be a lot of plumbing for a number that moves every three minutes. The
// poll is 10s while the switch is on and stops entirely while it is off.

import { useCallback, useEffect, useRef, useState } from "react";
import { Panel, Stat, Toggle, EmptyState } from "../ui";
import { accessPhrase, useCanControlCapture } from "../../lib/caps";
import {
  channelSummary,
  fmtIntegration,
  getSessionStack,
  resetSessionStack,
  sessionStackImageUrl,
  startSessionStack,
  stopSessionStack,
  type SessionStackStatus,
} from "../../api/sessionStack";

const POLL_MS = 10_000;

/** How the composite was built, in words. A viewer looking at a teal image
 *  should be able to find out why it is teal. */
export function modeLabel(status: SessionStackStatus): string {
  const chans = channelSummary(status.channels);
  if (status.mode === "rgb") return `colour · ${chans}`;
  if (status.mode === "narrowband") return `narrowband · ${chans}`;
  if (status.mode === "mono") return `mono · ${chans}`;
  return "nothing stacked yet";
}

export default function SessionStack() {
  const canControl = useCanControlCapture();
  const [status, setStatus] = useState<SessionStackStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const s = await getSessionStack();
      if (alive.current) {
        setStatus(s);
        setError(null);
      }
    } catch (e) {
      // A failed poll is not worth a red panel: the run is unaffected and the
      // next tick is ten seconds away. Only say so when there is nothing at
      // all to show, so the user is never staring at a stale picture that
      // looks live.
      if (alive.current) setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void refresh();
    return () => { alive.current = false; };
  }, [refresh]);

  const enabled = status?.enabled ?? false;
  useEffect(() => {
    if (!enabled) return;
    const t = setInterval(() => { void refresh(); }, POLL_MS);
    return () => clearInterval(t);
  }, [enabled, refresh]);

  const act = useCallback(async (fn: () => Promise<SessionStackStatus>) => {
    setBusy(true);
    try {
      const s = await fn();
      if (alive.current) { setStatus(s); setError(null); }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }, []);

  const toggle = useCallback((on: boolean) => {
    void act(on ? startSessionStack : stopSessionStack);
  }, [act]);

  const frames = status?.frames ?? 0;
  const hasImage = Boolean(status?.has_image && frames > 0);

  return (
    <Panel
      className="col-span-full sm:col-span-2 lg:col-span-6"
      title="Session stack"
      right={
        <div className="flex items-center gap-2">
          <Toggle
            checked={enabled}
            onChange={toggle}
            disabled={!canControl || busy}
            label="Stack accepted subs into a colour image"
            showState
          />
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        {!canControl && (
          <p className="text-xs text-dim">
            Switching the stack on needs {accessPhrase("control.capture")}.
          </p>
        )}

        {!enabled && (
          <EmptyState
            icon="gallery"
            size="inline"
            title="Off. The run's accepted subs are not being stacked."
          />
        )}

        {enabled && !hasImage && (
          <EmptyState
            icon="clock"
            size="inline"
            title={
              error
                ? "Can't reach the stack right now"
                : "On. Waiting for the first accepted sub."
            }
          />
        )}

        {enabled && hasImage && status && (
          <>
            <img
              // `seq` in the URL is the only thing that makes the browser
              // refetch: the route is no-store but an unchanged src is never
              // requested again at all.
              src={sessionStackImageUrl(status.seq)}
              alt={`Stacked composite of ${status.target || "the current target"}, ${
                frames} frames, ${fmtIntegration(status.integrated_s)} total`}
              className="w-full h-auto bg-black"
              style={{ imageRendering: "auto" }}
            />
            <p className="text-xs text-dim">
              {status.target || "untargeted"} · {modeLabel(status)}
            </p>
          </>
        )}

        {enabled && (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <Stat label="Frames" value={frames} />
              <Stat label="Integrated" value={fmtIntegration(status?.integrated_s ?? 0)} />
              <Stat
                label="Binned"
                value={status?.downsample ? `${status.downsample}x` : null}
                hint="The composite is accumulated at reduced resolution so a full-frame
                      stack per filter cannot exhaust the box's memory. It is a preview;
                      the frames on disk are untouched."
              />
            </div>

            {(status?.channels?.length ?? 0) > 0 && (
              <ul className="flex flex-col gap-1">
                {status!.channels.map((c) => (
                  <li
                    key={c.channel}
                    className="flex items-baseline justify-between gap-3 text-xs"
                  >
                    <span className="mono text-ink">{c.channel}</span>
                    <span className="text-dim mono">
                      {c.frames} {c.frames === 1 ? "frame" : "frames"} ·{" "}
                      {fmtIntegration(c.integrated_s)}
                      {c.rejected > 0 && ` · ${c.rejected} unaligned`}
                    </span>
                  </li>
                ))}
              </ul>
            )}

            <div className="flex items-center gap-2">
              <button
                type="button"
                className="btn !py-1 min-h-[44px]"
                disabled={!canControl || busy || frames === 0}
                onClick={() => { void act(resetSessionStack); }}
              >
                Reset stack
              </button>
              {status?.rejected ? (
                <span className="text-xs text-dim">
                  {status.rejected} sub{status.rejected === 1 ? "" : "s"} could not be
                  registered onto the stack
                </span>
              ) : null}
            </div>
          </>
        )}
      </div>
    </Panel>
  );
}
