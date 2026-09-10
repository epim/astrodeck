// SessionStackPanel.tsx - the run's colour composite (wave R7, area G:
// `components/preview/SessionStack.tsx`, chrome REBUILT, image KEPT).
//
// A preview tile shows ONE frame: five minutes of a nine-hour night, through
// whichever filter happened to be in the beam, at whatever the seeing was doing
// right then. It answers "is the rig still working" and it cannot answer "is
// this going to be a picture", which is the question the person who set the
// alarm for 3am has.
//
// So: opt-in, off by default, and when it is on every sub the sequence ACCEPTS
// is stacked into its filter's running mean on the server and the filters are
// composited into colour (`imaging/sessionstack.py`). This component is the
// switch, the picture, and an honest readout of what went into it.
//
// WHAT IS SHARED AND WHAT IS REBUILT. `modeLabel`, `POLL_MS` and
// `BACKFILL_POLL_MS` are imported from the legacy module, not re-derived: two
// front-ends polling one rig at 10 s and 3 s would double the status load and
// disagree about how fresh "now" is, and the words that explain why an image is
// teal must be the same words in both roots. The `<img>` is the same composite
// at the same URL. Everything around them is the design's own vocabulary.
//
// THE BACKFILL. Switching this on used to mean "from the next frame", so arming
// it at 2am showed two of the night's ninety subs and the composite was noise.
// The box next to the switch folds in the subs the run has already accepted,
// and it is TICKED by default because that is what the switch is for - but the
// count of what it will read is on the label, because the pass re-reads and
// registers every one of those frames off disk and on a full night that is
// minutes of work. An informed press, not a surprise.

import { useCallback, useEffect, useRef, useState, type JSX } from "react";

import {
  backfillLabel, backfillSessionStack, fmtIntegration, getSessionStack,
  resetSessionStack, sessionStackImageUrl, startSessionStack, stopSessionStack,
  type SessionStackStatus,
} from "../../../../../api/sessionStack";
import {
  BACKFILL_POLL_MS, POLL_MS, modeLabel,
} from "../../../../../components/preview/SessionStack";
import { useCanControlCapture } from "../../../../../lib/caps";
import { explainLock } from "../../../../shell/explain";
import {
  ActionButton, Card, Checkbox22, EmptyCard, Label, LockNote, Mono,
  ReadoutGrid, ReadoutTile, Switch,
} from "../../../../ui";
import { STACK_LOCK, STACK_LOCK_NOTE, hy } from "./frameCopy";

