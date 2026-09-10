// inspectStack.tsx - the `src=stack` half of the Inspect sheet (plan F.9, D16).
//
// The session composite is a JPEG the server renders from every accepted sub of
// the run. It carries NO `PreviewInfo`: no histogram bins, no star list, no
// per-frame stats, no full-well. So this half of the sheet shows what the
// composite genuinely knows - how many subs went in, how long that is, which
// filters folded onto which channel, and how the backfill is getting on - and
// then says, in one line, where the missing half lives. Faking a histogram off
// a JPEG would be the constant-where-it-should-vary defect the preview stack
// was built to avoid.
//
// The picture still pans and zooms, because a 1200 px composite on a 390 px
// phone is unreadable at fit: `usePreviewGestures` is the SAME hook the preview
// stage uses, over a plain <img> rather than the LUT canvas.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type JSX } from "react";
import {
  backfillLabel,
  fmtIntegration,
  getSessionStack,
  sessionStackImageUrl,
  type SessionStackStatus,
} from "../../../../api/sessionStack";
import { modeLabel } from "../../../../components/preview/SessionStack";
import { usePreviewGestures } from "../../../../components/preview/usePreviewGestures";
import type { Viewport } from "../../../../types";
import { ActionButton, Card, EmptyCard, Label, Mono, Pill } from "../../../ui";

// Transcribed from `components/preview/SessionStack.tsx:45-47`, not invented.
// The status route is cheap and `seq` only moves when a frame has landed, so a
// 10 s poll is the picture's own refresh rate; a running backfill needs a
// counter that moves at a believable rate instead.
const POLL_MS = 10_000;
const BACKFILL_POLL_MS = 1_500;

/** The one thing the composite cannot answer, and where the answer is. */
export const NO_PER_FRAME_LINE =
  "The composite has no per-frame statistics - open a single sub for the histogram, stars and stats.";

const FIT: Viewport = { scale: 1, x: 0, y: 0, fit: true };

/** Container size, measured. `ResizeObserver` is guarded because jsdom has none
 *  and a test that mounts this must not die on the measurement. */
