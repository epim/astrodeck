import { useMemo, useRef, useState } from "react";
import { api } from "../api";
import {
  useStore,
  useStatus,
  useFocus,
  useHfrThresholds,
  useLinkDown,
  useLivePreview,
  useLivePreviewId,
  useNight,
  useOverlays,
  usePlan,
  usePreviews,
  useSelectedPreviewId,
  useStretch,
  useViewport,
} from "../store";
import type { FocusEvent } from "../types";
import { VCurve, type FocusFit } from "../components/graphs";
import { ProviderBadge } from "../components/ProviderBadge";
import { PreviewStage } from "../components/preview/PreviewStage";
import { FocusVerdict, AutofocusVerdict } from "../components/preview/FocusVerdict";
import { FrameStats } from "../components/preview/FrameStats";
import { Field, Panel, Stat } from "../components/ui";
import { useCanControlCapture } from "../lib/caps";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import { HELP } from "../help";

export default function FocusView() {
  const status = useStatus();
  const focus = useFocus();
  const showToast = useStore((s) => s.showToast);
  const canFocus = useCanControlCapture(); // focuser/autofocus is imaging-control class

  // Live preview so manual focus is not blind (spec §10). Read-only here: zoom/pan
  // + verdict, no stretch/overlay controls (those are the Capture surface).
  const shown = useLivePreview();
  const previews = usePreviews();
  const selectedId = useSelectedPreviewId();
  const liveId = useLivePreviewId();
  const viewport = useViewport();
  const stretch = useStretch();
  const overlays = useOverlays();
  const { good: hfrGood, warn: hfrWarn } = useHfrThresholds();
  const night = useNight();
  const linkDown = useLinkDown();
  const setViewport = useStore((s) => s.setViewport);
  const selectPreview = useStore((s) => s.selectPreview);
  const focusControls = useRef<{ fit: () => void; hundred: () => void; zoomIn: () => void; zoomOut: () => void } | null>(
    null,
  );
  const pinned = selectedId != null && selectedId !== liveId;
  const prevFrame = useMemo(() => {
    if (!shown) return null;
    const idx = previews.findIndex((p) => p.id === shown.id);
    return idx > 0 ? previews[idx - 1] : null;
  }, [shown, previews]);
  const [absTarget, setAbsTarget] = useState("");
  const [afExposure, setAfExposure] = useState("2");
  const [afStep, setAfStep] = useState("350");

  const plan = usePlan();
  const foc = status?.focuser;
  const pos = foc?.position ?? 0;
  const running = focus?.state === "running";

  // The additive `fit` (method/R²/curve/trendlines) + `message` ride on the raw
  // `focus` event; types.ts FocusEvent stays untouched, so read them via a cast —
  // same pattern the store uses for `status.providers` (useProviders).
  const focusExt = focus as (FocusEvent & { fit?: FocusFit | null; message?: string }) | null;
  const focusFit = focusExt?.fit ?? null;
  const focusMessage = focusExt?.message ?? null;
  // Pixel scale for the arcsec HFR in the verdict: prefer computed optics, fall
  // back to the live frame's scale when the sensor reports it.
  const pixelScale = status?.optics?.image_scale_arcsec_px ?? shown?.pixel_scale_arcsec ?? null;
  const refocusArmed = plan.autofocus_every > 0 || plan.refocus_on_temp_delta_c > 0;

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
  };
  const moveTo = (p: number) => act(() => api.post("/api/focuser/move", { position: Math.round(p) }));

  return (
    <div className="grid gap-4 md:grid-cols-[1fr_300px]">
      <div className="flex flex-col gap-4">
        {/* live preview so manual focus is not blind (spec §10) */}
        <Panel title="Live Preview" right={<FocusVerdict preview={shown} prev={prevFrame} hfrGood={hfrGood} hfrWarn={hfrWarn} />}>
          <div className="flex flex-col gap-2">
            <PreviewStage
              compact
              preview={shown}
              viewport={viewport}
              setViewport={setViewport}
              stretch={stretch}
              overlays={overlays}
              hfrGood={hfrGood}
              hfrWarn={hfrWarn}
              night={night}
              linkDown={linkDown}
              pinned={pinned}
              newSincePinned={pinned && selectedId != null ? previews.filter((p) => p.id > selectedId).length : 0}
              onReturnToLive={() => selectPreview(null)}
              onControls={(c) => (focusControls.current = c)}
            />
            <div className="flex items-center gap-1 preview-toolbar">
              <button className="btn !px-2.5 min-h-11" aria-label="Zoom out" onClick={() => focusControls.current?.zoomOut()}>−</button>
              <button className="btn !px-2.5 min-h-11" aria-label="Zoom in" onClick={() => focusControls.current?.zoomIn()}>+</button>
              <button className="btn !px-2.5 min-h-11 text-[11px]" onClick={() => focusControls.current?.fit()}>Fit</button>
              <button className="btn !px-2.5 min-h-11 text-[11px]" onClick={() => focusControls.current?.hundred()}>100%</button>
            </div>
            {shown && <FrameStats preview={shown} hfrGood={hfrGood} hfrWarn={hfrWarn} compact />}
          </div>
        </Panel>

        <Panel title="V-Curve · HFR vs Position"
          right={
            <div className="flex items-center gap-2">
              {running && <span className="text-accent text-[11px] blink tracking-widest uppercase">measuring…</span>}
              <ProviderBadge cap="autofocus" />
            </div>
          }>
          <VCurve points={focus?.points ?? []} best={focus?.best ?? null} fit={focusFit} />
          {(focus?.state === "done" || focus?.state === "failed") && (
            <div className="mt-3">
              <AutofocusVerdict
                state={focus.state}
                hfr={focus.best?.hfr ?? null}
                r2={focusFit?.r2 ?? null}
                method={focusFit?.method ?? null}
                pixelScaleArcsec={pixelScale}
                hfrGood={hfrGood}
                hfrWarn={hfrWarn}
                message={focusMessage}
              />
            </div>
          )}
        </Panel>
      </div>

      <div className="flex flex-col gap-4">
        <Panel title="Focuser" right={!canFocus && <ReadOnlyBadge />}>
          <div className="flex items-end justify-between mb-4">
            <Stat label="position" value={foc ? pos : "—"} />
            <Stat label="max" value={foc?.max ?? "—"} />
            <Stat label="temp" value={foc?.temperature?.toFixed(1) ?? "—"} unit="°C" />
          </div>
          <div className="grid grid-cols-3 gap-2 mb-3">
            {[-1000, -100, -10, 10, 100, 1000].map((d) => (
              <button key={d} className="btn tap min-h-[44px] mono !normal-case" disabled={!canFocus || !foc || running}
                onClick={() => moveTo(pos + d)}>
                {d > 0 ? `+${d}` : d}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-[1fr_auto_auto] gap-2 items-end">
            <Field label="Go to position">
              <input className="field" placeholder={String(pos)} value={absTarget} disabled={!canFocus}
                onChange={(e) => setAbsTarget(e.target.value)} />
            </Field>
            <button className="btn tap min-h-[44px]" disabled={!canFocus || !foc || !absTarget || running}
              onClick={() => moveTo(Number(absTarget))}>Go</button>
            {/* Halt is urgent motion-stop -> stays 1-tap (R9). Disabled for viewers
                (they can't have a focuser move in flight to halt). */}
            <button className="btn btn-danger tap min-h-[44px]" disabled={!canFocus}
              onClick={() => act(() => api.post("/api/focuser/halt"))}>Halt</button>
          </div>
        </Panel>

        <Panel title="Autofocus" right={!canFocus && <ReadOnlyBadge />}>
          <div className="grid grid-cols-2 gap-3 mb-4">
            <Field label="Exposure (s)">
              <input className="field" value={afExposure} disabled={!canFocus} onChange={(e) => setAfExposure(e.target.value)} />
            </Field>
            <Field label="Step size" hint={HELP.stepSize}>
              <input className="field" value={afStep} disabled={!canFocus} onChange={(e) => setAfStep(e.target.value)} />
            </Field>
          </div>
          <button className="btn btn-accent w-full tap-lg min-h-[56px]" disabled={!canFocus || !foc || running}
            onClick={() => act(() => api.post("/api/focuser/autofocus", {
              exposure_s: Number(afExposure) || 2,
              step: Number(afStep) || 350,
            }))}>
            {running ? "Running…" : "◎ Run Autofocus"}
          </button>
          <p className="text-[11px] text-dim mt-3 leading-relaxed">
            Sweeps 4 steps each side of current position, measures star HFR,
            fits the V-curve and drives to its minimum.
          </p>
          <p className="mono text-[11px] text-faint mt-2">
            {refocusArmed
              ? `Refocus armed:${plan.autofocus_every > 0 ? ` every ${plan.autofocus_every} frames` : ""}` +
                `${plan.autofocus_every > 0 && plan.refocus_on_temp_delta_c > 0 ? " ·" : ""}` +
                `${plan.refocus_on_temp_delta_c > 0 ? ` Δtemp ${plan.refocus_on_temp_delta_c}°C` : ""}`
              : "Refocus: manual only (set cadence in the plan)"}
          </p>
        </Panel>
      </div>
    </div>
  );
}