export function SessionStackPanel(): JSX.Element {
  const canControl = useCanControlCapture();
  const [status, setStatus] = useState<SessionStackStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Ticked by default: "stack this run" is what the switch means, and the label
  // carries the number of subs so the cost is on screen before the press.
  const [withEarlier, setWithEarlier] = useState(true);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const s = await getSessionStack();
      if (alive.current) { setStatus(s); setError(null); }
    } catch (e) {
      // A failed poll is not worth a red panel: the run is unaffected and the
      // next tick is ten seconds away. Only say so when there is nothing at all
      // to show, so the user is never staring at a stale picture that looks
      // live.
      if (alive.current) setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void refresh();
    return () => { alive.current = false; };
  }, [refresh]);

  const enabled = status?.enabled ?? false;
  const backfilling = status?.backfill?.running ?? false;
  useEffect(() => {
    if (!enabled) return;
    const t = setInterval(() => { void refresh(); },
      backfilling ? BACKFILL_POLL_MS : POLL_MS);
    return () => clearInterval(t);
  }, [enabled, backfilling, refresh]);

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
    void act(on ? () => startSessionStack(withEarlier) : stopSessionStack);
  }, [act, withEarlier]);

  const frames = status?.frames ?? 0;
  const hasImage = Boolean(status?.has_image && frames > 0);
  const bf = status?.backfill;
  const available = bf?.available ?? 0;
  const bfLabel = backfillLabel(bf);
  const controlReason = !canControl ? STACK_LOCK
    : busy ? "The stack is still answering the last request." : null;

  return (
    <Card data-testid="gallery-stack">
      <div className="nx-frames">
        <div className="nx-frames-actions">
          <Label>SESSION STACK</Label>
          <span className="nx-frames-spacer" />
          <Mono size={10.5} tone="dim">{enabled ? "ON" : "OFF"}</Mono>
        </div>

        <Switch
          data-testid="gallery-stack-switch"
          checked={enabled}
          onChange={toggle}
          label="Stack accepted subs into a colour image"
          note="Every sub the sequence accepts is added to its filter's running mean on the rig, and the filters are composited."
          lockedReason={controlReason}
          onExplain={explainLock}
        />
        <LockNote reason={canControl ? null : STACK_LOCK_NOTE} />

        {!enabled && (
          <>
            <EmptyCard
              title="OFF"
              hint="The run's accepted subs are not being stacked."
            />
            {/* The count is IN the label, not in a tooltip: this is the one
                moment the user can decide whether minutes of disk reading are
                worth it, and "some subs" is not a basis for that decision. */}
            <Checkbox22
              data-testid="gallery-stack-backfill"
              checked={withEarlier}
              onChange={setWithEarlier}
              lockedReason={controlReason}
              onExplain={explainLock}
              label={available > 0
                ? `Also stack the ${available} sub${available === 1 ? "" : "s"} this run has already `
                  + "accepted. They are re-read and registered off disk, which takes a few "
                  + "minutes on a long run."
                : "Also stack the subs this run has already accepted, when there are any."}
            />
          </>
        )}

        {enabled && !hasImage && (
          <EmptyCard
            title={error ? "CANNOT REACH THE STACK" : backfilling ? "READING EARLIER SUBS" : "WAITING"}
            hint={error
              ? hy(error)
              : backfilling
                ? `Reading the run's earlier subs: ${bf?.done ?? 0} of ${bf?.total ?? 0}.`
                : "On. Waiting for the first accepted sub."}
          />
        )}

        {enabled && hasImage && status && (
          <>
            <img
              className="nx-frames-stackimg"
              data-testid="gallery-stack-img"
              // `seq` in the URL is the only thing that makes the browser
              // refetch: the route is no-store, but an unchanged src is never
              // requested again at all.
              src={sessionStackImageUrl(status.seq)}
              alt={`Stacked composite of ${status.target || "the current target"}, ${frames} frames, ${fmtIntegration(status.integrated_s)} total`}
            />
            <Mono size={10.5} tone="dim">
              {`${status.target || "untargeted"} - ${hy(modeLabel(status))}`}
            </Mono>
          </>
        )}

        {enabled && (
          <>
            {/* The counter, and the way to start a pass on a stack that is
                already on. Switching off and on again would reach the same pass
                by throwing away everything stacked since, which is why this is
                its own button. */}
            {(bfLabel || available > 0) && (
              <div className="nx-frames-actions">
                {bfLabel && (
                  <Mono size={10.5} tone="dim">
                    <span role={backfilling ? "status" : undefined}
                      aria-live={backfilling ? "polite" : undefined}>
                      {hy(bfLabel)}
                    </span>
                  </Mono>
                )}
                {available > 0 && !backfilling && (
                  <ActionButton
                    kind="ghost"
                    data-testid="gallery-stack-earlier"
                    lockedReason={controlReason}
                    onExplain={explainLock}
                    onPress={() => { void act(backfillSessionStack); }}
                  >
                    {`STACK ${available} EARLIER SUB${available === 1 ? "" : "S"}`}
                  </ActionButton>
                )}
              </div>
            )}

            <ReadoutGrid cols={3}>
              <ReadoutTile label="FRAMES" value={String(frames)} />
              <ReadoutTile label="INTEGRATED" value={fmtIntegration(status?.integrated_s ?? 0)} />
              <ReadoutTile
                label="BINNED"
                value={status?.downsample ? `${status.downsample}x` : "-"}
                sub="preview only"
              />
            </ReadoutGrid>
            <p className="nx-frames-note">
              The composite is accumulated at reduced resolution so a full-frame stack
              per filter cannot exhaust the box&apos;s memory. It is a preview; the
              frames on disk are untouched.
            </p>

            {(status?.channels?.length ?? 0) > 0 && (
              <div data-testid="gallery-stack-channels">
                {status!.channels.map((c) => (
                  <div key={c.channel} className="nx-frames-chan">
                    <Mono size={10.5}>{c.channel}</Mono>
                    <Mono size={10} tone="dim">
                      {`${c.frames} ${c.frames === 1 ? "frame" : "frames"} - ${fmtIntegration(c.integrated_s)}`
                        + (c.rejected > 0 ? ` - ${c.rejected} unaligned` : "")}
                    </Mono>
                  </div>
                ))}
              </div>
            )}

            <div className="nx-frames-actions">
              <ActionButton
                kind="ghost"
                data-testid="gallery-stack-reset"
                lockedReason={controlReason ?? (frames === 0 ? "Nothing is stacked yet." : null)}
                onExplain={explainLock}
                onPress={() => { void act(resetSessionStack); }}
              >
                RESET STACK
              </ActionButton>
              {status?.rejected ? (
                <Mono size={10} tone="warn">
                  {`${status.rejected} sub${status.rejected === 1 ? "" : "s"} could not be registered onto the stack`}
                </Mono>
              ) : null}
            </div>
          </>
        )}

        {error && (enabled ? hasImage : true) && (
          <Mono size={10} tone="warn" data-testid="gallery-stack-error">{hy(error)}</Mono>
        )}
      </div>
    </Card>
  );
}
