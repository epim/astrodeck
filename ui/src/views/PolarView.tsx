import { api } from "../api";
import { useStore, usePolar, useProviders } from "../store";
import { PolarReticle, knobHint, polarTier, polarInstruction, type KnobDir } from "../components/polar";
import GuideFramePreview from "../components/GuideFramePreview";
import { Icon } from "../components/icons";
import { Panel, Led } from "../components/ui";
import ProviderBadge from "../components/ProviderBadge";
import { useCanControlMount } from "../lib/caps";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import type { PolarState } from "../types";
import { useState } from "react";

/* Fields the native TPPA engine (server/astrodeck/polar/native.py) adds to the
   canonical `polar` payload beyond PolarState. The store forwards the whole
   event object, so they're present at runtime; typed here (this view owns the
   wizard) since the shared PolarState is intentionally backend-agnostic. */
type NativePolar = PolarState & {
  phase?: "measuring" | "adjusting";
  point_index?: number;
  az_direction?: KnobDir | null;
  alt_direction?: KnobDir | null;
  flags?: string[];
};

export default function PolarView() {
  const polar = usePolar() as NativePolar;
  const showToast = useStore((s) => s.showToast);
  const canMount = useCanControlMount(); // polar alignment slews the mount
  // UX-04: warn when the resolved polar provider is the SIMULATOR (fabricated az/alt).
  const isSimProvider = useProviders()?.polar_align?.kind === "sim";
  const running = polar.state === "running" || polar.state === "paused";

  const az = polar.az_error, alt = polar.alt_error, total = polar.total_error;
  const src = polar.source as string | null;

  // A fitted error exists once we've left the measuring phase (native emits
  // phase:"measuring" during the 3 solves; NINA/sim leave phase undefined and
  // just start streaming a non-zero error).
  const hasReading =
    polar.phase !== "measuring" && (total > 0 || polar.state === "done");
  const measuring = polar.phase === "measuring";

  // Measure→Adjust progress. point_index advances 0..2 as each solve lands;
  // adjusting (or any streamed reading) means all three are in.
  const measured = polar.phase === "adjusting"
    ? 3
    : polar.phase === "measuring"
      ? Math.min(3, (polar.point_index ?? -1) + 1)
      : hasReading ? 3 : 0;
  const adjusting = polar.phase === "adjusting" || (polar.phase === undefined && hasReading);
  const showPhase = polar.state !== "idle";

  // Tiered verdict (spec §HERO2): <2′ excellent · 2–10′ good · >10′ keep going.
  const tier = polarTier(total);
  const verdict = tier === "excellent"
    ? { led: "on" as const, tone: "text-good", text: "Excellent — stop here" }
    : tier === "good"
      ? { led: "warn" as const, tone: "text-warn", text: "Good — keep refining" }
      : { led: "bad" as const, tone: "text-bad", text: "Keep going" };

  const azHint = hasReading ? knobHint(polar.az_direction, az, "az") : null;
  const altHint = hasReading ? knobHint(polar.alt_direction, alt, "alt") : null;

  const sourceLabel = src === "nina" ? "NINA TPPA"
    : src === "native" ? "AstroDeck native"
    : src === "sim" ? "simulator" : null;

  // UX-16: local in-flight guard so a slow POST round-trip disables the trigger
  // buttons immediately (no dead-feeling double-fire before the server publishes state).
  const [busy, setBusy] = useState(false);
  const act = async (fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Tier-2 (doc 04 §6): a solve/geometry failure is sticky and unmissable —
          shape (square Led) + word, not color alone, so it survives night. */}
      {polar.state === "error" && (
        <div className="flex items-start gap-3 border border-bad bg-bad/10 px-4 py-3 rounded"
          role="alert">
          <Led state="bad" label="alignment error" />
          <div className="min-w-0">
            <p className="text-bad text-sm font-medium">Polar alignment stopped</p>
            <p className="text-dim text-xs mt-0.5">{polar.message || "the alignment could not complete"}</p>
          </div>
        </div>
      )}

      {/* TWO COLUMNS FROM sm, NOT lg. Polar alignment is done crouched at the
          mount with the phone in one hand, turning a bolt with the other, and a
          phone in landscape is 667-932px wide — under lg's 1024, so the reticle
          and the error readout used to stack and you had to scroll between the
          thing you aim and the number that says whether you are winning.
          sm (640) covers the narrowest phone landscape (SE, 667). The middle
          band gets plain even columns; the fixed 360px sidebar only returns at
          lg where there is room for it. Portrait is unchanged. */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-[1fr_360px]">
        <Panel title="Polar Alignment"
          right={
            <div className="flex items-center gap-2">
              <ProviderBadge cap="polar_align" />
              <span className={`text-[11px] tracking-widest uppercase ${
                polar.state === "done" ? "text-good"
                  : polar.state === "running" ? "text-accent blink"
                  : polar.state === "paused" ? "text-warn"
                  : polar.state === "error" ? "text-bad" : "text-dim"}`}>
                {polar.state}
              </span>
            </div>
          }>
          <PolarReticle
            az={az}
            alt={alt}
            azDir={polar.az_direction}
            altDir={polar.alt_direction}
            active={hasReading}
          />
          <p className="text-center text-xs text-dim mt-2 min-h-4">{polar.message || " "}</p>
        </Panel>

        <div className="flex flex-col gap-4">
          {/* Phase wizard: Measure (3 solve tiles) → Adjust */}
          {showPhase && (
            <Panel title="Phase" right={sourceLabel && (
              <span className="text-[11px] tracking-widest uppercase text-dim">{sourceLabel}</span>
            )}>
              <div className="flex items-center gap-2 flex-wrap">
                <div className="flex items-center gap-1.5">
                  {[1, 2, 3].map((n) => {
                    const filled = measured >= n;
                    return (
                      <span key={n}
                        className={`led-letter ${filled ? "bg-good text-accent-ink" : "border border-line text-faint"}`}
                        aria-label={`solve ${n} ${filled ? "done" : "pending"}`}>
                        {n}
                      </span>
                    );
                  })}
                </div>
                <span className={`text-[11px] tracking-widest uppercase ${adjusting ? "text-dim" : "text-accent"}`}>
                  measure
                </span>
                <span className="text-faint">→</span>
                <span className={`text-[11px] tracking-widest uppercase ${adjusting ? "text-accent" : "text-faint"}`}>
                  adjust
                </span>
              </div>
              {measuring && (
                <p className="text-xs text-dim mt-2">Plate-solving the mount's axis — hold steady.</p>
              )}
            </Panel>
          )}

          <Panel title="Total error">
            <div className="flex items-baseline gap-2 mb-1">
              <span className={`font-display font-semibold text-5xl mono tabular-nums ${
                hasReading ? "text-accent" : "text-faint"}`}>
                {hasReading ? total.toFixed(1) : "—"}
              </span>
              <span className="text-dim text-sm">arcmin</span>
            </div>

            {hasReading ? (
              <>
                <div className="flex items-center gap-2 mt-2">
                  <Led state={verdict.led} label={verdict.text} />
                  <span className={`text-sm font-medium ${verdict.tone}`}>{verdict.text}</span>
                </div>
                {/* WHICH ADJUSTMENT, not how good it is — the verdict above
                    already says that. Its thresholds (30′/10′/1′) are its own
                    and deliberately do not match the verdict's 2′/10′: above 30′
                    no bolt has the travel to fix it, so "keep going" would send
                    the user turning a knob that cannot reach. */}
                <p className="text-xs text-dim mt-1.5 leading-relaxed">
                  {polarInstruction(total)}
                </p>
              </>
            ) : measuring || running ? (
              <div className="flex items-center gap-2 mt-2">
                <Led state="busy" label="measuring" />
                <span className="text-sm text-dim">{measuring ? "Measuring axis…" : "Waiting for solve…"}</span>
              </div>
            ) : (
              /* UX-19: idle — no busy LED / "Waiting for solve…" before Start is pressed. */
              <p className="text-sm text-faint mt-2">Not started — press Start Alignment to measure.</p>
            )}

            {/* Which way to turn each bolt — arrow + magnitude + word, from the
                native engine's knob labels (fallback: the error's sign). */}
            <div className="flex flex-col mt-4">
              <div className="flex items-center gap-3 border-t border-line py-2.5">
                <span className="label w-16">Azimuth</span>
                <span className="text-accent text-base w-4 text-center">{azHint?.arrow ?? ""}</span>
                <span className="text-dim text-xs">{azHint?.text ?? "az bolt"}</span>
                <span className="mono text-sm tabular-nums ml-auto">
                  {hasReading ? `${Math.abs(az).toFixed(1)}′` : "—"}
                </span>
              </div>
              <div className="flex items-center gap-3 border-t border-line py-2.5">
                <span className="label w-16">Altitude</span>
                <span className="text-accent text-base w-4 text-center">{altHint?.arrow ?? ""}</span>
                <span className="text-dim text-xs">{altHint?.text ?? "alt bolt"}</span>
                <span className="mono text-sm tabular-nums ml-auto">
                  {hasReading ? `${Math.abs(alt).toFixed(1)}′` : "—"}
                </span>
              </div>
            </div>

            {polar.progress > 0 && polar.progress < 1 && (
              <div className="progress-track mt-4">
                <div className="progress-fill" style={{ width: `${polar.progress * 100}%` }} />
              </div>
            )}
          </Panel>

          <Panel title="Control" right={!canMount && <ReadOnlyBadge />}>
            <p className="text-xs text-dim mb-3 leading-relaxed">
              Runs three-point polar alignment: rotate in RA, plate-solve, and stream the
              live error here as you turn the mount's altitude / azimuth bolts. The engine in
              use is shown in the header.
            </p>
            {isSimProvider && (
              <p className="text-xs text-warn mb-3 leading-relaxed border border-warn/40 bg-warn/5 px-2.5 py-2">
                Simulator provider — alignment values are fabricated, not measured from your sky.
                Bundle or install a plate solver (ASTAP) for a real polar alignment.
              </p>
            )}
            <div className="flex flex-col gap-2">
              <button className="btn btn-accent" disabled={!canMount || running || busy}
                onClick={() => act(() => api.post("/api/polar/start"))}>
                <Icon name="align" size={14} className="inline -mt-0.5 mr-1" />Start Alignment
              </button>
              <div className="grid grid-cols-2 gap-2">
                {polar.state === "paused" ? (
                  <button className="btn" disabled={!canMount || !running || busy}
                    onClick={() => act(() => api.post("/api/polar/resume"))}>Resume</button>
                ) : (
                  <button className="btn" disabled={!canMount || !running || busy}
                    onClick={() => act(() => api.post("/api/polar/pause"))}>Pause</button>
                )}
                <button className="btn btn-danger" disabled={!canMount || !running || busy}
                  onClick={() => act(() => api.post("/api/polar/stop"))}>Stop</button>
              </div>
            </div>
            {polar.state === "done" && (
              <p className="text-good text-xs mono mt-3">✓ aligned to {total.toFixed(1)}′ total error</p>
            )}
          </Panel>

          {/* Guide view so the user can watch the field during alignment. */}
          <GuideFramePreview compact />
        </div>
      </div>
    </div>
  );
}
