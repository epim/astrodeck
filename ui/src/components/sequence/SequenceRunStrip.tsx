/* SequenceRunStrip — the "check the phone at 2am" glance (2026-08-07).

   While a sequence runs, the facts someone half-asleep actually wants are:
   is it running, which target, which frame of how many, how far through the
   CURRENT exposure, and when the night's work ends. All of them already ride
   the sequence bus event (SequenceProgress even carries the in-flight
   exposure's start epoch); this strip pins them sticky at the top of the
   Sequence view so no scroll position hides them.

   The exposure ring mounts mid-frame on purpose: `frame_started_at_ms` +
   `server_now_ms` give the elapsed seconds (offset-corrected against the
   client clock), and ActivityRing's negative animation-delay starts the fill
   exactly that far in — the CSS clock and the server's agree without a
   single client-side timer. */
import type { JSX } from "react";
import { useStore } from "../../store";
import ActivityRing from "../ui/ActivityRing";

function fmtEta(s: number): string {
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

export default function SequenceRunStrip(): JSX.Element | null {
  const seq = useStore((s) => s.sequence);
  const live = seq.state === "running" || seq.state === "paused"
    || seq.state === "aborting";
  if (!live) return null;

  const p = seq.progress;
  const exposure = p?.current_exposure_s;
  // Offset-correct the elapsed time against the client clock: the strip may
  // mount minutes into a frame after a navigation.
  let elapsed = 0;
  if (p?.frame_started_at_ms != null) {
    const skew = p.server_now_ms != null ? Date.now() - p.server_now_ms : 0;
    elapsed = Math.max(0, (Date.now() - skew - p.frame_started_at_ms) / 1000);
  }
  const exposing = seq.state === "running" && exposure != null && exposure > 0
    && elapsed < exposure + 5;

  return (
    <div className="sticky top-0 z-20 -mx-1 px-1" data-sequence-strip>
      <div className="border border-line rounded bg-panel/95 backdrop-blur px-3 py-2
                      flex items-center gap-3 min-h-10">
        <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${
          seq.state === "running" ? "bg-accent blink"
            : seq.state === "aborting" ? "bg-warn blink" : "bg-warn"}`} />
        <span className="min-w-0">
          <span className="text-[11px] tracking-widest uppercase text-dim block truncate"
            aria-live="polite">
            {seq.state === "running" ? (seq.target || seq.plan_name || "running")
              : seq.state === "aborting" ? "stopping…" : "paused"}
          </span>
          {p && (
            <span className="text-[10px] text-faint mono tabular-nums block">
              frame {Math.min(p.frames_done + 1, p.frames_total)}/{p.frames_total}
              {p.rejected > 0 ? ` · ${p.rejected} rejected` : ""}
              {p.eta_s != null
                ? ` · ${p.eta_confident === false ? "~" : ""}${fmtEta(p.eta_s)} left`
                : ""}
            </span>
          )}
        </span>
        <span className="ml-auto flex items-center gap-3">
          <span className="font-display font-semibold text-xl mono tabular-nums text-accent">
            {p ? `${Math.round(p.percent)}%` : "—"}
          </span>
          {exposing && (
            <ActivityRing
              mode="fill"
              seconds={exposure}
              elapsedS={elapsed}
              word={`${Math.round(exposure)}s`}
              resetKey={p?.frame_started_at_ms ?? p?.frames_done}
            />
          )}
        </span>
      </div>
    </div>
  );
}
