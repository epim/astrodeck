// GuideFramePreview.tsx — a self-contained, collapsible "Guide cam" preview used
// on BOTH the Capture and Polar screens. Lets the user glance at the guide-camera
// view to confirm the guide star / field isn't obstructed (clouds, dew, a stray
// cable). Owned by the SHARED lane.
//
// SELF-MANAGING + PRESENTATIONAL: it reads NOTHING from the store except
// useStatus() (to know whether a guider/guide-camera is even connected). All
// fetch/poll/state lives in its own useState/useEffect. When the toggle is OFF or
// the component unmounts, polling is paused (the interval is torn down and the
// in-flight <img> load is dropped) so we never hammer the backend in the
// background.
//
// ENDPOINT: GET /api/guide/frame.png — cache-busted with ?t=<ms> each poll.
// The backend lane this run creates that route. A 404 / load error => the
// "guide camera unavailable" state with a one-line note (e.g. NINA mode where the
// guider is PHD2 with no frame, or no guide camera attached).
import { useEffect, useRef, useState } from "react";
import { useStatus } from "../store";
import { u } from "../lib/base";
import { Icon } from "./icons";
import { Toggle } from "./ui";

const FRAME_URL = u("/api/guide/frame.png");
const POLL_MS = 2500;

type FrameState = "loading" | "image" | "unavailable";

export default function GuideFramePreview({ className = "", compact = false, reticle = false }: {
  className?: string;
  compact?: boolean;
  // reticle: overlay a center guide-region crosshair (the lock-region marker on
  // the GuideView). The GuideStats bus carries no per-frame star coordinates, so
  // this marks the frame's lock REGION rather than tracking the star pixel.
  reticle?: boolean;
}) {
  const status = useStatus();
  const [open, setOpen] = useState(false);
  const [src, setSrc] = useState<string | null>(null);
  const [state, setState] = useState<FrameState>("loading");

  // A guider is "present" when PHD2 (status.guider — a named guider object, no
  // `connected` flag in NINA mode) is wired OR a dedicated guide camera
  // (status.guide_camera) reports connected. We still allow the toggle when
  // unknown — the fetch itself is the source of truth (a 404 lands in the
  // unavailable note), so the header never lies by hiding a frame the backend
  // can actually serve.
  const guiderConnected =
    !!status?.guider?.name || !!status?.guide_camera?.connected;

  // Poll: while open, swap a cache-busted URL into <img> every POLL_MS. We do NOT
  // mutate state on each tick — the <img> onLoad/onError drive image/unavailable,
  // so a transient frame gap doesn't flicker the box. Tearing down on !open or
  // unmount pauses polling (the contract: no background fetching).
  useEffect(() => {
    if (!open) {
      setSrc(null);
      setState("loading");
      return;
    }
    setState("loading");
    setSrc(`${FRAME_URL}?t=${Date.now()}`);
    const id = window.setInterval(() => {
      setSrc(`${FRAME_URL}?t=${Date.now()}`);
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [open]);

  // Once unavailable, a later successful onLoad flips us back to "image" — so a
  // guider coming online mid-session recovers without a manual toggle.
  const onImgLoad = () => setState("image");
  const onImgError = () => setState((s) => (s === "image" ? "image" : "unavailable"));

  // Keep the last good <img> mounted across the cache-bust swap so the box doesn't
  // blank between frames — we only show the placeholder until the FIRST load.
  const everLoaded = useRef(false);
  if (state === "image") everLoaded.current = true;

  const boxH = compact ? "h-28" : "h-40";

  return (
    <section className={`panel p-3 ${className}`}>
      <header className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-2">
          <span className="text-dim" aria-hidden>
            <Icon name="guide" size={16} />
          </span>
          <h2 className="panel-title !mb-0">Guide cam</h2>
        </span>
        <Toggle
          checked={open}
          onChange={setOpen}
          label="Show guide camera preview"
          showState
        />
      </header>

      {open && (
        <div className="mt-3">
          {!guiderConnected && state !== "image" ? (
            <Unavailable note="No guider connected" />
          ) : (
            <div
              className={`relative w-full ${boxH} astro-surface overflow-hidden border border-line flex items-center justify-center`}
            >
              {/* the frame itself (hidden until first successful load) */}
              {src && (
                // eslint-disable-next-line jsx-a11y/img-redundant-alt
                <img
                  src={src}
                  alt="Guide camera frame"
                  onLoad={onImgLoad}
                  onError={onImgError}
                  className={`max-w-full max-h-full object-contain ${state === "image" ? "" : "hidden"}`}
                  draggable={false}
                />
              )}

              {/* lock-region reticle: a subtle center crosshair marking the
                  guide region (no per-frame star coords on the stats bus). */}
              {reticle && state === "image" && (
                <span
                  className="pointer-events-none absolute inset-0 flex items-center justify-center"
                  aria-hidden
                >
                  <span className="absolute w-full h-px bg-accent/40" />
                  <span className="absolute h-full w-px bg-accent/40" />
                  <span className="w-6 h-6 rounded-full border border-accent/70" />
                </span>
              )}

              {/* loading placeholder — only before the first frame ever lands */}
              {state === "loading" && !everLoaded.current && (
                <span className="text-dim text-[11px] tracking-[0.25em] uppercase">
                  Loading…
                </span>
              )}

              {/* unavailable note (404 / no frame) — only when we never had one */}
              {state === "unavailable" && !everLoaded.current && (
                <UnavailableInner note={ninaNote(status?.guider?.name)} />
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// Reason note: in NINA mode the guider is PHD2 (no raw frame), so we name it.
function ninaNote(guiderName?: string): string {
  return guiderName
    ? `No frame from ${guiderName} (guider exposes no image)`
    : "Guide camera frame unavailable";
}

function Unavailable({ note }: { note: string }) {
  return (
    <div className="astro-surface border border-line py-6 flex items-center justify-center">
      <UnavailableInner note={note} />
    </div>
  );
}

function UnavailableInner({ note }: { note: string }) {
  return (
    <span className="inline-flex flex-col items-center gap-1 text-center px-3">
      <span className="text-faint" aria-hidden>
        <Icon name="guide" size={28} strokeWidth={1} />
      </span>
      <span className="text-dim text-[11px] leading-snug max-w-[28ch]">{note}</span>
    </span>
  );
}
