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
  usePreviews,
  useSelectedPreviewId,
  useStretch,
  useViewport,
} from "../store";
import { VCurve } from "../components/graphs";
import { PreviewStage } from "../components/preview/PreviewStage";
import { FocusVerdict } from "../components/preview/FocusVerdict";
import { FrameStats } from "../components/preview/FrameStats";
import { Field, Panel, Stat } from "../components/ui";
import { HELP } from "../help";

export default function FocusView() {
  const status = useStatus();
  const focus = useFocus();
  const showToast = useStore((s) => s.showToast);

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

  const foc = status?.focuser;
  const pos = foc?.position ?? 0;
  const running = focus?.state === "running";

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
          right={running && <span className="text-accent text-[11px] blink tracking-widest uppercase">measuring…</span>}>
          <VCurve points={focus?.points ?? []} best={focus?.best ?? null} />
          {focus?.state === "done" && focus.best && (
            <p className="text-good text-xs mono mt-2">
              ✓ best focus {focus.best.position}{focus.best.hfr ? ` · HFR ${focus.best.hfr.toFixed(2)} px` : ""}
            </p>
          )}
          {focus?.state === "failed" && (
            <p className="text-bad text-xs mono mt-2">✗ autofocus failed — check stars in frame</p>
          )}
        </Panel>
      </div>

      <div className="flex flex-col gap-4">
        <Panel title="Focuser">
          <div className="flex items-end justify-between mb-4">
            <Stat label="position" value={foc ? pos : "—"} />
            <Stat label="max" value={foc?.max ?? "—"} />
            <Stat label="temp" value={foc?.temperature?.toFixed(1) ?? "—"} unit="°C" />
          </div>
          <div className="grid grid-cols-3 gap-2 mb-3">
            {[-1000, -100, -10, 10, 100, 1000].map((d) => (
              <button key={d} className="btn tap min-h-[44px] mono !normal-case" disabled={!foc || running}
                onClick={() => moveTo(pos + d)}>
                {d > 0 ? `+${d}` : d}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-[1fr_auto_auto] gap-2 items-end">
            <Field label="Go to position">
              <input className="field" placeholder={String(pos)} value={absTarget}
                onChange={(e) => setAbsTarget(e.target.value)} />
            </Field>
            <button className="btn tap min-h-[44px]" disabled={!foc || !absTarget || running}
              onClick={() => moveTo(Number(absTarget))}>Go</button>
            {/* Halt is urgent motion-stop -> stays 1-tap (R9). */}
            <button className="btn btn-danger tap min-h-[44px]"
              onClick={() => act(() => api.post("/api/focuser/halt"))}>Halt</button>
          </div>
        </Panel>

        <Panel title="Autofocus">
          <div className="grid grid-cols-2 gap-3 mb-4">
            <Field label="Exposure (s)">
              <input className="field" value={afExposure} onChange={(e) => setAfExposure(e.target.value)} />
            </Field>
            <Field label="Step size" hint={HELP.stepSize}>
              <input className="field" value={afStep} onChange={(e) => setAfStep(e.target.value)} />
            </Field>
          </div>
          <button className="btn btn-accent w-full tap-lg min-h-[56px]" disabled={!foc || running}
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
        </Panel>
      </div>
    </div>
  );
}