function useBoxSize(ref: React.RefObject<HTMLElement | null>): { w: number; h: number } {
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setSize({ w: el.clientWidth, h: el.clientHeight });
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0].contentRect;
      setSize({ w: cr.width, h: cr.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return size;
}

export function StackInspect({ onLastSub }: { onLastSub: () => void }): JSX.Element {
  const [status, setStatus] = useState<SessionStackStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const s = await getSessionStack();
      if (!alive.current) return;
      setStatus(s);
      setError(null);
    } catch (e) {
      if (!alive.current) return;
      // Stated, not swallowed: an empty panel with no reason reads as "the run
      // stacked nothing", which is a different and much worse claim.
      setError((e as Error)?.message || "the stack status could not be read");
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void refresh();
    return () => { alive.current = false; };
  }, [refresh]);

  // Poll only while the stack is on. A stack that is off produces no new
  // picture, so a timer against it is battery spent on an unchanging answer.
  const enabled = !!status?.enabled;
  const backfilling = !!status?.backfill?.running;
  useEffect(() => {
    if (!enabled) return;
    const ms = backfilling ? BACKFILL_POLL_MS : POLL_MS;
    const t = setInterval(() => { void refresh(); }, ms);
    return () => clearInterval(t);
  }, [enabled, backfilling, refresh]);

  // ------------------------------------------------------------- pan / zoom
  const boxRef = useRef<HTMLDivElement>(null);
  const box = useBoxSize(boxRef);
  const [imgDims, setImgDims] = useState({ w: 0, h: 0 });
  const [viewport, setViewportState] = useState<Viewport>(FIT);
  const setViewport = useCallback(
    (v: Partial<Viewport>) => setViewportState((p) => ({ ...p, ...v })),
    [],
  );

  const fitScale = useMemo(() => {
    if (!imgDims.w || !imgDims.h || !box.w || !box.h) return 1;
    return Math.min(box.w / imgDims.w, box.h / imgDims.h);
  }, [imgDims.w, imgDims.h, box.w, box.h]);

  const geom = useMemo(
    () => ({ cw: box.w, ch: box.h, iw: imgDims.w, ih: imgDims.h, fitScale }),
    [box.w, box.h, imgDims.w, imgDims.h, fitScale],
  );
  const { onKeyDown, setFit, zoomedIn } = usePreviewGestures(boxRef, viewport, setViewport, geom);

  // Re-apply Fit when the box or the picture changes underneath it, the same
  // rule PreviewStage keeps: a rotate must re-centre rather than strand the
  // composite off the edge of its own container.
  useEffect(() => {
    if (!viewport.fit) return;
    if (viewport.scale === fitScale && viewport.x === 0 && viewport.y === 0) return;
    setViewport({ scale: fitScale, x: 0, y: 0, fit: true });
  }, [fitScale, viewport.fit, viewport.scale, viewport.x, viewport.y, setViewport]);

  const hasImage = !!status?.has_image;
  const imgUrl = status && hasImage ? sessionStackImageUrl(status.seq, 1200) : null;
  const backfill = backfillLabel(status?.backfill);

  const lastSubButton = (
    <ActionButton kind="secondary" onPress={onLastSub} data-testid="inspect-last-sub">
      LAST SUB &rsaquo;
    </ActionButton>
  );

  return (
    <div
      data-testid="inspect-stack"
      style={{ display: "flex", flexDirection: "column", gap: 10 }}
    >
      {error && (
        <Card tone="default">
          <Mono size={11} tone="warn">{error}</Mono>
        </Card>
      )}

      {imgUrl ? (
        <div
          ref={boxRef}
          tabIndex={0}
          role="group"
          aria-label="Session stack composite. Arrow keys pan; plus and minus zoom; 0 fits."
          onKeyDown={onKeyDown}
          onDoubleClick={() => setFit()}
          data-testid="inspect-stack-stage"
          style={{
            position: "relative",
            width: "100%",
            minHeight: 380,
            overflow: "hidden",
            background: "#05070c",
            border: "1px solid var(--line)",
            borderRadius: 14,
            touchAction: zoomedIn ? "none" : "pan-y",
            outline: "none",
          }}
        >
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.scale})`,
              transformOrigin: "0 0",
            }}
          >
            <img
              src={imgUrl}
              alt={`Session stack of ${status?.target || "the current run"}`}
              onLoad={(e) =>
                setImgDims({
                  w: e.currentTarget.naturalWidth,
                  h: e.currentTarget.naturalHeight,
                })
              }
              style={{ display: "block" }}
            />
          </div>

          <div
            data-no-pan=""
            style={{
              position: "absolute", top: 8, left: 8, display: "flex",
              flexWrap: "wrap", gap: 6, maxWidth: "calc(100% - 16px)",
            }}
          >
            <Pill tone="accent" data-testid="inspect-stack-badge">
              {`LIVE STACK · ${status?.frames ?? 0} subs · ${fmtIntegration(status?.integrated_s ?? 0)}`}
            </Pill>
            {status && <Pill tone="dim">{modeLabel(status)}</Pill>}
            {status && status.downsample > 1 && (
              <Pill tone="dim">{`binned ${status.downsample}x for memory`}</Pill>
            )}
          </div>
        </div>
      ) : (
        <EmptyCard
          title="NOTHING STACKED YET"
          hint={
            status && !status.enabled
              ? "The session stack is switched off. It starts from Session - Now, where the sub count is on the switch."
              : "The first accepted sub of this run makes the composite."
          }
          data-testid="inspect-stack-empty"
        />
      )}

      {backfill && <div><Mono size={10.5} tone="dim">{backfill}</Mono></div>}

      {/* The honest limit, stated where the missing controls would have been. */}
      <Card tone="dashed" data-testid="inspect-stack-note">
        <p className="nx-empty-hint" style={{ margin: 0 }}>{NO_PER_FRAME_LINE}</p>
        <div style={{ marginTop: 10 }}>{lastSubButton}</div>
      </Card>

      {status && status.channels.length > 0 && (
        <div>
          <Label>CHANNELS</Label>
          <div data-testid="inspect-stack-channels">
            {status.channels.map((c) => (
              <div
                key={c.channel}
                style={{
                  display: "flex", alignItems: "baseline", gap: 8,
                  padding: "6px 0", borderBottom: "1px solid var(--line)",
                }}
              >
                <Mono size={11} tone="accent">{c.channel}</Mono>
                <Mono size={10.5} tone="dim">
                  {`${c.frames} sub${c.frames === 1 ? "" : "s"} · ${fmtIntegration(c.integrated_s)}`}
                  {c.rejected > 0 ? ` · ${c.rejected} refused` : ""}
                </Mono>
              </div>
            ))}
          </div>
        </div>
      )}

      {status && status.rejected > 0 && (
        <div>
          <Mono size={10.5} tone="dim">
            {`${status.rejected} sub${status.rejected === 1 ? "" : "s"} the stacker refused - no stars, or drifted off field.`}
          </Mono>
        </div>
      )}
    </div>
  );
}
